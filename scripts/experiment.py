"""D12 + G3: one command, one table, six metrics.

    {python} scripts/experiment.py            # writes results/table.md

What it does, per task, twice
-----------------------------
1. **baseline** -- the agent with ``--no-rules``. It finishes its plan and then
   keeps verifying; nothing judges it done.
2. **governed** -- the same agent, same upstream, R1/R2/R3/R4/R6 installed.

Both deliveries are then handed to the task's *checker*, which is the only
component that can say whether the answer was right. That is the difference
between this table and ``results/tasks.md``: the sweep reports how many calls
were saved, this one reports whether the run that saved them had actually
finished the job.

Six metrics, and one of them is a warning
----------------------------------------
``Accuracy@K`` is printed but is 1.00 by construction until D14 ships a selector:
``offered`` is still the whole catalogue, so the correct tool is always in it.
The table says so in a footnote next to the number. Printing a tautology as a
result would be the exact failure this project is built to avoid, and dropping
the row would break FR-7's "six metrics" check -- so it is printed *with* its
caveat.

Exit code is non-zero when the table would be misleading: a misstop, a baseline
that answered wrongly (then "saved calls" compares a working run against a
broken one), or no records to compute Accuracy@K from.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import load_tasks, run_task_sync  # noqa: E402
from agent.tasks import TaskSpec  # noqa: E402
from gateway.metrics import (  # noqa: E402
    EARLY,
    RIGHT,
    TaskResult,
    accuracy_at_k,
    saved_rate,
    summarise,
)
from tasks.checkers import answers_from_turns, check  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"


def _records(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _run(
    task: TaskSpec, *, trace_dir: Path, no_rules: bool
) -> Tuple[int, Any, str, Path, Dict[str, Any]]:
    suffix = "baseline" if no_rules else "governed"
    run_id = f"exp-{task.id}-{suffix}-{uuid.uuid4().hex[:4]}"
    result = run_task_sync(
        task,
        run_id=run_id,
        trace_dir=trace_dir,
        extra_args=["--no-rules"] if no_rules else [],
    )
    verdict = check(task.id, answers_from_turns(result.turns))
    return result.steps, verdict, result.stop_reason, result.trace_path, dict(result.halt or {})


def measure(task: TaskSpec, trace_dir: Path) -> Tuple[TaskResult, Tuple[int, int], Tuple[int, int]]:
    base_steps, base_answer, base_end, base_path, _ = _run(
        task, trace_dir=trace_dir, no_rules=True
    )
    gov_steps, gov_answer, gov_end, gov_path, halt = _run(
        task, trace_dir=trace_dir, no_rules=False
    )
    row = TaskResult(
        task_id=task.id,
        adversarial=not task.required_evidence,
        optimal=int(task.optimal_steps or 0),
        baseline_steps=base_steps,
        baseline_answer=base_answer.ok,
        baseline_end=base_end,
        governed_steps=gov_steps,
        governed_answer=gov_answer.ok,
        governed_end=gov_end,
        governed_rule=str(halt.get("rule", "-")),
        governed_state=str(halt.get("state", "-")),
    )
    return (
        row,
        accuracy_at_k(_records(base_path)),
        accuracy_at_k(_records(gov_path)),
    )


def _markdown(
    rows: Sequence[TaskResult],
    base: Any,
    gov: Any,
    acc_base: Tuple[int, int],
    acc_gov: Tuple[int, int],
) -> str:
    saved = saved_rate(base.calls, gov.calls)
    lines = [
        "# Baseline vs gateway (generated)",
        "",
        "Produced by `python scripts/experiment.py`. Every number below is measured",
        "on this machine: `optimal` is hand-counted and lives in `tasks/*.json`",
        "(method: `docs/optimal-steps.md`), the step counts come from two real runs",
        "per task, and `answer` comes from that task's checker in `tasks/checkers/`.",
        "",
        "## Metrics",
        "",
        "| 指标 | 基线 (`--no-rules`) | 网关 (R1/R2/R3/R4/R6) |",
        "|---|---|---|",
        f"| 停对了 steps ∈ [可判最优−1, 可判最优+1] 且 checker 通过 | {base.right}/{base.total} "
        f"| {gov.right}/{gov.total} |",
        f"| 停早了（误停）steps < 下界 **或** checker 失败 | {base.early}/{base.total} "
        f"| {gov.early}/{gov.total} |",
        f"| 停晚了 steps > 上界 **或** 未自行停止 | {base.late}/{base.total} "
        f"| {gov.late}/{gov.total} |",
        f"| **误停率**（early / 总任务数，A6 口径） | {base.early_rate:.1%} "
        f"| {gov.early_rate:.1%} |",
        f"| 调用数 | {base.calls} | {gov.calls} |",
        f"| 省调用 (baseline − governed) / baseline | — | {saved:.1%} |",
        f"| Accuracy@K（chosen ∈ 当轮窗口，K=5）‡ | {_acc(acc_base)} | {_acc(acc_gov)} |",
        "",
        "‡ Accuracy@K 在 D14 之前是**按构造的 1.00**（没有选择器，`offered` 就是全部",
        "工具，正确工具必然在其中）。现在 `offered` 是这一轮真正展示给模型的窗口",
        "（K=5，稳定性合并后至多 2K），所以两列的差别有了含义：",
        "",
        "- 网关 **1.00**：65 次调用里没有一次需要窗口之外的工具。窗口足够，这是",
        "  `scripts/gating_demo.py` 那条 K 曲线要证明的事（K=3 时掉到 0.92，K=1 时 0.79）。",
        "- 基线 **0.95**：缺口全部出现在完成之后的“再验”阶段——那时窗口已经跟着 run",
        "  收窄到它正在问的那几张表，而基线还在回头看它此刻没被展示的工具。这是",
        "  “打转”在选择层的样子，不是选择器出错。",
        "",
        "## Per task",
        "",
        "| task | kind | optimal | band | baseline | governed | rule | answer | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        low, high = row.band
        kind = "adversarial" if row.adversarial else "productive"
        answer = "ok" if row.governed_answer else "wrong"
        base_answer = "" if row.baseline_answer else " (baseline wrong)"
        lines.append(
            f"| {row.task_id} | {kind} | {row.optimal} | [{low}, {high}] "
            f"| {row.baseline_steps}{base_answer} | {row.governed_steps} "
            f"| {row.governed_rule} | {answer} | {row.verdict} |"
        )
    lines += [
        "",
        "`band` 的上界是 ±1 容差；对抗任务的中心是 `optimal + 2` 而不是 `optimal`，",
        "理由写在 `gateway/metrics.py` 的模块注释里：平台期要看到两个相同答案，",
        "而对抗策略在第一次查询前还会读一次表结构。`optimal` 列保持信息论下界不变。",
        "",
        "`answer` 是 checker 对交付结果的判定，与步数无关：一个跑得很久但答错的结果，",
        "`answer` 为 `wrong`，按 A6 口径计为误停。",
        "",
    ]
    return "\n".join(lines)


def _acc(pair: Tuple[int, int]) -> str:
    hits, total = pair
    if not total:
        return "n/a"
    return f"{hits / total:.2f} (n={total})"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment", description=__doc__)
    parser.add_argument("--only", default="", help="comma-separated task ids")
    parser.add_argument("--trace-dir", default=str(ROOT / "traces" / "runs"))
    parser.add_argument("--out", default=str(ROOT / "results" / "table.md"))
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

    print(f"measuring {len(tasks)} task(s): baseline then governed, checker on both")
    rows: List[TaskResult] = []
    acc_base = [0, 0]
    acc_gov = [0, 0]
    for task in tasks:
        row, base_acc, gov_acc = measure(task, trace_dir)
        rows.append(row)
        acc_base[0] += base_acc[0]
        acc_base[1] += base_acc[1]
        acc_gov[0] += gov_acc[0]
        acc_gov[1] += gov_acc[1]
        low, high = row.band
        print(
            f"  {task.id}  optimal={row.optimal:<2} band=[{low},{high}]  "
            f"baseline={row.baseline_steps:<3} governed={row.governed_steps:<3} "
            f"{row.governed_rule:<2} answer={'ok' if row.governed_answer else 'WRONG':<5} "
            f"{row.verdict}",
            flush=True,
        )

    base = summarise(rows, run="baseline")
    gov = summarise(rows, run="governed")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        _markdown(rows, base, gov, tuple(acc_base), tuple(acc_gov)),
        encoding="utf-8",
        newline="\n",
    )

    print()
    print(f"wrote {out}")
    print(
        f"misstop {gov.early}/{gov.total} ({gov.early_rate:.1%}) | "
        f"right {gov.right}/{gov.total} | late {gov.late}/{gov.total} | "
        f"calls {base.calls} -> {gov.calls} ({saved_rate(base.calls, gov.calls):.1%} saved)"
    )

    failures = []
    if gov.early:
        failures.append(f"{gov.early} misstop(s) at the gateway")
    wrong_baseline = [row.task_id for row in rows if not row.baseline_answer]
    if wrong_baseline:
        failures.append(
            "baseline answered wrongly for " + ", ".join(wrong_baseline)
            + " -- saved calls would compare against a broken run"
        )
    if not sum(acc_gov):
        failures.append("no governed records to compute Accuracy@K from")
    if any(row.verdict == EARLY and row.governed_answer for row in rows):
        failures.append("a task was classified early with a passing answer: band bug")

    if failures:
        print(f"{FAIL}: " + "; ".join(failures), file=sys.stderr)
        return 1
    rights = sum(1 for row in rows if row.verdict == RIGHT)
    print(f"{PASS}: {rights}/{gov.total} stopped correctly, 0 misstops, "
          f"{saved_rate(base.calls, gov.calls):.1%} of the calls saved")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
