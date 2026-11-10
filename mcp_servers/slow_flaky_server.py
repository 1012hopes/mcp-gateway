"""An upstream that is slow, occasionally empty, and occasionally enormous.

The third of the three dirty servers (DESIGN 2.6). Its job is to make three
failure modes of a *transport*, rather than of a schema, reproducible:

1. **It hangs.** Every ``--timeout-every``-th ``query`` sleeps past the client's
   deadline. A gateway that has no deadline blocks forever; one that has one must
   keep serving afterwards, on the same session, or the run dies at the first bad
   minute of somebody else's server.
2. **It answers nothing.** Every ``--empty-every``-th ``query`` returns the JSON
   literal ``null`` instead of a row set. The run must treat that as "no answer",
   not as "the empty answer", and it must not crash the extractor.
3. **It answers more than anyone can read.** ``huge`` returns a body just over a
   megabyte. The gateway flags it ``truncated`` and, crucially, does not let the
   half-read payload satisfy an evidence requirement (``session.MAX_RESULT_BYTES``).

Determinism is not a nicety here. A server that fails "20% of the time" at random
makes every test either flaky or untrue: the test would have to loop until the
bad branch happened, and the demo could never show the failing step twice. So the
pattern is a **call counter**, not a die: the 5th query is always slow, the 7th is
always empty. Both periods are command-line flags, so a test can make the flake
happen on the first call instead of the fifth.

The deadline is the client's, not this server's: ``call_timeout_s`` in
``config/gateway-dirty.json`` is 0.5 s while ``--sleep-s`` is 1.5 s. Putting the
short number on the client side is what makes the timeout a property of the
gateway rather than of a cooperative server that politely gives up.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

LABELS = [
    {"incident_id": 1, "label": "regression"},
    {"incident_id": 1, "label": "mcp"},
    {"incident_id": 2, "label": "flaky"},
    {"incident_id": 3, "label": "p1"},
    {"incident_id": 4, "label": "governance"},
    {"incident_id": 4, "label": "mcp"},
]

TOOLS: List[types.Tool] = [
    types.Tool(
        name="query",
        description="Run a query. Occasionally slow, occasionally empty.",
        input_schema={
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="huge",
        description="Return a body just over one megabyte.",
        input_schema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="stats",
        description="Cheap and reliable. The control case.",
        input_schema={"type": "object", "properties": {}},
    ),
]


class Flaky:
    """The call counter and the pattern. One instance per server process."""

    def __init__(
        self,
        *,
        timeout_every: int = 5,
        empty_every: int = 7,
        sleep_s: float = 1.5,
        huge_bytes: int = 1_200_000,
    ) -> None:
        self.timeout_every = max(0, timeout_every)
        self.empty_every = max(0, empty_every)
        self.sleep_s = sleep_s
        self.huge_bytes = huge_bytes
        #: Counts ``query`` calls only. Counting every tool would make the
        #: position of the flake depend on which *other* tools were called --
        #: i.e. on the test's incidental behaviour rather than on the pattern.
        self.queries = 0

    def tick(self) -> int:
        self.queries += 1
        return self.queries

    def should_hang(self, n: int) -> bool:
        return bool(self.timeout_every) and n % self.timeout_every == 0

    def should_be_empty(self, n: int) -> bool:
        # Independent of ``should_hang``: they coincide on ``lcm(5, 7)`` calls,
        # which is fine -- a hang dominates, and no assertion here depends on
        # the two never meeting.
        return bool(self.empty_every) and n % self.empty_every == 0


def _text(text: str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], is_error=is_error
    )


def _json(payload: Any) -> types.CallToolResult:
    return _text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


async def handle(
    name: str, arguments: Dict[str, Any], flaky: Flaky
) -> types.CallToolResult:
    if name == "query":
        n = flaky.tick()
        if flaky.should_hang(n):
            # ``await``, not ``time.sleep``: the defect under test is a *slow
            # answer*, not a server that has stopped reading its own mailbox. A
            # blocking sleep here would make every later call fail too, and the
            # experiment would then be measuring a dead process while claiming to
            # measure a deadline.
            await anyio.sleep(flaky.sleep_s)
        if flaky.should_be_empty(n):
            # Not ``{"rows": []}``. An empty row set is an answer; this is the
            # absence of one, and telling them apart is the point of the case.
            return _text("null")
        limit = int(arguments.get("limit", 50))
        rows = LABELS[:limit]
        return _json({"rows": rows, "row_count": len(rows)})

    if name == "huge":
        # ``padding`` is not part of any evidence shape, so the row set alone
        # would match -- the body is over the limit purely in bytes. That is
        # deliberate: the case must fail *if and only if* size is what gates it.
        payload = {
            "rows": LABELS,
            "row_count": len(LABELS),
            "padding": "x" * flaky.huge_bytes,
        }
        return _json(payload)

    if name == "stats":
        return _json({"queries": flaky.queries, "status": "fine"})

    return _text(json.dumps({"error": f"unknown tool: {name}"}), is_error=True)


def build_server(flaky: Flaky) -> Server:
    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(TOOLS))

    async def on_call_tool(
        ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return await handle(params.name, dict(params.arguments or {}), flaky)

    return Server(
        "slow-flaky-demo",
        version="0.1.0",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve(flaky: Flaky) -> None:
    server = build_server(flaky)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="slow_flaky_server", description=__doc__
    )
    parser.add_argument("--timeout-every", type=int, default=5)
    parser.add_argument("--empty-every", type=int, default=7)
    parser.add_argument("--sleep-s", type=float, default=1.5)
    parser.add_argument("--huge-bytes", type=int, default=1_200_000)
    args = parser.parse_args(argv)

    flaky = Flaky(
        timeout_every=args.timeout_every,
        empty_every=args.empty_every,
        sleep_s=args.sleep_s,
        huge_bytes=args.huge_bytes,
    )

    try:
        anyio.run(serve, flaky)
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
