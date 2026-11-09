"""Verify: first-error localisation, tamper detection, L1-before-L2 ordering."""

import json
import tempfile
import unittest
from pathlib import Path

from gateway.trace.canonical import canonical_bytes
from gateway.trace.record import StopCheck, TraceRecord, genesis_hash
from gateway.trace.verify import (
    REASON_BLANK_LINE,
    REASON_EMPTY,
    REASON_HASH_MISMATCH,
    REASON_JSON_PARSE,
    REASON_MISSING_FIELD,
    REASON_PREV_HASH_MISMATCH,
    REASON_SEQ_GAP,
    REASON_UNSUPPORTED_SCHEMA,
    record_hash,
    verify_lines,
    verify_path,
)

RUN_ID = "run-verify-test"
STEPS = 5


def build_records(count: int = STEPS) -> list[TraceRecord]:
    records: list[TraceRecord] = []
    prev = genesis_hash(RUN_ID)
    for seq in range(1, count + 1):
        record = TraceRecord(
            seq=seq,
            run_id=RUN_ID,
            session_id="sess",
            turn=seq,
            model_id="stub",
            gateway_version="0.1.0",
            rules_version="rules-v1",
            offered=["gh.search", "gh.get_file", "sql.query"],
            chosen="gh.search",
            args_hash=f"{seq:064d}",
            result_digest="b" * 64,
            evidence_added=["repo.list"] if seq == 1 else [],
            stop_check=StopCheck(
                rule=None, hit=False, evidence=[], missing=[], attempted=["R1", "R4"]
            ),
            decision="forward",
            state="RUNNING",
            latency_ms=100 + seq,
            prev_hash=prev,
        ).seal()
        records.append(record)
        prev = record.hash
    return records


def as_lines(records: list[TraceRecord]) -> list[str]:
    return [record.to_json_line() for record in records]


def mutate(lines: list[str], index: int, **changes) -> list[str]:
    """Change fields on one line and re-emit every line canonically."""
    out = []
    for position, line in enumerate(lines):
        data = json.loads(line)
        if position == index:
            data.update(changes)
        out.append(canonical_bytes(data).decode("utf-8"))
    return out


def reseal(lines: list[str], index: int) -> list[str]:
    """Recompute the hash so the record is self-consistent but semantically wrong."""
    out = []
    for position, line in enumerate(lines):
        data = json.loads(line)
        if position == index:
            data["hash"] = record_hash(data)
        out.append(canonical_bytes(data).decode("utf-8"))
    return out


def rebuild_from(lines: list[str], index: int) -> list[str]:
    """Rewrite hashes *and* links from ``index`` to the end.

    This is what a writer with file access does: changing one record forces
    every later record to be re-chained. It is the reason a bare hash chain
    cannot stop a determined forger -- see the note shipped with every
    successful verification.
    """
    out = [canonical_bytes(json.loads(line)).decode("utf-8") for line in lines]
    prev = None
    for position in range(len(out)):
        data = json.loads(out[position])
        if position >= index:
            if prev is not None:
                data["prev_hash"] = prev
            data["hash"] = record_hash(data)
            out[position] = canonical_bytes(data).decode("utf-8")
        prev = data["hash"]
    return out


class CleanChainTests(unittest.TestCase):
    def test_valid_chain_passes_and_counts_records(self):
        result = verify_lines(as_lines(build_records()))
        self.assertTrue(result.ok)
        self.assertEqual(result.records, STEPS)
        self.assertIsNone(result.first_bad_seq)

    def test_valid_chain_warns_about_tail_truncation(self):
        result = verify_lines(as_lines(build_records()))
        self.assertTrue(result.ok)
        self.assertTrue(any("尾部被截断" in note for note in result.notes))

