"""A synthetic code-host server: ten repository tools, and ``gh.search``.

The near-duplicate test
-----------------------
``gh.search`` and ``wiki.search`` share a name, a required argument and the verb
"search". Only the rest of the sentence separates them ("code, issues and pull
requests" against "wiki pages"). A selector that scores the *name* alone cannot
tell them apart; one that scores the description can. That difference is
measured in ``scripts/gating_demo.py`` rather than asserted here.

The data mirrors ``sqlite_server``'s repositories on purpose: the same five
projects appear in both, so a question about "stars" has two plausible answers
in the catalogue, one right (SQLite, which the task's evidence model declares)
and one a decoy. A decoy that shares the subject but not the producer is the
case the evidence model's ``producers`` rule exists for.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from .catalog_server import Tool, integer, main, one, select, text

REPOS: List[Dict[str, Any]] = [
    {
        "repo": "1012hopes/mcp-gateway",
        "description": "MCP gateway: tool gating, stop rules, verifiable traces",
        "stars": 1,
        "language": "Python",
        "default_branch": "main",
        "pushed_at": "2026-11-13",
    },
    {
        "repo": "IBM/mcp-context-forge",
        "description": "Context management for MCP servers",
        "stars": 4820,
        "language": "Python",
        "default_branch": "main",
        "pushed_at": "2026-05-02",
    },
    {
        "repo": "maximhq/bifrost",
        "description": "Fast LLM gateway in Go",
        "stars": 9100,
        "language": "Go",
        "default_branch": "main",
        "pushed_at": "2026-04-28",
    },
    {
        "repo": "coral-protocol/coral",
        "description": "Agent-to-agent protocol toolkit",
        "stars": 5100,
        "language": "Rust",
        "default_branch": "trunk",
        "pushed_at": "2026-04-30",
    },
    {
        "repo": "p1s4/mcp-search-proxy",
        "description": "Search-first MCP proxy with local BM25 tool gating",
        "stars": 9,
        "language": "Python",
        "default_branch": "main",
        "pushed_at": "2026-03-11",
    },
]

ISSUES: List[Dict[str, Any]] = [
    {"repo": "IBM/mcp-context-forge", "number": 118, "state": "closed", "title": "Tool schema not validated", "label": "bug", "assignee": "lin", "comments": 7},
    {"repo": "maximhq/bifrost", "number": 204, "state": "open", "title": "Timeout in retry budget", "label": "regression", "assignee": "ravi", "comments": 3},
    {"repo": "coral-protocol/coral", "number": 41, "state": "closed", "title": "Panic on malformed envelope", "label": "bug", "assignee": "jun", "comments": 11},
    {"repo": "1012hopes/mcp-gateway", "number": 7, "state": "open", "title": "Gating hides describe_table", "label": "design", "assignee": "kk", "comments": 2},
    {"repo": "1012hopes/mcp-gateway", "number": 3, "state": "open", "title": "Trace write race on abort", "label": "bug", "assignee": "kk", "comments": 5},
    {"repo": "p1s4/mcp-search-proxy", "number": 19, "state": "open", "title": "BM25 ranking is unstable", "label": "design", "assignee": "ada", "comments": 1},
]

PULLS: List[Dict[str, Any]] = [
    {"repo": "1012hopes/mcp-gateway", "number": 12, "state": "merged", "title": "gating: BM25 window", "author": "kk"},
    {"repo": "1012hopes/mcp-gateway", "number": 11, "state": "open", "title": "trace: schema v3", "author": "kk"},
    {"repo": "IBM/mcp-context-forge", "number": 302, "state": "open", "title": "schema: resolve refs", "author": "omar"},
    {"repo": "maximhq/bifrost", "number": 188, "state": "merged", "title": "retry: honour deadline", "author": "ravi"},
]

COMMITS: List[Dict[str, Any]] = [
    {"repo": "1012hopes/mcp-gateway", "sha": "f6a7b8c", "author": "kk", "date": "2026-11-06", "message": "trace: hash chain"},
    {"repo": "1012hopes/mcp-gateway", "sha": "a7b8c9d", "author": "kk", "date": "2026-11-08", "message": "stop: evidence complete"},
    {"repo": "1012hopes/mcp-gateway", "sha": "c9d0e1f", "author": "kk", "date": "2026-11-12", "message": "gating: BM25 + stability merge"},
    {"repo": "IBM/mcp-context-forge", "sha": "b2c3d4e", "author": "lin", "date": "2026-04-20", "message": "sse transport"},
    {"repo": "IBM/mcp-context-forge", "sha": "c3d4e5f", "author": "lin", "date": "2026-04-28", "message": "schema drift fix"},
    {"repo": "maximhq/bifrost", "sha": "d4e5f6a", "author": "ravi", "date": "2026-04-28", "message": "go gateway rewrite"},
    {"repo": "coral-protocol/coral", "sha": "e5f6a7b", "author": "jun", "date": "2026-04-30", "message": "rust rewrite"},
    {"repo": "p1s4/mcp-search-proxy", "sha": "a1b2c3d", "author": "ada", "date": "2026-03-02", "message": "init"},
]

FILES: Dict[str, str] = {
    "1012hopes/mcp-gateway:gateway/selector.py": "BM25 window, stability merge, pin rule",
    "1012hopes/mcp-gateway:gateway/transport.py": "the choke point: pre/post guards",
    "1012hopes/mcp-gateway:README.md": "how to run the demos",
    "IBM/mcp-context-forge:mcp_server.py": "server bootstrap",
    "maximhq/bifrost:gateway.go": "request routing",
}

BRANCHES: List[Dict[str, Any]] = [
    {"repo": "1012hopes/mcp-gateway", "name": "main", "default": True},
    {"repo": "1012hopes/mcp-gateway", "name": "d14-gating", "default": False},
    {"repo": "coral-protocol/coral", "name": "trunk", "default": True},
    {"repo": "IBM/mcp-context-forge", "name": "main", "default": True},
]

RELEASES: List[Dict[str, Any]] = [
    {"repo": "1012hopes/mcp-gateway", "tag": "v0.12-d13", "date": "2026-11-10", "breaking": False},
    {"repo": "IBM/mcp-context-forge", "tag": "v2.4.0", "date": "2026-05-02", "breaking": True},
    {"repo": "maximhq/bifrost", "tag": "v1.0.0", "date": "2026-04-28", "breaking": True},
    {"repo": "coral-protocol/coral", "tag": "v0.9.0", "date": "2026-04-30", "breaking": False},
]

COLLABORATORS: List[Dict[str, Any]] = [
    {"repo": "1012hopes/mcp-gateway", "user": "kk", "role": "admin"},
    {"repo": "1012hopes/mcp-gateway", "user": "qa-bot", "role": "read"},
    {"repo": "IBM/mcp-context-forge", "user": "lin", "role": "admin"},
    {"repo": "IBM/mcp-context-forge", "user": "omar", "role": "write"},
    {"repo": "maximhq/bifrost", "user": "ravi", "role": "admin"},
    {"repo": "coral-protocol/coral", "user": "jun", "role": "admin"},
    {"repo": "p1s4/mcp-search-proxy", "user": "ada", "role": "admin"},
]


def _search(args: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(args.get("kind") or "all").casefold()
    pools = []
    if kind in ("all", "issues"):
        pools.append(("issue", ISSUES, ("title", "label")))
    if kind in ("all", "pulls"):
        pools.append(("pull", PULLS, ("title",)))
    if kind in ("all", "code"):
        pools.append(("code", [{"path": key, "summary": value} for key, value in FILES.items()],
                      ("path", "summary")))
    needle = str(args.get("q") or "").casefold()
    repo = str(args.get("repo") or "").casefold()
    hits: List[Dict[str, Any]] = []
    for label, pool, fields in pools:
        for row in pool:
            if repo and repo not in str(row.get("repo", row.get("path", ""))).casefold():
                continue
            if needle and needle not in " ".join(str(row.get(f, "")) for f in fields).casefold():
                continue
            hits.append({"kind": label, **row})
    limit = int(args.get("limit", 10))
    return {"items": hits[:limit], "count": min(len(hits), limit)}


TOOLS = [
    Tool(
        name="search",
        description=(
            "Search code, issues and pull requests across repositories by "
            "keyword. Use kind=code|issues|pulls to narrow, or q to match text."
        ),
        properties={
            "q": text(),
            "kind": text(enum=["all", "code", "issues", "pulls"]),
            "repo": text(),
            "limit": integer(),
        },
        body=_search,
    ),
    Tool(
        name="get_repo",
        description=(
            "Repository metadata: description, stars, language, default branch "
            "and when it was last pushed."
        ),
        properties={"repo": text()},
        required=("repo",),
        body=lambda args: one(REPOS, args, field="repo", key="repo", missing="no such repo"),
    ),
    Tool(
        name="list_issues",
        description="List issues in a repository, optionally filtered by state or label.",
        properties={"repo": text(), "state": text(), "label": text(), "limit": integer()},
        required=("repo",),
        body=lambda args: select(
            ISSUES, args, fields=("repo", "state", "label"), search=("title",)
        ),
    ),
    Tool(
        name="get_issue",
        description="Fetch one issue by number: title, label, assignee, comment count.",
        properties={"repo": text(), "number": integer()},
        required=("repo", "number"),
        body=lambda args: _by_number(ISSUES, args, "issue"),
    ),
    Tool(
        name="list_pulls",
        description="List pull requests in a repository, optionally filtered by state.",
        properties={"repo": text(), "state": text(), "limit": integer()},
        required=("repo",),
        body=lambda args: select(PULLS, args, fields=("repo", "state"), search=("title",)),
    ),
    Tool(
        name="list_commits",
        description="List commits of a repository, newest first, with sha, author and date.",
        properties={"repo": text(), "limit": integer(), "since": text()},
        required=("repo",),
        body=lambda args: select(
            sorted(COMMITS, key=lambda row: row["date"], reverse=True),
            args,
            fields=("repo",),
            key="items",
        ),
    ),
    Tool(
        name="get_file",
        description="Read one file's contents at a ref: repo, path, optional branch.",
        properties={"repo": text(), "path": text(), "ref": text()},
        required=("repo", "path"),
        body=lambda args: _file(args),
    ),
    Tool(
        name="list_branches",
        description="List branches of a repository and which one is the default.",
        properties={"repo": text()},
        required=("repo",),
        body=lambda args: select(BRANCHES, args, fields=("repo",)),
    ),
    Tool(
        name="list_releases",
        description="List releases of a repository: tag, date, whether it is breaking.",
        properties={"repo": text(), "limit": integer()},
        required=("repo",),
        body=lambda args: select(RELEASES, args, fields=("repo",)),
    ),
    Tool(
        name="list_collaborators",
        description="List users with access to a repository and their role.",
        properties={"repo": text()},
        required=("repo",),
        body=lambda args: select(COLLABORATORS, args, fields=("repo",)),
    ),
]


def _by_number(rows: List[Dict[str, Any]], args: Dict[str, Any], key: str) -> Dict[str, Any]:
    repo = str(args.get("repo", "")).casefold()
    try:
        number = int(args.get("number", 0))
    except (TypeError, ValueError):
        return {"error": f"number must be an integer: {args.get('number')!r}"}
    for row in rows:
        if str(row["repo"]).casefold() == repo and int(row["number"]) == number:
            return {key: row}
    return {"error": f"no such {key}: {args.get('repo')}#{number}"}


def _file(args: Dict[str, Any]) -> Dict[str, Any]:
    key = f"{args.get('repo')}:{args.get('path')}"
    if key in FILES:
        return {"repo": args.get("repo"), "path": args.get("path"),
                "ref": args.get("ref") or "main", "summary": FILES[key]}
    return {"error": f"no such file: {key}"}


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:], "gh-demo", TOOLS))
