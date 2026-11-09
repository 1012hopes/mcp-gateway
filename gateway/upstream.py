"""Upstream MCP servers, spoken to over stdio with the official SDK.

D4 keeps exactly one upstream. What it does *not* skimp on is the two things
that are expensive to retrofit (DESIGN 2.8):

* **Namespace isolation.** Every exposed tool is ``<upstream>.<tool>``. Two
  upstreams with a ``search`` tool must not collide, and a trace must say which
  server was called. Retrofitting a prefix later would rewrite every historical
  ``chosen`` value and invalidate the comparisons the project is built on.
* **``schema_hash``.** A digest of (description + input schema). When an
  upstream upgrades and changes a schema, existing traces stop being comparable
  -- so the hash is recorded at registration time and can be frozen on drift
  instead of failing silently (DESIGN 2.6 pitfall 3).

Timeouts are per call and deliberately dumb (one number). Retry policy, health
checks and connection pooling belong to the registry in D5; a single upstream
does not need them and pretending otherwise would be untested code.
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from .session import digest_of

__all__ = ["UpstreamTool", "StdioUpstream", "NAMESPACE_SEP"]

NAMESPACE_SEP = "."


@dataclass(frozen=True)
class UpstreamTool:
    """A tool as the gateway exposes it, plus where it really lives."""

    upstream: str
    original_name: str
    exposed_name: str
    description: str
    input_schema: Mapping[str, Any]
    schema_hash: str

    def to_mcp(self) -> types.Tool:
        return types.Tool(
            name=self.exposed_name,
            description=self.description or None,
            input_schema=dict(self.input_schema),
        )


class UpstreamFailure(RuntimeError):
    """The upstream could not serve the request (transport or protocol level)."""


class StdioUpstream:
    """One MCP server, spawned as a child process."""

    def __init__(
        self,
        name: str,
        command: str,
        args: Sequence[str] = (),
        *,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None,
        call_timeout_s: float = 30.0,
    ) -> None:
        if not name or NAMESPACE_SEP in name:
            raise ValueError(f"upstream name must be non-empty and dot-free: {name!r}")
        self.name = name
        self._params = StdioServerParameters(
            command=command,
            args=list(args),
            env=dict(env) if env else None,
            cwd=cwd,
        )
        self._timeout_s = call_timeout_s
        self._stack: Optional[AsyncExitStack] = None
        self._session: Optional[ClientSession] = None
        self._tools: Optional[List[UpstreamTool]] = None
        self.server_info: Dict[str, Any] = {}

    # -- lifecycle --------------------------------------------------------

    async def __aenter__(self) -> "StdioUpstream":
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(self._params))
            session = await stack.enter_async_context(ClientSession(read, write))
            init = await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self.server_info = {
            "protocol_version": init.protocol_version,
            "name": getattr(init.server_info, "name", "?"),
            "version": getattr(init.server_info, "version", "?"),
        }
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            await stack.aclose()

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise UpstreamFailure(f"upstream {self.name!r} is not started")
        return self._session

    # -- tools ------------------------------------------------------------

    async def list_tools(self, *, refresh: bool = False) -> List[UpstreamTool]:
        if self._tools is not None and not refresh:
            return self._tools

        try:
            result = await self.session.list_tools()
        except Exception as exc:  # noqa: BLE001 - protocol/transport both map here
            raise UpstreamFailure(f"tools/list failed on {self.name}: {exc}") from exc

        tools: List[UpstreamTool] = []
        for tool in result.tools:
            schema = dict(tool.input_schema or {})
            tools.append(
                UpstreamTool(
                    upstream=self.name,
                    original_name=tool.name,
                    exposed_name=qualify(self.name, tool.name),
                    description=tool.description or "",
                    input_schema=schema,
                    schema_hash=digest_of(
                        {"description": tool.description or "", "input_schema": schema}
                    ),
                )
            )
        self._tools = tools
        return tools

    def resolve(self, exposed_name: str) -> str:
        """``sqlite.query`` -> ``query``. Rejects tools from other upstreams."""
        prefix = self.name + NAMESPACE_SEP
        if not exposed_name.startswith(prefix):
            raise UpstreamFailure(
                f"tool {exposed_name!r} is not served by upstream {self.name!r}"
            )
        return exposed_name[len(prefix):]

    async def call_tool(
        self, exposed_name: str, arguments: Optional[Mapping[str, Any]]
    ) -> types.CallToolResult:
        original = self.resolve(exposed_name)
        try:
            result = await self.session.call_tool(
                original, dict(arguments or {}), read_timeout_seconds=self._timeout_s
            )
        except Exception as exc:  # noqa: BLE001
            raise UpstreamFailure(f"tools/call {exposed_name} failed: {exc}") from exc
        if not isinstance(result, types.CallToolResult):
            raise UpstreamFailure(
                f"{exposed_name} returned {type(result).__name__}, not a tool result"
            )
        return result


def qualify(upstream: str, tool: str) -> str:
    return f"{upstream}{NAMESPACE_SEP}{tool}"


def tail_of_exception(exc: BaseException) -> str:
    """Short, stable text for digests -- exception reprs include addresses."""
    return f"{type(exc).__name__}: {exc}"


def default_python() -> str:
    return sys.executable
