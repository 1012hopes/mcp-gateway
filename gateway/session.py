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
* **It does not extract evidence.** That is ``gateway/evidence.py`` (D9): the
  session only *merges* what the model returns, so there is exactly one place
  where "what counts as a fact" is defined and exactly one place where the set
  grows. Until D9 it merged nothing, and every record honestly said
  ``evidence_added: []`` -- the placeholder was deliberately not faked, because
  a rule that looks alive before it is, is this project's whole subject.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set

from .limits import Budget, CallSignature
from .policy import redact
from .stop.normalize import normalize_args, spec_for
from .trace.canonical import NonCanonicalValue, digest_of
from .trace.record import StopCheck, TraceRecord
from .trace.sink import JsonlSink, TraceWriteError
from .version import GATEWAY_VERSION, RULES_VERSION, UNKNOWN_MODEL_ID

__all__ = [
    "Budget",
    "CallSignature",
    "RunSession",
    "TERMINAL_STATES",
    "digest_of",
    "digest_result",
    "new_run_id",
    "new_session_id",
    "result_size_bytes",
]


def digest_result(payload: Any) -> str:
    """The digest of a tool result: **redact, then hash** (FR-14).

    Exported and used by both the transport (which must publish this call's
    digest to R2 before the record exists) and ``record_call`` (which writes it
    down) so there is exactly one definition. Two call sites computing it
    separately is how a run ends up halting on a plateau the file does not show.
    """
    return digest_of(redact(payload))

#: Above this size a tool result is flagged ``truncated``. The flag matters more
#: than the truncation itself: evidence extraction (D9) must know that a partial
#: payload is not a complete evidence item, otherwise a half-returned row set
#: reads as "evidence present" and the run halts early (DESIGN 2.6 pitfall 2).
MAX_RESULT_BYTES = 1_000_000


def result_size_bytes(payload: Any) -> int:
    """How big the result is, in **bytes** -- the unit the constant's name claims.

    The check used to be ``len(repr(payload))``, which counts *characters*: a
    1.2 MB page of Chinese read as 400 k and was not flagged, while the same
    size of ASCII was. Same threshold, two different answers depending on the
    script -- and the failure direction is the bad one, since an unflagged
    partial result is what makes R4 claim completion.

    ``repr`` is the rendering rather than JSON on purpose: it cannot fail on a
    payload the digest step is about to accept or reject, and this is a size
    guard, not a serializer.
    """
    return len(repr(payload).encode("utf-8"))

