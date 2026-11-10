"""D13: the three dirty upstreams, tested where they can actually hurt.

``scripts/compat_demo.py`` is the demonstration; this is the part that has to
keep being true after somebody "cleans up" an upstream. The split follows the
claim in DESIGN 2.6, which is two claims wearing one sentence:

* **the envelope is not the fact** -- extraction depends on the body, not on how
  many text blocks it arrived in, whether ``is_error`` was spelled out, or
  whether ``structuredContent`` was null. Tested here without a transport,
  because a transport would only add ways for the test to be slow.
* **a non-body is not a fact** -- an error result carrying rows, a JSON
  ``null``, an empty content list, half-written JSON, a bare number. Each of
  these looks like an answer to a shape matcher and is not one. The pairs are
  written next to the positives on purpose: a rule that says "this counts" with
  no "this does not" beside it is untested in the direction that matters.

The live half asserts the parts only a real process can show: three servers come
up under one registry, a raising handler becomes a recorded call rather than a
dead gateway, and one deadline costs one call instead of the run.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Dict, List, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.config import load_config  # noqa: E402
from gateway.evidence import load_evidence_model  # noqa: E402
from gateway.registry import RawTool  # noqa: E402
from gateway.session import MAX_RESULT_BYTES, RunSession  # noqa: E402
from gateway.stop import StopController  # noqa: E402
from gateway.trace.sink import JsonlSink  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402

try:
    import mcp  # noqa: F401

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

#: Names that only exist when the SDK is installed. Imported here, used inside
#: method bodies -- never in a class body, which runs at import time, before any
#: ``skipUnless`` has had a chance to skip anything.
if HAS_MCP:
    from mcp import types

    from gateway.transport import Gateway
    from gateway.upstream import StdioUpstream

CONFIG = ROOT / "config" / "evidence.json"
DIRTY_CONFIG = ROOT / "config" / "gateway-dirty.json"

LABELS = [
    {"incident_id": 1, "label": "regression"},
    {"incident_id": 1, "label": "mcp"},
    {"incident_id": 2, "label": "flaky"},
    {"incident_id": 3, "label": "p1"},
    {"incident_id": 4, "label": "governance"},
    {"incident_id": 4, "label": "mcp"},
]
LABELS_BODY = {"rows": LABELS, "row_count": len(LABELS)}
LABELS_SQL = {"sql": "select * from labels"}
FACT = "sql.rows.labels"


def block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def same_body() -> Dict[str, Dict[str, Any]]:
    text = dump(LABELS_BODY)
    return {
        "single block": {"content": [block(text)]},
        "banner, payload, trailer": {
            "content": [block("Query OK"), block(text), block("(6 rows)")]
        },
        "explicit is_error false": {"content": [block(text)], "is_error": False},
        "null structured content": {
            "content": [block(text)],
            "structured_content": None,
        },
        "crlf inside the json": {
            "content": [block(json.dumps(LABELS_BODY, indent=2).replace("\n", "\r\n"))]
        },
        "an unknown extra key": {
            "content": [block(dump({**LABELS_BODY, "server_hint": "cached"}))]
        },
    }


def not_body() -> Dict[str, Dict[str, Any]]:
    return {
        "error flag set, rows attached": {
            "content": [block(dump(LABELS_BODY))],
            "is_error": True,
        },
        "the json literal null": {"content": [block("null")]},
        "no content blocks": {"content": []},
        "truncated json": {"content": [block('{"rows": [')]},
        "a bare number": {"content": [block("42")]},
    }


class EnvelopeTests(unittest.TestCase):
    """Hostile envelopes, no transport: the extractor's decision per payload."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_evidence_model(CONFIG)

    def extract(self, payload: Mapping[str, Any]) -> Dict[str, str]:
        return self.model.extract(payload, tool="sqlite.query", args=LABELS_SQL)

    def test_the_clean_encoding_is_the_control(self):
        # Without this, every "noise changes nothing" case below would still pass
        # if the extractor matched nothing at all.
        self.assertEqual(
            self.extract({"content": [block(dump(LABELS_BODY))]}),
            {FACT: "sqlite.query"},
        )

    def test_noise_does_not_change_the_fact(self):
        for name, payload in same_body().items():
            with self.subTest(encoding=name):
                self.assertEqual(self.extract(payload), {FACT: "sqlite.query"})

    def test_a_non_body_yields_no_fact(self):
        for name, payload in not_body().items():
            with self.subTest(payload=name):
                self.assertEqual(self.extract(payload), {})

    def test_a_json_preamble_wins_and_that_is_a_known_limit(self):
        """The first *parsable* block is the body; a JSON banner is a banner-sized hole.

        Real preambles are prose ("Query OK, 6 rows"), which is why this passes in
        practice. A server that prepends a JSON ``{"ok": true}`` would hide its own
        payload from the extractor -- and the gateway would report "no evidence"
        for a call that answered. Pinned rather than discovered: choosing a block
        by shape score would be a selector, and the selector is D14's problem.
        """
        payload = {"content": [block('{"ok": true}'), block(dump(LABELS_BODY))]}
        self.assertEqual(self.extract(payload), {})