class TamperTests(unittest.TestCase):
    """The five mutations G1 has to catch (PLAN D3)."""

    def test_tampering_with_the_first_record_is_caught(self):
        lines = mutate(as_lines(build_records()), 0, chosen="sql.query")
        result = verify_lines(lines)
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_seq, 1)
        self.assertEqual(result.reason, REASON_HASH_MISMATCH)

    def test_tampering_with_a_middle_record_is_caught(self):
        lines = mutate(as_lines(build_records()), 2, chosen="sql.query")
        result = verify_lines(lines)
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_seq, 3)

    def test_tampering_with_the_last_record_is_caught(self):
        lines = mutate(as_lines(build_records()), STEPS - 1, latency_ms=1)
        result = verify_lines(lines)
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_seq, STEPS)

    def test_tampering_with_the_offered_set_is_caught(self):
        lines = mutate(as_lines(build_records()), 1, offered=["sql.query"])
        result = verify_lines(lines)
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_seq, 2)
        self.assertEqual(result.reason, REASON_HASH_MISMATCH)

    def test_rewriting_the_hash_field_is_caught(self):
        lines = mutate(as_lines(build_records()), 2, hash="0" * 64)
        result = verify_lines(lines)
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_seq, 3)
        self.assertEqual(result.reason, REASON_HASH_MISMATCH)

    def test_deleting_a_middle_line_reports_a_seq_gap(self):
        lines = as_lines(build_records())
        del lines[2]  # seq 3
        result = verify_lines(lines)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_SEQ_GAP)
        self.assertEqual(result.first_bad_seq, 3)

    def test_replacing_a_line_with_a_blank_one_is_reported(self):
        lines = as_lines(build_records())
        lines[1] = ""
        result = verify_lines(lines)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_BLANK_LINE)
        self.assertEqual(result.first_bad_line, 2)

    def test_broken_json_is_reported_with_its_line_number(self):
        lines = as_lines(build_records())
        lines[1] = '{"seq": 2, '
        result = verify_lines(lines)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_JSON_PARSE)
        self.assertEqual(result.first_bad_line, 2)

    def test_broken_genesis_link_is_caught(self):
        lines = mutate(as_lines(build_records()), 0, prev_hash="f" * 64)
        result = verify_lines(lines)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_PREV_HASH_MISMATCH)

    def test_tail_truncation_is_not_detectable(self):
        # Documented limitation, asserted so nobody "fixes" the note away.
        lines = as_lines(build_records())[:-2]
        self.assertTrue(verify_lines(lines).ok)


class LayerOrderTests(unittest.TestCase):
    """L1 must run before L2, otherwise tampering reads as a schema problem."""

    def test_missing_field_without_reseal_reads_as_integrity_break(self):
        lines = mutate(as_lines(build_records()), 2)
        data = json.loads(lines[2])
        del data["chosen"]
        lines[2] = canonical_bytes(data).decode("utf-8")
        result = verify_lines(lines)
        self.assertEqual(result.reason, REASON_HASH_MISMATCH)

    def test_missing_field_with_reseal_reads_as_structure_error(self):
        lines = mutate(as_lines(build_records()), 2)
        data = json.loads(lines[2])
        del data["decision"]
        lines[2] = canonical_bytes(data).decode("utf-8")
        lines = reseal(lines, 2)
        result = verify_lines(lines)
        self.assertEqual(result.reason, REASON_MISSING_FIELD)
        self.assertEqual(result.first_bad_seq, 3)

    def test_unknown_schema_version_is_rejected_after_hash_passes(self):
        lines = mutate(as_lines(build_records()), 1, schema_version=99)
        lines = reseal(lines, 1)
        result = verify_lines(lines)
        self.assertEqual(result.reason, REASON_UNSUPPORTED_SCHEMA)

    def test_unknown_extra_field_is_accepted_after_reseal(self):
        """Forward compatibility: a newer writer may add fields."""
        lines = mutate(as_lines(build_records()), 1, future_field={"a": 1})
        lines = rebuild_from(lines, 1)
        result = verify_lines(lines)
        self.assertTrue(result.ok)
        self.assertEqual(result.records, STEPS)

    def test_rewriting_the_whole_tail_produces_a_valid_chain(self):
        """The known blind spot: a writer with file access can re-chain.

        Asserted on purpose -- it is what the tail-note warns about, and what
        external anchoring (signing, append-only log) would have to close.
        """
        lines = rebuild_from(mutate(as_lines(build_records()), 2, chosen="sql.query"), 2)
        result = verify_lines(lines)
        self.assertTrue(result.ok)
        self.assertTrue(any("外部锚定" in note for note in result.notes))


class FileTests(unittest.TestCase):
    def test_empty_file_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            result = verify_path(path)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_EMPTY)

    def test_result_serialises_for_the_cli(self):
        result = verify_lines(as_lines(build_records()))
        payload = result.to_dict()
        self.assertEqual(payload["records"], STEPS)
        self.assertTrue(payload["ok"])
        json.dumps(payload)  # must be JSON-safe


if __name__ == "__main__":
    unittest.main()
