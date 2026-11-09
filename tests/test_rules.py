"""D7-D9: the rules, tested as pure functions of one frozen snapshot.

These tests drive the rules directly with a hand-built ``StopContext``. That is
deliberate: every rule is a pure function of a snapshot, and a test that needed
a subprocess to see whether "the same call twice" is a duplicate would be testing
the harness. The end-to-end hit rate is asserted separately in
``test_agent_e2e.py``, because "unit tests pass" and "the rule ever fires in a
real run" are different claims and the plan calls out exactly that gap.

The completeness rule gets a disproportionate share of the cases below because
it is the only one whose failure is *silent in the flattering direction*: a
wrong subset test declares runs finished. The other rules fail by not firing,
which the end-to-end tests catch.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.evidence import EvidenceModel, EvidenceType  # noqa: E402
from gateway.limits import Budget, CallSignature  # noqa: E402
from gateway.rules import (  # noqa: E402
    BudgetExhausted,
    DuplicateCall,
    EvidenceComplete,
    EvidenceUnreachable,
    NoProgress,
)
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
    state_trail=(),
    state_hash=None,
    available=frozenset(),
    unreachable=frozenset(),
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
        state_trail=tuple(state_trail),
        state_hash=state_hash,
        available=frozenset(available),
        unreachable=frozenset(unreachable),
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


class NoProgressTests(unittest.TestCase):
    rule = NoProgress()

    def test_declares_the_post_stage(self):
        # The question is whether the *answer* was new. Before forwarding there
        # is no answer, so a pre-stage R2 could only compare questions.
        self.assertEqual(self.rule.stage, POST)
        self.assertEqual(self.rule.priority, 40)

    def test_first_call_cannot_be_stagnation(self):
        self.assertIsNone(self.rule.check(ctx(state_hash="h", state_trail=())))

    def test_an_unchanged_state_halts(self):
        verdict = self.rule.check(ctx(state_hash="hA", state_trail=("hA",)))

        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.state, "HALTED_NO_PROGRESS")
        self.assertEqual(verdict.decision, "halt_no_progress")
        self.assertIn("hA", verdict.reason)

    def test_a_new_piece_of_knowledge_resets_the_clock(self):
        # Previous state differed: this call taught the run something, so no
        # matter how many calls came before, it is not stagnation.
        self.assertIsNone(
            self.rule.check(ctx(state_hash="hB", state_trail=("hA", "hA", "hA")))
        )

    def test_only_the_tail_of_the_trail_is_compared(self):
        # An old plateau must not poison a run that has since made progress.
        self.assertIsNone(
            self.rule.check(ctx(state_hash="hC", state_trail=("hA", "hA", "hB")))
        )

    def test_no_measurement_means_no_verdict(self):
        # R2 must not guess. A context without a state hash (pre-stage, or a
        # rule run out of order) gets no opinion rather than a fabricated one.
        self.assertIsNone(self.rule.check(ctx(state_trail=("hA",), state_hash=None)))

    def test_window_one_is_refused(self):
        # One silent call is how an agent routes around a dead end; halting on it
        # would stop healthy runs, and the mistake is invisible in the trace.
        with self.assertRaises(ValueError):
            NoProgress(window=1)

    def test_a_wider_window_waits_for_that_many(self):
        rule = NoProgress(window=3)
        self.assertIsNone(rule.check(ctx(state_hash="hA", state_trail=("hA",))))
        self.assertIsNone(rule.check(ctx(state_hash="hA", state_trail=("hX", "hA"))))
        self.assertIsNotNone(rule.check(ctx(state_hash="hA", state_trail=("hA", "hA"))))


class EvidenceCompleteTests(unittest.TestCase):
    rule = EvidenceComplete()

    def test_declares_the_post_stage(self):
        # Completeness is a statement about evidence, and the last piece of
        # evidence is in the result. A pre-stage R4 could only read the plan.
        self.assertEqual(self.rule.stage, POST)
        self.assertEqual(self.rule.priority, 30)

    def test_partial_evidence_is_not_a_verdict(self):
        self.assertIsNone(
            self.rule.check(
                ctx(required={"a", "b"}, evidence={"a"})
            )
        )

    def test_complete_evidence_halts_as_complete(self):
        verdict = self.rule.check(ctx(required={"a", "b"}, evidence={"a", "b"}))
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.state, "HALTED_COMPLETE")
        self.assertEqual(verdict.decision, "halt_evidence_complete")
        self.assertIn("a", verdict.reason)

    def test_extra_evidence_is_not_a_problem(self):
        # A run that learned more than asked is finished, not confused.
        self.assertIsNotNone(
            self.rule.check(ctx(required={"a"}, evidence={"a", "b", "c"}))
        )

    def test_an_empty_requirement_never_completes(self):
        # ``set() <= anything`` is true, so a bare subset test would halt every
        # evidence-free run on its first call -- including t02, whose purpose is
        # to be stopped by R2. This is the difference between installed and
        # correct.
        self.assertIsNone(self.rule.check(ctx(required=set(), evidence=set())))
        self.assertIsNone(self.rule.check(ctx(required=set(), evidence={"a"})))

    def test_completeness_outranks_progress_and_budget(self):
        # A run that finishes on its last call is a completion, not a budget
        # failure: priority 30 < 40 < 50 is what makes the metric honest.
        controller = StopController(
            (BudgetExhausted(), NoProgress(), EvidenceComplete())
        )
        snapshot = ctx(
            required={"a"},
            evidence={"a"},
            state_hash="hA",
            state_trail=("hA",),
            calls_used=3,
            budget=Budget(max_calls=3, max_wall_ms=0),
        )
        post = controller.evaluate(POST, snapshot)
        self.assertEqual(post.verdict.rule, "R4")
        self.assertEqual(post.attempted, ("R4",), "R2 and R3 are never reached")


class EvidenceUnreachableTests(unittest.TestCase):
    def setUp(self):
        self.model = EvidenceModel(
            {
                "sql.rows": EvidenceType(
                    name="sql.rows",
                    why="because",
                    shapes=(("rows",),),
                    producers=("sqlite.query", "backup.query"),
                )
            }
        )
        self.rule = EvidenceUnreachable(self.model)

    def test_declares_the_pre_stage(self):
        # The fact is known before the first call, so the call is refused rather
        # than paid for -- zero upstream calls in a run that cannot finish.
        self.assertEqual(self.rule.stage, PRE)
        self.assertEqual(self.rule.priority, 35)

    def test_a_reachable_fact_is_not_a_verdict(self):
        self.assertIsNone(self.rule.check(ctx(required={"sql.rows"}, unreachable=set())))

    def test_a_task_that_requires_nothing_is_not_degraded(self):
        self.assertIsNone(self.rule.check(ctx(required=set(), unreachable=set())))

    def test_an_unreachable_fact_degrades_the_run(self):
        verdict = self.rule.check(
            ctx(required={"sql.rows"}, unreachable={"sql.rows"}, available={"sqlite.status"})
        )
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.state, "DEGRADED")
        self.assertEqual(verdict.decision, "halt_evidence_unreachable")
        self.assertIn("sqlite.query", verdict.reason, "the reason names who could have produced it")
        self.assertIn("backup.query", verdict.reason)

    def test_the_reason_names_every_missing_fact(self):
        verdict = self.rule.check(
            ctx(required={"sql.rows"}, unreachable={"sql.rows"}, available=set())
        )
        self.assertIn("sql.rows", verdict.reason)

    def test_degraded_outranks_progress(self):
        # "Cannot finish" is a better diagnosis than "is spinning", and both can
        # be true; priority 35 < 40 encodes it.
        controller = StopController(
            (NoProgress(), EvidenceUnreachable(self.model))
        )
        snapshot = ctx(
            required={"sql.rows"},
            unreachable={"sql.rows"},
            state_hash="hA",
            state_trail=("hA",),
        )
        self.assertEqual(controller.evaluate(PRE, snapshot).verdict.rule, "R6")


class ControllerOrderTests(unittest.TestCase):
    def setUp(self):
        self.controller = StopController((BudgetExhausted(), DuplicateCall()))

    def test_version_names_the_installed_set(self):
        self.assertEqual(self.controller.version, "rules-v0(R1,R3)")

    def test_the_installed_set_is_ordered_by_priority(self):
        controller = StopController((BudgetExhausted(), NoProgress(), DuplicateCall()))
        self.assertEqual(controller.names, ("R1", "R2", "R3"))
        self.assertEqual(controller.version, "rules-v0(R1,R2,R3)")

    def test_r2_wins_over_r3_when_both_would_fire(self):
        # A run that is spinning at the moment the budget runs out: the honest
        # verdict is "you were getting nowhere", not "you ran out of room".
        # Priority 40 < 50 encodes that, and R3's docstring says why it is last.
        controller = StopController((BudgetExhausted(), NoProgress(), DuplicateCall()))
        snapshot = ctx(
            state_hash="hA",
            state_trail=("hA",),
            calls_used=3,
            budget=Budget(max_calls=3, max_wall_ms=0),
        )
        post = controller.evaluate(POST, snapshot)
        self.assertEqual(post.verdict.rule, "R2")
        self.assertEqual(post.attempted, ("R2",), "R3 is never reached")

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
