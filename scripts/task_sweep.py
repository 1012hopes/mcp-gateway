"""D11: run every task twice -- with the rules and without -- and tabulate.

Why a sweep instead of twenty hand-copied numbers: the only number that means
anything is a *measured* one. ``optimal_steps`` is hand-counted and lives in the
task file; ``baseline`` and ``governed`` are produced here, by the same loop, the
same upstream and the same policy objects a reviewer can run themselves.

Two runs per task, in this order:

* ``--no-rules`` -- the agent's honest baseline. It stops when the policy runs
  out of ideas (or the turn budget does), never because anything judged it done.
* default -- the gateway with R1/R2/R3/R4/R6 installed.

The acceptance line from the plan is ``baseline >= optimal * 3``; the script
prints the ratio and exits non-zero when any task misses it, so the task set
cannot silently drift into "tasks that measure nothing" the next time someone
edits a plan.

Usage:  python scripts/task_sweep.py [--only t03,t04] [--out results/tasks.md]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import load_tasks, run_task_sync  # noqa: E402
from agent.tasks import TaskSpec  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"


@dataclass
class Row:
    task: TaskSpec
    baseline: int
    governed: int
    halt: str
    state: str
    baseline_end: str
    governed_end: str

    @property
    def adversarial(self) -> bool:
        return not self.task.required_evidence

    @property
    def optimal(self) -> int:
        return int(self.task.optimal_steps or 0)

    @property
    def ratio(self) -> float:
        return self.baseline / self.optimal if self.optimal else 0.0

    @property
    def ratio_ok(self) -> bool:
        return self.optimal > 0 and self.baseline >= self.optimal * 3

    @property
    def verdict_ok(self) -> bool:
        """Did the governed run end the way the rule set says it should?

        Productive tasks must be *complete* -- not merely stopped. A run that
        hits the budget instead means the evidence model disagrees with the
        task's own ``required_evidence``, and a table that showed the smaller
        number without this check would be reporting a failure as a win.
        """
        if self.adversarial:
            return self.halt == "R2"
        return self.halt == "R4" and self.state == "HALTED_COMPLETE"

    @property
    def ok(self) -> bool:
        return self.ratio_ok and self.verdict_ok and self.governed < self.baseline


def _one(task: TaskSpec, *, trace_dir: Path, no_rules: bool) -> tuple:
    suffix = "baseline" if no_rules else "governed"
    result = run_task_sync(
        task,
        run_id=f"{task.id}-{suffix}",
        trace_dir=trace_dir,
        extra_args=["--no-rules"] if no_rules else [],
    )
    halt = result.halt or {}
    return result.steps, halt.get("rule", "-"), halt.get("state", "-"), result.stop_reason


def sweep(tasks: Sequence[TaskSpec], trace_dir: Path) -> List[Row]:
    rows: List[Row] = []
    for task in tasks:
        base, _, _, base_end = _one(task, trace_dir=trace_dir, no_rules=True)
        gov, rule, state, gov_end = _one(task, trace_dir=trace_dir, no_rules=False)
        rows.append(
            Row(
                task=task,
                baseline=base,
                governed=gov,
                halt=rule,
                state=state,
                baseline_end=base_end,
                governed_end=gov_end,
            )
        )
        print(
            f"  {task.id}  optimal={task.optimal_steps}  baseline={base:<3}"
            f" governed={gov:<3} {rule:<3} {state:<17} {PASS if rows[-1].ok else FAIL}",
            flush=True,
        )
    return rows


def _markdown(rows: Sequence[Row]) -> str:
    lines = [
        "# Task sweep (generated)",
        "",
        "Produced by `python scripts/task_sweep.py`; the numbers are measured, not",
        "transcribed. `optimal` is hand-counted and lives in the task file -- see",
        "`docs/optimal-steps.md` for how it is counted.",
        "",
        "| task | kind | optimal | baseline | governed | ratio | halt | delivered | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        kind = "adversarial" if row.adversarial else "productive"
        lines.append(
            f"| {row.task.id} | {kind} | {row.optimal} | {row.baseline} | {row.governed} "
            f"| {row.ratio:.2f}x | {row.halt} | {row.state} | {PASS if row.ok else FAIL} |"
        )
    total_base = sum(row.baseline for row in rows)
    total_gov = sum(row.governed for row in rows)
    lines += [
        "",
        f"Totals: **{total_base} -> {total_gov} calls** across {len(rows)} tasks "
        f"({total_gov / total_base:.0%} of the baseline), "
        f"{sum(1 for row in rows if row.ok)}/{len(rows)} within the acceptance line.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="task_sweep", description=__doc__)
    parser.add_argument("--only", default="", help="comma-separated task ids")
    parser.add_argument("--trace-dir", default=str(ROOT / "traces" / "runs"))
    parser.add_argument("--out", default=str(ROOT / "results" / "tasks.md"))
    args = parser.parse_args(argv)

    tasks = load_tasks()
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        tasks = [task for task in tasks if task.id in wanted]
        missing = wanted - {task.id for task in tasks}
        if missing:
            print(f"unknown task id(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    if not tasks:
        print("no tasks found", file=sys.stderr)
        return 2

    trace_dir = Path(args.trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)

    print(f"sweeping {len(tasks)} task(s) -- baseline (--no-rules) then governed")
    rows = sweep(tasks, trace_dir)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_markdown(rows), encoding="utf-8")

    bad = [row for row in rows if not row.ok]
    print()
    print(f"wrote {out}")
    if bad:
        print(f"{FAIL}: {len(bad)} task(s) outside the acceptance line:", file=sys.stderr)
        for row in bad:
            why = []
            if not row.ratio_ok:
                why.append(f"ratio {row.ratio:.2f} < 3")
            if not row.verdict_ok:
                why.append(f"halt={row.halt} state={row.state}")
            if row.governed >= row.baseline:
                why.append("governed did not save a call")
            print(f"  {row.task.id}: {'; '.join(why)}", file=sys.stderr)
        return 1
    print(f"{PASS}: {len(rows)}/{len(rows)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
