"""A6: the six metrics, as arithmetic on measured rows.

Why this is a module and not forty lines inside the experiment script: the
classification rules are the part of the table a reader is entitled to
interrogate, and they should be testable without spawning twenty gateway
processes. ``scripts/experiment.py`` gathers the inputs; everything that decides
what a number *means* lives here.

The one place this module departs from A6's literal wording is the band for
adversarial tasks. A6 says "步数 ∈ [最优−1, 最优+1]", and for the six adversarial
tasks 最优 is 1 by the D11 definition (a call must contribute a required
``ev_type``; they require none). No repetition-based rule can fire at step 1:
detecting a plateau takes two identical answers, and the adversarial policy also
spends one call on the schema before its first query. So the earliest a rule can
*legitimately* stop is 最优+2, and a table measuring the literal band would report
6/6 "stopped late" for the six runs that stopped exactly as designed.

Both facts are structural rather than tuned -- change the policy's opening move
and the number changes with it -- and ``optimal`` stays untouched in the task
file so the adjustment is visible in the table instead of folded into a hand
count. ``tests/test_metrics.py`` pins the arithmetic; the docs record the
deviation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

__all__ = [
    "ADVERSARIAL_PENALTY",
    "EARLY",
    "LATE",
    "RIGHT",
    "Summary",
    "TaskResult",
    "accuracy_at_k",
    "saved_rate",
    "summarise",
    "verdict_for",
]

RIGHT = "right"
EARLY = "early"
LATE = "late"

#: Steps a repetition-based rule needs beyond the information minimum: one
#: opening structural call, plus the second identical answer that proves a
#: plateau. See the module docstring.
ADVERSARIAL_PENALTY = 2

#: Stop reasons that mean "the run ended because something ran out", not because
#: anything judged it finished. A6 counts these as stopped-late.
NEVER_STOPPED = ("max_turns", "policy_exhausted")


def verdict_for(steps: int, *, answer_ok: bool, band: Tuple[int, int], hit_budget: bool) -> str:
    """Classify one run, in A6's order of precedence.

    ``answer_ok`` first: a wrong answer is a miss at any length, so a run cannot
    buy its way into "stopped correctly" by stopping at the right step. The
    per-task table keeps the answer column separate so a run that over-ran *and*
    was wrong is still visible as both.
    """
    low, high = band
    if not answer_ok:
        return EARLY
    if steps < low:
        return EARLY
    if steps > high or hit_budget:
        return LATE
    return RIGHT


@dataclass(frozen=True)
class TaskResult:
    """What one task produced, from both runs, plus the checker's opinion."""

    task_id: str
    adversarial: bool
    optimal: int
    baseline_steps: int
    baseline_answer: bool
    baseline_end: str
    governed_steps: int
    governed_answer: bool
    governed_end: str
    governed_rule: str = "-"
    governed_state: str = "-"

    @property
    def detectable(self) -> int:
        return self.optimal + (ADVERSARIAL_PENALTY if self.adversarial else 0)

    @property
    def band(self) -> Tuple[int, int]:
        centre = self.detectable
        return (max(0, centre - 1), centre + 1)

    def verdict_of(self, steps: int, answer_ok: bool, end: str) -> str:
        return verdict_for(
            steps, answer_ok=answer_ok, band=self.band, hit_budget=end in NEVER_STOPPED
        )

    @property
    def baseline_verdict(self) -> str:
        return self.verdict_of(self.baseline_steps, self.baseline_answer, self.baseline_end)

    @property
    def verdict(self) -> str:
        """The governed run's verdict -- the column the table is about."""
        return self.verdict_of(self.governed_steps, self.governed_answer, self.governed_end)


@dataclass(frozen=True)
class Summary:
    total: int
    right: int
    early: int
    late: int
    calls: int

    @property
    def early_rate(self) -> float:
        """误停率: early / **total**, never / stopped. A6's denominator."""
        return self.early / self.total if self.total else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tasks": self.total,
            "right": self.right,
            "early": self.early,
            "late": self.late,
            "early_rate": round(self.early_rate, 4),
            "calls": self.calls,
        }


def summarise(rows: Iterable[TaskResult], *, run: str = "governed") -> Summary:
    """Aggregate one of the two runs. ``run`` is ``"governed"`` or ``"baseline"``."""
    rows = list(rows)
    if run == "governed":
        verdicts = [row.verdict for row in rows]
        calls = sum(row.governed_steps for row in rows)
    elif run == "baseline":
        verdicts = [row.baseline_verdict for row in rows]
        calls = sum(row.baseline_steps for row in rows)
    else:
        raise ValueError(f"unknown run {run!r}")
    counts = {RIGHT: 0, EARLY: 0, LATE: 0}
    for verdict in verdicts:
        counts[verdict] += 1
    return Summary(
        total=len(rows),
        right=counts[RIGHT],
        early=counts[EARLY],
        late=counts[LATE],
        calls=calls,
    )


def saved_rate(baseline_calls: int, governed_calls: int) -> float:
    """(baseline − governed) / baseline. Zero baseline is zero saving, not a div error."""
    if not baseline_calls:
        return 0.0
    return (baseline_calls - governed_calls) / baseline_calls


def accuracy_at_k(records: Sequence[Mapping[str, Any]]) -> Tuple[int, int]:
    """(chosen ∈ offered, records) -- A6's Accuracy@K, counted from the trace.

    Since D14 ``offered`` is the gating window of that turn, so this is a real
    measurement: the share of calls whose tool the window was showing. Two
    readings matter and they are not the same number:

    * a **governed** run at 1.00 says the window always contained what the run
      needed -- the gate cost no selection on this task set;
    * a **baseline** run below 1.00 (0.95 on the twenty tasks) misses only in
      the post-completion "verify again" phase, where the run goes back to tools
      the window has moved away from. That is waste seen from the selection
      layer, and it is why the two columns differ at all.

    Before D14 both were 1.00 by construction -- ``offered`` was the whole
    catalogue -- and the table said so in a footnote instead of pretending.
    """
    hits = total = 0
    for record in records:
        offered = record.get("offered") or []
        chosen = record.get("chosen")
        if chosen is None:
            continue
        total += 1
        hits += 1 if chosen in offered else 0
    return hits, total
