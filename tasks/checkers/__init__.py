"""One checker per task, addressable by task id.

``check`` is the only entry point the experiment uses:

    check("t03", answers)  ->  Verdict(ok=False, detail="breaking releases: 1 missing ...")

Raising on an unknown id is intentional. A task without a checker cannot be
scored, and "no checker" silently scored as "passed" is how a metric table ends
up reporting 20/20 that nobody can reproduce. ``tests/test_checkers.py`` asserts
the registry covers every file in ``tasks/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from . import adversarial, productive
from .core import Answer, Verdict

__all__ = [
    "Answer",
    "CHECKERS",
    "Verdict",
    "answers_from_turns",
    "check",
    "known_ids",
    "load_fixture",
]

Checker = Callable[[Sequence[Answer]], Verdict]

CHECKERS: Dict[str, Checker] = {
    "t01": productive.t01,
    "t02": adversarial.t02,
    "t03": productive.t03,
    "t04": productive.t04,
    "t05": productive.t05,
    "t06": productive.t06,
    "t07": productive.t07,
    "t08": productive.t08,
    "t09": productive.t09,
    "t10": productive.t10,
    "t11": productive.t11,
    "t12": productive.t12,
    "t13": productive.t13,
    "t14": productive.t14,
    "t15": productive.t15,
    "t16": adversarial.t16,
    "t17": adversarial.t17,
    "t18": adversarial.t18,
    "t19": adversarial.t19,
    "t20": adversarial.t20,
}


def known_ids() -> List[str]:
    return sorted(CHECKERS)


def check(task_id: str, answers: Sequence[Answer]) -> Verdict:
    try:
        checker = CHECKERS[task_id]
    except KeyError:
        raise KeyError(
            f"no checker for task {task_id!r}; known: {', '.join(known_ids())}"
        ) from None
    return checker(answers)


def answers_from_turns(turns: Sequence[Any]) -> List[Answer]:
    """Convert ``Observation`` objects (or anything shaped like one)."""
    return [Answer.from_turn(turn) for turn in turns]


def load_fixture(path: Path | str) -> Dict[str, Any]:
    """Read one ``tests/fixtures/{good,bad}/<task>.json`` file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
