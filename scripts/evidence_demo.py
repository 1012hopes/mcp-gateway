"""D9 in one command: a run that stops because the task is *finished*.

    {python} scripts/evidence_demo.py

Three runs, one claim each:

1. **governed** -- t01 with the rules. R4 halts on the call that completes the
   evidence set, and the answer it halted on is checked against the task. This is
   the acceptance criterion: ``required ⊆ evidence`` firing is worth nothing if
   the evidence does not answer the question.
2. **baseline** -- the same task with ``--no-rules``. It reaches the same
   evidence and then spends five more calls re-verifying. That gap is what R4 is
   for, and it is the number a comparison table needs.
3. **degraded** -- a configuration where the only tool that can produce
   ``sql.rows.repos`` is absent. R6 refuses the first call and files the run as
   ``DEGRADED``, naming the missing producer, instead of burning the budget on a
   task that cannot finish.

Exit code 0 only if all of that holds.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import load_tasks, run_task_sync  # noqa: E402
from agent.policy import Action, ScriptedPolicy  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402

TASK_ID = "t01"


def _records(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _print_steps(records: List[Dict[str, Any]]) -> None:
    for record in records:
        check = record["stop_check"]
        added = ",".join(record["evidence_added"]) or "-"
        missing = ",".join(check["missing"]) or "-"
        print(
            f"  {record['seq']:>2}. {record['chosen']:<24} "
            f"+{added:<20} still missing: {missing}"
        )


def check_t01_answer(turns: Sequence[Any]) -> Tuple[bool, str]:
    """The task-level check: did the run actually answer t01?

    Deliberately independent of the evidence model. The model proves the *shape*
    of what was read; only this proves the answer. D12 turns it into a per-task
    checker; here it is inline so the demo has no dependency on work not yet
    done.
    """
    incidents = None
    stars: Dict[str, Any] = {}
    for turn in turns:
        payload = turn.payload or {}
        rows = payload.get("rows")
        if not isinstance(rows, list):
            continue
        if rows and isinstance(rows[0], dict) and "severity" in rows[0]:
            incidents = rows
        elif rows and isinstance(rows[0], dict) and "stars" in rows[0]:
            stars = {row["name"]: row["stars"] for row in rows}

    if incidents is None:
        return False, "no turn returned incident rows"
    if sorted(row["id"] for row in incidents) != [2, 4]:
        return False, f"wrong unresolved incidents: {[row.get('id') for row in incidents]}"
    expected = {"maximhq/bifrost": 9100, "1012hopes/mcp-gateway": 1}
    if stars != expected:
        return False, f"wrong star counts: {stars}"

    severities = {row["repo"]: row["severity"] for row in incidents}
    return True, f"incidents {severities} + stars {stars}"


def _run_degraded(trace_dir: Path, run_id: str) -> Any:
    """t01 against a configuration that cannot produce every required fact."""
    with tempfile.TemporaryDirectory() as tmp:
        config = {
            "gateway": {"name": "mcp-gateway", "trace_dir": "traces/runs"},
            "upstreams": [
                {
                    "name": "dirty",
                    "command": "{python}",
                    "args": ["-m", "mcp_servers.dirty_schema_server"],
                    "cwd": "{root}",
                }
            ],
        }
        cfg = Path(tmp) / "gateway.json"
        cfg.write_text(json.dumps(config), encoding="utf-8")
        task = {t.id: t for t in load_tasks()}[TASK_ID]
        task = task.__class__(
            id=f"{task.id}-degraded",
            goal=task.goal,
            required_evidence=("sql.rows.repos",),
            max_turns=6,
        )
        return run_task_sync(
            task,
            policy=ScriptedPolicy([Action("dirty.status", {})]),
            run_id=run_id,
            trace_dir=trace_dir,
            extra_args=["--config", str(cfg)],
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D9: completeness, and the run that cannot finish")
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument(
        "--run-id",
        default=None,
        help="prefix for the three trace files (default: unique, so a second run "
        "cannot append to the first one's chain)",
    )
    args = parser.parse_args(argv)

    # A trace file is a chain, and the sink *resumes* an existing one. A fixed
    # default would append run 2 to run 1 and leave a file that verifies while
    # describing two different runs -- so the default is unique and an explicit
    # id is the caller saying they know what they are doing.
    run_id = args.run_id or f"run-d9-demo-{uuid.uuid4().hex[:6]}"

    tasks = {task.id: task for task in load_tasks()}
    if TASK_ID not in tasks:
        print(f"task {TASK_ID} not found in tasks/")
        return 2
    task = tasks[TASK_ID]
    trace_dir = Path(args.trace_dir) if args.trace_dir else None

    print(f"task {task.id}: {task.goal}")
    print(f"required evidence: {', '.join(task.required_evidence)}")
    print("=" * 78)

    print("governed -- rules installed")
    governed = run_task_sync(task, run_id=f"{run_id}-governed", trace_dir=trace_dir)
    governed_records = _records(governed.trace_path)
    _print_steps(governed_records)
    print(f"  stop: {governed.stop_reason} after {governed.steps} steps")
    if governed.halt:
        print(f"  halt: rule={governed.halt.get('rule')} state={governed.halt.get('state')}")
        print(f"        reason={governed.halt.get('reason')}")
    print("=" * 78)

    print("baseline -- no rules installed (same task, same policy)")
    baseline = run_task_sync(
        task, run_id=f"{run_id}-baseline", trace_dir=trace_dir, extra_args=["--no-rules"]
    )
    baseline_records = _records(baseline.trace_path)
    _print_steps(baseline_records)
    print(f"  stop: {baseline.stop_reason} after {baseline.steps} steps")
    print("=" * 78)

    print("degraded -- the tool that produces sql.rows.repos is not available")
    degraded = _run_degraded(
        trace_dir or (ROOT / "traces" / "runs"), f"{run_id}-degraded"
    )
    degraded_records = _records(degraded.trace_path)
    _print_steps(degraded_records)
    print(f"  stop: {degraded.stop_reason} after {degraded.steps} steps")
    if degraded.halt:
        print(f"  halt: rule={degraded.halt.get('rule')} state={degraded.halt.get('state')}")
        print(f"        reason={degraded.halt.get('reason')}")

    # -- checks -----------------------------------------------------------
    answer_ok, answer_detail = check_t01_answer(governed.turns)
    last = governed_records[-1]
    baseline_evidence = sorted(
        {item for record in baseline_records for item in record["stop_check"]["evidence"]}
    )

    checks = {
        "governed stops on R4": bool(governed.halt) and governed.halt.get("rule") == "R4",
        "state is HALTED_COMPLETE": governed.halt.get("state") == "HALTED_COMPLETE",
        "nothing is missing at the stop": last["stop_check"]["missing"] == [],
        "the last call is what completed it": last["evidence_added"] == ["sql.rows.repos"],
        "the answer checks out": answer_ok,
        "the baseline reached the same evidence": baseline_evidence
        == sorted(task.required_evidence),
        "governed is shorter than baseline": governed.steps < baseline.steps,
        "degraded stops on R6": bool(degraded.halt) and degraded.halt.get("rule") == "R6",
        "degraded is classified, not merely stopped": degraded.halt.get("state") == "DEGRADED",
        "degraded names the missing producer": "sqlite.query"
        in degraded.halt.get("reason", ""),
        "degraded costs one call, not the budget": degraded.steps == 1,
        "all traces verify": all(
            verify_path(r.trace_path).ok for r in (governed, baseline, degraded)
        ),
    }

    print("=" * 78)
    print(f"governed trace: {governed.trace_path}")
    print(f"baseline trace: {baseline.trace_path}")
    print(f"degraded trace: {degraded.trace_path}")
    print(f"rules: {governed_records[0]['rules_version']}")
    print(
        f"steps: {baseline.steps} -> {governed.steps}"
        f"  (saved {baseline.steps - governed.steps})"
    )
    print(f"checker: {answer_detail}")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    print("-" * 78)
    if all(checks.values()):
        print("PASS: the run stopped because the task was finished, and the answer proves it")
        return 0
    print("FAIL: see the checks above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
