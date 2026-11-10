"""D12: the arithmetic behind the table, tested without spawning a gateway.

The experiment script is 200 lines of process plumbing around six numbers. If
those numbers are wrong the table still renders, still says "20/20", and no
assertion anywhere fires -- so the classifying rules get their own tests here,
on synthetic rows where the expected answer is obvious.

Three of these are guards against specific ways a metrics module lies:

* a wrong answer counted as "stopped correctly" because the step count happened
  to be in range (the failure mode R4 alone cannot rule out);
* a misstop rate computed over *stopped* runs instead of *all* runs (A6's
  denominator; with a denominator of "runs that stopped" every rate looks small);
* the adversarial band silently absorbing the ±1 tolerance (centre must move,
  not the width).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.metrics import (  # noqa: E402
    ADVERSARIAL_PENALTY,
    EARLY,
    LATE,
    RIGHT,
    TaskResult,
    accuracy_at_k,
    saved_rate,
    summarise,
    verdict_for,
)


def _row(**overrides) -> TaskResult:
    base = dict(
        task_id="t01",
        adversarial=False,
        optimal=3,
        baseline_steps=9,
        baseline_answer=True,
        baseline_end="policy_exhausted",
        governed_steps=4,
        governed_answer=True,
        governed_end="halted",
        governed_rule="R4",
        governed_state="HALTED_COMPLETE",
    )
    base.update(overrides)
    return TaskResult(**base)


class VerdictTests(unittest.TestCase):
    def test_the_right_answer_at_the_right_step_is_right(self):
        self.assertEqual(
            verdict_for(4, answer_ok=True, band=(2, 4), hit_budget=False), RIGHT
        )

    def test_a_wrong_answer_is_a_miss_at_any_length(self):
        # In band, wrong answer: the step count must not rescue it.
        for steps in (2, 3, 4):
            with self.subTest(steps=steps):
                self.assertEqual(
                    verdict_for(steps, answer_ok=False, band=(2, 4), hit_budget=False),
                    EARLY,
                )

    def test_under_the_band_is_early(self):
        self.assertEqual(
            verdict_for(1, answer_ok=True, band=(2, 4), hit_budget=False), EARLY
        )

    def test_over_the_band_is_late(self):
        self.assertEqual(
            verdict_for(5, answer_ok=True, band=(2, 4), hit_budget=False), LATE
        )

    def test_never_stopping_is_late_even_when_the_step_count_looks_fine(self):
        # A run that ended because a cap ran out was not judged finished, whatever
        # its length: this is the DEGRADED-style classification error A6 warns of.
        self.assertEqual(
            verdict_for(3, answer_ok=True, band=(2, 4), hit_budget=True), LATE
        )


class BandTests(unittest.TestCase):
    def test_a_productive_task_is_centred_on_its_optimal(self):
        self.assertEqual(_row().band, (2, 4))

    def test_an_adversarial_task_is_centred_two_steps_later(self):
        row = _row(adversarial=True, optimal=1, governed_steps=3)
        self.assertEqual(row.detectable, 1 + ADVERSARIAL_PENALTY)
        self.assertEqual(row.band, (2, 4))
        self.assertEqual(row.verdict, RIGHT, "3 steps on t02 is a correct stop")

    def test_the_tolerance_stays_one_step_wide(self):
        # If the adversarial fix had been "widen the tolerance", a 6-step plateau
        # would pass as correct. It is the centre that moves.
        for row in (_row(), _row(adversarial=True, optimal=1)):
            low, high = row.band
            self.assertEqual(high - low, 2)

    def test_the_lower_bound_never_goes_negative(self):
        self.assertEqual(_row(optimal=0).band[0], 0)

    def test_baseline_and_governed_are_classified_separately(self):
        row = _row()
        self.assertEqual(row.baseline_verdict, LATE, "9 steps against a band of [2,4]")
        self.assertEqual(row.verdict, RIGHT)

    def test_a_baseline_that_stopped_short_is_not_flattered(self):
        self.assertEqual(_row(baseline_steps=1).baseline_verdict, EARLY)


class SummaryTests(unittest.TestCase):
    def test_misstop_rate_divides_by_all_tasks(self):
        rows = [_row(), _row(task_id="t02", governed_steps=1), _row(task_id="t03"), _row(task_id="t04")]
        summary = summarise(rows)
        self.assertEqual(summary.early, 1)
        self.assertAlmostEqual(summary.early_rate, 1 / 4)

    def test_each_run_is_summarised_from_its_own_column(self):
        rows = [_row(baseline_steps=9, governed_steps=4)]
        self.assertEqual(summarise(rows, run="baseline").calls, 9)
        self.assertEqual(summarise(rows, run="governed").calls, 4)
        self.assertEqual(summarise(rows, run="baseline").late, 1)
        self.assertEqual(summarise(rows, run="governed").right, 1)

    def test_an_unknown_run_name_is_an_error(self):
        with self.assertRaises(ValueError):
            summarise([_row()], run="goverend")

    def test_saved_rate(self):
        self.assertAlmostEqual(saved_rate(182, 65), 117 / 182)
        self.assertEqual(saved_rate(0, 0), 0.0, "no baseline means no claim")

    def test_accuracy_at_k_ignores_records_without_a_choice(self):
        records = [
            {"offered": ["a", "b"], "chosen": "a"},
            {"offered": [], "chosen": None},
            {"offered": ["a"], "chosen": "z"},
        ]
        self.assertEqual(accuracy_at_k(records), (1, 2))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
