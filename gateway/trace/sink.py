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
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator, List, Optional, Union

from .record import TraceRecord, genesis_hash

__all__ = ["JsonlSink", "TraceWriteError", "TraceChainError", "read_records"]


class TraceWriteError(OSError):
    """Disk-level failure. The run must abort; do not retry the same run."""


class TraceChainError(ValueError):
    """The record does not fit the chain that is already on disk."""


def read_records(path: Union[str, Path]) -> Iterator[dict]:
    """Yield parsed JSON objects, one per non-empty line."""
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                yield json.loads(stripped)


class JsonlSink:
    """Append sealed records to a ``.jsonl`` file, one JSON object per line."""

    def __init__(
        self,
        path: Union[str, Path],
        *,
        run_id: Optional[str] = None,
        fsync: bool = True,
    ) -> None:
        self.path = Path(path)
        self._run_id = run_id
        self._fsync = fsync
        self._broken = False
        self._closed = False
        self._last_seq = 0
        self._last_hash: Optional[str] = None

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._resume()

        # "a+" so we can read our own tail back after a failed write.
        self._handle = open(self.path, "a+", encoding="utf-8", newline="\n")
        # Position must be the true end of file, otherwise the rollback below
        # would truncate to the wrong offset and silently destroy records.
        self._handle.seek(0, os.SEEK_END)

    # -- state ------------------------------------------------------------

    def _resume(self) -> None:
        """Continue an existing chain instead of silently starting over."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        last: Optional[dict] = None
        for last in read_records(self.path):
            pass
        if last is None:
            return
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

    def _write_line(self, line: str) -> None:
        position = self._handle.tell()
        try:
            self._handle.write(line)
            self._handle.flush()
            if self._fsync:
                os.fsync(self._handle.fileno())
        except OSError as exc:
            self._broken = True
            self._rollback(position)
            raise TraceWriteError(
                f"failed to persist trace record to {self.path}; "
                f"the run must abort (fail-closed)"
            ) from exc

    def _rollback(self, position: int) -> None:
        """Best effort: leave no half-record behind.

        If the truncate itself fails we are in worse trouble, but the sink is
        already poisoned, so the caller still aborts.
        """
        try:
            self._handle.flush()
            self._handle.truncate(position)
            self._handle.flush()
            if self._fsync:
                os.fsync(self._handle.fileno())
        except OSError:
            pass

    def append_all(self, records: List[TraceRecord]) -> None:
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
