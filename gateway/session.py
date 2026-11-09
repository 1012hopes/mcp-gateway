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

D4 scope: one run per process, one upstream, no stop rules (``rules-v0``).
Evidence sets, budgets and offered-set gating arrive in D5-D9; they hang off
this object rather than being threaded through the transport.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .trace.canonical import digest_of
from .trace.record import StopCheck, TraceRecord
from .trace.sink import JsonlSink
from .version import GATEWAY_VERSION, RULES_VERSION, UNKNOWN_MODEL_ID

__all__ = ["RunSession", "digest_of", "new_run_id", "new_session_id"]

#: Above this size a tool result is flagged ``truncated``. The flag matters more
#: than the truncation itself: evidence extraction (D9) must know that a partial
#: payload is not a complete evidence item, otherwise a half-returned row set
#: reads as "evidence present" and the run halts early (DESIGN 2.6 pitfall 2).
MAX_RESULT_BYTES = 1_000_000


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
    model_id: str = UNKNOWN_MODEL_ID
    gateway_version: str = GATEWAY_VERSION
    rules_version: str = RULES_VERSION
    turn: int = 1

    state: str = "RUNNING"
    abort_reason: Optional[str] = None

    records_written: int = 0
    results_delivered: int = 0
    calls_attempted: int = 0

    # -- bookkeeping -------------------------------------------------------

    @property
    def aborted(self) -> bool:
        return self.state == "ABORTED"

    @property
    def trace_path(self) -> Path:
        """Where this run's evidence lives. Derived, never stored twice."""
        return self.sink.path

    def abort(self, reason: str) -> None:
        self.state = "ABORTED"
        self.abort_reason = reason

    def stats(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "abort_reason": self.abort_reason,
            "calls_attempted": self.calls_attempted,
            "records_written": self.records_written,
            "results_delivered": self.results_delivered,
        }

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
            args_hash=digest_of(args),
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
        return record

    def mark_delivered(self) -> None:
        """Call this only after the result actually left the gateway."""
        self.results_delivered += 1
