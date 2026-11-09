"""G2 in one command: a task that cannot be answered, run with and without rules.

    {python} scripts/g2_demo.py

The agent in ``t02`` asks the database for a repo that is not there. Its strategy
is "say it differently" -- twelve distinct SQL statements, one identical empty
answer. Nothing about any single call is wrong, which is why the stop has to be
decided outside the agent (DESIGN 0.2).

What it prints and checks:

1. the baseline spends its whole turn budget (11 calls) and never learns;
2. the governed run stops on R2 after the *second* identical answer;
3. the plateau is visible in the trace as two adjacent equal ``state_hash``
   values -- the verdict is backed by the file, not just asserted;
4. the baseline contains no exact repeat, so this is R2's catch and not R1's
   (a rule that fires for the wrong reason reports a right number and teaches
   the wrong lesson).

Exit code 0 only if all of that holds.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import load_tasks, run_task_sync  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402

TASK_ID = "t02"


def _load_records(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _print_steps(records: List[Dict[str, Any]]) -> None:
    """One line per step, with the fingerprint prefix and the plateau marked."""
    previous = None
    for record in records:
        state = (record["stop_check"].get("state_hash") or "-")[:10]
        mark = "  <- same state" if state == previous and state != "-" else ""
        print(f"  {record['seq']:>2}. {record['chosen']:<16} state={state}{mark}")
        previous = state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G2: the spin, stopped")
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument("--run-id", default="run-g2-demo")
    args = parser.parse_args(argv)

    tasks = {task.id: task for task in load_tasks()}
    if TASK_ID not in tasks:
        print(f"task {TASK_ID} not found in tasks/")
        return 2
    task = tasks[TASK_ID]
    trace_dir = Path(args.trace_dir) if args.trace_dir else None

    print(f"task {task.id}: {task.goal}")
    print(f"policy: {task.policy}   turn budget: {task.max_turns}")
    print("=" * 78)

    print("baseline -- no rules installed")
    baseline = run_task_sync(
        task, run_id=f"{args.run_id}-baseline", trace_dir=trace_dir, extra_args=["--no-rules"]
    )
    baseline_records = _load_records(baseline.trace_path)
    _print_steps(baseline_records)
    print(f"  stop: {baseline.stop_reason} after {baseline.steps} steps")
    print("=" * 78)

    print("governed -- rules installed")
    governed = run_task_sync(task, run_id=f"{args.run_id}-governed", trace_dir=trace_dir)
    governed_records = _load_records(governed.trace_path)
    _print_steps(governed_records)
    print(f"  stop: {governed.stop_reason} after {governed.steps} steps")
    if governed.halt:
        print(
            "  halt: rule={rule} state={state}\n        reason={reason}".format(
                rule=governed.halt.get("rule", "?"),
                state=governed.halt.get("state", "?"),
                reason=governed.halt.get("reason", ""),
            )
        )

    # -- checks -----------------------------------------------------------
    counts = Counter((r["chosen"], r["args_hash"]) for r in baseline_records)
    hashes = [r["stop_check"].get("state_hash") for r in governed_records]

    checks = {
        "baseline spends the whole turn budget": (
            baseline.steps == task.max_turns and baseline.stop_reason == "max_turns"
        ),
        "baseline makes no exact repeat (so this is not R1's catch)": not any(
            n > 1 for n in counts.values()
        ),
        "every baseline answer after the first is identical": len(
            {r["result_digest"] for r in baseline_records[1:]}
        )
        == 1,
        "governed stops on R2": bool(governed.halt) and governed.halt.get("rule") == "R2",
        "governed state is HALTED_NO_PROGRESS": (
            governed.halt.get("state") == "HALTED_NO_PROGRESS"
        ),
        "governed is shorter than baseline": governed.steps < baseline.steps,
        "the plateau is in the trace": len(hashes) >= 2 and hashes[-1] == hashes[-2],
        "both traces verify": (
            verify_path(baseline.trace_path).ok and verify_path(governed.trace_path).ok
        ),
    }

    print("=" * 78)
    print(f"baseline trace: {baseline.trace_path}")
    print(f"governed trace: {governed.trace_path}")
    print(f"rules: {governed_records[0]['rules_version'] if governed_records else '?'}")
    print(
        f"steps: {baseline.steps} -> {governed.steps}"
        f"  (saved {baseline.steps - governed.steps})"
    )
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    print("-" * 78)
    if all(checks.values()):
        print("PASS: the run was stopped on evidence it produced itself")
        return 0
    print("FAIL: see the checks above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
