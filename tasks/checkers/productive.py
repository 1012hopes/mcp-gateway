"""Checkers for t01-t15: the answer exists, and here is exactly what it is.

Each function is the *only* place the expected value is written down. That is
deliberate: the expectation is a frozen reading of the seed data, so a change to
``mcp_servers/seed.py`` that silently changes an answer breaks a good fixture
instead of sliding through the metric table.

The three-comparison shapes used below
--------------------------------------
* **exact set** (t03, t04, t05, ...): the question has one right collection of
  rows. Missing a row and inventing one are both failures, and the detail says
  which happened.
* **restricted set** (t01 stars, t12 languages): the run may deliver a wider
  table than asked for ("all repos' stars") and still answer, as long as the rows
  about *the subject of the question* are exactly right.
* **derived set** (t15): the answer is a set difference, so the checker performs
  the same subtraction over what was delivered. If the run forgot the
  ``resolved_at is null`` filter, the difference comes out wrong -- which is the
  point: the check is on the conclusion, not on the WHERE clause.
"""

from __future__ import annotations

from typing import Any, Dict, Sequence, Tuple

from .core import Answer, Verdict, conclude, facts, project

__all__ = [
    "t01", "t03", "t04", "t05", "t06", "t07", "t08", "t09", "t10",
    "t11", "t12", "t13", "t14", "t15",
]

# -- t01: unresolved incidents and their repos' stars -----------------------

T01_INCIDENTS = {(2, "maximhq/bifrost", "sev3"), (4, "1012hopes/mcp-gateway", "sev3")}
T01_STARS = {"maximhq/bifrost": 9100, "1012hopes/mcp-gateway": 1}


def t01(answers: Sequence[Answer]) -> Verdict:
    incidents = project(facts(answers, "incidents"), ("id", "repo", "severity"))
    verdict = conclude(incidents, T01_INCIDENTS, "unresolved incidents")
    if not verdict.ok:
        return verdict
    stars = {row for row in project(facts(answers, "repos"), ("name", "stars")) if row[0] in T01_STARS}
    return conclude(stars, set(T01_STARS.items()), "star counts of the affected repos")


# -- t03..t08: one fact per task, exact set --------------------------------

T03_BREAKING = {
    ("maximhq/bifrost", "v1.0.0", "2026-04-28"),
    ("IBM/mcp-context-forge", "v2.4.0", "2026-05-02"),
}


def t03(answers: Sequence[Answer]) -> Verdict:
    rows = project(facts(answers, "releases"), ("repo", "tag", "published_at"))
    return conclude(rows, T03_BREAKING, "breaking releases")


T04_COMMITS = {
    ("f6a7b8c", "kk", "2026-11-06"),
    ("a7b8c9d", "kk", "2026-11-08"),
}


def t04(answers: Sequence[Answer]) -> Verdict:
    # The question also asks "how many" -- the row count is that number, so no
    # separate count check is needed (and a separate one could disagree).
    rows = project(facts(answers, "commits"), ("sha", "author", "committed_at"))
    return conclude(rows, T04_COMMITS, "commits touching 1012hopes/mcp-gateway")


T05_VULNERABLE = {
    ("IBM/mcp-context-forge", "pydantic", "2.6.0"),
    ("maximhq/bifrost", "golang.org/x/net", "0.24.0"),
    ("maximhq/bifrost", "golang.org/x/crypto", "0.22.0"),
}


def t05(answers: Sequence[Answer]) -> Verdict:
    rows = project(facts(answers, "deps"), ("repo", "package", "version"))
    return conclude(rows, T05_VULNERABLE, "vulnerable dependencies")


T06_MAINTAINERS = {("ravi", "lead", "2025-09-01")}


def t06(answers: Sequence[Answer]) -> Verdict:
    # ``repo`` is left out of the projection on purpose: if the query forgot the
    # repo filter it returns seven rows and fails on count, which is the more
    # informative failure than "one row has the wrong repo".
    rows = project(facts(answers, "maintainers"), ("handle", "role", "since"))
    return conclude(rows, T06_MAINTAINERS, "maintainers of maximhq/bifrost")


T07_SCAN = {("semgrep", "tool schema not validated", "high", "2026-05-03")}


def t07(answers: Sequence[Answer]) -> Verdict:
    # "Most recent" is checked by requiring the answer to be that one row: an
    # unfiltered scan dump is a correct reading of the table and a wrong answer
    # to the question.
    rows = project(facts(answers, "scans"), ("tool", "finding", "severity", "created_at"))
    return conclude(rows, T07_SCAN, "the most recent scan of IBM/mcp-context-forge")


T08_LABELS = {("flaky",)}


def t08(answers: Sequence[Answer]) -> Verdict:
    rows = project(facts(answers, "labels"), ("label",))
    return conclude(rows, T08_LABELS, "labels of incident 2")


# -- t09: a ranking, so extras are allowed ---------------------------------

T09_TOP = {
    ("maximhq/bifrost", 9100, "Go"),
    ("coral-protocol/coral", 5100, "Rust"),
    ("IBM/mcp-context-forge", 4820, "Python"),
}


