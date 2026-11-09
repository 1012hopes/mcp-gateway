"""Upstream MCP servers, spoken to over stdio with the official SDK.

This module's job is deliberately narrow: **talk to one server and hand back what
it said.** Normalization, naming and hashing live in ``registry.py`` -- keeping
them out of here is what lets the registry be tested without spawning processes,
and lets a dirty upstream be fixed in one place instead of once per transport.

What it does keep: the raw name. A trace may call a tool ``sqlite.query`` while
the upstream knows it as ``query``; the translation belongs to the caller, which
has the registry entry.
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Mapping, Optional, Sequence

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from .registry import NAMESPACE_SEP, RawTool

__all__ = ["StdioUpstream", "UpstreamFailure", "NAMESPACE_SEP", "tail_of_exception"]


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
        self._tools: Optional[List[RawTool]] = None
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

    async def list_tools(self, *, refresh: bool = False) -> List[RawTool]:
        if self._tools is not None and not refresh:
            return self._tools

        try:
            result = await self.session.list_tools()
        except Exception as exc:  # noqa: BLE001 - protocol/transport both map here
            raise UpstreamFailure(f"tools/list failed on {self.name}: {exc}") from exc

        tools: List[RawTool] = []
        for tool in result.tools:
            tools.append(
                RawTool(
                    upstream=self.name,
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema or {}),
                )
            )
        self._tools = tools
        return tools

    async def call_tool(
        self, original_name: str, arguments: Optional[Mapping[str, Any]]
    ) -> types.CallToolResult:
        """Call by the name *the upstream* knows, not the exposed one."""
        try:
            result = await self.session.call_tool(
                original_name,
                dict(arguments or {}),
                read_timeout_seconds=self._timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            raise UpstreamFailure(
                f"tools/call {self.name}.{original_name} failed: {exc}"
            ) from exc
        if not isinstance(result, types.CallToolResult):
            raise UpstreamFailure(
                f"{original_name} returned {type(result).__name__}, not a tool result"
            )
        return result


def tail_of_exception(exc: BaseException) -> str:
    """Short, stable text for digests -- exception reprs include addresses."""
    return f"{type(exc).__name__}: {exc}"


def default_python() -> str:
    return sys.executable
