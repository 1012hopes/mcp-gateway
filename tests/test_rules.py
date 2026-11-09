"""D7: the two rules that can be decided without an evidence model.

These tests drive the rules directly with a hand-built ``StopContext``. That is
deliberate: R1 and R3 are pure functions of a frozen snapshot, and a test that
needed a subprocess to see whether "the same call twice" is a duplicate would be
testing the harness. The end-to-end hit rate is asserted separately in
``test_agent_e2e.py``, because "unit tests pass" and "the rule ever fires in a
real run" are different claims and the plan calls out exactly that gap.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.limits import Budget, CallSignature  # noqa: E402
from gateway.rules import BudgetExhausted, DuplicateCall  # noqa: E402
from gateway.stop import POST, PRE, StopContext, StopController  # noqa: E402


def ctx(
    *,
    tool="sqlite.query",
    args_hash="h1",
    history=(),
    calls_used=1,
    wall_ms=0,
    budget=None,
    evidence=frozenset(),
    required=frozenset(),
):
    return StopContext(
        run_id="run-test",
        turn=len(history) + 1,
        tool=tool,
        args_hash=args_hash,
        evidence=frozenset(evidence),
        required=frozenset(required),
        history=tuple(history),
        budget=budget or Budget(max_calls=24, max_wall_ms=0),
        calls_used=calls_used,
        wall_ms=wall_ms,
    )


class DuplicateCallTests(unittest.TestCase):
    rule = DuplicateCall()

    def test_declares_the_pre_stage(self):
        # Pre-stage is not decoration: R1's value is that the second call never
        # reaches the upstream. Post-stage would still pay for it.
        self.assertEqual(self.rule.stage, PRE)
        self.assertEqual(self.rule.priority, 20)

    def test_first_call_is_not_a_duplicate(self):
        self.assertIsNone(self.rule.check(ctx(history=())))

    def test_exact_repeat_halts(self):
        history = [CallSignature("sqlite.query", "h1")]
        verdict = self.rule.check(ctx(history=history))

        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.state, "HALTED_DUPLICATE")
        self.assertEqual(verdict.decision, "halt_duplicate")
        self.assertTrue(verdict.replay_last, "a repeated read should be answered, not refused")
        self.assertIn("step 1", verdict.reason)

    def test_the_reason_names_the_first_occurrence(self):
        history = [
            CallSignature("sqlite.list_tables", "h0"),
            CallSignature("sqlite.query", "h1"),
        ]
        verdict = self.rule.check(ctx(history=history, tool="sqlite.query", args_hash="h1"))
        self.assertIn("step 2", verdict.reason)

    def test_same_arguments_different_tool_is_not_a_duplicate(self):
        # count_rows({"table": "x"}) and describe_table({"table": "x"}) share a
        # digest. Calling the second a duplicate would hide R2's pseudo-progress.
        history = [CallSignature("sqlite.count_rows", "h1")]
        self.assertIsNone(
            self.rule.check(ctx(history=history, tool="sqlite.describe_table", args_hash="h1"))
        )

    def test_same_tool_different_arguments_is_not_a_duplicate(self):
        history = [CallSignature("sqlite.query", "h1")]
        self.assertIsNone(self.rule.check(ctx(history=history, args_hash="h2")))


class BudgetExhaustedTests(unittest.TestCase):
    rule = BudgetExhausted()

    def test_declares_the_post_stage(self):
        # The call that spends the last unit produced real evidence; the rule must
        # run after it, or that result would be thrown away.
        self.assertEqual(self.rule.stage, POST)
        self.assertEqual(self.rule.priority, 50)

    def test_room_left_is_not_a_verdict(self):
        self.assertIsNone(
            self.rule.check(ctx(calls_used=3, budget=Budget(max_calls=10, max_wall_ms=0)))
        )

    def test_last_call_of_the_budget_halts(self):
        verdict = self.rule.check(
            ctx(calls_used=10, budget=Budget(max_calls=10, max_wall_ms=0))
        )
        self.assertEqual(verdict.state, "HALTED_BUDGET")
        self.assertEqual(verdict.decision, "halt_budget")
        self.assertIn("max_calls=10", verdict.reason)

    def test_wall_clock_is_a_separate_reason(self):
        verdict = self.rule.check(
            ctx(calls_used=1, wall_ms=5000, budget=Budget(max_calls=10, max_wall_ms=5000))
        )
        self.assertIsNotNone(verdict)
        self.assertIn("max_wall_ms=5000", verdict.reason)

    def test_zero_disables_the_wall_clock(self):
        self.assertIsNone(
            self.rule.check(
                ctx(calls_used=1, wall_ms=10**9, budget=Budget(max_calls=10, max_wall_ms=0))
            )
        )

    def test_the_call_being_judged_counts(self):
        # ``calls_used`` includes this call, so max_calls=3 halts on the third.
        rule = self.rule
        self.assertIsNone(rule.check(ctx(calls_used=2, budget=Budget(max_calls=3, max_wall_ms=0))))
        self.assertIsNotNone(
            rule.check(ctx(calls_used=3, budget=Budget(max_calls=3, max_wall_ms=0)))
        )


class ControllerOrderTests(unittest.TestCase):
    def setUp(self):
        self.controller = StopController((BudgetExhausted(), DuplicateCall()))

    def test_version_names_the_installed_set(self):
        self.assertEqual(self.controller.version, "rules-v0(R1,R3)")

    def test_r1_wins_when_both_would_fire(self):
        # Both conditions hold on the third call: a repeat, and the last unit of
        # budget. Duplicate is the more specific diagnosis -- and the cheaper one,
        # since it prevents the call. Priority 20 < 50 encodes that.
        snapshot = ctx(
            history=[CallSignature("sqlite.query", "h1")],
            calls_used=3,
            budget=Budget(max_calls=3, max_wall_ms=0),
        )
        pre = self.controller.evaluate(PRE, snapshot)
        self.assertEqual(pre.verdict.rule, "R1")
        post = self.controller.evaluate(POST, snapshot)
        self.assertEqual(post.verdict.rule, "R3")

    def test_attempted_lists_only_what_ran(self):
        snapshot = ctx(calls_used=3, budget=Budget(max_calls=3, max_wall_ms=0))
        pre = self.controller.evaluate(PRE, snapshot)
        self.assertEqual(pre.attempted, ("R1",), "R1 ran and passed")
        self.assertIsNone(pre.verdict)

        post = self.controller.evaluate(POST, snapshot)
        self.assertEqual(post.attempted, ("R3",))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
