"""D14 in one command: the window, the curve, and what it costs.

    {python} scripts/gating_demo.py

The claim (DESIGN A1) is one sentence with two halves: showing K tools per turn
must save context, and must not hide the tool the run needs. Both halves are
measured here, on the twenty tasks and on the same traces the other demos use.

What is measured, and how
-------------------------
1. **Live.** Every task is run once with gating on. Each record already carries
   the window of its turn in ``offered``; that is the ground truth.
2. **Replay.** The same call stream is re-ranked offline at K = 1, 2, 3, 5, 10,
   30 and for three index variants. At K = 5 the replay must reproduce the live
   window *exactly* -- otherwise the curve describes the demo's selector rather
   than the gateway's.
3. **The criterion.** Accuracy@K = the share of calls whose tool was in that
   turn's window. PLAN's completion criterion is that K = 5 loses at most 10%
   against the whole catalogue (which is 1.00 by construction), so the bar is
   0.90 -- reported for all calls and again for warm turns (2 and later), since
   turn 1 is a cold start and always shows everything.
4. **The near-duplicate pair.** ``wiki.search`` and ``gh.search`` share a name
   and an argument. The check is that the description separates them and the
   name alone does not: that is the honest account of why the index matters,
   and it is printed as a rank list rather than asserted in prose.

Exit code 0 only when every check passes -- including "no task's answer
changed", which is the half of the claim that a narrower window must not touch.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import load_tasks, run_task_sync  # noqa: E402
from agent.tasks import TaskSpec  # noqa: E402
from gateway.config import GatewayConfig, load_config  # noqa: E402
from gateway.evidence import EvidenceModel, load_evidence_model  # noqa: E402
from gateway.registry import Registry, build_registry, load_baseline  # noqa: E402
from gateway.selector import (  # noqa: E402
    COLD_START,
    DEFAULT_K,
    DEFAULT_M,
    INDEX_VARIANTS,
    Selector,
    Window,
    sticky_seed,
)
from gateway.transport import gating_query, result_head  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402
from gateway.upstream import StdioUpstream  # noqa: E402
from tasks.checkers import answers_from_turns, check  # noqa: E402

#: The K values the curve is drawn at. 30 is the whole catalogue in this
#: deployment, so it is the "no gating" end of the curve and must be 1.00.
KEYS = (1, 2, 3, 5, 10, 30)


def _records(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def collect_registry(config: GatewayConfig) -> Registry:
    """The live catalogue, so sizes and documents are the real ones."""
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
    async with AsyncExitStack() as stack:
        for upstream in upstreams:
            await stack.enter_async_context(upstream)
        listing = [(upstream.name, await upstream.list_tools()) for upstream in upstreams]
        return build_registry(listing, baseline=load_baseline(config.schema_manifest))


@dataclass
class Run:
    task: TaskSpec
    turns: Sequence[Any]
    records: Sequence[Dict[str, Any]]
    trace: Path
    # What the client was offered on each turn. Not derivable from the records
    # for free: it is the one thing that separates "the window reached the wire"
    # from "the trace says a window existed".
    visible: Sequence[Sequence[str]] = ()


def replay(
    run: Run,
    entries: Sequence[Any],
    *,
    k: int,
    m: int,
    variant: str,
    subjects: Dict[str, List[str]],
) -> List[Window]:
    """Recompute the window of every turn from the recorded call stream.

    A pure function of (goal, calls, catalogue): it feeds each call back in as
    history, which is exactly what the gateway did. Evidence is tracked from the
    records so the pin rule sees the same "missing" set.
    """
    selector = Selector(k=k, m=m, variant=variant, selector_subjects=subjects)
    evidence: set = set()
    recent: List[tuple] = []
    previous: Optional[Window] = None
    windows: List[Window] = []
    for index, obs in enumerate(run.turns):
        record = run.records[index]
        pins: List[str] = []
        missing = set(run.task.required_evidence) - evidence
        servable = {entry.exposed_name for entry in entries}
        for ev_type in sorted(missing):
            for producer in _MODEL.producers(ev_type):
                if producer in servable and producer not in pins:
                    pins.append(producer)
        query = gating_query(run.task.goal, recent)
        window = selector.window(
            query,
            entries,
            previous=sticky_seed(previous),
            pins=pins,
            cold_start=index == 0,
        )
        windows.append(window)
        previous = window
        evidence |= set(record.get("evidence_added") or ())
        recent.append(
            (obs.tool, obs.args, "" if obs.is_error else result_head(obs.blocks[0] if obs.blocks else ""))
        )
    return windows


_MODEL: EvidenceModel = None  # type: ignore[assignment]
_SUBJECTS: Dict[str, List[str]] = {}

def accuracy(windows: Sequence[Window], chosen: Sequence[str]) -> Tuple[int, int]:
    hits = sum(1 for window, name in zip(windows, chosen) if name in window)
    return hits, len(windows)


def schema_bytes(entries: Sequence[Any]) -> Dict[str, int]:
    sizes: Dict[str, int] = {}
    for entry in entries:
        sizes[entry.exposed_name] = len(
            json.dumps(entry.to_mcp().model_dump(mode="json"), ensure_ascii=False)
        )
    return sizes


def run_all(
    tasks: Sequence[TaskSpec], trace_dir: Path
) -> List[Run]:
    runs: List[Run] = []
    for task in tasks:
        run_id = f"gate-{task.id}-{uuid.uuid4().hex[:4]}"
        result = run_task_sync(task, run_id=run_id, trace_dir=trace_dir)
        runs.append(
            Run(
                task,
                result.turns,
                _records(result.trace_path),
                result.trace_path,
                result.visible,
            )
        )
        print(f"  {task.id}: {len(result.turns)} calls, {result.stop_reason}", flush=True)
    return runs


def main(argv: Optional[Sequence[str]] = None) -> int:
    global _MODEL, _SUBJECTS

    parser = argparse.ArgumentParser(prog="gating_demo", description=__doc__)
    parser.add_argument("--only", default="", help="comma-separated task ids")
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    args = parser.parse_args(argv)

    config = load_config()
    _MODEL = load_evidence_model(config.evidence_model)
    for ev_type in _MODEL.types:
        for producer in _MODEL.producers(ev_type):
            _SUBJECTS.setdefault(producer, []).append(ev_type)

    tasks = load_tasks()
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        tasks = [task for task in tasks if task.id in wanted]
    if not tasks:
        print("no tasks", file=sys.stderr)
        return 2

    import anyio

    registry = anyio.run(collect_registry, config)
    entries = list(registry.offered)
    sizes = schema_bytes(entries)
    full_bytes = sum(sizes.values())
    print(f"gating: k={args.k} m={DEFAULT_M} index={config.gating.index}")
    print(f"catalogue: {len(entries)} tools, {full_bytes} bytes of schema")
    print("=" * 78)

    if args.trace_dir:
        trace_dir = Path(args.trace_dir)
        trace_dir.mkdir(parents=True, exist_ok=True)
        runs = run_all(tasks, trace_dir)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory()
        runs = run_all(tasks, Path(cleanup.name))

    checks: Dict[str, bool] = {}
    chosen = [obs.tool for run in runs for obs in run.turns]
    total_calls = len(chosen)
    print("-" * 78)
    print(f"{total_calls} calls over {len(runs)} tasks")

    # -- 1. the replay agrees with the live gateway ------------------------
    live_agree = 0
    disagree: List[str] = []
    for run in runs:
        windows = replay(
            run, entries, k=args.k, m=DEFAULT_M, variant=config.gating.index, subjects=_SUBJECTS
        )
        for obs, window, record in zip(run.turns, windows, run.records):
            if record["offered"] == list(window.names):
                live_agree += 1
            else:
                disagree.append(f"{run.task.id}#{record['seq']} {obs.tool}")
    checks["the offline replay reproduces every live window"] = live_agree == total_calls
    print(f"replay == live: {live_agree}/{total_calls}" + (f" ({disagree[:3]})" if disagree else ""))

    # -- 2. the curve ------------------------------------------------------
    curve: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for variant in INDEX_VARIANTS:
        for k in KEYS:
            hits = warm_hits = warm = 0
            warm_sizes: List[int] = []
            shown = warm_bytes = 0
            for run in runs:
                windows = replay(
                    run, entries, k=k, m=max(DEFAULT_M, k), variant=variant, subjects=_SUBJECTS
                )
                for index, (window, obs) in enumerate(zip(windows, run.turns)):
                    inside = obs.tool in window
                    hits += 1 if inside else 0
                    size = sum(sizes[name] for name in window.names)
                    if index > 0 and window.reason != COLD_START:
                        warm += 1
                        warm_hits += 1 if inside else 0
                        warm_sizes.append(len(window))
                        warm_bytes += size
            curve[(variant, k)] = {
                "accuracy": hits / total_calls,
                "warm": warm_hits / warm if warm else 1.0,
                "mean_size": sum(warm_sizes) / len(warm_sizes) if warm_sizes else float(len(entries)),
                "bytes": warm_bytes / warm if warm else sum(sizes.values()),
            }

    print()
    print(f"{'index':<12}{'K':>3}  {'accuracy':>9} {'warm':>7} {'|S| warm':>9} {'bytes/turn':>11}")
    for (variant, k), row in curve.items():
        print(
            f"{variant:<12}{k:>3}  {row['accuracy']:>9.3f} {row['warm']:>7.3f} "
            f"{row['mean_size']:>9.1f} {row['bytes']:>11.0f}"
        )
    print("  (turn 1 is a cold start and shows the whole catalogue; it is excluded)")

    primary = curve[(config.gating.index, args.k)]
    checks[f"Accuracy@{args.k} >= 0.90 overall"] = primary["accuracy"] >= 0.90
    checks[f"Accuracy@{args.k} >= 0.90 on warm turns"] = primary["warm"] >= 0.90
    checks["the whole catalogue is 1.00 by construction"] = (
        curve[(config.gating.index, max(KEYS))]["accuracy"] == 1.0
    )
    checks["the enriched index beats descriptions alone"] = (
        curve[("enriched", args.k)]["accuracy"] >= curve[("description", args.k)]["accuracy"]
    )

    # -- 3. the gate did not refuse anything, and no answer changed --------
    decisions = [record["decision"] for run in runs for record in run.records]
    refused = [name for name in decisions if name.startswith("block_")]
    checks["no call was refused for being outside the window"] = not refused
    checks["every recorded call is either inside the window or says it was not"] = all(
        record["chosen"] in record["offered"] or record["decision"] == "forward_outside"
        for run in runs
        for record in run.records
    )
    # The agent re-lists every turn (D14), so ``visible`` is what it actually
    # had to plan with. Comparing it to the record is the check that the number
    # in the trace is the number the model saw, not a recomputation of it.
    checks["the agent saw exactly the window each record claims"] = all(
        set(run.visible[index]) == set(record["offered"])
        for run in runs
        for index, record in enumerate(run.records)
    )
    answers = [
        check(run.task.id, answers_from_turns(run.turns)).ok for run in runs
    ]
    checks["every task still answers correctly with the gate on"] = all(answers)
    print(f"answers: {sum(answers)}/{len(answers)} ok | outside-window calls: "
          f"{decisions.count('forward_outside')}/{len(decisions)}")

    # -- 4. cold start, pins, stability, determinism, integrity -----------
    checks["a cold start shows the whole catalogue"] = all(
        run.records[0]["offered"] == [entry.exposed_name for entry in entries]
        for run in runs
    )

    pinned_turns = pinned_ok = 0
    for run in runs:
        windows = replay(
            run, entries, k=args.k, m=DEFAULT_M, variant=config.gating.index, subjects=_SUBJECTS
        )
        seen: set = set()
        for window, obs, record in zip(windows, run.turns, run.records):
            if window.reason == COLD_START:
                # The whole catalogue: the producer is there by definition.
                seen |= set(record["evidence_added"])
                continue
            for _ev_type in set(run.task.required_evidence) - seen:
                pinned_turns += 1
                pinned_ok += 1 if window.pinned and set(window.pinned) <= set(window.names) else 0
            seen |= set(record["evidence_added"])

    checks["a required-but-missing fact keeps its producer in the window"] = (
        pinned_turns == 0 or pinned_ok == pinned_turns
    )
    print(
        f"pins: {pinned_ok}/{pinned_turns} warm turns with a missing fact kept its producer"
    )

    # Where the slots went. The task set is SQL-shaped, so the honest reading of
    # a 100% accuracy is "the window kept showing the SQL server"; this line is
    # the number behind that sentence, not a claim about all thirty tools.
    upstream_of = {entry.exposed_name: entry.upstream for entry in entries}
    slots: Dict[str, float] = {}
    counted = 0
    for run in runs:
        windows = replay(
            run, entries, k=args.k, m=DEFAULT_M, variant=config.gating.index, subjects=_SUBJECTS
        )
        for window in windows[1:]:
            counted += 1
            for name in window.names:
                upstream = upstream_of.get(name, "?")
                slots[upstream] = slots.get(upstream, 0.0) + 1
    if counted:
        print(
            "slots per warm turn: "
            + ", ".join(
                f"{name} {slots.get(name, 0.0) / counted:.1f}"
                for name in sorted({entry.upstream for entry in entries})
            )
        )

    overlaps = []
    for run in runs:
        windows = replay(
            run, entries, k=args.k, m=DEFAULT_M, variant=config.gating.index, subjects=_SUBJECTS
        )
        for previous, current in zip(windows, windows[1:]):
            if previous.names:
                overlaps.append(
                    len(set(current.names) & set(previous.names)) / len(previous.names)
                )
    mean_overlap = sum(overlaps) / len(overlaps) if overlaps else 1.0
    checks["consecutive windows keep most of what they showed"] = mean_overlap >= 0.50
    print(f"stability: {mean_overlap:.2f} of each window was already shown last turn")

    twice = [
        replay(
            run, entries, k=args.k, m=DEFAULT_M, variant=config.gating.index, subjects=_SUBJECTS
        )
        for run in runs
    ]
    again = [
        replay(
            run, entries, k=args.k, m=DEFAULT_M, variant=config.gating.index, subjects=_SUBJECTS
        )
        for run in runs
    ]
    checks["the same call stream ranks the same way twice"] = all(
        [w.names for w in first] == [w.names for w in second]
        for first, second in zip(twice, again)
    )
    checks["every trace still verifies"] = all(verify_path(run.trace).ok for run in runs)

    # -- 5. the near-duplicate pair ---------------------------------------
    print()
    print("the near-duplicate pair -- same name, same argument, different server")
    wiki_q = "search the wiki for the incident triage page"
    code_q = "search the code for the retry budget change"
    pair = {}
    for variant in ("name", "description"):
        selector = Selector(k=5, m=DEFAULT_M, variant=variant, selector_subjects=_SUBJECTS)
        pair[variant] = (
            selector.rank(wiki_q, entries)[0],
            selector.rank(code_q, entries)[0],
        )
        print(f"  {variant:<12} wiki question -> {pair[variant][0]:<14} "
              f"code question -> {pair[variant][1]}")
    checks["the description separates wiki.search from gh.search"] = pair["description"] == (
        "wiki.search",
        "gh.search",
    )
    checks["the name alone cannot"] = pair["name"] != ("wiki.search", "gh.search")

    # -- report ------------------------------------------------------------
    print()
    print("=" * 78)
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print("-" * 78)
    saving = 1 - primary["bytes"] / full_bytes
    print(
        f"context: {full_bytes} bytes of schema -> {primary['bytes']:.0f} per warm turn "
        f"({saving:.0%} less), mean window {primary['mean_size']:.1f} of {len(entries)} tools"
    )
    if cleanup is not None:
        cleanup.cleanup()
    if all(checks.values()):
        print(
            f"PASS: {len(checks)} checks -- {primary['accuracy']:.0%} of calls kept their "
            f"tool at K={args.k}, {saving:.0%} less schema per turn"
        )
        return 0
    print("FAIL: see the checks above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
