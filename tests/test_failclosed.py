"""Fail-closed, tested where it actually matters: mid-run.

The interesting question is not "does the sink raise when the disk fails" -- the
sink tests already cover that. It is:

    after a write failure, did any result reach the model without a record?

So this drives the real ``Gateway`` against the real sqlite upstream, makes the
second ``fsync`` fail, and then checks the books:

    calls attempted 3, records written 1, results delivered 1

and that the third call is refused without the upstream ever being touched. If
the number of delivered results ever exceeds the number of records, the project's
central claim is false -- so that is the assertion, not a log line.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:  # the SDK lives in the venv; a bare interpreter should still run the rest
    import mcp  # noqa: F401

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

if HAS_MCP:
    from mcp import types

    from gateway.config import load_config
    from gateway.session import RunSession
    from gateway.trace.sink import JsonlSink
    from gateway.trace.verify import verify_path
    from gateway.transport import Gateway
    from gateway.upstream import StdioUpstream

RUN_ID = "run-failclosed"
CALLS = [
    ("sqlite.list_tables", {}),
    ("sqlite.count_rows", {"table": "repos"}),
    ("sqlite.describe_table", {"table": "repos"}),
]


def _failing_fsync_at(fail_on: int):
    state = {"n": 0}

    def sync(fd: int) -> None:
        state["n"] += 1
        if state["n"] == fail_on:
            raise OSError(28, "No space left on device (injected)")
        os.fsync(fd)

    return sync


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class FailClosedTests(unittest.TestCase):
    def _drive(self) -> dict:
        async def scenario() -> dict:
            config = load_config()
            spec = config.upstreams[0]
            upstream = StdioUpstream(
                spec.name, spec.command, spec.args, env=spec.env, cwd=spec.cwd
            )
            sink = JsonlSink(
                self.trace_path, run_id=RUN_ID, fsync_fn=_failing_fsync_at(2)
            )
            session = RunSession(sink=sink, run_id=RUN_ID)
            gateway = Gateway(config, [upstream], session)

            results = []
            async with upstream:
                await gateway.list_all_tools()
                for name, arguments in CALLS:
                    params = types.CallToolRequestParams(name=name, arguments=arguments)
                    results.append(await gateway.handle_call_tool(None, params))

            sink.close()
            return {"results": results, "session": session}

        return asyncio.run(scenario())

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trace_path = Path(self._tmp.name) / f"{RUN_ID}.jsonl"
        self.outcome = self._drive()
        self.session = self.outcome["session"]

    def test_run_is_aborted_after_the_write_failure(self):
        self.assertTrue(self.session.aborted)
        self.assertEqual(self.session.state, "ABORTED")
        self.assertIn("seq 2", self.session.abort_reason)

    def test_failed_call_returns_an_error_instead_of_a_result(self):
        results = self.outcome["results"]
        self.assertFalse(results[0].is_error, "first call should succeed")
        self.assertTrue(results[1].is_error, "second call must not deliver a result")
        self.assertIn("discarded", results[1].content[0].text)

    def test_no_result_escapes_without_a_record(self):
        # The invariant the whole design rests on.
        self.assertEqual(self.session.results_delivered, self.session.records_written)
        self.assertEqual(self.session.results_delivered, 1)
        self.assertEqual(self.session.calls_attempted, 3)

    def test_calls_after_the_abort_never_reach_the_upstream(self):
        results = self.outcome["results"]
        self.assertTrue(results[2].is_error)
        self.assertIn("run aborted", results[2].content[0].text)

    def test_trace_keeps_only_the_records_that_landed(self):
        result = verify_path(self.trace_path)
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(result.records, 1)

    def test_partial_line_was_rolled_back(self):
        lines = [
            line
            for line in self.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("}"), "no half-written JSON may remain")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
