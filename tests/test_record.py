"""TraceRecord: field completeness, hash stability, chain linkage."""

import json
import unittest
from pathlib import Path

from gateway.trace.record import (
    CURRENT_SCHEMA_VERSION,
    PAYLOAD_FIELDS_V1,
    StopCheck,
    TraceRecord,
    genesis_hash,
)

RUN_ID = "run-2026-11-09-001"


def make_record(seq: int = 1, prev: TraceRecord | None = None, **overrides) -> TraceRecord:
    kwargs = dict(
        seq=seq,
        run_id=RUN_ID,
        session_id="sess-1",
        turn=1,
        model_id="stub-model",
        gateway_version="0.1.0",
        rules_version="rules-v1",
        offered=["gh.search", "gh.get_file", "sql.query"],
        chosen="gh.search",
        args_hash="a" * 64,
        result_digest="b" * 64,
        evidence_added=["repo.list"],
        stop_check=StopCheck(rule=None, hit=False, attempted=["R5", "R1", "R4"]),
        decision="forward",
        state="RUNNING",
        latency_ms=412,
        prev_hash=prev.hash if prev is not None else genesis_hash(RUN_ID),
    )
    kwargs.update(overrides)
    return TraceRecord(**kwargs).seal()


class FieldCompletenessTests(unittest.TestCase):
    def test_payload_fields_match_design_2_3(self):
        expected = (
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
        self.assertEqual(PAYLOAD_FIELDS_V1, expected)

    def test_to_dict_has_every_payload_field_plus_hash(self):
        data = make_record().to_dict()
        self.assertEqual(set(data), set(PAYLOAD_FIELDS_V1) | {"hash"})
        self.assertEqual(len(data), 21)

    def test_stop_check_keeps_all_four_plus_attempted(self):
        data = make_record().to_dict()["stop_check"]
        self.assertEqual(
            set(data), {"rule", "hit", "evidence", "missing", "attempted"}
        )


class HashStabilityTests(unittest.TestCase):
    def test_two_identical_records_hash_identically(self):
        self.assertEqual(make_record().hash, make_record().hash)

    def test_compute_hash_is_idempotent(self):
        record = make_record()
        self.assertEqual(record.compute_hash(), record.compute_hash())
        self.assertEqual(record.compute_hash(), record.hash)

    def test_sealed_record_reports_valid(self):
        self.assertTrue(make_record().is_sealed())

    def test_any_field_change_invalidates_the_seal(self):
        record = make_record()
        record.chosen = "gh.get_file"
        self.assertFalse(record.is_sealed())

    def test_offered_order_is_semantic(self):
        a = make_record(offered=["gh.search", "sql.query"])
        b = make_record(offered=["sql.query", "gh.search"])
        self.assertNotEqual(a.hash, b.hash)

    def test_unknown_field_changes_the_hash(self):
        """DESIGN 2.8: the hash covers fields this version does not know about."""
        base = make_record()
        extended = TraceRecord.from_dict(base.to_dict())
        extended._extra = {"future_rule": "R6"}
        self.assertNotEqual(base.compute_hash(), extended.compute_hash())

    def test_latency_change_changes_the_hash(self):
        self.assertNotEqual(
            make_record(latency_ms=412).hash, make_record(latency_ms=413).hash
        )


class RoundTripTests(unittest.TestCase):
    def test_json_line_round_trips_to_the_same_hash(self):
        record = make_record()
        restored = TraceRecord.from_dict(json.loads(record.to_json_line()))
        self.assertEqual(restored.hash, record.hash)
        self.assertTrue(restored.is_sealed())

    def test_unknown_fields_survive_a_round_trip(self):
        record = make_record()
        data = record.to_dict()
        data["future_rule"] = "R6"
        data["hash"] = TraceRecord.from_dict(data).compute_hash()
        restored = TraceRecord.from_dict(data)
        self.assertEqual(restored._extra, {"future_rule": "R6"})
        self.assertTrue(restored.is_sealed())

    def test_unknown_fields_inside_stop_check_survive(self):
        record = make_record()
        data = record.to_dict()
        data["stop_check"]["budget_snapshot"] = {"calls": 3}
        data["hash"] = TraceRecord.from_dict(data).compute_hash()
        restored = TraceRecord.from_dict(data)
        self.assertIn("budget_snapshot", restored.payload()["stop_check"])
        self.assertTrue(restored.is_sealed())


class ChainTests(unittest.TestCase):
    def test_genesis_hash_is_deterministic_and_hex(self):
        self.assertEqual(genesis_hash(RUN_ID), genesis_hash(RUN_ID))
        self.assertEqual(len(genesis_hash(RUN_ID)), 64)
        self.assertNotEqual(genesis_hash(RUN_ID), genesis_hash(RUN_ID + "x"))

    def test_three_records_link_head_to_tail(self):
        r1 = make_record(seq=1)
        r2 = make_record(seq=2, prev=r1, turn=2)
        r3 = make_record(seq=3, prev=r2, turn=3)
        self.assertEqual(r2.prev_hash, r1.hash)
        self.assertEqual(r3.prev_hash, r2.hash)
        self.assertEqual(len({r1.hash, r2.hash, r3.hash}), 3)

    def test_tampering_with_a_middle_record_breaks_the_next_link(self):
        r1 = make_record(seq=1)
        r2 = make_record(seq=2, prev=r1)
        r2.chosen = "sql.query"
        self.assertNotEqual(r2.compute_hash(), r2.hash)


class ValidationTests(unittest.TestCase):
    def test_seq_must_start_at_one(self):
        with self.assertRaises(ValueError):
            make_record(seq=0)

    def test_negative_latency_is_rejected(self):
        with self.assertRaises(ValueError):
            make_record(latency_ms=-1)

    def test_sealing_an_old_schema_version_is_rejected(self):
        # Readable is not writable: an old version may still be verified, never
        # minted, or a record would claim a schema its fields do not follow.
        record = make_record()
        record.schema_version = CURRENT_SCHEMA_VERSION - 1
        with self.assertRaises(ValueError):
            record.seal()

    def test_a_v1_record_still_verifies(self):
        """The old format must stay verifiable after the v2 field was added.

        Built by hand from a v2 payload minus the optional key: that is exactly
        what a v1 file looks like on disk, and re-serializing it must reproduce
        the same bytes (otherwise ``verify`` would report a hash mismatch on a
        record nobody touched).
        """
        from gateway.trace.verify import record_hash

        current = make_record().to_dict()
        v1 = {k: v for k, v in current.items() if k != "hash"}
        v1["schema_version"] = 1
        v1["hash"] = record_hash(v1)

        rebuilt = TraceRecord.from_dict(v1).to_dict()
        self.assertEqual(rebuilt, v1)
        self.assertEqual(record_hash(rebuilt), v1["hash"])
        self.assertNotIn("state_hash", rebuilt["stop_check"])

    def test_state_hash_is_omitted_when_not_measured(self):
        # A missing measurement must not be encoded as null: the key's presence
        # is the difference between "the state did not change" and "nobody looked".
        self.assertNotIn("state_hash", make_record().to_dict()["stop_check"])
        measured = make_record(stop_check=StopCheck(hit=False, state_hash="c" * 64))
        self.assertEqual(measured.to_dict()["stop_check"]["state_hash"], "c" * 64)

    def test_empty_run_id_has_no_genesis(self):
        with self.assertRaises(ValueError):
            genesis_hash("")


class DecisionNamesTests(unittest.TestCase):
    """The declared decision set must cover every name the code can write.

    This is the invariant that was missing for eleven days: ``DECISIONS``
    named ``halt_complete`` while the rule wrote ``halt_evidence_complete``,
    omitted ``halt_duplicate``, and invented ``waiting_approval``. Nothing read
    the set, so nothing complained. The D15 viewer reads whatever string is in
    the file, which is what made the gap visible -- a consumer is what turns a
    declaration into a claim.
    """

    GATEWAY = Path(__file__).resolve().parents[1] / "gateway"

    def _written_literals(self):
        """Every literal decision this code can put in a record."""
        import re

        found = {}
        for path in sorted(self.GATEWAY.rglob("*.py")):
            if path.name == "record.py":
                continue  # the declaration itself, not a producer
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r'\bdecision\s*=\s*"([a-z_]+)"', text):
                found.setdefault(match.group(1), set()).add(path.name)
            for match in re.finditer(r'_Blocked\(\s*"([a-z_]+)"', text):
                found.setdefault(match.group(1), set()).add(path.name)
        return found

    def test_every_written_decision_is_declared(self):
        from gateway.trace.record import DECISIONS

        written = self._written_literals()
        self.assertTrue(written, "扫不到任何 decision 字面量，正则失效了？")
        undeclared = sorted(name for name in written if name not in DECISIONS)
        self.assertEqual(
            undeclared,
            [],
            "这些 decision 真的会被写进轨迹，但 DECISIONS 里没有："
            f"{ {name: sorted(written[name]) for name in undeclared} }",
        )

    def test_every_declared_decision_has_a_producer(self):
        from gateway.trace.record import DECISION_SOURCES, DECISIONS

        written = self._written_literals()
        # ``forward`` is returned as a tuple element, not assigned, so the grep
        # cannot see it; it is the one name allowed to be declared without a
        # literal of that shape.
        harmless = {"forward"}
        orphaned = sorted(
            name for name in DECISIONS if name not in written and name not in harmless
        )
        self.assertEqual(
            orphaned, [], f"DECISIONS 里这些名字没有任何模块会写：{orphaned}"
        )
        self.assertEqual(set(DECISION_SOURCES), set(DECISIONS))

    def test_the_two_names_that_were_wrong(self):
        """Pin the specific mistakes so they cannot quietly come back."""
        from gateway.trace.record import DECISIONS

        self.assertIn("halt_evidence_complete", DECISIONS)
        self.assertIn("halt_duplicate", DECISIONS)
        self.assertNotIn("halt_complete", DECISIONS)
        self.assertNotIn("waiting_approval", DECISIONS)


if __name__ == "__main__":
    unittest.main()
