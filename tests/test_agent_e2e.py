"""D6 acceptance: a real task, a real subprocess, a complete trace.

Everything here is a claim the README makes, so it is asserted rather than
demonstrated once:

* offered / chosen / prev_hash on **every** line -- the step is judgeable and the
  chain is verifiable, not just the last line;
* one record per accepted call;
* the run stops because the policy is out of ideas, because D6 has no rules yet.
  When D7-D9 land, ``stop_reason`` becomes ``halted`` and *this assertion changes*
  -- which is the point of writing it down: a silent change in why runs end would
  otherwise look like a good day.
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


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class AgentLoopEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        task = load_tasks()[0]
        cls.result = run_task_sync(
            task, run_id="run-agent-e2e", trace_dir=Path(cls._tmp.name)
        )
        cls.records = [
            json.loads(line)
            for line in cls.result.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_task_id_is_the_one_we_think_it_is(self):
        self.assertTrue(self.result.tools)
        self.assertIn("sqlite.query", self.result.tools)

    def test_every_step_produced_a_record(self):
        self.assertEqual(len(self.records), self.result.steps)
        self.assertEqual([r["seq"] for r in self.records], list(range(1, len(self.records) + 1)))

    def test_every_record_carries_the_fields_that_make_it_judgeable(self):
        for record in self.records:
            self.assertTrue(record["offered"], record["seq"])
            self.assertIn(record["chosen"], record["offered"])
            self.assertTrue(record["prev_hash"])
            self.assertTrue(record["args_hash"])
            self.assertTrue(record["result_digest"])

    def test_offered_is_the_full_set_at_this_stage(self):
        # D14 shrinks this by gating; until then "offered" must mean "everything
        # the registry exposes", or Accuracy@K would be computed against a list
        # nobody was ever shown.
        for record in self.records:
            self.assertEqual(sorted(record["offered"]), sorted(self.result.tools))

    def test_the_loop_is_closed_and_overshoots(self):
        # Four useful calls plus the verification plan -- see agent/policy.py.
        self.assertGreater(self.result.steps, 4)

    def test_the_baseline_contains_a_repeated_call(self):
        # Identity is (tool, args_hash), not args_hash alone: ``count_rows`` and
        # ``describe_table`` can share arguments and are not the same call. This
        # is exactly the trap R1's signature has to avoid.
        counts = Counter((r["chosen"], r["args_hash"]) for r in self.records)
        repeated = {key: n for key, n in counts.items() if n > 1}
        self.assertEqual(len(repeated), 1, repeated)
        self.assertEqual(list(repeated.values()), [2], "R1's target for D7")

    def test_run_ends_because_the_policy_ran_out_not_because_it_stopped(self):
        self.assertEqual(self.result.stop_reason, "policy_exhausted")
        self.assertIsNone(self.result.halt, "no rules are installed at D6")
        for record in self.records:
            self.assertEqual(record["rules_version"], "rules-v0(none)")
            self.assertEqual(record["stop_check"]["attempted"], [])
            self.assertFalse(record["stop_check"]["hit"])

    def test_trace_verifies(self):
        from gateway.trace.verify import verify_path

        outcome = verify_path(self.result.trace_path)
        self.assertTrue(outcome.ok, outcome.summary())

    def test_no_result_payload_is_stored(self):
        raw = self.result.trace_path.read_text(encoding="utf-8")
        self.assertNotIn("maximhq/bifrost", raw, "traces must not become data copies")
        self.assertNotIn("severity", raw)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
