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
   chosen" has no denominator -- Accuracy@K is uncomputable (DESIGN 2.4). The
   registry decides membership, so ``offered`` is exactly what ``tools/list``
   returned; D14 will shrink it by gating and the field keeps its meaning.

Handlers are plain methods rather than closures so that the fail-closed path can
be driven in-process by tests and by ``scripts/failclosed_demo.py`` without a
subprocess in the way.
"""

from __future__ import annotations

import sys
import time
from contextlib import AsyncExitStack
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .config import GatewayConfig
from .registry import Registry, ToolEntry, build_registry
from .session import MAX_RESULT_BYTES, RunSession
from .trace.sink import TraceChainError, TraceWriteError
from .upstream import StdioUpstream, UpstreamFailure, tail_of_exception
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
        baseline: Optional[Mapping[str, str]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.upstreams = list(upstreams)
        self.session = session
        self._clock = clock
        self._baseline = baseline
        self._registry: Optional[Registry] = None
        self._by_upstream: Dict[str, StdioUpstream] = {}
        for upstream in self.upstreams:
            if upstream.name in self._by_upstream:
                # Two servers under one prefix would silently shadow each other
                # and make ``chosen`` ambiguous in every trace written afterwards.
                raise ValueError(f"duplicate upstream name: {upstream.name!r}")
            self._by_upstream[upstream.name] = upstream

    # -- tool surface ------------------------------------------------------

    async def registry(self, *, refresh: bool = False) -> Registry:
        """List every upstream once, then normalize/namespace/hash the union."""
        if self._registry is not None and not refresh:
            return self._registry
        listing = [
            (upstream.name, await upstream.list_tools(refresh=refresh))
            for upstream in self.upstreams
        ]
        registry = build_registry(listing, baseline=self._baseline)
        self._registry = registry
        # stderr, never stdout: stdout is the JSON-RPC stream. A warning that
        # lands in the protocol stream looks like a hung client, not a warning.
        for note in registry.warnings():
            print(f"registry: {note}", file=sys.stderr, flush=True)
        return registry

    async def list_all_tools(self, *, refresh: bool = False) -> List[ToolEntry]:
        """The tools the agent may pick from right now (frozen ones excluded)."""
        return list((await self.registry(refresh=refresh)).offered)

    # -- MCP handlers ------------------------------------------------------

    async def handle_list_tools(self, ctx: Any, params: Any) -> types.ListToolsResult:
        registry = await self.registry()
        return types.ListToolsResult(tools=[entry.to_mcp() for entry in registry.offered])

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

        registry = await self.registry()
        chosen = params.name
        offered = registry.offered_names()
        entry = registry.get(chosen)
        started = self._clock()
        decision = "forward"

        try:
            if entry is None:
                decision = "block_unknown_tool"
                raise UpstreamFailure(
                    f"unknown tool {chosen!r}; offered: {', '.join(offered) or '(none)'}"
                )
            if entry.frozen:
                # The upstream changed its contract since the baseline was
                # frozen. Serving it would put incomparable traces in the same
                # file; refusing keeps the comparison honest (DESIGN 2.6 pitfall 3).
                decision = "block_schema_drift"
                raise UpstreamFailure(
                    f"{chosen} is frozen: {entry.drift}. Re-freeze the baseline "
                    f"only after deciding the old traces stay comparable."
                )
            result = await self._by_upstream[entry.upstream].call_tool(
                entry.original_name, params.arguments
            )
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
                decision=decision,
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
