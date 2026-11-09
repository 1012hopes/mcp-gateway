"""One run's state: the only place that writes to the trace.

The rule this class exists to enforce (DESIGN 1.5, FR-13):

    **no result leaves the gateway without a sealed record for it.**

Not "write the record after returning the result" -- a crash in between would
leave a call the model acted on but the trace never saw, which is the one gap a
hash chain cannot detect. The order is:

    1. execute the call
    2. build + seal + persist the record      <- may raise
    3. only then hand the result back

If step 2 raises, step 3 never happens: the caller gets an error, the run is
aborted, and no evidence reached the model. The counters below exist so that
this can be *asserted* rather than trusted:

    results_delivered <= records_written      always
    results_delivered == records_written      when the run is healthy or aborted

D6 scope: the session now carries everything a stop rule needs to look at --
the evidence set, the required set, the call history, the budget and the wall
clock. The rules themselves arrive in D7-D9; keeping the *inputs* here rather
than threading them through the transport is what lets a rule be a pure
function of one context object.

Two things this class deliberately does *not* do:

* **It does not decide.** ``halt()`` is called by the transport after a rule
  fired; the ordering, the criteria and the "why" live in ``gateway/stop/``.
* **It does not extract evidence.** ``extract_evidence`` is the D9 seam; until
  then it returns nothing and every record honestly says ``evidence_added: []``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set

from .limits import Budget, CallSignature
from .stop.normalize import normalize_args, spec_for
from .trace.canonical import digest_of
from .trace.record import StopCheck, TraceRecord
from .trace.sink import JsonlSink
from .version import GATEWAY_VERSION, RULES_VERSION, UNKNOWN_MODEL_ID

__all__ = [
    "Budget",
    "CallSignature",
    "RunSession",
    "TERMINAL_STATES",
    "digest_of",
    "extract_evidence",
    "new_run_id",
    "new_session_id",
]

#: Above this size a tool result is flagged ``truncated``. The flag matters more
#: than the truncation itself: evidence extraction (D9) must know that a partial
#: payload is not a complete evidence item, otherwise a half-returned row set
#: reads as "evidence present" and the run halts early (DESIGN 2.6 pitfall 2).
MAX_RESULT_BYTES = 1_000_000

#: States from which no further call may be served. ``DEGRADED`` is absent on
#: purpose: a degraded run continues, it just records what it could not get.
TERMINAL_STATES = frozenset(
    {
        "ABORTED",
        "HALTED_COMPLETE",
        "HALTED_DUPLICATE",
        "HALTED_NO_PROGRESS",
        "HALTED_BUDGET",
        "WAITING_APPROVAL",
    }
)


# ``Budget`` and ``CallSignature`` are re-exported, not defined here: they are
# leaf values that the stop controller also needs, and defining them here made
# ``session -> stop.normalize -> stop.controller -> session`` a cycle.


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def new_session_id() -> str:
    return f"sess-{uuid.uuid4().hex[:8]}"


def extract_evidence(payload: Any) -> Dict[str, str]:
    """``result payload -> {ev_type: source}``. Placeholder until D9.

    Returning an empty dict is the honest D6 state: no extractor is registered,
    so no record may claim evidence. A fake extractor here would make R4 look
    alive before it is, which is the failure mode this project is about.
    """
    return {}


@dataclass
class RunSession:
    """Mutable per-run state. One instance per run, never shared across runs."""

    sink: JsonlSink
    run_id: str = field(default_factory=new_run_id)
    session_id: str = field(default_factory=new_session_id)
    parent_run_id: Optional[str] = None
    model_id: str = UNKNOWN_MODEL_ID
    gateway_version: str = GATEWAY_VERSION
    rules_version: str = RULES_VERSION
    turn: int = 1

    state: str = "RUNNING"
    abort_reason: Optional[str] = None
    halt_reason: Optional[str] = None
    halt_rule: Optional[str] = None

    records_written: int = 0
    results_delivered: int = 0
    calls_attempted: int = 0

    # -- what the stop rules look at (D7-D9 read, nothing writes but us) ---
    budget: Budget = field(default_factory=Budget)
    required: Set[str] = field(default_factory=set)
    evidence: Dict[str, str] = field(default_factory=dict)
    history: List[CallSignature] = field(default_factory=list)
    #: Digests of the results this run delivered, in order. R2 reads the *set*:
    #: a result that repeats one already delivered taught the run nothing.
    result_digests: List[str] = field(default_factory=list)
    #: Fingerprint after each committed call, oldest first. The trace carries the
    #: same value per record, so a plateau is visible in the file itself.
    state_trail: List[str] = field(default_factory=list)

    clock: Any = time.monotonic
    started_at: Optional[float] = None

    def __post_init__(self) -> None:
        if self.started_at is None:
            self.started_at = self.clock()

    # -- bookkeeping -------------------------------------------------------

    @property
    def aborted(self) -> bool:
        return self.state == "ABORTED"

    @property
    def halted(self) -> bool:
        return self.state.startswith("HALTED_") or self.state == "WAITING_APPROVAL"

    @property
    def closed(self) -> bool:
        """No further call may be served. True for aborted *and* halted runs."""
        return self.state in TERMINAL_STATES

    @property
    def trace_path(self) -> Path:
        """Where this run's evidence lives. Derived, never stored twice."""
        return self.sink.path

    @property
    def missing(self) -> List[str]:
        return sorted(self.required - set(self.evidence))

    def wall_ms(self) -> int:
        if self.started_at is None:
            return 0
        return max(0, int((self.clock() - self.started_at) * 1000))

    def abort(self, reason: str) -> None:
        self.state = "ABORTED"
        self.abort_reason = reason

    def halt(self, state: str, *, rule: str, reason: str) -> None:
        """End the run on its own terms. The transport still delivers this result."""
        if state not in TERMINAL_STATES or state == "ABORTED":
            raise ValueError(f"not a halt state: {state!r}")
        self.state = state
        self.halt_rule = rule
        self.halt_reason = reason

    def stats(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "abort_reason": self.abort_reason,
            "halt_rule": self.halt_rule,
            "calls_attempted": self.calls_attempted,
            "records_written": self.records_written,
            "results_delivered": self.results_delivered,
            "evidence": sorted(self.evidence),
            "missing": self.missing,
        }

    # -- the stop-rule inputs ---------------------------------------------

    def args_hash_for(self, tool: Optional[str], args: Any) -> str:
        """One place where call identity is computed.

        Two call sites computing identity differently would make R1 either never
        fire or fire on everything, and neither failure raises an error. So both
        the pre-stage guard and the record go through here (DESIGN A2.1).

        ``tool`` is part of the identity twice over: its namespace picks the
        normalization policy, and the pair ``(tool, args_hash)`` -- not the hash
        alone -- is what R1 compares. ``count_rows({"table": "x"})`` and
        ``describe_table({"table": "x"})`` share arguments and are not the same
        call.
        """
        return digest_of(normalize_args(args, spec_for(tool)))

    def proposed_signature(self, tool: str, args: Any) -> CallSignature:
        return CallSignature(tool, self.args_hash_for(tool, args))

    def recent_signatures(self, limit: int = 3) -> List[CallSignature]:
        """The tail R2 folds into its state hash. Oldest first."""
        if limit <= 0:
            return []
        return list(self.history[-limit:])

    def add_evidence(self, items: Mapping[str, str]) -> List[str]:
        """Merge new evidence types. Returns the ones that were actually new."""
        added: List[str] = []
        for ev_type, source in items.items():
            if ev_type in self.evidence:
                continue
            self.evidence[ev_type] = source
            added.append(ev_type)
        return added

    # -- R2's information state -------------------------------------------

    def state_hash(self, *pending: str) -> str:
        """Fingerprint of what the run *knows*: evidence plus result digests.

        Deliberately not ``(tool, args_hash)`` (DESIGN's first draft): folding
        the *questions* into the state makes the state change whenever the
        question changes, so "ask the same thing a different way and learn
        nothing" -- the exact behaviour R2 exists to catch -- can never look
        like no-progress. It would only ever see literal cycles, which R1
        already catches one stage earlier.

        Both inputs are sets that only ever grow, so this value changes **iff**
        the run learned something. ``pending`` is for a result that has not been
        committed yet: the post-stage guard must judge the call *before* the
        record is written, and a digest absorbed before the write would leave
        the session claiming knowledge the trace does not have.
        """
        results = set(self.result_digests)
        results.update(pending)
        return digest_of({"evidence": sorted(self.evidence), "results": sorted(results)})

    def commit_result(self, digest: str) -> None:
        """Absorb a delivered result. Called only after its record landed."""
        self.result_digests.append(digest)
        self.state_trail.append(self.state_hash())

    # -- the write path ----------------------------------------------------

    def record_call(
        self,
        *,
        offered: List[str],
        chosen: Optional[str],
        args: Any,
        result: Any,
        latency_ms: int,
        decision: str = "forward",
        state: Optional[str] = None,
        evidence_added: Optional[List[str]] = None,
        stop_check: Optional[StopCheck] = None,
        truncated: bool = False,
    ) -> TraceRecord:
        """Seal and persist one record. Raises if the record cannot be persisted.

        ``args``/``result`` are hashed, never stored: the trace proves *what kind*
        of call happened without becoming a copy of the data (DESIGN 2.3).
        """
        seq, prev_hash = self.sink.expected_next()
        args_hash = self.args_hash_for(chosen, args)
        record = TraceRecord(
            seq=seq,
            run_id=self.run_id,
            parent_run_id=self.parent_run_id,
            session_id=self.session_id,
            turn=self.turn,
            model_id=self.model_id,
            gateway_version=self.gateway_version,
            rules_version=self.rules_version,
            offered=list(offered),
            chosen=chosen,
            args_hash=args_hash,
            result_digest=digest_of(result),
            truncated=truncated,
            evidence_added=list(evidence_added or []),
            stop_check=stop_check or StopCheck(),
            decision=decision,
            state=state or self.state,
            latency_ms=max(0, int(latency_ms)),
            prev_hash=prev_hash,
        ).seal()

        self.sink.append(record)
        self.records_written += 1
        if chosen is not None:
            # Only recorded calls enter the history: a blocked call did not
            # happen, so a rule that looks at history must not see it.
            self.history.append(CallSignature(chosen, args_hash))
        return record

    def mark_delivered(self) -> None:
        """Call this only after the result actually left the gateway."""
        self.results_delivered += 1
