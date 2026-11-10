"""D14 live tests: the window reaches the wire, and it cannot break a run.

The unit tests in ``test_selector`` pin the ranking rules. What they cannot
show is that the gateway uses them: that ``tools/list`` and ``offered`` agree,
that an outside-window call is forwarded and labelled rather than refused, and
that a required fact's producer is kept even when the query says nothing about
it. Those are the three ways this feature could be wired up wrongly without
anything raising.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

try:  # the live half needs the SDK and the servers
    from mcp import types

    HAS_MCP = True
except Exception:  # pragma: no cover - exercised by the naked interpreter
    HAS_MCP = False

if HAS_MCP:
    from gateway.config import GatewayConfig, GatingSpec, load_config
    from gateway.evidence import load_evidence_model
    from gateway.registry import load_baseline
    from gateway.session import RunSession
    from gateway.stop import StopController
    from gateway.trace.sink import JsonlSink
    from gateway.transport import Gateway
    from gateway.upstream import StdioUpstream
else:  # pragma: no cover - the naked interpreter has no SDK to import
    # Every test class below is skipped, but the module still has to import:
    # ``gateway.transport`` imports ``mcp`` at module level, so a collection
    # error here would hide all 356 tests that do not need the SDK.
    GatewayConfig = GatingSpec = None  # type: ignore[assignment]
    load_config = load_evidence_model = load_baseline = None  # type: ignore
    RunSession = StopController = JsonlSink = Gateway = StdioUpstream = None  # type: ignore


def _upstreams(config: GatewayConfig) -> List[StdioUpstream]:
    return [
        StdioUpstream(
            spec.name,
            spec.command,
            spec.args,
            env=spec.env,
            cwd=spec.cwd,
            call_timeout_s=spec.call_timeout_s,
        )
        for spec in config.upstreams
    ]


def _records(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class WindowOnTheWireTests(unittest.TestCase):
    """In-process: two calls, with a goal that ranks the SQL server last."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.trace_dir = Path(self._tmp.name)
        self.config = load_config()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(
        self, *, gating: GatingSpec, goal: str, required: tuple = ("sql.rows.deps",)
    ) -> List[Dict[str, Any]]:
        config = replace(self.config, gating=gating)
        model = load_evidence_model(config.evidence_model)
        upstreams = _upstreams(config)
        sink = JsonlSink(self.trace_dir / "run.jsonl", run_id="gating-test")
        session = RunSession(sink=sink, run_id="gating-test", required=set(required))
        gateway = Gateway(
            config,
            upstreams,
            session,
            controller=StopController(()),
            evidence=model,
            baseline=load_baseline(config.schema_manifest),
            goal=goal,
        )

        async def scenario() -> None:
            async with AsyncExitStack() as stack:
                for upstream in upstreams:
                    await stack.enter_async_context(upstream)
                for name, args in (
                    ("sqlite.list_tables", {}),
                    ("wiki.search", {"q": "anything"}),
                ):
                    await gateway.handle_call_tool(
                        None, types.CallToolRequestParams(name=name, arguments=args)
                    )
            sink.close()

        asyncio.run(scenario())
        return _records(session.trace_path)

    def test_the_window_reaches_the_record_and_the_outside_call_is_labelled(self):
        # A goal made of nothing any document mentions: with no lexical signal,
        # the ranking falls back to the alphabet, which is exactly the situation
        # a live model can produce ("do the thing we discussed").
        records = self._run(gating=GatingSpec(enabled=True, k=5, m=10), goal="zzzz qqqq")
        self.assertEqual(len(records), 2)

        # Turn 1 is a cold start: nothing to rank by, so everything is shown.
        self.assertEqual(len(records[0]["offered"]), 30)
        self.assertEqual(records[0]["decision"], "forward")

        # Turn 2 is ranked. The pinned producer is there even though the query
        # never mentions SQL -- without it R6 would call this run impossible --
        # and the call the agent actually made is outside the window, which the
        # record says rather than hides.
        second = records[1]
        self.assertEqual(len(second["offered"]), 5)
        self.assertIn("sqlite.query", second["offered"])
        self.assertEqual(second["chosen"], "wiki.search")
        self.assertNotIn("wiki.search", second["offered"])
        self.assertEqual(second["decision"], "forward_outside")

    def test_gating_off_offers_the_whole_catalogue_every_time(self):
        records = self._run(
            gating=GatingSpec(enabled=False), goal="zzzz qqqq", required=()
        )
        for record in records:
            self.assertEqual(len(record["offered"]), 30, record["seq"])
            self.assertEqual(record["decision"], "forward", record["seq"])


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class ARealTaskUnderGatingTests(unittest.TestCase):
    """End to end: the client sees the window, and the answer is unchanged."""

    @classmethod
    def setUpClass(cls) -> None:
        from agent import load_tasks, run_task_sync
        from tasks.checkers import answers_from_turns, check

        cls._tmp = tempfile.TemporaryDirectory()
        cls.task = {task.id: task for task in load_tasks()}["t05"]
        cls.result = run_task_sync(
            cls.task, run_id="gating-live", trace_dir=Path(cls._tmp.name)
        )
        cls.records = _records(cls.result.trace_path)
        cls.answer = check(cls.task.id, answers_from_turns(cls.result.turns))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_the_agent_saw_exactly_what_the_record_claims(self):
        self.assertEqual(len(self.result.visible), len(self.records))
        for visible, record in zip(self.result.visible, self.records):
            self.assertEqual(set(visible), set(record["offered"]), record["seq"])

    def test_the_window_narrows_only_after_the_cold_start(self):
        self.assertEqual(len(self.records[0]["offered"]), 30)
        for record in self.records[1:]:
            self.assertLessEqual(len(record["offered"]), 15, record["seq"])
            self.assertGreaterEqual(len(record["offered"]), 5, record["seq"])

    def test_the_run_still_answers_the_question(self):
        self.assertTrue(self.answer.ok, self.answer.detail)
        self.assertEqual(self.records[-1]["state"], "HALTED_COMPLETE")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
