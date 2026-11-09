"""The trace record: the unit of evidence.

Design rules that this module enforces (DESIGN 2.3 / 2.8):

* **Hash covers every byte, the version number explains the meaning.**
  ``hash`` is computed over the canonical form of *all* fields, including
  fields this version does not know about (kept in ``_extra``). Any byte
  change is therefore detectable, forever.
* **A record is append-only.** No setter mutates a sealed record; the only
  mutation path is :meth:`TraceRecord.seal`, which is called exactly once,
  right before the sink writes the line.
* **Unknown fields survive a round-trip.** Old readers must not silently
  drop new fields -- that would change the bytes and break the chain.

Field list, state names and decision names are copied from DESIGN 2.3 / 2.5.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .canonical import canonical_bytes

#: v2 adds one optional nested key, ``stop_check.state_hash`` (D8); v3 adds one
#: optional top-level key, ``task_id`` (D10). Both follow the same rule: the key
#: is **omitted when absent**, so a record written under the older schema reads
#: and rewrites byte-identically, and the payload field list is unchanged for
#: both versions -- a reader that demanded the new key would reject every old
#: line it was written to keep verifying.
CURRENT_SCHEMA_VERSION = 3

#: Versions this code can read. Writing is restricted to ``CURRENT``: old
#: records must stay verifiable forever, new ones must never be minted with an
#: old semantics.
READABLE_SCHEMA_VERSIONS = (1, 2, 3)

#: Payload field order for schema version 1 (DESIGN 2.3). ``hash`` is not part
#: of the payload -- it is the digest *of* the payload.
PAYLOAD_FIELDS_V1: Tuple[str, ...] = (
    "schema_version",
    "seq",
    "run_id",
    "parent_run_id",
    "session_id",
    "turn",
    "model_id",
    "gateway_version",
    "rules_version",
    "offered",
    "chosen",
    "args_hash",
    "result_digest",
    "truncated",
    "evidence_added",
    "stop_check",
    "decision",
    "state",
    "latency_ms",
    "prev_hash",
)

STOP_CHECK_FIELDS_V1: Tuple[str, ...] = (
    "rule",
    "hit",
    "evidence",
    "missing",
    "attempted",
)

#: v2 adds ``state_hash``. Present only when a state was actually computed, so
#: ``from_dict``/``to_dict`` round-trips a v1 record without inventing a key
#: (inventing one would change the bytes and break the chain).
STOP_CHECK_FIELDS_V2: Tuple[str, ...] = STOP_CHECK_FIELDS_V1 + ("state_hash",)

#: v3 adds one optional top-level field. It is not in ``PAYLOAD_FIELDS_V1``
#: because it is not *required* by any version -- listing it there would make
#: every v1 and v2 line unreadable.
OPTIONAL_PAYLOAD_FIELDS_V3: Tuple[str, ...] = ("task_id",)

#: DESIGN 2.5 state names.
RUN_STATES = frozenset(
    {
        "RUNNING",
        "HALTED_COMPLETE",
        "HALTED_DUPLICATE",
        "HALTED_NO_PROGRESS",
        "HALTED_BUDGET",
        "WAITING_APPROVAL",
        "DEGRADED",
    }
)

#: DESIGN 2.4 decision names.
DECISIONS = frozenset(
    {
        "forward",
        "block_policy",
        "block_duplicate",
        "halt_complete",
        "halt_no_progress",
        "halt_budget",
        "waiting_approval",
    }
)


def genesis_hash(run_id: str) -> str:
    """First ``prev_hash`` of a run (DESIGN A4: ``hash_0 = H(run_id)``)."""
    if not run_id:
        raise ValueError("run_id must be non-empty")
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()


@dataclass(kw_only=True)
class StopCheck:
    """The assertion produced by the stop controller, not an explanation.

    ``attempted`` records every rule that was evaluated, in evaluation order.
    Without it, "why did *this* rule fire instead of another one" is
    unanswerable during replay -- which is exactly the question a reviewer asks.

    ``state_hash`` (v2) is the run's information fingerprint *after* the call.
    It is what R2 compares: two adjacent records with the same value are a run
    that kept working and kept learning nothing. Storing it means the plateau is
    visible in the file, not just inferable from the verdict.
    """

    rule: Optional[str] = None
    hit: bool = False
    evidence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    attempted: List[str] = field(default_factory=list)
    state_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "rule": self.rule,
            "hit": self.hit,
            "evidence": list(self.evidence),
            "missing": list(self.missing),
            "attempted": list(self.attempted),
        }
        if self.state_hash is not None:
            body["state_hash"] = self.state_hash
        return body

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "StopCheck":
        if not data:
            return cls()
        unknown = {k: v for k, v in data.items() if k not in STOP_CHECK_FIELDS_V2}
        check = cls(
            rule=data.get("rule"),
            hit=bool(data.get("hit", False)),
            evidence=list(data.get("evidence") or []),
            missing=list(data.get("missing") or []),
            attempted=list(data.get("attempted") or []),
            state_hash=data.get("state_hash"),
        )
        if unknown:
            # Keep unknown keys so the hash still covers them.
            check._extra = dict(unknown)  # type: ignore[attr-defined]
        return check


@dataclass(kw_only=True)
class TraceRecord:
    """One tool-call decision, ready to be chained and written as one JSON line."""

    schema_version: int = CURRENT_SCHEMA_VERSION
    seq: int
    run_id: str
    session_id: str
    turn: int
    model_id: str
    gateway_version: str
    rules_version: str
    offered: List[str]
    args_hash: str
    result_digest: str
    decision: str
    state: str
    latency_ms: int
    prev_hash: str
    parent_run_id: Optional[str] = None
    #: v3: the task the caller declared for this run (FR-12). Omitted when the
    #: caller declared nothing, which is why it cannot be a required field.
    #: Storing it is what lets a reviewer read "this file was the follow-up to
    #: that one" off the trace instead of trusting a filename.
    task_id: Optional[str] = None
    chosen: Optional[str] = None
    truncated: bool = False
    evidence_added: List[str] = field(default_factory=list)
    stop_check: StopCheck = field(default_factory=StopCheck)
    hash: str = ""

    #: Field names this version does not know about; hashed like any other
    #: field, never interpreted (DESIGN 2.8 rule 2).
    _extra: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.seq < 1:
            raise ValueError(f"seq must be >= 1, got {self.seq}")
        if self.turn < 1:
            raise ValueError(f"turn must be >= 1, got {self.turn}")
        if self.latency_ms < 0:
            raise ValueError(f"latency_ms must be >= 0, got {self.latency_ms}")
        if self.schema_version not in READABLE_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported schema_version={self.schema_version}; "
                f"readable: {READABLE_SCHEMA_VERSIONS}"
            )
        # A version this code can *read* is not one it may *mint*: sealing an old
        # version with new field semantics would produce a record that claims a
        # schema it does not follow. Old versions stay readable, never writable.
        if self._extra and set(self._extra) & set(PAYLOAD_FIELDS_V1):
            raise ValueError("_extra must not shadow known fields")

    # -- serialization ----------------------------------------------------

    def payload(self) -> Dict[str, Any]:
        """All hashed fields: known ones plus preserved unknown ones."""
        body: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "seq": self.seq,
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "session_id": self.session_id,
            "turn": self.turn,
            "model_id": self.model_id,
            "gateway_version": self.gateway_version,
            "rules_version": self.rules_version,
            "offered": list(self.offered),
            "chosen": self.chosen,
            "args_hash": self.args_hash,
            "result_digest": self.result_digest,
            "truncated": self.truncated,
            "evidence_added": list(self.evidence_added),
            "stop_check": self.stop_check.to_dict(),
            "decision": self.decision,
            "state": self.state,
            "latency_ms": self.latency_ms,
            "prev_hash": self.prev_hash,
        }
        stop_extra = getattr(self.stop_check, "_extra", None)
        if stop_extra:
            body["stop_check"] = {**stop_extra, **body["stop_check"]}
        if self.task_id is not None:
            # Inserted right after ``parent_run_id`` (where the field list puts
            # it) rather than appended: canonical order is sorted anyway, so this
            # only affects readability of ``payload()`` for humans.
            body["task_id"] = self.task_id
        body.update(self._extra)
        return body

    def to_dict(self) -> Dict[str, Any]:
        body = self.payload()
        body["hash"] = self.hash
        return body

    def to_json_line(self) -> str:
        """One JSONL line. Must be byte-identical to what ``verify`` rehashes."""
        return canonical_bytes(self.to_dict()).decode("utf-8")

    # -- hashing ----------------------------------------------------------

    def compute_hash(self) -> str:
        """``H(canonical(payload))``.

        ``prev_hash`` lives inside the payload, so this is equivalent to the
        DESIGN A4 formula ``H(hash_{i-1} || canonical(record \\ {hash}))``
        while remaining a single, unambiguous serialization step.
        """
        return hashlib.sha256(canonical_bytes(self.payload())).hexdigest()

    def seal(self) -> "TraceRecord":
        """Fix ``hash``. Idempotent *only* if nothing changed in between."""
        if self.schema_version != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"refusing to seal schema_version={self.schema_version}; "
                f"current is {CURRENT_SCHEMA_VERSION}"
            )
        self.hash = self.compute_hash()
        return self

    def is_sealed(self) -> bool:
        return bool(self.hash) and self.hash == self.compute_hash()

    # -- construction -----------------------------------------------------

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TraceRecord":
        """Rebuild a record, keeping unknown fields so their bytes still hash."""
        known_payload = set(PAYLOAD_FIELDS_V1) | {"hash"} | set(OPTIONAL_PAYLOAD_FIELDS_V3)
        extra = {k: v for k, v in data.items() if k not in known_payload}
        record = cls(
            schema_version=int(data.get("schema_version", CURRENT_SCHEMA_VERSION)),
            seq=int(data["seq"]),
            run_id=str(data["run_id"]),
            parent_run_id=data.get("parent_run_id"),
            task_id=data.get("task_id"),
            session_id=str(data.get("session_id", "")),
            turn=int(data.get("turn", 1)),
            model_id=str(data.get("model_id", "")),
            gateway_version=str(data.get("gateway_version", "")),
            rules_version=str(data.get("rules_version", "")),
            offered=list(data.get("offered") or []),
            chosen=data.get("chosen"),
            args_hash=str(data.get("args_hash", "")),
            result_digest=str(data.get("result_digest", "")),
            truncated=bool(data.get("truncated", False)),
            evidence_added=list(data.get("evidence_added") or []),
            stop_check=StopCheck.from_dict(data.get("stop_check")),
            decision=str(data.get("decision", "forward")),
            state=str(data.get("state", "RUNNING")),
            latency_ms=int(data.get("latency_ms", 0)),
            prev_hash=str(data.get("prev_hash", "")),
            hash=str(data.get("hash", "")),
            _extra=extra,
        )
        return record
