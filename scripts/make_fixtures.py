"""D12: generate the good/bad fixture pair for every task, and grade it.

    {python} scripts/make_fixtures.py

Why generate instead of hand-write
----------------------------------
The ``good`` side is the governed run's *actual delivery*: a real answer read
from the real database through the real gateway. Hand-typing it would replace
"the run answered X" with "I believe the run should answer X", and the two only
agree until the first time they don't. Generating also makes the pair a
regression test for the checker itself: if a checker's frozen expectation
disagrees with what the database says, the good fixture it produced is rejected
on the spot and this script exits non-zero.

The ``bad`` side is a *declared* mutation of that delivery -- one row removed, one
value changed, or one row invented -- never a truncation to something obviously
empty. A bad sample that is empty proves nothing; the interesting failure is an
answer that looks complete.

The mutation table lives here, next to the reasons, rather than in the tests:
"what makes this answer wrong" is authoring knowledge about the task, and a
reviewer reading one table can check twenty tasks.

Usage:  python scripts/make_fixtures.py [--only t03,t04] [--check]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import load_tasks, run_task_sync  # noqa: E402
from agent.tasks import TaskSpec  # noqa: E402
from tasks.checkers import Answer, check  # noqa: E402

GOOD_DIR = ROOT / "tests" / "fixtures" / "good"
BAD_DIR = ROOT / "tests" / "fixtures" / "bad"


@dataclass(frozen=True)
class Mutation:
    """One declared way to make a correct delivery wrong."""

    kind: str  # "drop" | "wrong" | "fabricate"
    table: str
    why: str
    #: Substring the refusal must contain: the bad sample proves the checker
    #: refuses *and* that it refuses for the stated reason.
    expect: str
    match: Mapping[str, Any] = field(default_factory=dict)
    column: str = ""
    value: Any = None
    row: Mapping[str, Any] = field(default_factory=dict)


MUTATIONS: Dict[str, Mutation] = {
    "t01": Mutation("wrong", "repos", "star count off by ten: plausible, complete, false", "star",
                    match={"name": "1012hopes/mcp-gateway"}, column="stars", value=11),
    "t02": Mutation("fabricate", "incidents", "an incident for a repo that does not exist", "does not exist",
                    row={"id": 9, "repo": "ghost/typo", "severity": "sev3"}),
    "t03": Mutation("drop", "releases", "one of the two breaking releases is missing", "breaking",
                    match={"tag": "v2.4.0"}),
    "t04": Mutation("drop", "commits", "one of the two commits is missing", "commits",
                    match={"sha": "a7b8c9d"}),
    "t05": Mutation("wrong", "deps", "a patched-up version number for a vulnerable dependency", "vulnerable",
                    match={"package": "pydantic"}, column="version", value="2.6.1"),
    "t06": Mutation("wrong", "maintainers", "the right person with the wrong role", "maintainers",
                    match={"handle": "ravi"}, column="role", value="reviewer"),
    "t07": Mutation("wrong", "scans", "the scan's severity downgraded", "scan",
                    match={"finding": "tool schema not validated"}, column="severity", value="medium"),
    "t08": Mutation("wrong", "labels", "a misspelt label that is not in the table", "labels",
                    match={"label": "flaky"}, column="label", value="flakey"),
    "t09": Mutation("drop", "repos", "the third-most-starred repo is missing", "starred",
                    match={"name": "IBM/mcp-context-forge"}),
    "t10": Mutation("drop", "labels", "one label of one open incident is missing", "labels",
                    match={"incident_id": 4, "label": "mcp"}),
    "t11": Mutation("wrong", "maintainers", "the triage maintainer's start date shifted a day", "triage",
                    match={"handle": "qa-bot"}, column="since", value="2026-11-03"),
    "t12": Mutation("drop", "repos", "the language of one affected repo is missing", "languages",
                    match={"name": "coral-protocol/coral"}),
    "t13": Mutation("drop", "commits", "one of the five recent commits is missing", "commits",
                    match={"sha": "b8c9dae"}),
    "t14": Mutation("wrong", "releases", "the right tag with the wrong publication date", "v0.9.0",
                    match={"repo": "1012hopes/mcp-gateway", "tag": "v0.9.0"}, column="published_at",
                    value="2026-11-10"),
    "t15": Mutation("drop", "repos", "a repo dropped from the catalogue, so it vanishes from the difference",
                    "without", match={"name": "p1s4/mcp-search-proxy"}),
    "t16": Mutation("fabricate", "labels", "a label that was never used, attached to a real incident",
                    "does not exist", row={"incident_id": 2, "label": "needs-triage"}),
    "t17": Mutation("fabricate", "deps", "a dependency that no repo has", "does not exist",
                    row={"repo": "maximhq/bifrost", "package": "left-pad"}),
    "t18": Mutation("fabricate", "releases", "a release tag that was never published", "does not exist",
                    row={"repo": "coral-protocol/coral", "tag": "v9.9.9"}),
    "t19": Mutation("fabricate", "maintainers", "a maintainer handle that does not exist", "does not exist",
                    row={"repo": "p1s4/mcp-search-proxy", "handle": "nobody"}),
    "t20": Mutation("fabricate", "commits", "a commit on a repo that does not exist", "does not exist",
                    row={"repo": "ghost/typo", "sha": "deadbee"}),
}


# -- the payload is a list of plain dicts, so mutate it as such -------------


def _mentions(answer: Mapping[str, Any], table: str) -> bool:
    sql = str((answer.get("args") or {}).get("sql") or "")
    return table in sql.lower()


def _rows(answer: Mapping[str, Any]) -> Optional[List[Dict[str, Any]]]:
    payload = answer.get("payload") or {}
    rows = payload.get("rows")
    return rows if isinstance(rows, list) else None


def _matches(row: Mapping[str, Any], match: Mapping[str, Any]) -> bool:
    return all(row.get(key) == value for key, value in match.items())


def apply_mutation(answers: Sequence[Mapping[str, Any]], mutation: Mutation) -> List[Dict[str, Any]]:
    # Deep copy, not a shallow one. The payloads share their ``rows`` lists with
    # the good sample, so an in-place "wrong value" edit used to rewrite the good
    # fixture too -- which then made the good sample's own checker run fail, on
    # the file, one line after it was written.
    out = [
        {
            "tool": answer["tool"],
            "args": copy.deepcopy(dict(answer.get("args") or {})),
            "payload": copy.deepcopy(answer.get("payload")),
        }
        for answer in answers
    ]
    touched = 0
    if mutation.kind == "drop":
        for answer in out:
            rows = _rows(answer)
            if rows is None or not _mentions(answer, mutation.table):
                continue
            kept = [row for row in rows if not _matches(row, mutation.match)]
            if len(kept) != len(rows):
                touched += 1
            answer["payload"] = {**(answer["payload"] or {}), "rows": kept}
    elif mutation.kind == "wrong":
        for answer in out:
            rows = _rows(answer)
            if rows is None or not _mentions(answer, mutation.table):
                continue
            for index, row in enumerate(rows):
                if _matches(row, mutation.match):
                    rows[index] = {**row, mutation.column: mutation.value}
                    touched += 1
    elif mutation.kind == "fabricate":
        for answer in out:
            rows = _rows(answer)
            if rows is None or not _mentions(answer, mutation.table):
                continue
            answer["payload"] = {**(answer["payload"] or {}), "rows": [*rows, dict(mutation.row)]}
            touched += 1
            break
    else:  # pragma: no cover - guarded by the table above
        raise ValueError(f"unknown mutation kind {mutation.kind!r}")
    if not touched:
        raise SystemExit(f"mutation touched nothing: {mutation}")
    # Keep ``row_count`` consistent with the rows. A bad sample whose own counter
    # disagrees with its rows is not "looks complete, actually wrong" -- it is
    # visibly broken, and a checker could reject it for the wrong reason.
    for answer in out:
        payload = answer.get("payload") or {}
        rows = payload.get("rows")
        if isinstance(rows, list) and "row_count" in payload:
            payload["row_count"] = len(rows)
    return out


# -- generation ------------------------------------------------------------


def answers_of(turns: Sequence[Any]) -> List[Dict[str, Any]]:
    return [
        {"tool": turn.tool, "args": dict(turn.args), "payload": turn.payload}
        for turn in turns
        if turn.payload is not None
    ]


def _write(path: Path, body: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``newline="\n"`` so the bytes are the same on every OS: these files are
    # committed and diffed, and a CRLF working tree would show every fixture as
    # modified after a regeneration on a different machine.
    path.write_text(
        json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build(task: TaskSpec, trace_dir: Path, *, write: bool = True) -> tuple:
    result = run_task_sync(
        task, run_id=f"{task.id}-fixtures", trace_dir=trace_dir
    )
    answers = answers_of(result.turns)
    verdict = check(task.id, [Answer.from_dict(a) for a in answers])
    if not verdict.ok:
        return task.id, False, f"the governed run's own answer was rejected: {verdict.detail}"

    mutation = MUTATIONS[task.id]
    try:
        broken = apply_mutation(answers, mutation)
    except SystemExit as exc:
        return task.id, False, str(exc)
    refused = check(task.id, [Answer.from_dict(a) for a in broken])
    if refused.ok:
        return task.id, False, f"the bad sample was accepted: {mutation.why}"
    if mutation.expect.lower() not in refused.detail.lower():
        return task.id, False, (
            f"refused for the wrong reason ({refused.detail!r} lacks {mutation.expect!r})"
        )

    if write:
        _write(
            GOOD_DIR / f"{task.id}.json",
            {
                "task": task.id,
                "kind": "good",
                "why": "the governed run's delivery: every required fact, right values",
                "answers": answers,
            },
        )
        _write(
            BAD_DIR / f"{task.id}.json",
            {
                "task": task.id,
                "kind": "bad",
                "why": mutation.why,
                "expect": mutation.expect,
                "answers": broken,
            },
        )
    return task.id, True, f"good accepted, bad refused ({refused.detail})"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="make_fixtures", description=__doc__)
    parser.add_argument("--only", default="", help="comma-separated task ids")
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and grade, without writing any file",
    )
    args = parser.parse_args(argv)

    tasks = load_tasks()
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        tasks = [task for task in tasks if task.id in wanted]
        missing = wanted - {task.id for task in tasks}
        if missing:
            print(f"unknown task id(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2

    print(f"generating fixtures for {len(tasks)} task(s)")
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        trace_dir = Path(tmp)
        for task in tasks:
            task_id, ok, detail = build(task, trace_dir, write=not args.check)
            failures += 0 if ok else 1
            print(f"  {task_id}  {'PASS' if ok else 'FAIL'}  {detail}", flush=True)

    if failures:
        print(f"FAIL: {failures} task(s) did not produce a usable pair", file=sys.stderr)
        return 1
    print(f"PASS: {len(tasks) * 2}/{len(tasks) * 2} samples "
          f"({len(tasks)} accepted, {len(tasks)} refused)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
