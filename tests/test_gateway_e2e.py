"""End to end: agent -> gateway subprocess -> sqlite upstream -> trace file.

These assertions are the D4 acceptance criteria, written down so they cannot
quietly stop being true:

* the gateway exposes the upstream's tools under a namespace
* every call that came back has a record, and the record's ``offered`` list
  contains the tool that was actually chosen (otherwise Accuracy@K is
  uncomputable, DESIGN 2.4)
* the resulting trace verifies

Slow by unit-test standards (spawns processes) but that is the point: everything
below the transport layer is already covered by fast tests.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import mcp  # noqa: F401

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

if HAS_MCP:
    from echo_client import drive


CALLS = [
    ("sqlite.list_tables", {}),
    ("sqlite.count_rows", {"table": "incidents"}),
]


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class GatewayEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.trace_dir = Path(cls._tmp.name)
        cls.outcome = asyncio.run(drive(CALLS, run_id="run-e2e", trace_dir=cls.trace_dir))
        cls.trace_path = cls.trace_dir / "run-e2e.jsonl"
        cls.records = [
            json.loads(line)
            for line in cls.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_tools_are_namespaced_by_upstream(self):
        self.assertEqual(len(self.outcome["tools"]), 4)
        for name in self.outcome["tools"]:
            self.assertTrue(name.startswith("sqlite."), name)

    def test_calls_reach_the_upstream_and_return_data(self):
        self.assertEqual(len(self.outcome["calls"]), len(CALLS))
        for call in self.outcome["calls"]:
            self.assertFalse(call["is_error"], call)
        self.assertIn("incidents", self.outcome["calls"][0]["text"])

    def test_one_record_per_delivered_call(self):
        self.assertEqual(len(self.records), len(CALLS))
        self.assertEqual([r["seq"] for r in self.records], [1, 2])

    def test_offered_is_recorded_and_contains_the_chosen_tool(self):
        for record in self.records:
            self.assertTrue(record["offered"], "offered must never be empty")
            self.assertIn(record["chosen"], record["offered"])

    def test_records_carry_versions_and_state(self):
        for record in self.records:
            self.assertEqual(record["schema_version"], 1)
            self.assertTrue(record["gateway_version"])
            self.assertTrue(record["rules_version"])
            self.assertEqual(record["state"], "RUNNING")
            self.assertEqual(record["decision"], "forward")

    def test_stop_check_is_present_and_empty_at_d4(self):
        # No rules yet: the field must exist and say "nothing was attempted",
        # rather than be missing -- a missing field would be ambiguous later.
        for record in self.records:
            self.assertEqual(record["stop_check"]["attempted"], [])
            self.assertFalse(record["stop_check"]["hit"])

    def test_trace_verifies(self):
        from gateway.trace.verify import verify_path

        result = verify_path(self.trace_path)
        self.assertTrue(result.ok, result.summary())

    def test_arguments_and_results_are_not_stored(self):
        # Only digests go into the trace: a record proves a call happened
        # without becoming a copy of the customer data it touched.
        raw = self.trace_path.read_text(encoding="utf-8")
        self.assertNotIn("incidents", raw)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
