"""Fail-closed JSONL sink.

The contract, in one line: **either the whole record is on disk, or the caller
gets an exception and nothing was written.**

Why not "best effort": a hash chain proves the recorded part was not modified,
but it cannot prove a call was not omitted. Deleting the tail leaves a
perfectly valid chain. So an unrecorded call is the one blind spot of this
whole design, and the only defence is refusing to continue (DESIGN 1.5):

    write fails -> raise -> caller aborts the run

Two failure modes are handled differently on purpose:

* ``TraceChainError`` -- a **logic** error (wrong seq, broken link, unsealed
  record). The disk is fine; the caller built a bad record. Do not retry.
* ``TraceWriteError`` -- an **I/O** error. The sink poisons itself: every later
  append raises too, because a gap in the chain cannot be repaired by writing
  more records after it.

Why ``TraceWriteError`` also covers the failures that are not ``OSError``
-----------------------------------------------------------------------
The encoding step already happened in ``to_json_line`` (it round-trips through
``canonical_bytes``), and a lone surrogate never gets this far: ``seal()``
rejects it. So on paper a failed write is an ``OSError`` and nothing else.

The audit found the hole in "on paper": the failure this sink most needs to
classify is *a handle that is no longer usable*, and that raises ``ValueError``
("I/O operation on closed file"), not ``OSError``. Worse, it was raised by
``self._handle.tell()`` -- which sat **outside** the ``try``. So the record was
neither written nor reported as a write failure: the caller got a ``ValueError``
nothing classified, the sink stayed "healthy", and the next append would have
been attempted against the same dead handle.

Two changes: the rollback point is now ``self._size`` (the offset of the last
byte this sink committed, which the append-only contract makes identical to the
real end of file), so it is defined *before* anything can fail; and the handler
catches ``Exception``, because every way of not getting the record on disk means
the same thing to the caller.

Resuming: verify first, and only ever append
--------------------------------------------
``_resume`` used to read the last line and take its ``seq``/``hash``. Two
consequences, both measured in the D17 audit:

* **A tail without its newline silently destroyed the record before it.** File
  ends ``...turn":2}`` (the crash happened between writing the bytes and the
  ``\\n``); resume reads ``seq=2`` happily and appends ``{"seq":3...}\\n``
  directly after the ``}``. The two records become one line, the earlier record
  is now unparseable, and nothing raised.
* **A half-written tail raised a raw ``JSONDecodeError``**, and a
  non-UTF-8 tail a raw ``UnicodeDecodeError``. The one program that must
  survive a damaged file answered with a traceback instead of a line number.

So resume now runs the real verifier over the file and refuses if it does not
pass, refuses a tail that is not newline-terminated, and refuses bytes that are
not UTF-8 -- all as ``TraceChainError``, which ``prepare()`` already turns into
"refusing to start" (never into a half-open run). It does **not** repair any of
these: this module's promise is that it only appends, and a repair would be the
one place where it rewrote bytes it did not write.

Why the writer checks that nobody else wrote
--------------------------------------------
Two sinks on one file both resume to the same ``seq`` and both append it: the
chain then contains a duplicate and no code path noticed (``verify`` reports it
later as ``seq_gap``). A file lock would be a feature; noticing is a fix. Each
append compares the file's size with the size this sink has written to, so a
second writer -- or a truncation by something else -- becomes a refusal instead
of a corrupt trace.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from .record import TraceRecord, genesis_hash
from .verify import decode_trace, verify_lines

__all__ = ["JsonlSink", "TraceWriteError", "TraceChainError"]


class TraceWriteError(OSError):
    """Disk-level failure. The run must abort; do not retry the same run."""


class TraceChainError(ValueError):
    """The record does not fit the chain that is already on disk."""


class JsonlSink:
    """Append sealed records to a ``.jsonl`` file, one JSON object per line."""

    def __init__(
        self,
        path: Union[str, Path],
        *,
        run_id: Optional[str] = None,
        fsync: bool = True,
        fsync_fn: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.path = Path(path)
        self._run_id = run_id
        self._fsync = fsync
        # Dependency-injection seam for fault injection. Not an env var, not a
        # flag: a caller that wants to test "what if the disk fails here" passes
        # a function that raises. Production callers never pass it.
        self._fsync_fn = fsync_fn
        self._broken = False
        self._closed = False
        self._last_seq = 0
        self._last_hash: Optional[str] = None
        #: Bytes this sink believes are on disk. Compared before every append so
        #: a second writer (or a truncation) cannot be mistaken for our own tail.
        self._size = 0

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._resume()

        # "a+" so we can read our own tail back after a failed write.
        self._handle = open(self.path, "a+", encoding="utf-8", newline="\n")
        # Position must be the true end of file, otherwise the rollback below
        # would truncate to the wrong offset and silently destroy records.
        self._handle.seek(0, os.SEEK_END)
        self._size = os.path.getsize(self.path)

    # -- state ------------------------------------------------------------

    def _resume(self) -> None:
        """Continue an existing chain -- only if that chain verifies as it stands.

        Everything this method refuses is a file the previous writer did not
        finish, and every refusal is a ``TraceChainError`` (which ``prepare()``
        reports as "refusing to start") instead of the raw
        ``JSONDecodeError``/``UnicodeDecodeError`` the audit found here. Adding
        a record to a damaged trace would not repair it: the new record's
        ``prev_hash`` would point at a hash that describes nothing.
        """
        if not self.path.exists():
            return
        data = self.path.read_bytes()
        if not data:
            return

        text, failure = decode_trace(data)
        if failure is not None:
            raise TraceChainError(
                f"cannot append to {self.path}: line {failure.first_bad_line} is "
                f"not UTF-8 ({failure.reason}); the writer that produced this file "
                f"did not finish. Nothing was written."
            )
        if not text.endswith("\n"):
            # Not cosmetic: appending to an unterminated line would glue the new
            # record onto the old one and destroy the old one's last field.
            raise TraceChainError(
                f"cannot append to {self.path}: the last line has no terminating "
                f"newline ({len(data)} bytes), so it is a record whose write did "
                f"not complete. Append a newline by hand if the record is known "
                f"to be whole; this sink only ever appends and will not repair it."
            )

        result = verify_lines(text.splitlines())
        if not result.ok:
            raise TraceChainError(
                f"cannot append to {self.path}: line {result.first_bad_line} does "
                f"not verify ({result.reason}: {result.detail}). A record after it "
                f"would claim a chain that the file does not have."
            )

        last = json.loads(text.splitlines()[-1])
        self._last_seq = int(last["seq"])
        self._last_hash = str(last["hash"])

    @property
    def broken(self) -> bool:
        return self._broken

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @property
    def last_hash(self) -> Optional[str]:
        return self._last_hash

    def expected_next(self) -> tuple[int, str]:
        """``(seq, prev_hash)`` that the next appended record must carry."""
        if self._last_hash is None:
            if not self._run_id:
                raise TraceChainError(
                    "new chain needs run_id so the genesis hash is well defined"
                )
            return 1, genesis_hash(self._run_id)
        return self._last_seq + 1, self._last_hash

    # -- write ------------------------------------------------------------

    def append(self, record: TraceRecord) -> None:
        """Chain-validate, then write. Raises before touching the file."""
        if self._closed:
            raise TraceChainError("sink is closed")
        if self._broken:
            raise TraceWriteError(
                f"sink is poisoned after an earlier write failure: {self.path}"
            )

        self._check_nobody_else_wrote()

        expected_seq, expected_prev = self.expected_next()
        if record.seq != expected_seq:
            raise TraceChainError(
                f"seq out of order: expected {expected_seq}, got {record.seq}"
            )
        if record.prev_hash != expected_prev:
            raise TraceChainError(
                f"prev_hash does not match the chain tail at seq {record.seq}"
            )
        if not record.is_sealed():
            raise TraceChainError(
                f"record {record.seq} is not sealed, or was mutated after sealing"
            )
        if self._run_id is not None and record.run_id != self._run_id:
            raise TraceChainError(
                f"record run_id {record.run_id!r} != sink run_id {self._run_id!r}"
            )

        line = record.to_json_line() + "\n"
        self._write_line(line)

        self._last_seq = record.seq
        self._last_hash = record.hash

    def _check_nobody_else_wrote(self) -> None:
        """Refuse if the file is no longer the one this sink has been writing.

        Checked before the chain, because "the file changed under me" is the
        *cause* and a stale ``seq`` is only its symptom. Not a lock: two writers
        can still race between this check and the write. It does mean the
        mistake cannot stay invisible -- whichever writer notices second refuses,
        and the file keeps a verifiable chain.
        """
        actual = os.path.getsize(self.path)
        if actual == self._size:
            return
        direction = "grew" if actual > self._size else "shrank"
        raise TraceChainError(
            f"{self.path} {direction} from {self._size} to {actual} bytes since "
            f"this sink opened it: another writer appended to it, or it was "
            f"truncated. Refusing to append seq {self._last_seq + 1}, which would "
            f"duplicate or skip a record."
        )

    def _sync(self) -> None:
        if not self._fsync:
            return
        # Resolved at call time so tests can patch ``os.fsync`` globally.
        (self._fsync_fn or os.fsync)(self._handle.fileno())

    def _write_line(self, line: str) -> None:
        # The rollback point is the offset of the last byte this sink committed,
        # not ``self._handle.tell()``. Two reasons: the append-only contract
        # (plus ``_check_nobody_else_wrote``) makes them the same number, and
        # ``tell()`` itself raises ``ValueError`` on a dead handle -- which was
        # how a failed write could escape this method unclassified.
        position = self._size
        try:
            self._handle.write(line)
            self._handle.flush()
            self._sync()
        except Exception as exc:  # noqa: BLE001 -- any way of not getting it on disk
            self._broken = True
            self._rollback(position)
            raise TraceWriteError(
                f"failed to persist trace record to {self.path} "
                f"({type(exc).__name__}); the run must abort (fail-closed)"
            ) from exc
        self._size += len(line.encode("utf-8"))

    def _rollback(self, position: int) -> None:
        """Best effort: leave no half-record behind.

        Catches ``Exception``, not ``OSError``: the usual reason to be here is a
        dead handle, and a rollback that raised would replace the real diagnosis
        with its own. The sink is already poisoned, so the caller aborts either
        way -- the only thing left to protect is the message.
        """
        try:
            self._handle.flush()
            self._handle.truncate(position)
            self._handle.flush()
            if self._fsync:
                (self._fsync_fn or os.fsync)(self._handle.fileno())
        except Exception:  # noqa: BLE001
            pass

    def append_all(self, records: "Sequence[TraceRecord]") -> None:
        for record in records:
            self.append(record)

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._handle.close()
        except OSError:
            pass

    def __enter__(self) -> "JsonlSink":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
