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

    def tearDown(self) -> None:
        self._tmp.cleanup()

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


if __name__ == "__main__":
    unittest.main()
