"""A real, deliberately boring MCP server: read-only SQL over SQLite.

Why write our own instead of wiring a public server: the experiments in D11-D12
need an upstream whose data is fixed and whose answers are checkable. A third
party server makes the numbers non-reproducible and couples the project's
conclusions to someone else's uptime (PLAN risk R4).

Read-only is enforced, not advertised: the connection is opened with
``mode=ro`` and the statement is parsed for its leading keyword. A demo server
that can be talked into ``DROP TABLE`` by a prompt would undermine the point of
the trace.

Tools are intentionally close to the SQL surface -- ``list_tables``,
``describe_table``, ``count_rows``, ``query`` -- because the evidence model in
D9 extracts ``sql.rows`` / ``sql.schema`` from exactly these result shapes.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "var" / "demo.sqlite3"

SEED = {
    "repos": (
        "CREATE TABLE repos (name TEXT PRIMARY KEY, stars INT, language TEXT, last_commit TEXT)",
        [
            ("p1s4/mcp-search-proxy", 9, "Python", "2026-03-11"),
            ("IBM/mcp-context-forge", 4820, "Python", "2026-05-02"),
            ("maximhq/bifrost", 9100, "Go", "2026-04-28"),
            ("coral-protocol/coral", 5100, "Rust", "2026-04-30"),
            ("1012hopes/mcp-gateway", 1, "Python", "2026-11-13"),
        ],
    ),
    "incidents": (
        "CREATE TABLE incidents (id INT PRIMARY KEY, repo TEXT, severity TEXT, "
        "opened_at TEXT, resolved_at TEXT)",
        [
            (1, "IBM/mcp-context-forge", "sev2", "2026-04-01", "2026-04-02"),
            (2, "maximhq/bifrost", "sev3", "2026-04-11", None),
            (3, "coral-protocol/coral", "sev1", "2026-05-06", "2026-05-06"),
            (4, "1012hopes/mcp-gateway", "sev3", "2026-05-09", None),
        ],
    ),
    # -- added in D11 -----------------------------------------------------
    # Six more tables so the task set can ask twenty *different* questions
    # instead of twenty phrasings of the same one. Every table has a column set
    # that makes a single readable query sufficient -- a task set that needed
    # joins would put the SQL dialect, not the gateway, under test.
    "commits": (
        "CREATE TABLE commits (id INT PRIMARY KEY, repo TEXT, sha TEXT, "
        "author TEXT, committed_at TEXT, message TEXT)",
        [
            (1, "p1s4/mcp-search-proxy", "a1b2c3d", "ada", "2026-03-02", "init"),
            (2, "IBM/mcp-context-forge", "b2c3d4e", "lin", "2026-04-20", "sse transport"),
            (3, "IBM/mcp-context-forge", "c3d4e5f", "lin", "2026-04-28", "schema drift fix"),
            (4, "maximhq/bifrost", "d4e5f6a", "ravi", "2026-04-28", "go gateway rewrite"),
            (5, "coral-protocol/coral", "e5f6a7b", "jun", "2026-04-30", "rust rewrite"),
            (6, "1012hopes/mcp-gateway", "f6a7b8c", "kk", "2026-11-06", "trace: hash chain"),
            (7, "1012hopes/mcp-gateway", "a7b8c9d", "kk", "2026-11-08", "stop: evidence complete"),
            (8, "maximhq/bifrost", "b8c9dae", "ravi", "2026-05-01", "retry budget"),
        ],
    ),
    "releases": (
        "CREATE TABLE releases (id INT PRIMARY KEY, repo TEXT, tag TEXT, "
        "published_at TEXT, breaking INT)",
        [
            (1, "p1s4/mcp-search-proxy", "v0.1.0", "2026-03-05", 0),
            (2, "IBM/mcp-context-forge", "v2.3.1", "2026-04-10", 0),
            (3, "maximhq/bifrost", "v1.0.0", "2026-04-28", 1),
            (4, "coral-protocol/coral", "v0.9.0", "2026-04-30", 0),
            (5, "IBM/mcp-context-forge", "v2.4.0", "2026-05-02", 1),
            (6, "1012hopes/mcp-gateway", "v0.9.0", "2026-11-09", 0),
        ],
    ),
    "deps": (
        "CREATE TABLE deps (repo TEXT, package TEXT, version TEXT, "
        "pinned INT, vulnerable INT)",
        [
            ("p1s4/mcp-search-proxy", "requests", "2.31.0", 1, 0),
            ("IBM/mcp-context-forge", "pydantic", "2.6.0", 1, 1),
            ("IBM/mcp-context-forge", "httpx", "0.27.0", 0, 0),
            ("maximhq/bifrost", "golang.org/x/net", "0.24.0", 1, 1),
            ("maximhq/bifrost", "golang.org/x/crypto", "0.22.0", 0, 1),
            ("coral-protocol/coral", "serde", "1.0.200", 1, 0),
            ("1012hopes/mcp-gateway", "anyio", "4.3.0", 1, 0),
            ("1012hopes/mcp-gateway", "pydantic", "2.7.1", 1, 0),
        ],
    ),
    "maintainers": (
        "CREATE TABLE maintainers (repo TEXT, handle TEXT, role TEXT, since TEXT)",
        [
            ("p1s4/mcp-search-proxy", "ada", "lead", "2026-01-10"),
            ("IBM/mcp-context-forge", "lin", "lead", "2025-11-02"),
            ("IBM/mcp-context-forge", "omar", "reviewer", "2026-02-15"),
            ("maximhq/bifrost", "ravi", "lead", "2025-09-01"),
            ("coral-protocol/coral", "jun", "lead", "2026-01-20"),
            ("1012hopes/mcp-gateway", "kk", "lead", "2026-11-01"),
            ("1012hopes/mcp-gateway", "qa-bot", "triage", "2026-11-02"),
        ],
    ),
    "scans": (
        "CREATE TABLE scans (id INT PRIMARY KEY, repo TEXT, tool TEXT, "
        "finding TEXT, severity TEXT, created_at TEXT)",
        [
            (1, "p1s4/mcp-search-proxy", "semgrep", "hardcoded host", "medium", "2026-03-20"),
            (2, "IBM/mcp-context-forge", "semgrep", "tool schema not validated", "high", "2026-05-03"),
            (3, "maximhq/bifrost", "govulncheck", "x/crypto CVE-2026-1111", "critical", "2026-05-04"),
            (4, "coral-protocol/coral", "clippy", "unwrap in request path", "low", "2026-05-05"),
            (5, "maximhq/bifrost", "semgrep", "missing timeout", "medium", "2026-05-06"),
            (6, "1012hopes/mcp-gateway", "bandit", "no issues", "none", "2026-11-09"),
        ],
    ),
    "labels": (
        "CREATE TABLE labels (incident_id INT, label TEXT)",
        [
            (1, "regression"),
            (1, "mcp"),
            (2, "flaky"),
            (3, "p1"),
            (4, "governance"),
            (4, "mcp"),
        ],
    ),
}

FORBIDDEN = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "replace",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "reindex",
)

TOOLS: List[types.Tool] = [
    types.Tool(
        name="list_tables",
        description="List the tables in the database.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="describe_table",
        description="Show column names and types for one table.",
        input_schema={
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="count_rows",
        description="Count rows in one table.",
        input_schema={
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="query",
        description=(
            "Run one read-only SELECT statement and return rows as objects. "
            "Writes are rejected."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    ),
]


def ensure_seed(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        for name, (ddl, rows) in SEED.items():
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            if exists:
                continue
            conn.execute(ddl)
            placeholders = ",".join("?" * len(rows[0]))
            conn.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)


def connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def assert_read_only(sql: str) -> str:
    statement = sql.strip().rstrip(";").strip()
    if not statement:
        raise ValueError("empty SQL")
    if ";" in statement:
        raise ValueError("only one statement per call is accepted")
    lowered = statement.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("only SELECT / WITH statements are accepted")
    for word in FORBIDDEN:
        if f" {word} " in f" {lowered} ":
            raise ValueError(f"statement contains a write keyword: {word}")
    return statement


def _text(text: str, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], is_error=is_error
    )


def _json(payload: Dict[str, Any], is_error: bool = False) -> types.CallToolResult:
    import json

    return _text(json.dumps(payload, ensure_ascii=False, sort_keys=True), is_error)


def handle(name: str, arguments: Dict[str, Any], db: Path) -> types.CallToolResult:
    try:
        with connect_read_only(db) as conn:
            if name == "list_tables":
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                return _json({"tables": [row["name"] for row in rows]})

            if name == "describe_table":
                table = str(arguments["table"])
                rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                if not rows:
                    return _json({"error": f"no such table: {table}"}, True)
                return _json(
                    {
                        "table": table,
                        "columns": [
                            {"name": r["name"], "type": r["type"], "notnull": bool(r["notnull"])}
                            for r in rows
                        ],
                    }
                )

            if name == "count_rows":
                table = str(arguments["table"])
                row = conn.execute(f"SELECT COUNT(*) AS n FROM '{table}'").fetchone()
                return _json({"table": table, "count": row["n"]})

            if name == "query":
                statement = assert_read_only(str(arguments["sql"]))
                limit = int(arguments.get("limit", 50))
                rows = conn.execute(statement).fetchmany(limit)
                return _json({"rows": [dict(r) for r in rows], "row_count": len(rows)})

            return _json({"error": f"unknown tool: {name}"}, True)
    except (ValueError, sqlite3.Error) as exc:
        return _json({"error": f"{type(exc).__name__}: {exc}"}, True)


def build_server(db: Path) -> Server:
    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(TOOLS))

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return handle(params.name, dict(params.arguments or {}), db)

    return Server(
        "sqlite-demo",
        version="0.1.0",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve(db: Path) -> None:
    ensure_seed(db)
    server = build_server(db)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sqlite_server", description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    db = Path(args.db)
    if not db.is_absolute():
        db = REPO_ROOT / db

    import anyio

    try:
        anyio.run(serve, db)
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
