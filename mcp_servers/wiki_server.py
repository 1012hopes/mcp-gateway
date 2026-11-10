"""A synthetic internal wiki: ten documentation tools, one of them a decoy twin.

The twin is the point
---------------------
``wiki.search`` and ``gh.search`` are both called "search" and both take ``q``.
A ranker that matched on the tool name alone could not tell them apart, which
is what makes them useful: the demo asserts the gate puts ``wiki.search`` first
for a documentation question and ``gh.search`` first for a code question. If it
cannot, then no amount of "we use BM25" is a claim about anything.

Why ten tools
-------------
A1 sizes the problem at ``|T| ≈ 30–60``. Four SQL tools plus six deliberately
broken ones is a catalogue the model can read whole; adding twenty across two
heterogeneous servers is the smallest thing that makes "show five" a real
restriction. Ten is also about the number of tools a docs server plausibly has
without inventing absurd ones.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from .catalog_server import Tool, group_count, integer, main, one, select, text

PAGES: List[Dict[str, Any]] = [
    {
        "slug": "incident-triage",
        "title": "Incident triage",
        "space": "ops",
        "tags": "runbook,incident",
        "editor": "kk",
        "edited_at": "2026-11-04",
        "views": 412,
        "body": (
            "How a sev1 or sev2 incident is triaged: open a thread, assign an "
            "owner, record the affected repository, and close only after the "
            "resolved_at timestamp is set."
        ),
    },
    {
        "slug": "severity-levels",
        "title": "Severity levels",
        "space": "ops",
        "tags": "incident,policy",
        "editor": "lin",
        "edited_at": "2026-10-22",
        "views": 233,
        "body": (
            "sev1 means data loss or a public outage, sev2 a degraded path, "
            "sev3 cosmetic or internal only. Every incident row carries one."
        ),
    },
    {
        "slug": "gateway-overview",
        "title": "Gateway overview",
        "space": "platform",
        "tags": "gateway,design",
        "editor": "kk",
        "edited_at": "2026-11-08",
        "views": 158,
        "body": (
            "The gateway sits between the agent and every MCP server, records "
            "each call in a hash chain, and decides when a run has learned "
            "enough to stop."
        ),
    },
    {
        "slug": "tool-gating",
        "title": "Tool gating",
        "space": "platform",
        "tags": "gateway,retrieval",
        "editor": "kk",
        "edited_at": "2026-11-09",
        "views": 96,
        "body": (
            "Only K tools are shown per turn. Ranking is BM25 over the tool "
            "descriptions; the window is stable across turns so the model does "
            "not re-plan. A tool outside the window can still be called."
        ),
    },
    {
        "slug": "evidence-model",
        "title": "The evidence model",
        "space": "platform",
        "tags": "gateway,design",
        "editor": "kk",
        "edited_at": "2026-11-07",
        "views": 120,
        "body": (
            "A fact type is declared with its producers, an argument predicate "
            "and a shape. All three must match, otherwise a stub server's fake "
            "rows would satisfy a question about the real database."
        ),
    },
    {
        "slug": "release-checklist",
        "title": "Release checklist",
        "space": "platform",
        "tags": "release,checklist",
        "editor": "omar",
        "edited_at": "2026-11-02",
        "views": 77,
        "body": (
            "Freeze the schema manifest, run the full suite, re-run the "
            "comparison table, tag, push. A release that skips the table is a "
            "release nobody can compare."
        ),
    },
    {
        "slug": "oncall-handover",
        "title": "On-call handover",
        "space": "ops",
        "tags": "oncall,process",
        "editor": "ravi",
        "edited_at": "2026-10-30",
        "views": 64,
        "body": (
            "Handover covers open incidents, flaky tests, and any dependency "
            "with an unresolved vulnerability."
        ),
    },
    {
        "slug": "dependency-policy",
        "title": "Dependency policy",
        "space": "engineering",
        "tags": "deps,policy",
        "editor": "jun",
        "edited_at": "2026-10-11",
        "views": 191,
        "body": (
            "Every package is pinned. A vulnerable dependency blocks a release "
            "until it is upgraded or an exception is recorded with an owner."
        ),
    },
    {
        "slug": "review-guide",
        "title": "Review guide",
        "space": "engineering",
        "tags": "process,review",
        "editor": "lin",
        "edited_at": "2026-09-28",
        "views": 302,
        "body": (
            "Small pull requests, one behaviour per change, and a test that "
            "fails before the fix. Reviewers check the failure mode first."
        ),
    },
    {
        "slug": "wiki-style",
        "title": "Wiki style guide",
        "space": "engineering",
        "tags": "process,docs",
        "editor": "ada",
        "edited_at": "2026-09-15",
        "views": 45,
        "body": (
            "One page per question, a title that is a noun phrase, and links to "
            "the pages that depend on it."
        ),
    },
]

SPACES = [
    {"space": "ops", "owner": "ravi", "pages": 3},
    {"space": "platform", "owner": "kk", "pages": 3},
    {"space": "engineering", "owner": "lin", "pages": 4},
]

REVISIONS = [
    {"slug": "incident-triage", "revision": 7, "editor": "kk", "edited_at": "2026-11-04"},
    {"slug": "incident-triage", "revision": 6, "editor": "lin", "edited_at": "2026-10-19"},
    {"slug": "gateway-overview", "revision": 3, "editor": "kk", "edited_at": "2026-11-08"},
    {"slug": "tool-gating", "revision": 2, "editor": "kk", "edited_at": "2026-11-09"},
    {"slug": "evidence-model", "revision": 5, "editor": "kk", "edited_at": "2026-11-07"},
]

LINKS = [
    {"from_slug": "gateway-overview", "to_slug": "tool-gating"},
    {"from_slug": "gateway-overview", "to_slug": "evidence-model"},
    {"from_slug": "tool-gating", "to_slug": "evidence-model"},
    {"from_slug": "incident-triage", "to_slug": "severity-levels"},
    {"from_slug": "release-checklist", "to_slug": "dependency-policy"},
    {"from_slug": "oncall-handover", "to_slug": "incident-triage"},
    {"from_slug": "review-guide", "to_slug": "wiki-style"},
]


def _tags(args: Dict[str, Any]) -> Dict[str, Any]:
    tag = args.get("tag")
    if not tag:
        return group_count(
            [{"tag": value} for row in PAGES for value in str(row["tags"]).split(",")],
            "tag",
        )
    hits = [
        {"slug": row["slug"], "title": row["title"]}
        for row in PAGES
        if str(tag).casefold() in str(row["tags"]).casefold().split(",")
    ]
    return {"items": hits, "count": len(hits)}


TOOLS = [
    Tool(
        name="search",
        description=(
            "Search wiki pages by words in the title or body. Returns matching "
            "page slugs with the space they live in."
        ),
        properties={"q": text(), "space": text(), "limit": integer()},
        required=("q",),
        body=lambda args: select(
            PAGES, args, fields=("space",), search=("title", "body", "slug")
        ),
    ),
    Tool(
        name="get_page",
        description="Fetch one wiki page by slug: title, body text, last editor.",
        properties={"slug": text()},
        required=("slug",),
        body=lambda args: one(PAGES, args, field="slug", key="page", missing="no such page"),
    ),
    Tool(
        name="list_spaces",
        description="List the spaces this wiki is organised into, with owner and page count.",
        body=lambda args: {"items": SPACES, "count": len(SPACES)},
    ),
    Tool(
        name="list_pages",
        description="List the pages in one space, most recently edited first.",
        properties={"space": text(), "limit": integer()},
        required=("space",),
        body=lambda args: select(
            PAGES, args, fields=("space",), key="items"
        ),
    ),
    Tool(
        name="backlinks",
        description="List the pages that link to a given slug.",
        properties={"slug": text()},
        required=("slug",),
        body=lambda args: {
            "items": [
                link for link in LINKS
                if str(link["to_slug"]).casefold() == str(args.get("slug", "")).casefold()
            ],
            "count": sum(
                1
                for link in LINKS
                if str(link["to_slug"]).casefold() == str(args.get("slug", "")).casefold()
            ),
        },
    ),
    Tool(
        name="recent_changes",
        description="List the most recent page edits across all spaces.",
        properties={"limit": integer(), "since": text()},
        body=lambda args: {
            "items": sorted(PAGES, key=lambda row: row["edited_at"], reverse=True)[
                : int(args.get("limit", 5))
            ],
            "count": min(len(PAGES), int(args.get("limit", 5))),
        },
    ),
    Tool(
        name="page_history",
        description="List the revisions of one page, newest first.",
        properties={"slug": text(), "limit": integer()},
        required=("slug",),
        body=lambda args: select(REVISIONS, args, fields=("slug",), key="items"),
    ),
    Tool(
        name="search_by_tag",
        description="List pages carrying a tag, by slug and title.",
        properties={"tag": text(), "limit": integer()},
        required=("tag",),
        body=_tags,
    ),
    Tool(
        name="tags",
        description="List every tag used in the wiki with how many pages carry it.",
        properties={},
        body=lambda args: _tags({}),
    ),
    Tool(
        name="page_stats",
        description="View counts per wiki page over the last 30 days.",
        properties={"slug": text(), "limit": integer()},
        body=lambda args: select(PAGES, args, fields=("slug",), key="items")
        if args.get("slug")
        else {
            "items": [
                {"slug": row["slug"], "views": row["views"]}
                for row in sorted(PAGES, key=lambda r: -r["views"])[
                    : int(args.get("limit", 5))
                ]
            ],
            "count": min(len(PAGES), int(args.get("limit", 5))),
        },
    ),
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:], "wiki-demo", TOOLS))
