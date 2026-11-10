"""Shared plumbing for the two synthetic catalogues (``wiki_server``, ``gh_server``).

Why two more upstreams at all
-----------------------------
D14 is about *which* tools are shown, and that question only exists when the
catalogue is bigger than the window. The twenty tasks still use the SQLite
server exclusively, so these two are decoys by design: they must be plausible
enough that BM25 could mistake one for the tool a task needs (``wiki.search``
against ``gh.search``), and functional enough that calling one answers
something rather than erroring. Leaving the task set alone also keeps D12's
numbers comparable -- the catalogue tripled, the tasks did not change.

Why one implementation for both
-------------------------------
The subject under test is the *catalogue*, not these files. Two copies of the
serve/handshake boilerplate would be two places for a typo to make a server
look broken when the selector was fine, and a broken decoy is indistinguishable
from a gate that hid the right tool.

Read-only, deterministic, no network: the same rule as ``sqlite_server`` -- an
experiment must reproduce on any machine and forever.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

Body = Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    """One catalogue entry: what it says about itself, and what it answers."""

    name: str
    description: str
    properties: Mapping[str, Any] = field(default_factory=dict)
    required: Tuple[str, ...] = ()
    body: Optional[Body] = None

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(self.properties),
            "required": list(self.required),
            "additionalProperties": False,
        }

    def to_mcp(self) -> types.Tool:
        return types.Tool(
            name=self.name, description=self.description, input_schema=self.schema()
        )


def text(**extra: Any) -> Dict[str, Any]:
    return {"type": "string", **extra}


def integer(**extra: Any) -> Dict[str, Any]:
    return {"type": "integer", **extra}


# ---------------------------------------------------------------------------
# query helpers -- the whole "database" of both catalogues is a list of dicts
# ---------------------------------------------------------------------------


def _haystack(row: Mapping[str, Any], fields: Sequence[str]) -> str:
    return " ".join(str(row.get(name, "")) for name in fields).casefold()


def select(
    rows: Sequence[Mapping[str, Any]],
    args: Mapping[str, Any],
    *,
    fields: Sequence[str] = (),
    search: Sequence[str] = (),
    default_limit: int = 20,
    key: str = "items",
) -> Dict[str, Any]:
    """Equality filters, then a substring search, then a limit.

    Deliberately crude: a decoy tool whose filtering is subtle would make the
    demo depend on this file's semantics instead of on the selector's ranking.
    """
    out = list(rows)
    for name in fields:
        wanted = args.get(name)
        if wanted in (None, ""):
            continue
        out = [
            row
            for row in out
            if str(row.get(name, "")).casefold() == str(wanted).casefold()
        ]
    needle = args.get("q")
    if needle not in (None, ""):
        text = str(needle).casefold()
        out = [row for row in out if text in _haystack(row, search)]
    try:
        limit = int(args.get("limit", default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    out = out[: max(0, limit)]
    return {key: out, "count": len(out)}


def one(
    rows: Sequence[Mapping[str, Any]],
    args: Mapping[str, Any],
    *,
    field: str,
    key: str,
    missing: str = "not found",
) -> Dict[str, Any]:
    wanted = args.get(field)
    for row in rows:
        if str(row.get(field, "")).casefold() == str(wanted).casefold():
            return {key: row}
    return {"error": f"{missing}: {wanted!r}"}


def group_count(
    rows: Sequence[Mapping[str, Any]], field: str, *, key: str = "items"
) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(field, ""))
        counts[value] = counts.get(value, 0) + 1
    return {key: [{"name": name, "count": counts[name]} for name in sorted(counts)],
            "count": len(counts)}


# ---------------------------------------------------------------------------
# server wiring
# ---------------------------------------------------------------------------


def build_server(server_name: str, tools: Sequence[Tool]) -> Server:
    by_name = {tool.name: tool for tool in tools}

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[tool.to_mcp() for tool in tools])

    async def on_call_tool(
        ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        tool = by_name.get(params.name)
        if tool is None or tool.body is None:
            return _result({"error": f"unknown tool: {params.name}"}, True)
        try:
            body = tool.body(dict(params.arguments or {}))
        except Exception as exc:  # noqa: BLE001 - a decoy must not take the run down
            return _result({"error": f"{type(exc).__name__}: {exc}"}, True)
        return _result(body, "error" in body)

    return Server(
        server_name, version="0.1.0", on_list_tools=on_list_tools, on_call_tool=on_call_tool
    )


def _result(body: Dict[str, Any], is_error: bool) -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text", text=json.dumps(body, ensure_ascii=False, sort_keys=True)
            )
        ],
        is_error=is_error,
    )


async def serve(server_name: str, tools: Sequence[Tool]) -> None:
    server = build_server(server_name, tools)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: Optional[List[str]], server_name: str, tools: Sequence[Tool]) -> int:
    parser = argparse.ArgumentParser(prog=server_name, description=server_name)
    parser.parse_args(list(argv or []))

    import anyio

    try:
        anyio.run(serve, server_name, tools)
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit("this module is a helper; run wiki_server or gh_server")
