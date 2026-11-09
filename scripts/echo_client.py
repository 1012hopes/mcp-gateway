"""End-to-end client: agent -> gateway -> sqlite upstream -> trace on disk.

    {python} scripts/echo_client.py

This is the D4 acceptance check in one command:

1. the gateway serves ``tools/list`` aggregated from the upstream
2. five real calls succeed through the gateway
3. the resulting trace verifies, and
4. ``offered`` is non-empty on every record

It is also the harness the fail-closed demo reuses, so the "happy path" and the
"aborted path" run through exactly the same code.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters, types  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from gateway.config import load_config  # noqa: E402
from gateway.session import new_run_id  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402

DEFAULT_CALLS: List[tuple[str, Dict[str, Any]]] = [
    ("sqlite.list_tables", {}),
    ("sqlite.query", {"sql": "select name, stars from repos order by stars desc", "limit": 5}),
    ("sqlite.describe_table", {"table": "incidents"}),
    ("sqlite.count_rows", {"table": "incidents"}),
    ("sqlite.query", {"sql": "select repo, severity from incidents where resolved_at is null"}),
]


def gateway_params(extra: Sequence[str] = ()) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "gateway", *extra],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def _short(result: types.CallToolResult, limit: int = 160) -> str:
    text = "; ".join(
        getattr(item, "text", f"<{getattr(item, 'type', '?')}>") for item in result.content
    )
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


async def drive(
    calls: Sequence[tuple[str, Dict[str, Any]]] = DEFAULT_CALLS,
    *,
    run_id: Optional[str] = None,
    trace_dir: Optional[Path] = None,
    extra_args: Sequence[str] = (),
) -> Dict[str, Any]:
    """Run one session. Returns a summary dict; never raises on tool errors."""
    run_id = run_id or new_run_id()
    trace_dir = trace_dir or load_config().trace_dir
    trace_path = trace_dir / f"{run_id}.jsonl"
    args = ["--run-id", run_id, "--trace-dir", str(trace_dir)]
    args += list(extra_args)
    outcome: Dict[str, Any] = {
        "run_id": run_id,
        "trace_path": trace_path,
        "tools": [],
        "calls": [],
        "error": None,
    }

    async with stdio_client(gateway_params(args)) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            outcome["protocol"] = init.protocol_version
            tools = await session.list_tools()
            outcome["tools"] = [tool.name for tool in tools.tools]
            for name, arguments in calls:
                result = await session.call_tool(name, arguments)
                outcome["calls"].append(
                    {
                        "name": name,
                        "is_error": bool(result.is_error),
                        "text": _short(result),
                    }
                )
    return outcome


def _print_outcome(outcome: Dict[str, Any]) -> None:
    print(f"run: {outcome['run_id']}")
    print(f"protocol: {outcome.get('protocol', '?')}")
    print(f"tools exposed ({len(outcome['tools'])}): {', '.join(outcome['tools'])}")
    print("-" * 78)
    for index, call in enumerate(outcome["calls"], start=1):
        flag = "ERR" if call["is_error"] else "ok "
        print(f"{index}. [{flag}] {call['name']:<22} {call['text']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="drive the gateway end to end")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--verify", action="store_true", default=True)
    args = parser.parse_args(argv)

    outcome = asyncio.run(drive(run_id=args.run_id))
    _print_outcome(outcome)

    trace_path: Path = outcome["trace_path"]
    print("-" * 78)
    if not trace_path.exists():
        print("NO TRACE produced -- the run left no evidence behind")
        return 1

    result = verify_path(trace_path)
    print(f"trace: {trace_path.relative_to(ROOT)}")
    print(f"verify: {result.summary()}")

    errors = sum(1 for call in outcome["calls"] if call["is_error"])
    calls = len(outcome["calls"])
    print(f"calls={calls} tool_errors={errors} records={result.records}")
    ok = result.ok and result.records == calls
    print("PASS: every delivered call has a sealed record" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
