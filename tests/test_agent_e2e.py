"""D7/D8 acceptance: every rule that is installed must fire in a real run.

The plan's warning for this stage is specific: a rule that never fires raises
nothing, prints nothing, and turns every number downstream into zero. So the
assertions here are not "the rule is correct" (``test_rules.py`` covers that with
a hand-built context) but **the hit rate is greater than zero, in a subprocess,
with the real upstream**.

Five runs, five claims:

* ``BaselineRun`` -- t01 with ``--no-rules``. Nine steps, and it ends because the
  policy ran out of ideas. This is the denominator the compression numbers need,
  and it must stay reproducible after the rules exist.
* ``GovernedRun`` -- t01, five steps, halted by R1 on a call the baseline makes
  twice. The duplicate is still *visible* in the trace (the refusal is
  recorded), which is what makes "it was caught here" checkable afterwards.
* ``BudgetRun`` -- t01 with ``--max-calls 3``: R3 halts on the third call, and
  that call's result is still delivered, because it was paid for.
* ``SpinBaselineRun`` -- t02 with ``--no-rules``: eleven calls, no two alike, and
  the same empty answer every time. This is the G2 adversary.
* ``SpinGovernedRun`` -- t02 with the rules: three steps, R2 halts on the second
  identical answer, and the plateau is visible as two adjacent equal
  ``state_hash`` values in the file.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import mcp  # noqa: F401

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

if HAS_MCP:
    from agent import load_tasks, run_task_sync


def _records(path: Path) -> list:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class _RunMixin:
    """Spawn one run per class; each subclass asserts a different ending.

    A mixin rather than a ``TestCase`` subclass: unittest would collect the base
    class too and spawn a fourth run to assert the same five things a third time.
    """

    extra_args: tuple = ()
    task_id: str = "t01"

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tasks = {task.id: task for task in load_tasks()}
        cls.result = run_task_sync(
            tasks[cls.task_id],
            run_id=cls.__name__.lower(),
            trace_dir=Path(cls._tmp.name),
            extra_args=list(cls.extra_args),
        )
        cls.records = _records(cls.result.trace_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    # -- the same claims for every run -------------------------------------

    def test_tools_are_namespaced_and_offered(self):
        self.assertIn("sqlite.query", self.result.tools)

    def test_every_step_produced_a_record(self):
        self.assertEqual(len(self.records), self.result.steps)
        self.assertEqual(
            [r["seq"] for r in self.records], list(range(1, len(self.records) + 1))
        )

    def test_every_record_carries_the_fields_that_make_it_judgeable(self):
        for record in self.records:
            self.assertTrue(record["offered"], record["seq"])
            self.assertIn(record["chosen"], record["offered"])
            self.assertTrue(record["prev_hash"])
            self.assertTrue(record["args_hash"])
            self.assertTrue(record["result_digest"])

    def test_trace_verifies(self):
        from gateway.trace.verify import verify_path

        outcome = verify_path(self.result.trace_path)
        self.assertTrue(outcome.ok, outcome.summary())

    def test_no_result_payload_is_stored(self):
        raw = self.result.trace_path.read_text(encoding="utf-8")
        self.assertNotIn("maximhq/bifrost", raw, "traces must not become data copies")
        self.assertNotIn("severity", raw)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class BaselineRun(_RunMixin, unittest.TestCase):
    """No rules: the overshoot D6 measured, kept as the comparison floor."""

    extra_args = ("--no-rules",)

    def test_the_baseline_is_the_nine_step_overshoot(self):
        self.assertEqual(self.result.steps, 9)
        self.assertEqual(self.result.stop_reason, "policy_exhausted")

    def test_the_baseline_contains_the_duplicate_r1_targets(self):
        # Identity is (tool, args_hash), not args_hash alone: ``count_rows`` and
        # ``describe_table`` can share arguments and are not the same call.
        counts = Counter((r["chosen"], r["args_hash"]) for r in self.records)
        repeated = {key: n for key, n in counts.items() if n > 1}
        self.assertEqual(len(repeated), 1, repeated)
        self.assertEqual(list(repeated.values()), [2])

    def test_no_rule_is_installed_so_attempted_is_empty(self):
        for record in self.records:
            self.assertEqual(record["rules_version"], "rules-v0(none)")
            self.assertEqual(record["stop_check"]["attempted"], [])
            self.assertFalse(record["stop_check"]["hit"])


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class GovernedRun(_RunMixin, unittest.TestCase):
    """R1 installed: the duplicate is refused before it reaches the upstream."""

    def test_r1_fires_and_ends_the_run(self):
        self.assertEqual(self.result.stop_reason, "halted")
        self.assertEqual(self.result.halt["rule"], "R1")
        self.assertEqual(self.result.halt["state"], "HALTED_DUPLICATE")

    def test_steps_drop_from_nine_to_five(self):
        # Four useful calls plus the attempt that got caught. Not a compression
        # yet -- G2 in D8 is where the number is claimed -- but the run stops
        # earlier without losing evidence.
        self.assertEqual(self.result.steps, 5)

    def test_the_duplicate_is_recorded_as_refused(self):
        counts = Counter((r["chosen"], r["args_hash"]) for r in self.records)
        repeated = {key: n for key, n in counts.items() if n > 1}
        self.assertEqual(list(repeated.values()), [2], "the pair is still visible")
        key = next(iter(repeated))
        duplicate_records = [r for r in self.records if (r["chosen"], r["args_hash"]) == key]
        self.assertEqual(duplicate_records[0]["decision"], "forward")
        self.assertEqual(duplicate_records[-1]["decision"], "halt_duplicate")
        self.assertEqual(duplicate_records[-1]["state"], "HALTED_DUPLICATE")

    def test_the_replayed_result_is_provably_the_earlier_one(self):
        # R1 answers with the result the agent already had instead of an error.
        # Equal digests prove it is the same bytes -- the replay is auditable,
        # not merely asserted in a docstring.
        self.assertEqual(
            self.records[-1]["result_digest"], self.records[2]["result_digest"]
        )
        self.assertFalse(self.result.turns[-1].is_error, "a duplicate read is answered, not refused")

    def test_attempted_shows_what_ran_before_the_verdict(self):
        # R1 ran on every call (and passed on the first four); after each
        # forwarded call R2 and R3 ran in priority order. The halted call never
        # reached the post stage: R1 short circuits it, which is why neither
        # "R2" nor "R3" appears in the last record.
        self.assertEqual(self.records[-1]["stop_check"]["attempted"], ["R1"])
        self.assertTrue(self.records[-1]["stop_check"]["hit"])
        for record in self.records[:-1]:
            self.assertEqual(record["stop_check"]["attempted"], ["R1", "R2", "R3"])
            self.assertFalse(record["stop_check"]["hit"])

    def test_the_rules_version_names_the_installed_set(self):
        for record in self.records:
            self.assertEqual(record["rules_version"], "rules-v0(R1,R2,R3)")


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class BudgetRun(_RunMixin, unittest.TestCase):
    """R3 installed with a three-call budget: the hard stop, on a real run."""

    extra_args = ("--max-calls", "3")

    def test_r3_fires_on_the_call_that_spends_the_budget(self):
        self.assertEqual(self.result.steps, 3)
        self.assertEqual(self.result.stop_reason, "halted")
        self.assertEqual(self.result.halt["rule"], "R3")
        self.assertEqual(self.result.halt["state"], "HALTED_BUDGET")
        self.assertIn("max_calls=3", self.result.halt["reason"])

    def test_the_final_result_is_still_delivered(self):
        # The third call was already paid for. Dropping its result to save a call
        # that already happened would be theatre, not control.
        self.assertFalse(self.result.turns[-1].is_error)
        self.assertEqual(self.records[-1]["decision"], "halt_budget")
        self.assertEqual(self.records[-1]["state"], "HALTED_BUDGET")

    def test_early_calls_are_unaffected(self):
        for record in self.records[:-1]:
            self.assertEqual(record["decision"], "forward")
            self.assertEqual(record["state"], "RUNNING")


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class SpinBaselineRun(_RunMixin, unittest.TestCase):
    """t02 without rules: the agent rephrases until the turn budget runs out."""

    task_id = "t02"
    extra_args = ("--no-rules",)

    def test_the_baseline_burns_the_whole_turn_budget(self):
        self.assertEqual(self.result.steps, 11)
        self.assertEqual(self.result.stop_reason, "max_turns")

    def test_no_call_is_a_literal_repeat(self):
        # The spin is *pseudo*-progress: different question, identical answer. If
        # any call repeated exactly, this task would be measuring R1, not R2.
        counts = Counter((r["chosen"], r["args_hash"]) for r in self.records)
        self.assertEqual([n for n in counts.values() if n > 1], [], "not R1's task")

    def test_the_answers_really_are_identical(self):
        # Otherwise R2 would be catching a coincidence rather than stagnation.
        self.assertEqual(len({r["result_digest"] for r in self.records[1:]}), 1)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class SpinGovernedRun(_RunMixin, unittest.TestCase):
    """t02 with the rules installed: R2 halts on the second identical answer."""

    task_id = "t02"

    def test_r2_fires_and_ends_the_run(self):
        self.assertEqual(self.result.stop_reason, "halted")
        self.assertEqual(self.result.halt["rule"], "R2")
        self.assertEqual(self.result.halt["state"], "HALTED_NO_PROGRESS")

    def test_eleven_steps_become_three(self):
        # One look at the schema, then two questions with the same answer. The
        # second identical answer is the proof that rephrasing is the strategy.
        self.assertEqual(self.result.steps, 3)

    def test_the_plateau_is_visible_in_the_trace(self):
        # The evidence R2 acted on is in the file, not only in the verdict: the
        # last two records carry the same fingerprint, the first two do not.
        self.assertEqual(
            self.records[-1]["stop_check"]["state_hash"],
            self.records[-2]["stop_check"]["state_hash"],
        )
        self.assertNotEqual(
            self.records[1]["stop_check"]["state_hash"],
            self.records[0]["stop_check"]["state_hash"],
        )

    def test_the_two_identical_answers_are_provably_identical(self):
        self.assertEqual(
            self.records[-1]["result_digest"], self.records[-2]["result_digest"]
        )

    def test_the_final_result_is_still_delivered(self):
        # R2 judges after the fact. The result was paid for and is real evidence
        # of absence, so it is handed back along with the halt notice.
        self.assertFalse(self.result.turns[-1].is_error)
        self.assertEqual(self.records[-1]["decision"], "halt_no_progress")
        self.assertEqual(self.records[-1]["stop_check"]["attempted"], ["R1", "R2"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
