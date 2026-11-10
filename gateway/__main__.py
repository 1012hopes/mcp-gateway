"""Process entry point: one run, one trace file, exit codes that mean something.

    {python} -m gateway --config config/gateway.json

Exit codes:

* ``0``  run finished (the agent closed the stream)
* ``3``  **refused to start**: the trace file cannot be opened. No call is made
          without a place to record it, so the gateway exits instead of running
          blind. This is the "fail closed" half of DESIGN 1.5 that a test can
          check without simulating a disk failure -- just take the write
          permission away.
* ``4``  started but aborted mid-run because a record could not be persisted

stdout carries the MCP stream and nothing else. Every diagnostic here goes to
stderr; a print() to stdout would look like a hung client rather than a bug.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

import anyio

from .config import GatewayConfig, load_config, repo_root
from .evidence import EvidenceModel, load_evidence_model
from .registry import RegistryError, load_baseline
from .rules import (
    BudgetExhausted,
    DuplicateCall,
    EvidenceComplete,
    EvidenceUnreachable,
    NoProgress,
)
from .session import Budget, RunSession, new_run_id, new_session_id
from .stop import StopController
from .trace.sink import JsonlSink, TraceChainError, TraceWriteError
from .transport import Gateway
from .upstream import StdioUpstream
from .version import UNKNOWN_MODEL_ID

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNWRITABLE = 3
EXIT_ABORTED = 4


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway", description="MCP gateway: termination control + verifiable trace"
    )
    parser.add_argument("--config", default=None, help="path to gateway.json")
    parser.add_argument("--run-id", default=None, help="override the generated run id")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--parent-run-id", default=None)
    parser.add_argument(
        "--task-id",
        default=None,
        help="the task this first run serves. A later _meta.gateway.task naming "
        "a different task starts a new run (FR-12), with its own trace file",
    )
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument(
        "--goal",
        default="",
        help="the run's goal in words, used only to rank tools (A1). Not written "
        "to the trace: it belongs to the task file, and a second copy of it here "
        "would be a second place to change it",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="EV_TYPE",
        help="evidence type this task needs; repeatable. An undeclared type "
        "refuses to start rather than degrading the run (see evidence.json)",
    )
    parser.add_argument("--max-calls", type=int, default=24)
    parser.add_argument("--max-wall-ms", type=int, default=600_000)
    parser.add_argument(
        "--no-rules",
        action="store_true",
        help="run with an empty controller: the baseline an experiment compares against",
    )
    return parser


def build_controller(
    evidence: EvidenceModel, *, enabled: bool = True
) -> StopController:
    """The single assembly point for stop criteria (DESIGN 2.2).

    One entry per rule -- no registry object, no import side effects. A rule set
    that assembled itself would make ``rules_version`` depend on import order,
    and the stamp in the trace is the only thing that keeps two runs comparable.

    ``enabled=False`` is not a debug switch: the experiment needs a run governed
    by nothing to answer "how many steps does this task take without the
    gateway", and reproducing D6's numbers must not require reverting a commit.
    """
    if not enabled:
        return StopController(())
    return StopController(
        (
            DuplicateCall(),                 # 20  pre   same call again
            EvidenceComplete(),              # 30  post  the task is done
            EvidenceUnreachable(evidence),   # 35  pre   it can never be done
            NoProgress(),                    # 40  post  learning nothing
            BudgetExhausted(),               # 50  post  out of room
        )
    )


def prepare(config: GatewayConfig, args: argparse.Namespace) -> RunSession:
    """Open the sink **before** any upstream is contacted."""
    run_id = args.run_id or new_run_id()
    trace_dir = Path(args.trace_dir) if args.trace_dir else config.trace_dir
    if not trace_dir.is_absolute():
        trace_dir = repo_root() / trace_dir
    trace_path = trace_dir / f"{run_id}.jsonl"

    # Raises OSError if the location is not writable -- on purpose. A run that
    # cannot write evidence must not begin, or "recorded" stops meaning anything.
    sink = JsonlSink(trace_path, run_id=run_id)

    return RunSession(
        sink=sink,
        run_id=run_id,
        session_id=args.session_id or new_session_id(),
        parent_run_id=args.parent_run_id,
        task_id=args.task_id,
        model_id=args.model_id or config.model_id or UNKNOWN_MODEL_ID,
        budget=Budget(max_calls=args.max_calls, max_wall_ms=args.max_wall_ms),
        required=set(args.require or ()),
    )


async def _serve(gateway: Gateway) -> None:
    await gateway.run()


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except (OSError, ValueError, KeyError) as exc:
        _log(f"config error: {exc}")
        return EXIT_USAGE

    # The evidence model is loaded before the sink is opened. A run whose
    # required facts cannot even be named must not leave a trace file behind:
    # the file would say "a run happened here" about a run that never could.
    try:
        evidence = load_evidence_model(config.evidence_model)
    except (OSError, ValueError) as exc:
        _log(f"evidence model error ({config.evidence_model}): {exc}")
        return EXIT_USAGE

    # A typo in --require is a refuse-to-start, not a degraded run. Treating it
    # as "unreachable evidence" would let every task silently end DEGRADED at
    # step 1, which is the failure mode of a gateway that half-works (DESIGN
    # 2.6 pitfall 4: refusing to start beats starting wrongly).
    unknown = sorted(set(args.require or ()) - set(evidence.types))
    if unknown:
        _log(
            f"unknown evidence type(s): {', '.join(unknown)}; "
            f"declared in {config.evidence_model}: {', '.join(evidence.types)}"
        )
        return EXIT_USAGE

    try:
        session = prepare(config, args)
    except TraceChainError as exc:
        _log(f"refusing to start: {exc}")
        return EXIT_UNWRITABLE
    except OSError as exc:
        _log(f"refusing to start: trace is not writable ({exc})")
        _log("fail-closed: a call that cannot be recorded will not be executed")
        return EXIT_UNWRITABLE

    upstreams = [
        StdioUpstream(
            spec.name,
            spec.command,
            spec.args,
            env=spec.env,
            cwd=spec.cwd,
            call_timeout_s=spec.call_timeout_s,
        )
        for spec in config.upstreams
    ]

    # A malformed baseline is fatal on purpose: without it, drift detection is
    # silently off, and traces written today become incomparable with yesterday's
    # without anyone noticing. Refusing to start is the cheaper failure.
    try:
        baseline = load_baseline(config.schema_manifest)
    except (OSError, ValueError, RegistryError) as exc:
        _log(f"schema baseline error ({config.schema_manifest}): {exc}")
        return EXIT_USAGE

    try:
        gateway = Gateway(
            config,
            upstreams,
            session,
            controller=build_controller(evidence, enabled=not args.no_rules),
            evidence=evidence,
            baseline=baseline,
            goal=args.goal,
        )
    except ValueError as exc:
        _log(f"config error: {exc}")
        return EXIT_USAGE

    _log(f"gateway {session.gateway_version} | run={session.run_id} | {session.trace_path}")
    _log(
        f"task: {session.task_id or '(undeclared)'} | "
        f"upstreams: {', '.join(u.name for u in upstreams)} | rules={session.rules_version}"
    )
    _log(
        f"evidence: {evidence.version} ({', '.join(evidence.types)}) | "
        f"required: {', '.join(sorted(session.required)) or '(none)'}"
    )
    _log(
        "schema baseline: "
        + ("frozen, drift will freeze tools" if baseline is not None else "absent (no drift check)")
    )
    if config.gating.enabled:
        _log(
            f"gating: on (k={config.gating.k}, m={config.gating.m}, "
            f"index={config.gating.index}) | goal: {args.goal or '(none)'}"
        )
    else:
        _log("gating: off (offered = the whole catalogue)")

    try:
        anyio.run(_serve, gateway)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        # The *last* run, not the one this function opened: a declared task
        # change starts a new run with its own trace file (FR-12), and reporting
        # the first one here would print stats for a run that ended hours ago.
        final = gateway.session
        final.sink.close()
        stats = final.stats()
        _log("--- " + json.dumps(stats, ensure_ascii=False))
        # The invariant a reviewer should check: nothing was delivered that was
        # not recorded. It must hold both on a clean run and on an aborted one.
        if final.results_delivered != final.records_written:
            _log(
                "INVARIANT BROKEN: results_delivered != records_written "
                f"({final.results_delivered} != {final.records_written})"
            )

    if final.aborted:
        return EXIT_ABORTED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
