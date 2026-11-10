"""D13 in one command: three deliberately bad servers, and what survives them.

    {python} scripts/compat_demo.py

The claim under test (DESIGN 2.6) is not "we support every server" -- it is the
narrower, checkable sentence: **hanging up three non-conforming servers does not
crash the gateway, and the evidence extraction on their output is identical to
the clean upstream's.** Five checks, in the order a reviewer would ask them:

1. **They are all up.** Four upstreams (one clean, three dirty) are spawned and
   listed in one registry. Every exposed name is legal (``[A-Za-z0-9_.-]{1,64}``)
   and unique, so nothing collided on the way in.
2. **The dirty schema does not move the clean one.** ``dirty.query`` and
   ``sqlite.query`` are two names for two different servers; the non-ASCII tool
   name is escaped and the 74-character one is truncated rather than dropped.
3. **Envelope noise changes nothing.** One row set, six encodings -- single block,
   banner/payload/trailer, CRLF, explicit ``is_error: false``, a null
   ``structuredContent`` -- all yield the same evidence. This is the check the
   first two dirty servers exist for.
4. **A body that is not the payload yields no fact.** Error flag set, JSON
   ``null``, empty content, truncated JSON, a bare number: five things that look
   like an answer and are not. Each must extract to ``{}``, and none may raise.
5. **The size gate is what gates.** Same tool, same arguments, same shape, two
   payloads differing only in bytes: 200 B is evidence, 1.2 MB is ``truncated``
   and is not. This is the only check that needs a stub upstream -- anything else
   would vary the tool name too, and then "no evidence" would be explained by the
   producer rule instead of by the truncation.

Then a live section drives the flaky upstream through the real gateway: one
timed-out call is recorded as exactly one failed call, the run stays open, the
next call to the same server succeeds, and the trace verifies.

Rules are **off** in the live section, deliberately. Five identical queries are
what a flake needs in order to fire, and R2 would call the third one a plateau
and halt -- correctly, but that is the controller's demo, not this one's.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from mcp import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.config import GatewayConfig, load_config  # noqa: E402
from gateway.evidence import EvidenceModel, load_evidence_model  # noqa: E402
from gateway.registry import RawTool  # noqa: E402
from gateway.session import MAX_RESULT_BYTES, RunSession  # noqa: E402
from gateway.stop import StopController  # noqa: E402
from gateway.trace.sink import JsonlSink  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402
from gateway.transport import Gateway  # noqa: E402
from gateway.upstream import StdioUpstream, UpstreamFailure  # noqa: E402

DIRTY_CONFIG = ROOT / "config" / "gateway-dirty.json"
EVIDENCE = ROOT / "config" / "evidence.json"

#: The prefix every tool name must match. MCP's own rule; the gateway's job is to
#: produce names a strict client can carry, not names it happens to like.
LEGAL_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,64}")

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

#: The fact each encoding below either carries or does not. Held fixed so that a
#: difference between two results can only come from the encoding.
FACT = "sql.rows.labels"


# ---------------------------------------------------------------------------
# payload builders -- the encodings, written out so the diff is readable
# ---------------------------------------------------------------------------


def _block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def same_body() -> Dict[str, Dict[str, Any]]:
    """Six ways to deliver one row set. All must extract to ``{FACT: ...}``."""
    text = _dump(LABELS_BODY)
    return {
        "one block (the clean upstream)": {"content": [_block(text)]},
        "banner / payload / trailer": {
            "content": [
                _block("Query OK, 6 rows in set (0.00 sec)"),
                _block(text),
                _block("(6 rows)"),
            ]
        },
        "explicit is_error false": {"content": [_block(text)], "is_error": False},
        "null structured content": {
            "content": [_block(text)],
            "structured_content": None,
        },
        "crlf and padding inside the json": {
            "content": [_block(json.dumps(LABELS_BODY, indent=2).replace("\n", "\r\n"))]
        },
        "an extra unused key": {
            "content": [_block(_dump({**LABELS_BODY, "server_hint": "cached"}))]
        },
    }


def not_body() -> Dict[str, Dict[str, Any]]:
    """Five payloads that look like an answer and are not. All must extract to {}."""
    text = _dump(LABELS_BODY)
    return {
        "error flag set, rows attached": {
            "content": [_block(text)],
            "is_error": True,
        },
        "the json literal null": {"content": [_block("null")]},
        "no content blocks at all": {"content": []},
        "truncated json": {"content": [_block('{"rows": [')]},
        "a bare number": {"content": [_block("42")]},
    }


# ---------------------------------------------------------------------------
# part 3's stub: the only variable allowed to change is size
# ---------------------------------------------------------------------------


class StubUpstream:
    """A real ``StdioUpstream`` stand-in whose answer is one of two sizes.

    It exists so that check 5 can hold **tool, arguments and shape** fixed while
    changing only the byte count. Calling a genuinely dirty server for this would
    move the tool name too, and then "no evidence" would be explained by the
    producer rule -- a correct answer to a different question.
    """

    def __init__(self, name: str, body: Mapping[str, Any]) -> None:
        self.name = name
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
            content=[types.TextContent(type="text", text=_dump(self._body))]
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _records(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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


def extraction_checks(model: EvidenceModel) -> Dict[str, bool]:
    """Checks 3 and 4: noise is invisible, a non-body is not a fact."""
    clean = model.extract(
        {"content": [_block(_dump(LABELS_BODY))]}, tool="sqlite.query", args=LABELS_SQL
    )
    checks: Dict[str, bool] = {
        "the control encoding does yield the fact": clean == {FACT: "sqlite.query"},
    }
    for name, payload in same_body().items():
        got = model.extract(payload, tool="sqlite.query", args=LABELS_SQL)
        checks[f"same fact despite: {name}"] = got == clean
    for name, payload in not_body().items():
        try:
            got = model.extract(payload, tool="sqlite.query", args=LABELS_SQL)
        except Exception as exc:  # noqa: BLE001 - "does not raise" is the claim
            checks[f"no fact from: {name}"] = False
            print(f"    raised on {name}: {type(exc).__name__}: {exc}")
            continue
        checks[f"no fact from: {name}"] = got == {}
    return checks


def truncation_checks(
    model: EvidenceModel, config: GatewayConfig, trace_dir: Path, run_id: str
) -> Tuple[Dict[str, bool], List[str]]:
    """Check 5: same call, two sizes, opposite verdicts."""
    import asyncio

    notes: List[str] = []

    small = dict(LABELS_BODY, row_count=6)
    large = dict(small, padding="x" * (MAX_RESULT_BYTES + 200_000))

    async def scenario() -> List[Dict[str, Any]]:
        results = []
        for size in (small, large):
            upstream = StubUpstream("sqlite", size)
            sink = JsonlSink(trace_dir / f"{run_id}-{len(results)}.jsonl", run_id=run_id)
            session = RunSession(sink=sink, run_id=run_id)
            gateway = Gateway(
                config,
                [upstream],  # type: ignore[list-item]
                session,
                controller=StopController(()),
                evidence=model,
            )
            await gateway.handle_call_tool(
                None,
                types.CallToolRequestParams(
                    name="sqlite.query", arguments=dict(LABELS_SQL)
                ),
            )
            sink.close()
            results.append(_records(session.trace_path)[0])
        return results

    records = asyncio.run(scenario())
    ok, big = records
    notes.append(f"small payload: truncated={ok['truncated']} evidence={ok['evidence_added']}")
    notes.append(f"large payload: truncated={big['truncated']} evidence={big['evidence_added']}")
    checks = {
        "the small payload is evidence": ok["evidence_added"] == [FACT]
        and ok["truncated"] is False,
        "the large payload is flagged truncated": big["truncated"] is True,
        "the large payload is not evidence": big["evidence_added"] == [],
        "the two calls are otherwise identical": ok["chosen"] == big["chosen"]
        and ok["args_hash"] == big["args_hash"],
        "the traces verify": verify_path(Path(trace_dir / f"{run_id}-0.jsonl")).ok
        and verify_path(Path(trace_dir / f"{run_id}-1.jsonl")).ok,
    }
    return checks, notes


def live_checks(
    config: GatewayConfig, model: EvidenceModel, trace_dir: Path, run_id: str
) -> Tuple[Dict[str, bool], Dict[str, Any]]:
    """Checks 1, 2 and the timeout section -- one real gateway, four real servers."""
    import asyncio

    from mcp import types

    config = GatewayConfig(
        name=config.name,
        trace_dir=trace_dir,
        model_id="compat-fixture",
        schema_manifest=config.schema_manifest,
        evidence_model=config.evidence_model,
        upstreams=config.upstreams,
    )
    upstreams = _upstreams(config)
    sink = JsonlSink(trace_dir / f"{run_id}.jsonl", run_id=run_id)
    session = RunSession(sink=sink, run_id=run_id)
    gateway = Gateway(
        config,
        upstreams,
        session,
        controller=StopController(()),
        evidence=model,
    )

    async def call(name: str, arguments: Dict[str, Any]) -> Any:
        return await gateway.handle_call_tool(
            None, types.CallToolRequestParams(name=name, arguments=arguments)
        )

    async def scenario() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        async with AsyncExitStack() as stack:
            for upstream in upstreams:
                await stack.enter_async_context(upstream)

            listed = await gateway.handle_list_tools(None, None)
            out["offered"] = [tool.name for tool in listed.tools]
            out["upstreams"] = {u.name: bool(u.server_info) for u in upstreams}

            out["boom"] = await call("weird.boom", {})
            out["mirror"] = await call("weird.mirror_labels", {})
            out["error_with_data"] = await call("weird.error_with_data", {})
            out["noisy"] = await call("weird.noisy", {})
            # Re-list *after* the burst. Notifications are not supposed to move
            # the surface under the agent mid-run.
            out["offered_after"] = [
                tool.name for tool in (await gateway.handle_list_tools(None, None)).tools
            ]

            # Five queries: the flaky server hangs on the fifth. Distinct
            # arguments so that a *repeated call* is never what is being measured.
            outcomes = []
            for i in range(1, 6):
                result = await call(
                    "flake.query",
                    {"sql": f"select * from labels where incident_id >= {i}"},
                )
                outcomes.append(bool(result.is_error))
            out["flake_errors"] = outcomes
            # ... and the same upstream must still answer afterwards. Its slow
            # handler sleeps concurrently, so this is not a race against it.
            after = await call("flake.stats", {})
            out["after_timeout"] = after
        sink.close()
        out["session"] = session
        return out

    data = asyncio.run(scenario())
    session = data["session"]
    records = _records(session.trace_path)
    offered = data["offered"]
    by_name = {record["chosen"]: record for record in records}

    checks = {
        "four upstreams are up": len(config.upstreams) == 4
        and all(data["upstreams"].values()),
        "every exposed name is legal and unique": all(
            LEGAL_NAME.fullmatch(name) for name in offered
        )
        and len(set(offered)) == len(offered),
        "the two `query` tools did not collide": "sqlite.query" in offered
        and "dirty.query" in offered,
        "a non-ascii tool name was escaped, not dropped": any(
            name.startswith("dirty._u") for name in offered
        ),
        "an over-long tool name was truncated to 64": any(
            len(name) == 64 and name.startswith("dirty.tool_with_a_name") for name in offered
        ),
        "a protocol error is a recorded call, not a crash": data["boom"].is_error
        and by_name["weird.boom"]["decision"] == "forward",
        "an error result with rows in it adds no evidence": by_name[
            "weird.error_with_data"
        ]["evidence_added"]
        == [],
        "a three-block answer is still readable": data["mirror"].is_error is False,
        "five list_changed notifications do not change the surface": data[
            "offered_after"
        ]
        == offered,
        "exactly one of five queries timed out": data["flake_errors"].count(True) == 1,
        "the run is still open after the timeout": session.state == "RUNNING",
        "the server answers again afterwards": data["after_timeout"].is_error is False,
        "nothing was delivered that was not recorded": session.results_delivered
        == session.records_written,
        "the trace verifies": verify_path(session.trace_path).ok,
    }
    notes = {
        "records": len(records),
        "trace": session.trace_path,
        "offered": len(offered),
    }
    return checks, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D13: three dirty upstreams, one gateway")
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    run_id = args.run_id or f"run-d13-demo-{uuid.uuid4().hex[:6]}"

    with tempfile.TemporaryDirectory() as tmp:
        trace_dir = Path(args.trace_dir) if args.trace_dir else Path(tmp)

        config = load_config(str(DIRTY_CONFIG))
        model = load_evidence_model(EVIDENCE)

        print("upstreams: " + ", ".join(spec.name for spec in config.upstreams))
        print(f"evidence: {model.version} ({len(model.types)} types)")
        print("=" * 78)

        print("1-2, 6 -- four upstreams, one registry, and a flaky deadline")
        print(
            "  (the traceback on stderr below is the *upstream's*, served on "
            "purpose: weird.boom raises. The gateway records it and keeps going.)"
        )
        live, live_notes = live_checks(config, model, trace_dir, run_id)
        print(f"  {live_notes['records']} records -> {live_notes['trace']}")
        print(f"  offered: {live_notes['offered']} tools")

        print("3-4 -- what the extractor does with hostile envelopes")
        envelope = extraction_checks(model)

        print("5 -- the size gate")
        truncation, trunc_notes = truncation_checks(model, config, trace_dir, run_id)
        for note in trunc_notes:
            print(f"  {note}")

        checks = {**envelope, **truncation, **live}
        print("=" * 78)
        for name, ok in checks.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

        print("-" * 78)
        if all(checks.values()):
            print(
                f"PASS: {len(checks)} checks -- three dirty servers, one clean one, "
                "no crash and no invented fact"
            )
            return 0
        print("FAIL: see the checks above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
