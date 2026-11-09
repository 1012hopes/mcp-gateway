"""The controller: ordering, short circuit, and the log of what was tried.

The rules here are fakes on purpose. What is under test is the *plumbing* --
that priority decides, that the first verdict wins, that a rule which never ran
never appears in ``attempted``, and that a stage mismatch cannot silently
disable a rule. The real criteria arrive in D7-D9 and get their own tests.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.session import Budget, CallSignature  # noqa: E402
from gateway.stop import PRE, POST, StopContext, StopController, StopVerdict  # noqa: E402


def context(**overrides) -> StopContext:
    base = dict(
        run_id="run-x",
        turn=3,
        tool="sqlite.query",
        args_hash="h1",
        evidence=frozenset(),
        required=frozenset(),
        history=(),
        budget=Budget(),
        calls_used=2,
        wall_ms=10,
    )
    base.update(overrides)
    return StopContext(**base)


class FakeRule:
    def __init__(
        self,
        name: str,
        priority: int,
        stage: str = POST,
        verdict: Optional[StopVerdict] = None,
    ) -> None:
        self.name = name
        self.priority = priority
        self.stage = stage
        self.verdict = verdict
        self.calls: List[str] = []

    def check(self, ctx: StopContext) -> Optional[StopVerdict]:
        self.calls.append(ctx.tool)
        return self.verdict


def verdict(rule: str, state: str = "HALTED_COMPLETE", decision: str = "halt_complete"):
    return StopVerdict(rule=rule, decision=decision, state=state, reason=f"{rule} fired")


class OrderingTests(unittest.TestCase):
    def test_runs_in_priority_order_regardless_of_registration(self):
        late = FakeRule("late", 50)
        early = FakeRule("early", 10)
        controller = StopController([late, early])
        outcome = controller.evaluate(POST, context())
        self.assertEqual(outcome.attempted, ("early", "late"))
        self.assertFalse(outcome.hit)

    def test_first_verdict_wins_and_stops_evaluation(self):
        first = FakeRule("first", 10, verdict=verdict("first"))
        second = FakeRule("second", 20, verdict=verdict("second"))
        third = FakeRule("third", 30)
        outcome = StopController([second, third, first]).evaluate(POST, context())

        self.assertTrue(outcome.hit)
        self.assertEqual(outcome.verdict.rule, "first")
        # The rule after the winner must not have been evaluated -- and
        # therefore must not claim to have been.
        self.assertEqual(outcome.attempted, ("first",))
        self.assertEqual(third.calls, [])

    def test_duplicate_priority_is_refused(self):
        # Equal priorities make the verdict depend on tuple order, which is
        # invisible in the trace. Refusing at assembly time is cheaper than
        # debugging a replay that disagrees with the original run.
        with self.assertRaises(ValueError):
            StopController([FakeRule("a", 20), FakeRule("b", 20)])

    def test_duplicate_name_is_refused(self):
        with self.assertRaises(ValueError):
            StopController([FakeRule("a", 10), FakeRule("a", 20)])

    def test_unknown_stage_is_refused(self):
        with self.assertRaises(ValueError):
            StopController([FakeRule("a", 10, stage="sideways")])


class StageTests(unittest.TestCase):
    def test_stage_split_keeps_global_order(self):
        # R5/R1 before the call, R4/R2/R3 after. Concatenating the two stages
        # gives the execution order a reviewer expects.
        pre = FakeRule("pre", 20, stage=PRE)
        post = FakeRule("post", 30, stage=POST)
        controller = StopController([post, pre])

        self.assertEqual(controller.evaluate(PRE, context()).attempted, ("pre",))
        self.assertEqual(controller.evaluate(POST, context()).attempted, ("post",))

    def test_rule_in_the_wrong_stage_never_fires(self):
        # A completeness rule that declared PRE would never see evidence.
        # The controller cannot fix that, but it must not hide it either: the
        # rule simply is not evaluated, and ``attempted`` says so.
        pre_only = FakeRule("complete", 30, stage=PRE, verdict=verdict("complete"))
        outcome = StopController([pre_only]).evaluate(POST, context())
        self.assertFalse(outcome.hit)
        self.assertEqual(outcome.attempted, ())


class EmptyControllerTests(unittest.TestCase):
    def test_no_rules_means_no_attempts_and_no_hit(self):
        outcome = StopController(()).evaluate(POST, context())
        self.assertFalse(outcome.hit)
        self.assertEqual(outcome.attempted, ())

    def test_version_names_the_installed_rules(self):
        self.assertEqual(StopController(()).version, "rules-v0(none)")
        controller = StopController([FakeRule("b", 20), FakeRule("a", 10)])
        self.assertEqual(controller.version, "rules-v0(a,b)")


class ContextTests(unittest.TestCase):
    def test_missing_is_required_minus_evidence(self):
        ctx = context(required=frozenset({"sql.rows", "sql.schema"}), evidence=frozenset({"sql.rows"}))
        self.assertEqual(ctx.missing, frozenset({"sql.schema"}))

    def test_seen_finds_an_identical_call(self):
        ctx = context(history=(CallSignature("sqlite.query", "h0"),))
        self.assertTrue(ctx.seen("sqlite.query", "h0"))
        self.assertFalse(ctx.seen("sqlite.query", "h1"))

    def test_context_is_frozen(self):
        # A rule that could mutate the run would make replay depend on order.
        ctx = context()
        with self.assertRaises(Exception):
            ctx.tool = "other"  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
