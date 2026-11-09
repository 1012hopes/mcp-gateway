"""The gateway as an MCP server: the single choke point between agent and tools.

Everything the project claims depends on this file being the *only* way a tool
gets called. Three consequences, in order of how easy they are to get wrong:

1. **Nothing is written to stdout except protocol messages.** A stray ``print``
   corrupts the JSON-RPC stream and the failure looks like a hung client, not
   like a logging bug. All diagnostics go to stderr.

2. **The record is written before the result is returned.** If persisting the
   record fails, the caller gets an error result, the run is marked aborted and
   every later call is refused. Handing back a result whose record never landed
   would create exactly the gap a hash chain cannot detect (DESIGN 1.5).

3. **``offered`` is captured, not inferred.** It is the set of tools the agent
   could have picked from *at that moment*. Without it "was the wrong tool
   chosen" has no denominator -- Accuracy@K is uncomputable (DESIGN 2.4). D4
   exposes everything, so ``offered`` is the full list; D14 will make it a
   subset by gating, and the field already holds the right meaning either way.

Handlers are plain methods rather than closures so that the fail-closed path can
be driven in-process by tests and by ``scripts/failclosed_demo.py`` without a
subprocess in the way.
"""

from __future__ import annotations

import time
from contextlib import AsyncExitStack
from typing import Any, Callable, Dict, List, Optional, Sequence

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .config import GatewayConfig
from .session import MAX_RESULT_BYTES, RunSession, digest_of
from .trace.sink import TraceChainError, TraceWriteError
from .upstream import StdioUpstream, UpstreamFailure, UpstreamTool, tail_of_exception
from .version import GATEWAY_VERSION

__all__ = ["Gateway", "result_payload", "text_result", "error_result"]


def result_payload(result: types.CallToolResult) -> Dict[str, Any]:
    """What gets digested. Digested, not stored: traces must not become data copies."""
    return result.model_dump(mode="json", exclude_none=True)


def text_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], is_error=is_error
    )


def error_result(message: str) -> types.CallToolResult:
    return text_result(message, is_error=True)


class Gateway:
    """Aggregates upstreams and records every call. One per run."""

    def __init__(
        self,
        config: GatewayConfig,
        upstreams: Sequence[StdioUpstream],
        session: RunSession,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.upstreams = list(upstreams)
        self.session = session
        self._clock = clock
        self._tools: Optional[List[UpstreamTool]] = None

    # -- tool surface ------------------------------------------------------

    async def list_all_tools(self, *, refresh: bool = False) -> List[UpstreamTool]:
        if self._tools is not None and not refresh:
            return self._tools
        tools: List[UpstreamTool] = []
        for upstream in self.upstreams:
            tools.extend(await upstream.list_tools(refresh=refresh))
        if self.config.max_tools is not None:
            tools = tools[: self.config.max_tools]
        self._tools = tools
        return tools

    def _upstream_for(self, exposed_name: str) -> StdioUpstream:
        for upstream in self.upstreams:
            if exposed_name.startswith(upstream.name + "."):
                return upstream
        known = ", ".join(u.name for u in self.upstreams)
        raise UpstreamFailure(f"unknown tool {exposed_name!r}; upstreams: {known}")

    # -- MCP handlers ------------------------------------------------------

    async def handle_list_tools(self, ctx: Any, params: Any) -> types.ListToolsResult:
        tools = await self.list_all_tools()
        return types.ListToolsResult(tools=[tool.to_mcp() for tool in tools])

    async def handle_call_tool(
        self, ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        session = self.session
        session.calls_attempted += 1

        if session.aborted:
            # The run is already dead. Refusing here is what stops an agent from
            # continuing on evidence that was never written down.
            return error_result(
                f"run aborted ({session.abort_reason}); no further calls are accepted"
            )

        chosen = params.name
        offered = [tool.exposed_name for tool in await self.list_all_tools()]
        started = self._clock()

        try:
            upstream = self._upstream_for(chosen)
            result = await upstream.call_tool(chosen, params.arguments)
            payload = result_payload(result)
            is_error = bool(result.is_error)
            response = result
        except UpstreamFailure as exc:
            payload = {"error": tail_of_exception(exc)}
            is_error = True
            response = error_result(f"{chosen} failed: {exc}")

        latency_ms = int((self._clock() - started) * 1000)
        truncated = len(repr(payload)) > MAX_RESULT_BYTES

        try:
            session.record_call(
                offered=offered,
                chosen=chosen,
                args=dict(params.arguments or {}),
                result=payload,
                latency_ms=latency_ms,
                decision="forward",
                state=session.state,
                truncated=truncated,
            )
        except (TraceWriteError, TraceChainError) as exc:
            # The call happened but cannot be proven afterwards. Drop the result
            # on the floor rather than let the agent act on unrecorded evidence.
            session.abort(f"trace write failed at seq {session.records_written + 1}: {exc}")
            return error_result(
                "trace write failed; run aborted and this result is discarded "
                "(fail-closed)"
            )

        session.mark_delivered()
        session.turn += 1
        return response

    # -- run loop ----------------------------------------------------------

    def build_server(self) -> Server:
        return Server(
            self.config.name,
            version=GATEWAY_VERSION,
            on_list_tools=self.handle_list_tools,
            on_call_tool=self.handle_call_tool,
        )

    async def run(self) -> None:
        async with AsyncExitStack() as stack:
            for upstream in self.upstreams:
                await stack.enter_async_context(upstream)
            server = self.build_server()
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())