class _StubUpstream:
    """Answers ``query`` with one body. Only the size may differ between cases."""

    def __init__(self, body: Mapping[str, Any]) -> None:
        self.name = "sqlite"
        self._body = dict(body)

    async def list_tools(self, *, refresh: bool = False) -> List[RawTool]:
        return [
            RawTool(
                upstream=self.name,
                name="query",
                description="Run one read-only SELECT statement.",
                input_schema={
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
            )
        ]

    async def call_tool(self, original_name: str, arguments: Any) -> Any:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=dump(self._body))]
        )


def _two_sizes() -> List[Dict[str, Any]]:
    small = dict(LABELS_BODY)
    return [small, dict(small, padding="x" * (MAX_RESULT_BYTES + 200_000))]


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class TruncationGateTests(unittest.TestCase):
    """Same tool, same args, same shape -- only the byte count moves."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trace_dir = Path(self._tmp.name)
        self.config = load_config()
        self.model = load_evidence_model(CONFIG)

    def _run_once(self, body: Mapping[str, Any], run_id: str) -> Dict[str, Any]:
        async def scenario() -> None:
            sink = JsonlSink(self.trace_dir / f"{run_id}.jsonl", run_id=run_id)
            session = RunSession(sink=sink, run_id=run_id)
            gateway = Gateway(
                self.config,
                [_StubUpstream(body)],  # type: ignore[list-item]
                session,
                controller=StopController(()),
                evidence=self.model,
            )
            await gateway.handle_call_tool(
                None,
                types.CallToolRequestParams(
                    name="sqlite.query", arguments=dict(LABELS_SQL)
                ),
            )
            sink.close()

        asyncio.run(scenario())
        path = self.trace_dir / f"{run_id}.jsonl"
        lines = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(verify_path(path).ok)
        return lines[0]

    def test_the_size_gate_is_what_gates(self):
        small, large = _two_sizes()
        ok = self._run_once(small, "run-small")
        big = self._run_once(large, "run-big")

        self.assertFalse(ok["truncated"])
        self.assertEqual(ok["evidence_added"], [FACT])
        self.assertTrue(big["truncated"])
        self.assertEqual(big["evidence_added"], [])
        # The premise: nothing but size differs, so size is the explanation.
        self.assertEqual(ok["chosen"], big["chosen"])
        self.assertEqual(ok["args_hash"], big["args_hash"])


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class LiveDirtyTests(unittest.TestCase):
    """Three real processes under one registry."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trace_dir = Path(self._tmp.name)
        self.config = load_config(str(DIRTY_CONFIG))

    def _gateway(self, run_id: str):
        sink = JsonlSink(self.trace_dir / f"{run_id}.jsonl", run_id=run_id)
        session = RunSession(sink=sink, run_id=run_id)
        upstreams = [
            StdioUpstream(
                spec.name,
                spec.command,
                spec.args,
                env=spec.env,
                cwd=spec.cwd,
                call_timeout_s=spec.call_timeout_s,
            )
            for spec in self.config.upstreams
        ]
        gateway = Gateway(
            self.config,
            upstreams,
            session,
            controller=StopController(()),
            evidence=load_evidence_model(CONFIG),
        )
        return gateway, upstreams, session

    def test_the_three_dirty_servers_come_up_and_answer(self):
        gateway, upstreams, session = self._gateway("run-d13-up")
        tools = (
            "weird.echo",
            "weird.mirror_labels",
            "weird.null_body",
            "weird.empty_content",
            "weird.broken_json",
            "weird.scalar_body",
            "weird.error_with_data",
            "weird.nonstandard_error",
            "weird.noisy",
            "flake.huge",
        )

        async def scenario() -> Dict[str, Any]:
            out: Dict[str, Any] = {}
            async with AsyncExitStack() as stack:
                for upstream in upstreams:
                    await stack.enter_async_context(upstream)
                listed = await gateway.handle_list_tools(None, None)
                out["offered"] = [tool.name for tool in listed.tools]
                out["prefixes"] = {u.name for u in upstreams}

                for tool in tools:
                    out[tool] = await gateway.handle_call_tool(
                        None, types.CallToolRequestParams(name=tool, arguments={})
                    )
                # The multi-block answer must still *be* multi-block, or the
                # tolerance asserted above would be vacuous.
                weird = next(u for u in upstreams if u.name == "weird")
                out["mirror_blocks"] = len(
                    (await weird.call_tool("mirror_labels", {})).content
                )
            session.sink.close()
            return out

        data = asyncio.run(scenario())

        self.assertEqual(data["prefixes"], {"sqlite", "dirty", "weird", "flake"})
        self.assertIn("weird.mirror_labels", data["offered"])
        self.assertIn("flake.huge", data["offered"])
        self.assertEqual(data["mirror_blocks"], 3, "the fixture must stay dirty")

        for tool in tools:
            if tool in ("weird.error_with_data", "weird.nonstandard_error"):
                continue  # errors on purpose; recorded, see below
            self.assertFalse(data[tool].is_error, tool)

        records = [
            json.loads(line)
            for line in session.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), len(tools))
        self.assertTrue(all(r["decision"] == "forward" for r in records))
        # None of these payloads is a fact about the sqlite upstream: the
        # producers rule does that work, and the error flag does the rest.
        self.assertEqual([r["evidence_added"] for r in records], [[]] * len(tools))
        self.assertEqual(session.results_delivered, session.records_written)
        self.assertTrue(verify_path(session.trace_path).ok)

    def test_a_deadline_costs_one_call_not_the_run(self):
        gateway, upstreams, session = self._gateway("run-d13-timeout")

        async def call(tool: str, arguments: Dict[str, Any]):
            return await gateway.handle_call_tool(
                None, types.CallToolRequestParams(name=tool, arguments=arguments)
            )

        async def scenario() -> Dict[str, Any]:
            out: Dict[str, Any] = {}
            async with AsyncExitStack() as stack:
                for upstream in upstreams:
                    await stack.enter_async_context(upstream)
                # The flaky server sleeps 1.5 s on its fifth query, against a
                # 0.5 s deadline in the config. Different arguments per call so
                # "the same call again" is never what is being measured.
                errors = []
                for i in range(1, 6):
                    result = await call(
                        "flake.query",
                        {"sql": f"select * from labels where incident_id >= {i}"},
                    )
                    errors.append(bool(result.is_error))
                out["errors"] = errors
                out["after"] = await call("flake.stats", {})
            session.sink.close()
            return out

        data = asyncio.run(scenario())

        self.assertEqual(data["errors"].count(True), 1, data["errors"])
        self.assertEqual(session.state, "RUNNING", "a timeout is not a reason to close")
        self.assertFalse(data["after"].is_error, "the upstream must still answer")

        records = [
            json.loads(line)
            for line in session.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 6)
        # A deadline is a *forwarded* call that failed, not a refusal: the
        # gateway did send it, so the record must not claim otherwise.
        self.assertTrue(all(r["decision"] == "forward" for r in records))
        self.assertEqual(
            session.results_delivered,
            session.records_written,
            "a failed call is still a delivered result",
        )
        self.assertTrue(verify_path(session.trace_path).ok)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
