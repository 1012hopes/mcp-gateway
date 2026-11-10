"""The checker interface: a pure function from *delivered answers* to a verdict.

What a checker is
-----------------
Every other part of the project asks whether the run *read* something. This is
the only part that asks whether what it read was **true**. That difference is why
D9's evidence model could not self-certify: ``sql.rows.repos`` proves rows came
back, not that the star counts were right. A checker therefore reproduces the
answer from a frozen expectation and compares.

What a checker is not
---------------------
Not a second agent, not an LLM judge, and not a re-run. It reads the same
payloads the agent saw and returns ``ok``/``detail``. Anything that needed a
model would make "did it stop correctly" depend on a second sampling event, and
then the metric would measure the judge.

Input is the *whole delivery*, not the last turn
------------------------------------------------
``facts`` unions every row set the run was ever shown for a table, because the
overshoot tail re-asks the same question with ``limit 1``: a checker reading only
the last answer would grade the re-read, and a truncated re-read would then look
like a wrong answer from a correct run. Union has the opposite failure mode --
two truncated answers can compose into the truth -- and that is accepted
deliberately: if the run did deliver every row across two calls, it did deliver
the answer.

The empty set is not free
-------------------------
``facts`` returns ``[]`` both when the run asked and found nothing and when it
never asked at all. For the five adversarial tasks the expected answer *is* ``[]``,
so a checker comparing sets alone would accept a run that touched nothing -- the
same "空集是任何集合的子集" trap that R4 has to guard against. ``touched`` is
required by every adversarial checker for exactly that reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

__all__ = [
    "Answer",
    "Verdict",
    "conclude",
    "facts",
    "project",
    "touched",
]


@dataclass(frozen=True)
class Answer:
    """One turn, reduced to what a checker may look at."""

    tool: str
    args: Mapping[str, Any]
    payload: Optional[Mapping[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Answer":
        args = data.get("args") or {}
        payload = data.get("payload")
        return cls(
            tool=str(data.get("tool", "")),
            args=dict(args) if isinstance(args, Mapping) else {},
            payload=dict(payload) if isinstance(payload, Mapping) else None,
        )

    @classmethod
    def from_turn(cls, turn: Any) -> "Answer":
        """Read an ``agent.policy.Observation`` without importing it.

        Duck-typed on purpose: the checkers must stay importable on an
        interpreter without the MCP SDK, and ``Observation`` lives in a module
        that needs it.
        """
        return cls(
            tool=str(getattr(turn, "tool", "")),
            args=dict(getattr(turn, "args", {}) or {}),
            payload=getattr(turn, "payload", None),
        )

    @property
    def sql(self) -> str:
        return str(self.args.get("sql") or "")

    @property
    def rows(self) -> Optional[List[Dict[str, Any]]]:
        payload = self.payload or {}
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return None
        return [dict(row) for row in rows if isinstance(row, Mapping)]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    detail: str


def _row_key(row: Mapping[str, Any]) -> Tuple[Tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in row.items()))


def _mentions(answer: Answer, table: str) -> bool:
    return re.search(rf"\b{re.escape(table)}\b", answer.sql, re.IGNORECASE) is not None


def facts(answers: Sequence[Answer], table: str) -> List[Dict[str, Any]]:
    """Every row the run was shown from ``table``, deduped, in delivery order."""
    seen: Set[Tuple[Tuple[str, str], ...]] = set()
    out: List[Dict[str, Any]] = []
    for answer in answers:
        rows = answer.rows
        if rows is None or not _mentions(answer, table):
            continue
        for row in rows:
            key = _row_key(row)
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def touched(answers: Sequence[Answer], table: str) -> int:
    """How many answered queries over ``table`` there were (0 = never asked)."""
    return sum(1 for a in answers if a.rows is not None and _mentions(a, table))


def project(
    rows: Iterable[Mapping[str, Any]], columns: Sequence[str]
) -> Set[Tuple[Any, ...]]:
    """Rows as tuples over ``columns``; rows missing any of them are dropped.

    Dropping rather than padding is what makes "the answer never selected the
    star count" a failure instead of a ``None`` that might compare equal by
    accident.
    """
    out: Set[Tuple[Any, ...]] = set()
    for row in rows:
        if all(column in row for column in columns):
            out.add(tuple(row[column] for column in columns))
    return out


def conclude(got: Set[Tuple[Any, ...]], expected: Iterable[Tuple[Any, ...]], what: str) -> Verdict:
    want = set(expected)
    if got == want:
        return Verdict(True, f"{what}: {len(want)} row(s), as expected")
    missing = sorted(want - got)
    extra = sorted(got - want)
    parts = []
    if missing:
        parts.append(f"{len(missing)} missing e.g. {missing[0]}")
    if extra:
        parts.append(f"{len(extra)} unexpected e.g. {extra[0]}")
    return Verdict(False, f"{what}: " + "; ".join(parts))
