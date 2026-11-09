"""D7 acceptance: R1 and R3 each fire in a real run, or this file fails.

The plan's warning for this stage is specific: a rule that never fires raises
nothing, prints nothing, and turns every number downstream into zero. So the
assertions here are not "the rule is correct" (``test_rules.py`` covers that with
a hand-built context) but **the hit rate is greater than zero, in a subprocess,
with the real upstream**.

Three runs, three claims:

* ``BaselineRun`` -- the same task with ``--no-rules``. Nine steps, and it ends
  because the policy ran out of ideas. This is the denominator the compression
  numbers need, and it must stay reproducible after the rules exist.
* ``GovernedRun`` -- five steps, halted by R1 on a call the baseline makes twice.
  The duplicate is still *visible* in the trace (the refusal is recorded), which
  is what makes "it was caught here" checkable afterwards.
* ``BudgetRun`` -- the same task with ``--max-calls 3``: R3 halts on the third
  call, and that call's result is still delivered, because it was paid for.
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

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.result = run_task_sync(
            load_tasks()[0],
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
        # R1 ran on every call (and passed on the first four), R3 ran after each
        # forwarded one. The halted call never reached the post stage: R1 short
        # circuits it, which is why "R3" is absent from the last record.
        self.assertEqual(self.records[-1]["stop_check"]["attempted"], ["R1"])
        self.assertTrue(self.records[-1]["stop_check"]["hit"])
        for record in self.records[:-1]:
            self.assertEqual(record["stop_check"]["attempted"], ["R1", "R3"])
            self.assertFalse(record["stop_check"]["hit"])

    def test_the_rules_version_names_the_installed_set(self):
        for record in self.records:
            self.assertEqual(record["rules_version"], "rules-v0(R1,R3)")


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