def t09(answers: Sequence[Answer]) -> Verdict:
    """The three highest-starred repos may be delivered as a top-3 or as the table.

    Re-ranking what was delivered instead of demanding three rows keeps the
    check on the *answer* ("name the top three") rather than on the query's
    ``limit``. A row without ``language`` is dropped by ``project``, so an answer
    that named the repos but not their languages fails.
    """
    rows: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    for row in facts(answers, "repos"):
        if "name" in row and "stars" in row:
            rows.setdefault((row["name"], row["stars"]), row)
    if not rows:
        return Verdict(False, "three most-starred repos: no repo rows were delivered")
    ranked = sorted(rows.values(), key=lambda r: int(r["stars"]), reverse=True)[:3]
    got = {(r["name"], r["stars"], r.get("language")) for r in ranked}
    return conclude(got, T09_TOP, "three most-starred repos")


# -- t10, t12: two facts, one of them a lookup ------------------------------

T10_INCIDENTS = {(2, "maximhq/bifrost", "sev3"), (4, "1012hopes/mcp-gateway", "sev3")}
T10_LABELS = {(2, "flaky"), (4, "governance"), (4, "mcp")}


def t10(answers: Sequence[Answer]) -> Verdict:
    incidents = project(facts(answers, "incidents"), ("id", "repo", "severity"))
    verdict = conclude(incidents, T10_INCIDENTS, "open incidents")
    if not verdict.ok:
        return verdict
    known = {row[0] for row in T10_INCIDENTS}
    labels = {row for row in project(facts(answers, "labels"), ("incident_id", "label")) if row[0] in known}
    return conclude(labels, T10_LABELS, "labels of the open incidents")


T11_TRIAGE = {("1012hopes/mcp-gateway", "qa-bot", "2026-11-02")}


def t11(answers: Sequence[Answer]) -> Verdict:
    rows = project(facts(answers, "maintainers"), ("repo", "handle", "since"))
    return conclude(rows, T11_TRIAGE, "repos with a triage maintainer")


T12_INCIDENTS = {(1, "IBM/mcp-context-forge", "sev2"), (3, "coral-protocol/coral", "sev1")}
T12_LANGUAGES = {"IBM/mcp-context-forge": "Python", "coral-protocol/coral": "Rust"}


def t12(answers: Sequence[Answer]) -> Verdict:
    incidents = project(facts(answers, "incidents"), ("id", "repo", "severity"))
    verdict = conclude(incidents, T12_INCIDENTS, "sev1/sev2 incidents")
    if not verdict.ok:
        return verdict
    languages = {
        row for row in project(facts(answers, "repos"), ("name", "language")) if row[0] in T12_LANGUAGES
    }
    return conclude(languages, set(T12_LANGUAGES.items()), "languages of the affected repos")


# -- t13, t14 --------------------------------------------------------------

T13_COMMITS = {
    ("maximhq/bifrost", "d4e5f6a", "2026-04-28"),
    ("IBM/mcp-context-forge", "c3d4e5f", "2026-04-28"),
    ("coral-protocol/coral", "e5f6a7b", "2026-04-30"),
    ("maximhq/bifrost", "b8c9dae", "2026-05-01"),
    ("1012hopes/mcp-gateway", "f6a7b8c", "2026-11-06"),
    ("1012hopes/mcp-gateway", "a7b8c9d", "2026-11-08"),
}


def t13(answers: Sequence[Answer]) -> Verdict:
    rows = project(facts(answers, "commits"), ("repo", "sha", "committed_at"))
    return conclude(rows, T13_COMMITS, "commits after 2026-04-25")


T14_RELEASE = {("1012hopes/mcp-gateway", "v0.9.0", "2026-11-09")}


def t14(answers: Sequence[Answer]) -> Verdict:
    """Two repos published a v0.9.0; only one of them is the subject.

    ``breaking`` is not required: the goal asks whether the release exists and
    how many stars the repo has, so demanding a fourth column would fail a
    correct answer for not answering a question nobody asked.
    """
    releases = {
        row
        for row in project(facts(answers, "releases"), ("repo", "tag", "published_at"))
        if row[0] == "1012hopes/mcp-gateway"
    }
    verdict = conclude(releases, T14_RELEASE, "the v0.9.0 release of 1012hopes/mcp-gateway")
    if not verdict.ok:
        return verdict
    stars = {
        row for row in project(facts(answers, "repos"), ("name", "stars")) if row[0] == "1012hopes/mcp-gateway"
    }
    return conclude(stars, {("1012hopes/mcp-gateway", 1)}, "star count of 1012hopes/mcp-gateway")


# -- t15: a set difference ------------------------------------------------

T15_OPEN = {"maximhq/bifrost", "1012hopes/mcp-gateway"}
T15_WITHOUT = {"p1s4/mcp-search-proxy", "IBM/mcp-context-forge", "coral-protocol/coral"}


def t15(answers: Sequence[Answer]) -> Verdict:
    open_repos = {row[0] for row in project(facts(answers, "incidents"), ("repo",))}
    if not open_repos:
        return Verdict(False, "repos without an open incident: no incident rows were delivered")
    all_repos = {row[0] for row in project(facts(answers, "repos"), ("name",))}
    if not all_repos:
        return Verdict(False, "repos without an open incident: the repo catalogue was never delivered")
    without = {name for name in all_repos if name not in open_repos}
    return conclude({(name,) for name in without}, {(n,) for n in T15_WITHOUT}, "repos without an open incident")
