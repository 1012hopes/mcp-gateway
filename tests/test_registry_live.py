"""Live registry behaviour: two real servers, one drift baseline.

The unit tests prove the registry's logic on synthetic schemas. These prove it
survives the two things only a real transport shows:

* the SDK itself refuses some malformed schemas (a top-level missing ``type``
  never reaches the gateway -- see docs/progress.md), so the dirty server had to
  be dirty in ways that are actually transmittable;
* a frozen tool must be refused *and* that refusal must be recorded, otherwise
  the fail-closed invariant (``results_delivered == records_written``) breaks on
  a path nobody looks at.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
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

RUN_ID = "run-drift"


def _upstreams():
    config = load_config()
    return config, [
        StdioUpstream(
            spec.name, spec.command, spec.args, env=spec.env, cwd=spec.cwd
        )
        for spec in config.upstreams
    ]


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class DriftLiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trace_path = Path(self._tmp.name) / f"{RUN_ID}.jsonl"

    def test_drifted_tool_is_frozen_and_its_call_is_recorded_as_blocked(self):
        async def scenario():
            config, upstreams = _upstreams()
            sink = JsonlSink(self.trace_path, run_id=RUN_ID)
            session = RunSession(sink=sink, run_id=RUN_ID)
            baseline = {"sqlite.query": "0" * 64}
            gateway = Gateway(config, upstreams, session, baseline=baseline)
            async with AsyncExitStack() as stack:
                for upstream in upstreams:
                    await stack.enter_async_context(upstream)

                registry = await gateway.registry()
                listed = await gateway.handle_list_tools(None, None)
                params = types.CallToolRequestParams(
                    name="sqlite.query", arguments={"sql": "select 1"}
                )
                frozen_result = await gateway.handle_call_tool(None, params)
                params_ok = types.CallToolRequestParams(
                    name="sqlite.list_tables", arguments={}
                )
                ok_result = await gateway.handle_call_tool(None, params_ok)
            sink.close()
            return registry, listed, frozen_result, ok_result, session

        registry, listed, frozen_result, ok_result, session = asyncio.run(scenario())

        self.assertTrue(registry.get("sqlite.query").frozen)
        self.assertNotIn("sqlite.query", [t.name for t in listed.tools])
        self.assertTrue(frozen_result.is_error)
        self.assertIn("frozen", frozen_result.content[0].text)
        self.assertFalse(ok_result.is_error, "unrelated tools must keep working")

        records = [
            json.loads(line)
            for line in self.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 2)
        blocked, forwarded = records
        self.assertEqual(blocked["decision"], "block_schema_drift")
        self.assertEqual(blocked["chosen"], "sqlite.query")
        self.assertEqual(forwarded["decision"], "forward")
        # ``offered`` must show the frozen tool as absent, not just unlisted.
        self.assertNotIn("sqlite.query", forwarded["offered"])
        self.assertIn("sqlite.list_tables", forwarded["offered"])
        self.assertEqual(session.results_delivered, session.records_written)
        self.assertTrue(verify_path(self.trace_path).ok)

    def test_unknown_tool_is_blocked_and_recorded(self):
        async def scenario():
            config, upstreams = _upstreams()
            sink = JsonlSink(self.trace_path, run_id=RUN_ID)
            session = RunSession(sink=sink, run_id=RUN_ID)
            gateway = Gateway(config, upstreams, session)
            async with AsyncExitStack() as stack:
                for upstream in upstreams:
                    await stack.enter_async_context(upstream)
                result = await gateway.handle_call_tool(
                    None, types.CallToolRequestParams(name="nope.query", arguments={})
                )
            sink.close()
            return result, session

        result, session = asyncio.run(scenario())
        self.assertTrue(result.is_error)
        record = json.loads(self.trace_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["decision"], "block_unknown_tool")
        self.assertEqual(session.results_delivered, session.records_written)

    def test_hashes_are_identical_across_two_process_starts(self):
        """The D5 criterion, checked against a real transport rather than theory.

        Two separate ``asyncio.run`` calls mean two separate spawn-and-list
        cycles of both servers. If anything in normalization depended on dict
        order, iteration order or a timestamp, these would differ.
        """
        first = asyncio.run(self._manifest_once())
        second = asyncio.run(self._manifest_once())
        self.assertEqual(first, second)
        self.assertGreater(len(first), 0)

    async def _manifest_once(self):
        from gateway.registry import build_registry

        config, upstreams = _upstreams()
        async with AsyncExitStack() as stack:
            for upstream in upstreams:
                await stack.enter_async_context(upstream)
            listing = [
                (upstream.name, await upstream.list_tools()) for upstream in upstreams
            ]
        return build_registry(listing).manifest()["tools"]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
