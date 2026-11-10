"""Sink: chain validation before writing, and no half-records on failure."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gateway.trace.record import StopCheck, TraceRecord, genesis_hash
from gateway.trace.sink import JsonlSink, TraceChainError, TraceWriteError
from gateway.trace.verify import verify_path

RUN_ID = "run-sink-test"


def make_record(seq: int, prev_hash: str, **overrides) -> TraceRecord:
    kwargs = dict(
        seq=seq,
        run_id=RUN_ID,
        session_id="sess",
        turn=seq,
        model_id="stub",
        gateway_version="0.1.0",
        rules_version="rules-v1",
        offered=["gh.search"],
        chosen="gh.search",
        args_hash="a" * 64,
        result_digest="b" * 64,
        stop_check=StopCheck(attempted=["R1"]),
        decision="forward",
        state="RUNNING",
        latency_ms=10,
        prev_hash=prev_hash,
    )
    kwargs.update(overrides)
    return TraceRecord(**kwargs).seal()


class SinkTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "trace.jsonl"
        # ``addCleanup``, not ``tearDown``: unittest runs cleanups in reverse
        # order of registration, so a sink closed by a later ``addCleanup`` is
        # shut *before* the directory goes away. With ``tearDown`` the directory
        # was removed first and Windows refused ("another program is using this
        # file"), which reads as a test failure while the test itself passed.
        self.addCleanup(self._tmp.cleanup)

    def open(self, **kwargs) -> JsonlSink:
        """A sink that is always closed before the temp dir disappears."""
        kwargs.setdefault("run_id", RUN_ID)
        kwargs.setdefault("fsync", False)
        sink = JsonlSink(self.path, **kwargs)
        self.addCleanup(sink.close)
        return sink

    def write_chain(self, count: int, sink: JsonlSink, start: int = 1) -> None:
        for index in range(count):
            seq = start + index
            prev = sink.last_hash or genesis_hash(RUN_ID)
            sink.append(make_record(seq, prev))


class HappyPathTests(SinkTestCase):
    def test_appends_records_and_chain_verifies(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            self.write_chain(3, sink)
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 3)
        self.assertTrue(verify_path(self.path).ok)

    def test_reopening_continues_the_chain_instead_of_restarting(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            self.write_chain(2, sink)
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            self.assertEqual(sink.last_seq, 2)
            self.write_chain(2, sink, start=3)
        result = verify_path(self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.records, 4)

    def test_expected_next_reports_seq_and_prev_hash(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            self.assertEqual(sink.expected_next(), (1, genesis_hash(RUN_ID)))
            self.write_chain(1, sink)
            self.assertEqual(sink.expected_next()[0], 2)


class ChainGuardTests(SinkTestCase):
    def test_seq_out_of_order_is_refused(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            self.write_chain(1, sink)
            with self.assertRaises(TraceChainError):
                sink.append(make_record(5, sink.last_hash))

    def test_broken_prev_hash_is_refused(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            self.write_chain(1, sink)
            with self.assertRaises(TraceChainError):
                sink.append(make_record(2, "f" * 64))

    def test_unsealed_record_is_refused(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            record = make_record(1, genesis_hash(RUN_ID))
            record.chosen = "sql.query"  # mutated after sealing
            with self.assertRaises(TraceChainError):
                sink.append(record)

    def test_foreign_run_id_is_refused(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            with self.assertRaises(TraceChainError):
                sink.append(
                    make_record(1, genesis_hash("other-run"), run_id="other-run")
                )

    def test_new_chain_without_run_id_cannot_start(self):
        with JsonlSink(self.path, fsync=False) as sink:
            with self.assertRaises(TraceChainError):
                sink.append(make_record(1, genesis_hash(RUN_ID)))

    def test_refused_append_leaves_no_bytes(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            with self.assertRaises(TraceChainError):
                sink.append(make_record(3, genesis_hash(RUN_ID)))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "")


class FailClosedTests(SinkTestCase):
    def test_write_failure_raises_and_leaves_no_partial_line(self):
        # fsync=True so the mocked failure actually fires.
        with JsonlSink(self.path, run_id=RUN_ID, fsync=True) as sink:
            with mock.patch(
                "gateway.trace.sink.os.fsync", side_effect=OSError("disk full")
            ):
                with self.assertRaises(TraceWriteError):
                    sink.append(make_record(1, genesis_hash(RUN_ID)))
            self.assertTrue(sink.broken)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "")

    def test_failure_does_not_damage_records_written_before(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            self.write_chain(3, sink)
        with JsonlSink(self.path, run_id=RUN_ID, fsync=True) as sink:
            with mock.patch(
                "gateway.trace.sink.os.fsync", side_effect=OSError("torn write")
            ):
                with self.assertRaises(TraceWriteError):
                    sink.append(make_record(4, sink.last_hash))
        result = verify_path(self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.records, 3)

    def test_sink_stays_poisoned_after_a_failure(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=True) as sink:
            with mock.patch(
                "gateway.trace.sink.os.fsync", side_effect=OSError("io error")
            ):
                with self.assertRaises(TraceWriteError):
                    sink.append(make_record(1, genesis_hash(RUN_ID)))
            with self.assertRaises(TraceWriteError):
                sink.append(make_record(2, genesis_hash(RUN_ID)))

    def test_closed_sink_refuses_writes(self):
        sink = JsonlSink(self.path, run_id=RUN_ID, fsync=False)
        sink.close()
        with self.assertRaises(TraceChainError):
            sink.append(make_record(1, genesis_hash(RUN_ID)))


class DurabilityTests(SinkTestCase):
    def test_fsync_is_called_once_per_record(self):
        with JsonlSink(self.path, run_id=RUN_ID, fsync=True) as sink:
            with mock.patch("gateway.trace.sink.os.fsync") as fsync:
                self.write_chain(2, sink)
        self.assertEqual(fsync.call_count, 2)
        self.assertGreaterEqual(os.path.getsize(self.path), 1)


class ResumeRefusesWhatItCannotVerify(SinkTestCase):
    """Resuming is an append to somebody else's file. It has to be earned.

    Every case here was measured in the D17 audit, and the worst one destroyed
    data silently: a file whose last write lost its newline made the next append
    glue two records into one line, so the *earlier* record became unparseable
    and nothing raised.
    """

    def _chain(self, count=2) -> None:
        with JsonlSink(self.path, run_id=RUN_ID, fsync=False) as sink:
            self.write_chain(count, sink)

    def test_a_tail_without_a_newline_is_refused_not_glued(self):
        self._chain(2)
        data = self.path.read_bytes()
        self.assertTrue(data.endswith(b"\n"))
        self.path.write_bytes(data[:-1])  # the crash happened before the newline

        with self.assertRaises(TraceChainError) as caught:
            JsonlSink(self.path, run_id=RUN_ID, fsync=False)
        self.assertIn("no terminating newline", str(caught.exception))
        # The point of refusing: the bytes are untouched.
        self.assertEqual(self.path.read_bytes(), data[:-1])

    def test_a_half_written_tail_is_a_chain_error_not_a_json_error(self):
        self._chain(2)
        self.path.write_bytes(self.path.read_bytes()[:-40])
        with self.assertRaises(TraceChainError):
            JsonlSink(self.path, run_id=RUN_ID, fsync=False)

    def test_a_tampered_tail_is_refused(self):
        self._chain(2)
        text = self.path.read_text(encoding="utf-8")
        self.path.write_text(
            text.replace('"latency_ms":10', '"latency_ms":11'), encoding="utf-8"
        )
        with self.assertRaises(TraceChainError) as caught:
            JsonlSink(self.path, run_id=RUN_ID, fsync=False)
        self.assertIn("does not verify", str(caught.exception))
        self.assertIn("hash_mismatch", str(caught.exception))

    def test_a_non_utf8_tail_reports_the_line_it_died_on(self):
        self._chain(2)
        self.path.write_bytes(self.path.read_bytes() + b'{"seq": 3, "x": "\xff"}\n')
        with self.assertRaises(TraceChainError) as caught:
            JsonlSink(self.path, run_id=RUN_ID, fsync=False)
        message = str(caught.exception)
        self.assertIn("not UTF-8", message)
        self.assertIn("line 3", message)


class TwoWritersTests(SinkTestCase):
    """Two sinks on one file both resumed to the same ``seq`` and both wrote it.

    No lock here -- a lock is a feature. Noticing is the fix: the file can still
    end up with a duplicate, but one of the writers now refuses instead of both
    believing they own the chain.
    """

    def test_a_second_writer_cannot_append_behind_the_first(self):
        first = self.open()
        second = self.open()

        first.append(make_record(1, genesis_hash(RUN_ID)))
        with self.assertRaises(TraceChainError) as caught:
            second.append(make_record(1, genesis_hash(RUN_ID)))
        self.assertIn("grew", str(caught.exception))
        self.assertEqual(first.last_seq, 1)
        self.assertTrue(verify_path(self.path).ok, "the one record on disk is intact")

    def test_a_truncated_file_is_refused(self):
        sink = self.open()
        self.write_chain(2, sink)
        self.path.write_bytes(self.path.read_bytes()[:-10])
        with self.assertRaises(TraceChainError) as caught:
            sink.append(make_record(3, sink.last_hash))
        self.assertIn("shrank", str(caught.exception))

    # A "file replaced under the sink" case is not testable here: Windows will
    # not unlink a file that is still open, and the check is size-based anyway --
    # a same-size substitution would pass it. Testing the property the code has
    # (size drift) and not the one its name suggests is the point.


class UnclassifiedFailureTests(SinkTestCase):
    """The failure this module exists for must not escape unclassified.

    ``_write_line`` used to read ``self._handle.tell()`` **outside** its ``try``,
    so a handle that is no longer usable raised ``ValueError`` before anything
    caught it: the record was not written, the sink stayed "healthy", and the
    caller got an exception nothing called a write failure.
    """

    def test_a_dead_handle_is_classified_as_a_write_failure(self):
        sink = self.open()
        sink._handle.close()
        with self.assertRaises(TraceWriteError) as caught:
            sink.append(make_record(1, genesis_hash(RUN_ID)))
        self.assertIn("ValueError", str(caught.exception))
        self.assertTrue(sink.broken, "the sink must not stay usable after this")
        self.assertEqual(self.path.stat().st_size, 0)

    def test_rollback_does_not_mask_the_original_failure(self):
        # If the rollback raised, the diagnosis would become "truncate failed"
        # instead of "the write failed", which is the wrong bug to chase.
        sink = self.open()
        sink._handle.close()
        with self.assertRaises(TraceWriteError):
            sink.append(make_record(1, genesis_hash(RUN_ID)))


if __name__ == "__main__":
    unittest.main()