#: States from which no further call may be served.
#:
#: ``DEGRADED`` is terminal (D9), which is a change from DESIGN's first draft
#: ("degrade, then keep going"). Continuing buys nothing: if every declared
#: producer of a required fact is unavailable, completion is impossible, so the
#: remaining calls can only burn budget to reach a conclusion already known.
#: The classification is the point -- ``DEGRADED`` is neither a completion nor a
#: budget failure, so a metrics table can exclude it from "stopped too late"
#: instead of counting a structurally impossible task as a slow one.
TERMINAL_STATES = frozenset(
    {
        "ABORTED",
        "DEGRADED",
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


@dataclass
class RunSession:
    """Mutable per-run state. One instance per run, never shared across runs."""

    sink: JsonlSink
    run_id: str = field(default_factory=new_run_id)
    session_id: str = field(default_factory=new_session_id)
    parent_run_id: Optional[str] = None
    #: The task the caller declared for this run (FR-12). ``None`` means it never
    #: said, which is the state every run starts in and the state a single-task
    #: process stays in forever. It is what a boundary is compared against.
    task_id: Optional[str] = None
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
    def closed(self) -> bool:
        """No further call may be served. True for aborted *and* halted runs.

        There is no separate ``halted`` property (D9 removed it): once DEGRADED
        became terminal, "was this run stopped by a rule" stopped being a
        different question from "may it still be called", and a property returning
        False for a degraded run would be a trap rather than an API.
        """
        return self.state in TERMINAL_STATES

    @property
    def trace_path(self) -> Path:
        """Where this run's evidence lives. Derived, never stored twice."""
        return self.sink.path

    @property
    def missing(self) -> List[str]:
        return self.missing_with()

    def missing_with(self, *pending: str) -> List[str]:
        """``required ⁻ (evidence ∪ pending)`` -- for a call not yet committed.

        Needed because the post-stage rule must judge the call *before* its
        record exists: the verdict has to see the fact, while the session must
        not claim it until the trace can prove it.
        """
        return sorted(self.required - set(self.evidence) - set(pending))

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
        if self.aborted:
            # An aborted run has already lost its trace; relabelling it a
            # completion would make ``stats()`` report a successful stop for a
            # run whose record was never written. The reverse order is legal and
            # required: a halt verdict is followed by the write that can fail.
            raise ValueError(
                f"run already aborted ({self.abort_reason}); "
                f"refusing to relabel it {state}"
            )
        self.state = state
        self.halt_rule = rule
        self.halt_reason = reason

    def stats(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "parent_run_id": self.parent_run_id,
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

        Order is ``redact -> normalize -> digest`` (FR-14). Redaction goes first
        because it has to see the value as sent: normalize trims strings, and a
        credential with trailing whitespace would slip past a pattern applied
        afterwards. Nothing else about the order is free either -- swapping
        normalize and redact would let a folded key (``.lower()``) change which
        key name the redactor recognizes.
        """
        return digest_of(normalize_args(redact(args), spec_for(tool)))

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

    # -- run boundary (FR-12) ---------------------------------------------

    def next_run(
        self,
        *,
        task_id: Optional[str],
        inherit: bool,
        required: Optional[set] = None,
    ) -> "RunSession":
        """Open the successor run: **a new file**, not a new segment of this one.

        A trace file is a chain, and its genesis hash is derived from the first
        record's ``run_id`` -- so a second run appended to the same file would be
        indistinguishable from a continuation of the first, which is exactly the
        confusion the boundary exists to prevent. The link between the two is
        ``parent_run_id`` instead: the follow-up's first record points at the run
        it inherited from, and the parent's evidence is on disk in *its* file.

        That is also why there is no ``inherited_evidence`` field. The claim
        "this run started knowing X" is derivable -- parent id, then the parent's
        last record -- and a field would be a second copy of something the chain
        already proves. Same rule as ``evidence``: record the projection, not a
        summary that can drift.

        What is reset is what belongs to *this* run's activity: history, result
        digests, the state trail, the counters, and the turn number. Carrying any
        of them across a boundary would make the new run look like it had already
        done things -- the false-halt this module exists to prevent.
        """
        run_id = new_run_id()
        sink = JsonlSink(self.trace_path.parent / f"{run_id}.jsonl", run_id=run_id)
        return RunSession(
            sink=sink,
            run_id=run_id,
            # One conversation, several runs: the session id survives, so a
            # reviewer can group the files without guessing from timestamps.
            session_id=self.session_id,
            parent_run_id=self.run_id if inherit else None,
            task_id=task_id,
            model_id=self.model_id,
            gateway_version=self.gateway_version,
            rules_version=self.rules_version,
            budget=self.budget,
            required=set(self.required if required is None else required),
            evidence=dict(self.evidence) if inherit else {},
            clock=self.clock,
        )

    # -- R2's information state -------------------------------------------
    def state_hash(
        self, *pending: str, evidence: Optional[Mapping[str, str]] = None
    ) -> str:
        """Fingerprint of what the run *knows*: evidence plus result digests.

        Deliberately not ``(tool, args_hash)`` (DESIGN's first draft): folding
        the *questions* into the state makes the state change whenever the
        question changes, so "ask the same thing a different way and learn
        nothing" -- the exact behaviour R2 exists to catch -- can never look
        like no-progress. It would only ever see literal cycles, which R1
        already catches one stage earlier.

        Both inputs are sets that only ever grow, so this value changes **iff**
        the run learned something. ``pending`` and ``evidence`` are the same idea
        for the two halves: the post-stage guard must judge a call *before* its
        record exists, and anything absorbed before the write would leave the
        session claiming knowledge the trace does not have. So neither the fact
        nor its digest is merged here -- both are passed in, and both are only
        kept by the caller once the record has landed.
        """
        results = set(self.result_digests)
        results.update(pending)
        facts = set(self.evidence)
        if evidence:
            facts.update(evidence)
        return digest_of({"evidence": sorted(facts), "results": sorted(results)})

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
        try:
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
                task_id=self.task_id,
                offered=list(offered),
                chosen=chosen,
                args_hash=args_hash,
                result_digest=digest_result(result),
                truncated=truncated,
                evidence_added=list(evidence_added or []),
                stop_check=stop_check or StopCheck(),
                decision=decision,
                state=state or self.state,
                latency_ms=max(0, int(latency_ms)),
                prev_hash=prev_hash,
            ).seal()
        except NonCanonicalValue as exc:
            # A record that cannot be sealed is a record that cannot be written,
            # so it leaves this method as ``TraceWriteError`` -- the one exception
            # the transport's fail-closed branch understands. Anything else is a
            # *stray* exception past that branch: it escapes ``_handle_call``
            # after the upstream was already called, leaving a call the trace
            # cannot show and a run that keeps serving. Reachable from the wire
            # rather than from a programming mistake: ``json.loads`` accepts
            # ``NaN``/``Infinity``, so an upstream can put a non-finite number in
            # ``structuredContent`` and ``canonical_bytes`` refuses it (D16's
            # finding, one layer down).
            raise TraceWriteError(
                f"seq {seq}: the record cannot be sealed ({exc})"
            ) from exc

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
