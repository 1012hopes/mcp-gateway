"""An upstream that is deliberately badly behaved in exactly the ways real ones are.

Not a mock: it is a real MCP server that speaks the protocol correctly and
advertises schemas no sane client would accept verbatim. Every defect here was
copied from a real server someone published.

Why it exists (DESIGN 2.6): the gateway's evidence rules read *field signatures*
out of tool results, and those signatures come from normalized schemas. A
compatibility layer that is "shallow" therefore does not fail loudly -- it makes
the evidence rules silently see nothing. That failure mode is invisible in a demo
that only talks to a well-formed server, so the dirty server exists to make it
visible and testable.

| tool | defect |
|---|---|
| ``query`` | ``$ref`` into ``$defs`` two levels deep; same short name as ``sqlite.query`` |
| ``set_mode`` | the arguments only exist inside ``oneOf`` variants, so ``properties`` is empty |
| ``set_flag`` | ``additionalProperties: false`` but ``required`` names are not in ``properties`` -- uncallable by a strict client |
| ``工具查询`` | non-ASCII tool name |
| ``tool_with_a_name_long_enough_to_...`` | exceeds the 64-character limit *after* namespacing |
| ``status`` | 2 kB description, empty schema ``{}`` |

Results are deterministic and tiny. This server is a schema surface, not a data
source; the sqlite upstream is the one the experiments run against.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

LONG_DESCRIPTION = (
    "Return the status of the dirty demo server. "
    + "This description is intentionally long because real servers paste their "
    "entire documentation into the tool description, and a registry that assumes "
    "short descriptions will either truncate them silently or blow up the prompt "
    "budget. The gateway normalizes whitespace and keeps every word; deciding "
    "what to do about length is a gating concern, not a registry concern. "
) * 8

_OP_DEF = {
    "type": "string",
    "enum": ["eq", "lt", "gt", "like"],
}

_FILTER_DEF = {
    "type": "object",
    "properties": {
        "column": {"type": "string"},
        "op": {"$ref": "#/$defs/Op"},
        "value": {"type": ["string", "number", "null"]},
    },
    "required": ["column", "op"],
    "additionalProperties": False,
}

_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "where": {"$ref": "#/$defs/Filter"},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["where"],
    "$defs": {
        "Op": _OP_DEF,
        "Filter": _FILTER_DEF,
    },
}

_SET_MODE_SCHEMA = {
    # No ``properties`` at this level: the only description of the arguments is
    # inside the variants. Tools shaped like this read as "no arguments" unless
    # the gateway flattens the union.
    "type": "object",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"const": "fast"},
                "budget_ms": {"type": "integer"},
            },
            "required": ["mode"],
        },
        {
            "type": "object",
            "properties": {
                "mode": {"const": "exact"},
                "statement": {"type": "string"},
            },
            "required": ["mode", "statement"],
        },
    ],
}

_SET_FLAG_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": ["flag"],
    "additionalProperties": False,
}

TOOLS: List[types.Tool] = [
    types.Tool(
        name="query",
        description="Run a filtered query. Shares its short name with sqlite.query.",
        input_schema=_QUERY_SCHEMA,
    ),
    types.Tool(
        name="set_mode",
        description="Switch execution mode.",
        input_schema=_SET_MODE_SCHEMA,
    ),
    types.Tool(
        name="set_flag",
        description="Set a boolean flag.",
        input_schema=_SET_FLAG_SCHEMA,
    ),
    types.Tool(
        name="status",
        description=LONG_DESCRIPTION,
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="工具查询",
        description="Non-ASCII tool name -- legal JSON, illegal in a tool name.",
        input_schema={"type": "object", "properties": {"关键词": {"type": "string"}}},
    ),
    types.Tool(
        name="tool_with_a_name_long_enough_to_overflow_the_limit_once_namespaced",
        description="Exceeds 64 characters after the upstream prefix is added.",
        input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
    ),
]


def _json(payload: Dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=json.dumps(payload, ensure_ascii=False, sort_keys=True)
            )
        ]
    )


def handle(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
    if name == "query":
        return _json({"rows": [{"id": 1, "value": "dirty"}], "row_count": 1})
    if name == "set_mode":
        return _json({"mode": arguments.get("mode", "fast")})
    if name == "set_flag":
        return _json({"flag": arguments.get("flag")})
    if name == "status":
        return _json({"ok": True, "description_chars": len(LONG_DESCRIPTION)})
    if name == "工具查询":
        return _json({"hits": [], "query": arguments.get("关键词")})
    return _json({"ok": True, "echo": arguments})


def build_server() -> Server:
    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(TOOLS))

    async def on_call_tool(
        ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return handle(params.name, dict(params.arguments or {}))

    return Server(
        "dirty-schema-demo",
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
