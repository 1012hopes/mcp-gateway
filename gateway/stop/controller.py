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
their whole value is preventing it. So must R6, whose fact ("no available tool
can produce this") is known before the first call. Completeness (R4), progress
(R2) and budget (R3) can only be judged *after*, because they need the new
evidence. So a rule declares ``stage`` and the controller only runs the rules
that are meaningful there. The global priority order is preserved (10/20/35
before 30/40/50), and ``attempted`` stays a faithful execution log by
concatenation.

At D6 the rule tuple was empty: the socket wired, no devices plugged in. Every
trace said ``rules-v0(none)``, which was worth writing precisely because there
was nothing else to say.

A criterion that raises is a halt, not a crash
----------------------------------------------
Rules are code, so they have bugs. Originally a raising rule propagated out of
``evaluate`` and out of ``_handle_call`` **before** ``session.record_call``, so
the call being judged left no record at all -- and an unrecorded call is the one
thing this design cannot detect afterwards, because the chain it did not join
still verifies. So a crash is converted into a verdict: ``halt_rule_error`` in
state ``DEGRADED``, naming the rule and the exception. The normal halt path then
writes the record, and the run stops instead of pretending it judged the call.

``BaseException`` is not caught on purpose: an interrupt must still interrupt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Protocol, Sequence, Tuple

from ..limits import Budget, CallSignature
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
    #: Calls the run has spent **counting the one being judged** -- so a rule
    #: that halts on "call 3 of 3" agrees with the ``seq`` a reviewer reads.
    calls_used: int
    wall_ms: int
    #: Information fingerprints of the calls already committed, oldest first.
    #: ``state_trail[-1]`` is what the run knew before the call being judged.
    state_trail: Tuple[str, ...] = ()
    #: Fingerprint *after* this call's result (post-stage only). ``None`` before
    #: the result exists -- R2 cannot judge a call it has not seen the answer to.
    state_hash: Optional[str] = None
    #: Namespaced tools the agent may call right now. Frozen and unknown tools
    #: are already absent -- that is the admission decision, not a guess.
    available: FrozenSet[str] = frozenset()
    #: Required evidence types no available tool is allowed to produce (D9).
    #: Computed by the transport from the evidence model, so a rule cannot
    #: disagree with itself about what "unreachable" means.
    unreachable: FrozenSet[str] = frozenset()

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
            try:
                verdict = rule.check(ctx)
            except Exception as exc:  # noqa: BLE001 -- a rule is code; code has bugs
                # A raising rule must not be the one case that produces no
                # record. It used to escape ``evaluate``, so the exception flew
                # out of ``_handle_call`` before ``session.record_call`` and the
                # call vanished from the trace -- which is exactly the blind
                # spot this design exists to close (DESIGN 1.5: an unrecorded
                # call cannot be detected afterwards, because the chain stays
                # valid). Turning it into a verdict means the normal halt path
                # runs: the record is written, it names the rule, and the run
                # stops in ``DEGRADED`` rather than pretending to have judged.
                return StopOutcome(
                    verdict=StopVerdict(
                        rule=rule.name,
                        # A distinct decision, not ``halt_budget`` or a reused
                        # one: "the criterion crashed" and "the criterion said
                        # stop" are different facts, and a trace that merges
                        # them cannot answer why a run ended.
                        decision="halt_rule_error",
                        state="DEGRADED",
                        reason=(
                            f"rule {rule.name} raised {type(exc).__name__}: "
                            f"{str(exc)[:200] or '(no message)'}"
                        ),
                    ),
                    attempted=tuple(attempted),
                )
            if verdict is not None:
                return StopOutcome(verdict=verdict, attempted=tuple(attempted))
        return StopOutcome(verdict=None, attempted=tuple(attempted))


def merge_checks(
    *,
    verdict: Optional[StopVerdict],
    attempted: Sequence[str],
    evidence: Iterable[str],
    missing: Iterable[str],
    state_hash: Optional[str] = None,
) -> StopCheck:
    """Assemble the trace field. Kept here so the trace never invents a shape."""
    return StopCheck(
        rule=verdict.rule if verdict else None,
        hit=verdict is not None,
        evidence=sorted(set(evidence)),
        missing=sorted(set(missing)),
        attempted=list(attempted),
        state_hash=state_hash,
    )
