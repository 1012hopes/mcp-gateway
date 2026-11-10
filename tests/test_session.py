"""RunSession: the counters that make "no result without a record" assertable."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gateway.session import (
    MAX_RESULT_BYTES,
    RunSession,
    digest_of,
    result_size_bytes,
)
from gateway.trace.record import genesis_hash
from gateway.trace.sink import JsonlSink, TraceWriteError
from gateway.trace.verify import verify_path


class ResultSizeTests(unittest.TestCase):
    """``MAX_RESULT_BYTES`` is a byte threshold; the check has to agree.

    Measured failure: a 1.2 MB page of Chinese counted as 400 k characters and
    went unflagged, while the same number of bytes of ASCII was flagged. The
    direction matters -- an unflagged partial result is what lets R4 claim
    completion on half a row set.
    """

    def test_the_same_byte_size_flags_the_same_way_in_any_script(self):
        wide = 400_000  # 1.2 MB as UTF-8 (three bytes per character)
        cjk = {"rows": [{"body": "\u4e2d" * wide}]}
        ascii_same_bytes = {"rows": [{"body": "x" * (wide * 3)}]}
        self.assertGreater(result_size_bytes(cjk), MAX_RESULT_BYTES)
        self.assertGreater(result_size_bytes(ascii_same_bytes), MAX_RESULT_BYTES)
        # Same payload, same bytes, whatever the script: the thresholds agree.
        self.assertEqual(result_size_bytes(cjk), result_size_bytes(ascii_same_bytes))

    def test_a_small_result_is_measured_in_bytes_not_in_objects(self):
        self.assertLess(result_size_bytes({"rows": []}), MAX_RESULT_BYTES)


class RunSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "run.jsonl"
        self.sink = JsonlSink(self.path, run_id="run-unit")
        self.addCleanup(self.sink.close)
        self.session = RunSession(sink=self.sink, run_id="run-unit")

    def test_first_record_uses_genesis_hash_and_is_sealed(self):
        record = self.session.record_call(
            offered=["sqlite.query"], chosen="sqlite.query", args={}, result={"rows": []},
            latency_ms=3,
        )
        self.assertEqual(record.seq, 1)
        self.assertEqual(record.prev_hash, genesis_hash("run-unit"))
        self.assertTrue(record.is_sealed())
        self.assertEqual(self.session.records_written, 1)

    def test_second_record_links_to_the_first(self):
        first = self.session.record_call(
            offered=["a"], chosen="a", args={}, result=1, latency_ms=1
        )
        second = self.session.record_call(
            offered=["a"], chosen="a", args={}, result=2, latency_ms=1
        )
        self.assertEqual(second.prev_hash, first.hash)
        self.assertEqual(verify_path(self.path).records, 2)

    def test_args_are_hashed_not_stored(self):
        # This case used to assert ``digest_of(secret)`` -- i.e. that the token
        # was *part of* the identity, which is precisely what FR-14 forbids
        # (D10). The example is unchanged on purpose: it is the one that used to
        # prove the wrong thing.
        secret = {"token": "s3cret-value"}
        record = self.session.record_call(
            offered=["a"], chosen="a", args=secret, result="ok", latency_ms=1
        )
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("s3cret-value", raw)
        self.assertEqual(record.args_hash, self.session.args_hash_for("a", secret))
        self.assertNotEqual(
            record.args_hash, digest_of(secret), "the credential must not be hashed"
        )

    def test_counters_separate_delivery_from_recording(self):
        self.session.record_call(offered=["a"], chosen="a", args={}, result=1, latency_ms=1)
        self.assertEqual(self.session.results_delivered, 0)
        self.session.mark_delivered()
        self.assertEqual(self.session.results_delivered, 1)

    def test_abort_is_terminal_and_reported(self):
        self.assertFalse(self.session.aborted)
        self.session.abort("trace write failed")
        self.assertTrue(self.session.aborted)
        self.assertEqual(self.session.stats()["abort_reason"], "trace write failed")

    def test_an_aborted_run_cannot_be_relabelled_a_halt(self):
        # ``halt`` used to overwrite the state without looking, so an aborted
        # run could report ``HALTED_COMPLETE`` while still carrying an
        # ``abort_reason``: a run that lost its trace counted as a clean stop.
        # The reverse order stays legal and is required -- a halt verdict is
        # followed by the write that can fail.
        self.session.abort("trace write failed at seq 1")
        with self.assertRaises(ValueError):
            self.session.halt("HALTED_COMPLETE", rule="R4", reason="complete")
        self.assertEqual(self.session.state, "ABORTED")

    def test_a_halt_can_still_be_followed_by_an_abort(self):
        self.session.halt("HALTED_COMPLETE", rule="R4", reason="complete")
        self.session.abort("the record for that halt could not be written")
        self.assertEqual(self.session.state, "ABORTED")

    def test_a_record_that_cannot_be_sealed_is_a_write_failure(self):
        """``NaN`` is legal JSON -- ``json.loads`` accepts it -- and not
        hashable, so an upstream can hand the gateway a payload it cannot
        record. That must leave ``record_call`` as the exception the transport's
        fail-closed branch already handles; as a stray ``NonCanonicalValue`` it
        escaped after the call had been made, leaving a call the trace cannot
        show and a run that keeps serving (DESIGN 1.5's one undetectable gap).
        """
        with self.assertRaises(TraceWriteError):
            self.session.record_call(
                offered=["a"], chosen="a", args={}, result={"rows": [float("nan")]},
                latency_ms=1,
            )
        self.assertEqual(self.session.records_written, 0)
        self.assertEqual(self.path.read_bytes(), b"", "no half-written record")
        self.assertEqual(self.session.state, "RUNNING")
        # The chain is unharmed: the next record is still seq 1.
        record = self.session.record_call(
            offered=["a"], chosen="a", args={}, result={"rows": []}, latency_ms=1
        )
        self.assertEqual(record.seq, 1)
        self.assertEqual(verify_path(self.path).records, 1)

    def test_turn_increments_only_when_caller_does_it(self):
        self.session.record_call(offered=["a"], chosen="a", args={}, result=1, latency_ms=1)
        self.assertEqual(self.session.turn, 1)
        self.session.turn += 1
        record = self.session.record_call(
            offered=["a"], chosen="a", args={}, result=2, latency_ms=1
        )
        self.assertEqual(record.turn, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
