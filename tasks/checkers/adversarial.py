"""Checkers for the six adversarial tasks: the right answer is "there is no row".

These are the tasks where a checker earns its keep. Every one of them can be
"satisfied" by an agent that never asks (empty set ⊆ everything) or by an agent
that invents a row, and both cases look identical in a step count. Two guards:

* ``touched`` -- at least one query over the right table returned a row set.
* the subject fragment -- that query was *about the subject asked*, so an empty
  answer to some other question does not count as answering this one.

The tokens are substring tests, not parses: the adversarial policies rephrase the
same question eleven ways, and pinning the exact statement would make the
checker a copy of the policy. "Every rephrasing contains one of these tokens" is
the property that actually holds, and it is asserted by
``tests/test_checkers.py::AdversarialSubjectTests`` against the task files --
so a future rephrasing that drops every token fails a test instead of quietly
turning the guard off.
"""

from __future__ import annotations

from typing import Dict, Sequence, Tuple

from .core import Answer, Verdict, facts, touched

__all__ = ["SUBJECTS", "t02", "t16", "t17", "t18", "t19", "t20"]

WHAT = "rows for something that does not exist"

#: task id -> (table the answer lives in, subject as written for humans, tokens
#: of which a rephrasing must contain at least one).
#:
#: A tuple rather than one token because the rephrasings genuinely abbreviate:
#: the ghost/typo spin names the owner in one statement and the name in the next
#: (``repo like 'ghost%'`` / ``repo like '%/typo'``), and demanding both would
#: reject a question the policy really asks. Incomplete coverage is the residual
#: risk -- a rephrasing that names neither would silently disable the guard --
#: which is why ``tests/test_checkers.py`` checks every declared rephrasing
#: against this table.
SUBJECTS: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "t02": ("incidents", "ghost/typo", ("ghost", "typo")),
    "t16": ("labels", "label 'needs-triage'", ("triage", "needs")),
    "t17": ("deps", "package 'left-pad'", ("left", "-pad")),
    "t18": ("releases", "tag 'v9.9.9'", ("9.9", "v9")),
    "t19": ("maintainers", "handle 'nobody'", ("nob", "body")),
    "t20": ("commits", "repo 'ghost/typo'", ("ghost", "typo")),
}


def _nothing(answers: Sequence[Answer], task_id: str) -> Verdict:
    table, subject, tokens = SUBJECTS[task_id]
    asked_about_subject = any(
        any(token in answer.sql.lower() for token in tokens)
        for answer in answers
        if answer.rows is not None
    )
    if not asked_about_subject:
        return Verdict(False, f"{WHAT}: never asked about {subject} ({table})")
    rows = facts(answers, table)
    if rows:
        return Verdict(
            False,
            f"{WHAT}: {len(rows)} row(s) returned for {subject}, e.g. {rows[0]}",
        )
    return Verdict(
        True, f"{WHAT}: {touched(answers, table)} query/queries over {table}, all empty"
    )


def t02(answers: Sequence[Answer]) -> Verdict:
    return _nothing(answers, "t02")


def t16(answers: Sequence[Answer]) -> Verdict:
    return _nothing(answers, "t16")


def t17(answers: Sequence[Answer]) -> Verdict:
    return _nothing(answers, "t17")


def t18(answers: Sequence[Answer]) -> Verdict:
    return _nothing(answers, "t18")


def t19(answers: Sequence[Answer]) -> Verdict:
    return _nothing(answers, "t19")


def t20(answers: Sequence[Answer]) -> Verdict:
    return _nothing(answers, "t20")
