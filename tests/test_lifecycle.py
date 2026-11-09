"""The lifecycle, driven in-process with fake rules and a fake upstream.

D6's claim is narrow and worth pinning down exactly: the seven steps are wired in
the right order, a verdict produced *before* the call prevents it, a verdict
produced *after* still lets that result through, and both are visible in the
trace. No real subprocess: the point is the ordering, not the transport (which
D4 already covers).
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

try:
    import mcp  # noqa: F401

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

if HAS_MCP:
    from mcp import types

    from gateway.config import GatewayConfig
    from gateway.registry import RawTool
    from gateway.session import Budget, RunSession
    from gateway.stop import POST, PRE, StopVerdict
    from gateway.trace.canonical import digest_of
    from gateway.trace.sink import JsonlSink
    from gateway.trace.verify import verify_path
    from gateway.transport import Gateway, result_payload
    from gateway.wire import parse_notice


class FakeUpstream:
    """Just enough of ``StdioUpstream``: lists one tool, counts calls."""

    name = "sqlite"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self, *, refresh: bool = False) -> list:
        return [
            RawTool(
                upstream="sqlite",
                name="query",
                description="run a select",
                input_schema={"type": "object", "properties": {}},
            )
        ]

    async def call_tool(self, original_name: str, arguments) -> "types.CallToolResult":
        self.calls.append((original_name, dict(arguments or {})))
        return types.CallToolResult(
            content=[types.TextContent(type="text", text='{"rows": [1]}')]
        )


class Rule:
    def __init__(self, name, priority, stage, when=None, verdict=None) -> None:
        self.name = name
        self.priority = priority
        self.stage = stage
        self.when = when or (lambda ctx: False)
        self.verdict = verdict or StopVerdict(
            rule=name, decision="halt_complete", state="HALTED_COMPLETE", reason=f"{name} fired"
        )

    def check(self, ctx):
        return self.verdict if self.when(ctx) else None


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class LifecycleTests(unittest.TestCase):
    def _run(self, rules, calls, required=()):
        async def scenario():
            self.upstream = FakeUpstream()
            sink = JsonlSink(self.trace_path, run_id="run-life")
            session = RunSession(
                sink=sink,
                run_id="run-life",
                budget=Budget(max_calls=10),
                required=set(required),
            )
            from gateway.stop import StopController

            gateway = Gateway(
                GatewayConfig(),
                [self.upstream],
                session,
                controller=StopController(rules),
            )
            results = []
            async with _noop():
                for arguments in calls:
                    params = types.CallToolRequestParams(name="sqlite.query", arguments=arguments)
                    results.append(await gateway.handle_call_tool(None, params))
            sink.close()
            return session, results

        return asyncio.run(scenario())

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trace_path = Path(self._tmp.name) / "run-life.jsonl"

    def records(self):
        return [
            json.loads(line)
            for line in self.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # -- post-stage: the result still gets through -------------------------

    def test_post_halt_is_recorded_and_its_result_is_still_delivered(self):
        # ``calls_used`` counts the call being judged, so ``>= 2`` is the second.
        rule = Rule("R4", 30, POST, when=lambda ctx: ctx.calls_used >= 2)
        session, results = self._run([rule], [{"sql": "a"}, {"sql": "b"}, ])

        self.assertEqual(session.state, "HALTED_COMPLETE")
        self.assertFalse(results[0].is_error)
        # The halted call's result is real evidence and must not be thrown away.
        self.assertFalse(results[1].is_error, "the call that triggered the halt still delivers")

        records = self.records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["decision"], "forward")
        self.assertEqual(records[0]["state"], "RUNNING")
        self.assertEqual(records[1]["decision"], "halt_complete")
        self.assertEqual(records[1]["state"], "HALTED_COMPLETE")
        self.assertEqual(records[1]["stop_check"]["rule"], "R4")
        self.assertTrue(records[1]["stop_check"]["hit"])

    def test_halt_notice_is_appended_and_parsed(self):
        rule = Rule("R4", 30, POST, when=lambda ctx: True)
        _, results = self._run([rule], [{"sql": "a"}])

        self.assertEqual(len(results[0].content), 2, "upstream block + notice")
        notice = parse_notice(results[0].content[1].text)
        self.assertEqual(notice["kind"], "halt")
        self.assertEqual(notice["state"], "HALTED_COMPLETE")
        self.assertEqual(notice["rule"], "R4")

    def test_notice_is_not_part_of_the_result_digest(self):
        # The digest covers what the upstream said. If the notice leaked into
        # it, the same upstream answer would hash differently depending on the
        # gateway's verdict, and cross-run comparison would be meaningless.
        rule = Rule("R4", 30, POST, when=lambda ctx: True)
        _, _ = self._run([rule], [{"sql": "a"}])
        record = self.records()[0]

        upstream_only = types.CallToolResult(
            content=[types.TextContent(type="text", text='{"rows": [1]}')]
        )
        self.assertEqual(record["result_digest"], digest_of(result_payload(upstream_only)))

    def test_calls_after_a_halt_are_refused_and_leave_no_record(self):
        rule = Rule("R4", 30, POST, when=lambda ctx: True)
        session, results = self._run([rule], [{"sql": "a"}, {"sql": "b"}, {"sql": "c"}])

        self.assertTrue(results[1].is_error)
        self.assertIn("no further calls are accepted", results[1].content[0].text)
        self.assertEqual(parse_notice(results[1].content[0].text)["kind"], "closed")
        self.assertEqual(session.calls_attempted, 3)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(self.upstream.calls), 1, "a refused call must not reach the upstream")

    # -- pre-stage: the call never happens ---------------------------------

    def test_pre_halt_prevents_the_upstream_call(self):
        rule = Rule(
            "R1",
            20,
            PRE,
            when=lambda ctx: True,
            verdict=StopVerdict(
                rule="R1", decision="halt_duplicate", state="HALTED_DUPLICATE", reason="same call"
            ),
        )
        session, results = self._run([rule], [{"sql": "a"}])

        self.assertEqual(self.upstream.calls, [], "a pre-stage verdict must block the call")
        self.assertEqual(session.state, "HALTED_DUPLICATE")
        record = self.records()[0]
        self.assertEqual(record["decision"], "halt_duplicate")
        self.assertEqual(record["state"], "HALTED_DUPLICATE")
        # What got recorded is the digest of what would have been returned --
        # "the call was blocked" is auditable, the arguments are not stored.
        self.assertEqual(record["result_digest"], digest_of({"blocked": "same call"}))

    def test_pre_halt_with_replay_returns_the_earlier_result(self):
        # The real R1, not a fake: the second identical call must not reach the
        # upstream, and the agent must get the data it already had instead of an
        # error. Both halves matter -- a "duplicate" answered with an error
        # throws away evidence the run already paid for.
        from gateway.rules import DuplicateCall

        session, results = self._run([DuplicateCall()], [{"sql": "a"}, {"sql": "a"}])

        self.assertEqual(len(self.upstream.calls), 1, "the duplicate must not be forwarded")
        self.assertEqual(session.state, "HALTED_DUPLICATE")
        self.assertFalse(results[1].is_error)
        self.assertEqual(results[1].content[0].text, results[0].content[0].text)
        self.assertEqual(parse_notice(results[1].content[1].text)["rule"], "R1")

        records = self.records()
        self.assertEqual(records[1]["decision"], "halt_duplicate")
        self.assertEqual(
            records[1]["result_digest"],
            records[0]["result_digest"],
            "the replay is provably the same result, not a re-execution",
        )

    def test_attempted_lists_every_rule_that_ran_in_order(self):
        quiet_pre = Rule("R1", 20, PRE, when=lambda ctx: False)
        quiet_post = Rule("R2", 40, POST, when=lambda ctx: False)
        fires = Rule("R4", 30, POST, when=lambda ctx: ctx.calls_used >= 0)
        _, _ = self._run([quiet_post, fires, quiet_pre], [{"sql": "a"}])

        check = self.records()[0]["stop_check"]
        self.assertEqual(check["attempted"], ["R1", "R4"])
        self.assertEqual(check["rule"], "R4")

    def test_stop_check_reports_evidence_and_missing(self):
        rule = Rule("R2", 40, POST, when=lambda ctx: False)
        _, _ = self._run([rule], [{"sql": "a"}], required=["sql.rows", "sql.schema"])
        check = self.records()[0]["stop_check"]

        self.assertEqual(check["evidence"], [])
        self.assertEqual(check["missing"], ["sql.rows", "sql.schema"])

    def test_rules_version_comes_from_the_controller(self):
        rule = Rule("R4", 30, POST)
        session, _ = self._run([rule], [{"sql": "a"}])
        self.assertEqual(session.rules_version, "rules-v0(R4)")
        self.assertEqual(self.records()[0]["rules_version"], "rules-v0(R4)")

    def test_run_with_no_rules_is_still_fully_wired(self):
        session, results = self._run([], [{"sql": "a"}])
        record = self.records()[0]

        self.assertEqual(record["rules_version"], "rules-v0(none)")
        self.assertEqual(record["stop_check"]["attempted"], [])
        self.assertFalse(record["stop_check"]["hit"])
        self.assertEqual(len(results[0].content), 1, "no notice when nothing halted")
        self.assertEqual(session.results_delivered, session.records_written)

    def test_trace_still_verifies_after_a_halt(self):
        rule = Rule("R4", 30, POST, when=lambda ctx: True)
        self._run([rule], [{"sql": "a"}])
        self.assertTrue(verify_path(self.trace_path).ok)


class _SlowUpstream(FakeUpstream):
    """Sleeps inside ``call_tool`` so concurrent handlers really do interleave.

    Without an await point in the middle, ``gather`` of three calls would still
    run them one after another by accident, and a test of serialization would
    pass against code that has none.
    """

    async def call_tool(self, original_name: str, arguments):
        await asyncio.sleep(0.01)
        return await super().call_tool(original_name, arguments)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class SerializationTests(unittest.TestCase):
    """FR-16: an agent may emit three calls in one turn; the trace stays linear."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trace_path = Path(self._tmp.name) / "run-parallel.jsonl"

    def test_three_concurrent_calls_are_recorded_one_at_a_time(self):
        async def scenario():
            upstream = _SlowUpstream()
            sink = JsonlSink(self.trace_path, run_id="run-parallel")
            session = RunSession(
                sink=sink, run_id="run-parallel", budget=Budget(max_calls=10)
            )
            from gateway.stop import StopController

            gateway = Gateway(
                GatewayConfig(), [upstream], session, controller=StopController(())
            )
            params = [
                types.CallToolRequestParams(name="sqlite.query", arguments={"sql": f"q{i}"})
                for i in range(3)
            ]
            results = await asyncio.gather(
                *(gateway.handle_call_tool(None, p) for p in params)
            )
            sink.close()
            return session, upstream, results

        session, upstream, results = asyncio.run(scenario())

        records = [
            json.loads(line)
            for line in self.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(results), 3)
        self.assertEqual(len(records), 3)
        self.assertEqual([r["seq"] for r in records], [1, 2, 3], "linear, no interleaving")
        # ``turn`` is read-then-incremented by whichever handler holds the lock.
        # Without one, two handlers read the same value and the trace claims two
        # calls happened in the same turn.
        self.assertEqual([r["turn"] for r in records], [1, 2, 3])
        self.assertEqual(len(upstream.calls), 3, "each call reached the upstream once")
        self.assertEqual(verify_path(self.trace_path).ok, True)
        self.assertEqual(session.results_delivered, session.records_written)

    def test_a_call_that_arrives_while_the_run_halts_is_still_refused(self):
        # Serialization must not turn into "queue everything and decide later":
        # the second call sees the halt the first one produced.
        async def scenario():
            upstream = _SlowUpstream()
            sink = JsonlSink(self.trace_path, run_id="run-parallel")
            session = RunSession(
                sink=sink, run_id="run-parallel", budget=Budget(max_calls=10)
            )
            from gateway.stop import StopController

            gateway = Gateway(
                GatewayConfig(),
                [upstream],
                session,
                controller=StopController(
                    [Rule("R4", 30, POST, when=lambda ctx: True)]
                ),
            )
            first = types.CallToolRequestParams(name="sqlite.query", arguments={"sql": "a"})
            second = types.CallToolRequestParams(name="sqlite.query", arguments={"sql": "b"})
            out = await asyncio.gather(
                gateway.handle_call_tool(None, first),
                gateway.handle_call_tool(None, second),
            )
            sink.close()
            return session, out

        session, out = asyncio.run(scenario())
        self.assertEqual(session.state, "HALTED_COMPLETE")
        # The refusal is the whole result, not an appended notice: there is no
        # upstream data to append it to.
        self.assertTrue(out[1].is_error)
        self.assertIn("no further calls are accepted", out[1].content[0].text)


class _noop:
    """Stands in for ``async with upstream``: the fake never connects."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
