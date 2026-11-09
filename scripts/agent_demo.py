"""D7 acceptance in one command: the same task twice -- without rules, then with.

    {python} scripts/agent_demo.py
    {python} scripts/agent_demo.py --max-calls 3     # force R3 instead of R1

Two runs of the *same* task and the *same* policy. The only difference is whether
the gateway has criteria installed, which is what makes the step counts
comparable: any change in behaviour is attributable to the rules, not to a
different agent.

What it prints and checks:

1. the baseline overshoots and ends because the policy ran out of ideas (the D6
   measurement, still reproducible through ``--no-rules``);
2. the governed run stops on a rule, and the trace says which one and why;
3. the duplicate R1 fires on is still *visible* in the baseline -- otherwise
   "caught" would mean "the harness avoided it";
4. every record carries ``offered`` / ``chosen`` / ``prev_hash``, and both traces
   verify. The rules change when a run ends, never what gets recorded.

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


def _load_records(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _print_turns(result: Any) -> None:
    for turn in result.turns:
        flag = "ERR" if turn.is_error else "ok "
        text = " ".join(turn.text.split())
        if len(text) > 92:
            text = text[:92] + "…"
        print(f"  {turn.index}. [{flag}] {turn.tool:<22} {text}")


def _structure_checks(result: Any, records: List[Dict[str, Any]]) -> Dict[str, bool]:
    return {
        "one record per delivered call": len(records) == result.steps,
        "every record has offered": all(r["offered"] for r in records),
        "chosen is inside offered": all(r["chosen"] in r["offered"] for r in records),
        "every record chains": all(r["prev_hash"] for r in records),
        "seq is contiguous": [r["seq"] for r in records] == list(range(1, len(records) + 1)),
        "chain verifies": verify_path(result.trace_path).ok,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="D7: baseline vs governed, same task, same policy"
    )
    parser.add_argument("--task", default=None, help="task id (default: first task)")
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="call budget for the governed run (default: gateway default)",
    )
    args = parser.parse_args(argv)

    tasks = load_tasks()
    if not tasks:
        print("no tasks found in tasks/")
        return 2
    task = next((t for t in tasks if t.id == args.task), tasks[0])
    trace_dir = Path(args.trace_dir) if args.trace_dir else None
    run_id = args.run_id or "run-d7-demo"

    print(f"task {task.id}: {task.goal}")
    print(f"required evidence: {', '.join(task.required_evidence) or '(none)'}")
    print("=" * 78)

    print("baseline -- no rules installed")
    baseline = run_task_sync(
        task, run_id=f"{run_id}-baseline", trace_dir=trace_dir, extra_args=["--no-rules"]
    )
    _print_turns(baseline)
    baseline_records = _load_records(baseline.trace_path)
    print(f"  stop: {baseline.stop_reason} after {baseline.steps} steps")
    print("=" * 78)

    print("governed -- rules installed")
    extra = ["--max-calls", str(args.max_calls)] if args.max_calls else []
    governed = run_task_sync(
        task, run_id=f"{run_id}-governed", trace_dir=trace_dir, extra_args=extra
    )
    _print_turns(governed)
    governed_records = _load_records(governed.trace_path)
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
    repeated = {key: n for key, n in counts.items() if n > 1}

    checks: Dict[str, bool] = {}
    checks.update(_structure_checks(baseline, baseline_records))
    checks.update(_structure_checks(governed, governed_records))
    checks["baseline ends when the policy runs out"] = (
        baseline.stop_reason == "policy_exhausted"
    )
    checks["baseline contains a repeated call"] = bool(repeated)
    checks["governed stops on a rule"] = bool(governed.halt)
    checks["governed is shorter than baseline"] = governed.steps < baseline.steps

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
        print("PASS: the rules changed when the run ends, not what was recorded")
        return 0
    print("FAIL: see the checks above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
