"""An upstream that is wrong at the *protocol edge*, not in its schema.

``dirty_schema_server`` covers servers whose tool schemas are unusable. This one
covers the other half of what real servers actually ship: results and errors that
are shaped in ways no spec blesses. Every case below was copied from a real
server, and every one of them is a way for a naive gateway to do the wrong thing
*silently*:

| tool | defect | what a naive gateway does |
|---|---|---|
| ``mirror_labels`` | the answer arrives as the *second* of three text blocks, behind a human-readable banner and trailed by "(6 rows)" | reads block 0, finds no JSON, concludes "no evidence" |
| ``null_body`` | a successful call whose text is the JSON literal ``null`` (sloppy serializer, not an error) | treats ``null`` as "the empty object" and invents a fact |
| ``empty_content`` | ``content: []`` -- legal, and means "nothing to say" | crashes on ``content[0]`` |
| ``broken_json`` | truncated JSON: ``{"rows": [`` | raises inside the extractor, aborting a healthy run |
| ``scalar_body`` | text is ``42``, not an object | iterates over a non-mapping |
| ``error_with_data`` | ``isError: true`` **carrying** a full ``{"rows": ...}`` body | counts the rows: an error is not an answer |
| ``nonstandard_error`` | application error with a **string** code (``"E_WEIRD_42"``) instead of a number | assumes numeric codes, or ignores the error flag |
| ``boom`` | raises, so the client gets a JSON-RPC error with code ``9999`` -- outside the reserved range | lets a protocol error escape as a crash |
| ``noisy`` | fires five ``notifications/tools/list_changed`` per call | re-lists the tool surface five times, or dies on an unknown notification |

Why these matter to *this* project rather than to a proxy: the evidence model
reads field signatures out of result bodies (DESIGN A3). A compatibility layer
that only forwards well-formed results never has to distinguish "the body is
missing" from "the body is empty" from "the body is wrong" -- and those three
have different consequences. The claim this server lets us test is narrow and
checkable: **envelope noise changes nothing; a body that is not the payload
produces no fact.**

The three-block layout of ``mirror_labels`` is deliberate and mirrors MySQL's
CLI: banner, payload, row count. The extractor accepts the first *parsable*
block, so a banner that is itself JSON would win -- that limitation is written
down in DESIGN (limits) rather than papered over here.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

#: The same six rows the clean sqlite upstream returns for ``labels``. Duplicated
#: on purpose: this server must be startable with no database, and the equality
#: being tested is between two *encodings of one body*, not between two sources.
LABELS = [
    {"incident_id": 1, "label": "regression"},
    {"incident_id": 1, "label": "mcp"},
    {"incident_id": 2, "label": "flaky"},
    {"incident_id": 3, "label": "p1"},
    {"incident_id": 4, "label": "governance"},
    {"incident_id": 4, "label": "mcp"},
]

BANNER = "Query OK, 6 rows in set (0.00 sec)"
TRAILER = "(6 rows)"

TOOLS: List[types.Tool] = [
    types.Tool(
        name="echo",
        description="Return the arguments. Ordinary on purpose: the control case.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "additionalProperties": True,
        },
    ),
    types.Tool(
        name="mirror_labels",
        description="The labels table, split across three text blocks.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="null_body",
        description="A successful call whose payload is the JSON literal null.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="empty_content",
        description="Returns no content blocks at all.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="broken_json",
        description="Returns text that is JSON up to the second character.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="scalar_body",
        description="Returns a bare number where an object belongs.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="error_with_data",
        description="Error flag set, full row set attached anyway.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="nonstandard_error",
        description="Error whose code is a string, not a JSON-RPC integer.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="boom",
        description="Raises instead of returning: a protocol-level error.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="noisy",
        description="Sends five tools/list_changed notifications per call.",
        input_schema={"type": "object", "properties": {}},
    ),
]

#: How many list_changed notifications ``noisy`` fires. Five, not one, because a
#: gateway that reacts once and a gateway that reacts per notification look the
#: same under a single event -- the defect is the *rate*, so the rate has to be
#: visible in the trace of what the client did.
NOTIFICATION_BURST = 5


def _text(text: str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], is_error=is_error
    )


def _json(payload: Any, *, is_error: bool = False) -> types.CallToolResult:
    return _text(json.dumps(payload, ensure_ascii=False, sort_keys=True), is_error=is_error)


async def handle(
    name: str, arguments: Dict[str, Any], ctx: Any
) -> types.CallToolResult:
    if name == "echo":
        return _json({"ok": True, "echo": arguments})

    if name == "mirror_labels":
        # Three blocks: a banner that is not JSON, the payload, a row count that
        # is not JSON either. The fact is in the middle and nothing says so.
        return types.CallToolResult(
            content=[
                types.TextContent(type="text", text=BANNER),
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {"rows": LABELS, "row_count": len(LABELS)}, sort_keys=True
                    ),
                ),
                types.TextContent(type="text", text=TRAILER),
            ]
        )

    if name == "null_body":
        # ``json.dumps(None)`` -- what you get when a serializer forgets that
        # "empty result" and "no result" are different, which is the whole point.
        return _text("null")

    if name == "empty_content":
        return types.CallToolResult(content=[])

    if name == "broken_json":
        return _text('{"rows": [')

    if name == "scalar_body":
        return _text("42")

    if name == "error_with_data":
        # The expensive one. A gateway that unwraps the body before checking the
        # error flag will record a fact the server explicitly said it did not
        # produce, and the run halts "complete" on it.
        return _json({"rows": LABELS, "row_count": len(LABELS)}, is_error=True)

    if name == "nonstandard_error":
        return _json(
            {"error": {"code": "E_WEIRD_42", "message": "not a JSON-RPC code"}},
            is_error=True,
        )

    if name == "boom":
        raise RuntimeError("upstream exploded on purpose")

    if name == "noisy":
        for _ in range(NOTIFICATION_BURST):
            await ctx.session.send_tool_list_changed()
        return _json({"ok": True, "notifications": NOTIFICATION_BURST})

    return _json({"error": f"unknown tool: {name}"}, is_error=True)


def build_server() -> Server:
    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(TOOLS))

    async def on_call_tool(
        ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return await handle(params.name, dict(params.arguments or {}), ctx)

    # Only ``on_list_tools`` / ``on_call_tool`` are supplied, so the server
    # advertises the tools capability and nothing else -- a client that assumes
    # resources or prompts exist is the client this server is designed to fail.
    return Server(
        "weird-capability-demo",
        version="0.1.0",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: List[str] | None = None) -> int:
    import anyio

    try:
        anyio.run(serve)
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
