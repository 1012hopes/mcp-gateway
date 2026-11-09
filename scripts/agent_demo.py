"""D6 acceptance in one command: a real task, driven by an agent, fully traced.

    {python} scripts/agent_demo.py              # task 01
    {python} scripts/agent_demo.py --task t01 --trace-dir traces/runs

What it proves, in order:

1. the loop is **closed**: every call after the first is chosen from what the
   previous results said, not from a list written in advance;
2. every record carries ``offered`` (full set -- gating is D14), ``chosen`` and
   ``prev_hash``, so the step is judgeable *and* the chain is verifiable;
3. records == calls the gateway accepted, and the trace verifies;
4. the run ends because the policy ran out of ideas -- **not** because anything
   decided the task was done. That gap is what R4 closes in D9, and seeing it
   here is the point of stopping at D6 rather than pretending.

Exit code 0 only if all four hold.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import load_tasks, run_task_sync  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402


def _load_records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default=None, help="task id (default: first task)")
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    tasks = load_tasks()
    if not tasks:
        print("no tasks found in tasks/")
        return 2
    task = next((t for t in tasks if t.id == args.task), tasks[0])

    print(f"task {task.id}: {task.goal}")
    print(f"required evidence: {', '.join(task.required_evidence) or '(none)'}")
    print("-" * 78)

    result = run_task_sync(
        task, trace_dir=Path(args.trace_dir) if args.trace_dir else None, run_id=args.run_id
    )

    for turn in result.turns:
        flag = "ERR" if turn.is_error else "ok "
        text = " ".join(turn.text.split())
        if len(text) > 96:
            text = text[:96] + "…"
        print(f"{turn.index}. [{flag}] {turn.tool:<22} {text}")

    print("-" * 78)
    print(f"stop: {result.stop_reason} after {result.steps} steps")
    if result.halt:
        print(f"halt: {result.halt}")

    if not result.trace_path.exists():
        print("NO TRACE produced -- the run left no evidence behind")
        return 1

    records = _load_records(result.trace_path)
    checks = {
        "one record per delivered call": len(records) == result.steps,
        "every record has offered": all(r["offered"] for r in records),
        "chosen is inside offered": all(r["chosen"] in r["offered"] for r in records),
        "every record chains": all(r["prev_hash"] for r in records),
        "seq is contiguous": [r["seq"] for r in records] == list(range(1, len(records) + 1)),
    }
    verified = verify_path(result.trace_path)
    checks["chain verifies"] = verified.ok

    print(f"trace: {result.trace_path}")
    print(f"verify: {verified.summary()}")
    print(f"rules: {records[0]['rules_version'] if records else '?'}")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    print("-" * 78)
    if all(checks.values()):
        print("PASS: every step is judgeable, chained, and accounted for")
        return 0
    print("FAIL: see the checks above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
