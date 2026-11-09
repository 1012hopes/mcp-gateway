"""Where the stop criteria plug in -- and, at D6, nothing else.

This module contains **no criteria**. Every rule lives in ``gateway/rules/`` and
is instantiated in ``__main__.py``, the single assembly point (DESIGN 2.2). The
constraint is structural: adding a sixth criterion must not require editing this
file, only adding a module and one line where the rules are wired.

What the controller does own:

1. **Ordering.** Rules sort by ``priority``; equal priorities are an error, not
   a race. Priorities are spaced by 10 (10/20/30/40/50) so a new rule can be
   slotted between two existing ones without renumbering anything.
2. **Short circuit.** The first non-``None`` verdict wins. Rules after it are not
   evaluated, and therefore must not appear in ``attempted``.
3. **The attempt log.** Every evaluated rule name, in evaluation order. Without
   it, "why did *this* rule fire instead of that one" is unanswerable on replay,
   which is the first question a reviewer asks (DESIGN A2).

Why two stages instead of one ordered list
------------------------------------------
Safety (R5) and duplicate (R1) guards must run *before* the call is forwarded --
their whole value is preventing it. Completeness (R4), progress (R2) and budget
(R3) can only be judged *after*, because they need the new evidence. So a rule
declares ``stage`` and the controller only runs the rules that are meaningful
there. The global priority order is preserved (10/20 before 30/40/50), and
``attempted`` stays a faithful execution log by concatenation.

At D6 the rule tuple is empty: the socket is wired, no devices are plugged in.
Every trace says ``rules-v0(none)``, which is worth writing precisely because
there is nothing else to say.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Protocol, Sequence, Tuple

from ..session import Budget, CallSignature
from ..trace.record import StopCheck

__all__ = [
    "PRE",
    "POST",
    "Rule",
    "StopContext",
    "StopController",
    "StopOutcome",
    "StopVerdict",
]

PRE = "pre"
POST = "post"
STAGES = (PRE, POST)


@dataclass(frozen=True)
class StopContext:
    """Everything a rule may look at. One object, no back-channel to the session.

    Frozen on purpose: a rule that mutated run state would make the order of
    evaluation matter in a way ``priority`` does not describe, and the same trace
    could then replay to different verdicts.
    """

    run_id: str
    turn: int
    tool: str
    args_hash: str
    evidence: FrozenSet[str]
    required: FrozenSet[str]
    history: Tuple[CallSignature, ...]
    budget: Budget
    calls_used: int
    wall_ms: int

    @property
    def missing(self) -> FrozenSet[str]:
        return self.required - self.evidence

    def seen(self, tool: str, args_hash: str) -> bool:
        """Has this exact call already happened in this run? (R1's O(1) test)"""
        return CallSignature(tool, args_hash) in self.history


@dataclass(frozen=True)
class StopVerdict:
    """A rule's answer: what to do, and why -- both machine-readable."""

    rule: str
    decision: str
    state: str
    reason: str
    #: R1 replays the earlier result instead of forwarding (DESIGN 2.4 step 3).
    replay_last: bool = False


@dataclass(frozen=True)
class StopOutcome:
    verdict: Optional[StopVerdict]
    attempted: Tuple[str, ...]

    @property
    def hit(self) -> bool:
        return self.verdict is not None


class Rule(Protocol):
    """The contract a criterion implements. Four attributes, one method."""

    name: str
    priority: int
    stage: str

    def check(self, ctx: StopContext) -> Optional[StopVerdict]: ...


def _validate(rules: Sequence[Rule]) -> None:
    seen: Dict[int, str] = {}
    names: Dict[str, int] = {}
    for rule in rules:
        if rule.stage not in STAGES:
            raise ValueError(f"rule {rule.name!r} has unknown stage {rule.stage!r}")
        if rule.priority in seen:
            # Two rules at the same priority make the verdict depend on tuple
            # order, i.e. on registration order -- invisible in the trace.
            raise ValueError(
                f"duplicate priority {rule.priority}: {seen[rule.priority]!r} and {rule.name!r}"
            )
        if rule.name in names:
            raise ValueError(f"duplicate rule name {rule.name!r}")
        seen[rule.priority] = rule.name
        names[rule.name] = rule.priority


class StopController:
    """Orders rules, runs them, stops at the first verdict, logs what it tried."""

    def __init__(self, rules: Iterable[Rule] = ()) -> None:
        ordered = sorted(rules, key=lambda rule: rule.priority)
        _validate(ordered)
        self._rules: Tuple[Rule, ...] = tuple(ordered)

    @property
    def rules(self) -> Tuple[Rule, ...]:
        return self._rules

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(rule.name for rule in self._rules)

    @property
    def version(self) -> str:
        """Goes into every record: verdicts from different rule sets are not comparable."""
        if not self._rules:
            return "rules-v0(none)"
        return "rules-v0(" + ",".join(self.names) + ")"

    def evaluate(self, stage: str, ctx: StopContext) -> StopOutcome:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        attempted: List[str] = []
        for rule in self._rules:
            if rule.stage != stage:
                continue
            attempted.append(rule.name)
            verdict = rule.check(ctx)
            if verdict is not None:
                return StopOutcome(verdict=verdict, attempted=tuple(attempted))
        return StopOutcome(verdict=None, attempted=tuple(attempted))


def merge_checks(
    *,
    verdict: Optional[StopVerdict],
    attempted: Sequence[str],
    evidence: Iterable[str],
    missing: Iterable[str],
) -> StopCheck:
    """Assemble the trace field. Kept here so the trace never invents a shape."""
    return StopCheck(
        rule=verdict.rule if verdict else None,
        hit=verdict is not None,
        evidence=sorted(set(evidence)),
        missing=sorted(set(missing)),
        attempted=list(attempted),
    )
