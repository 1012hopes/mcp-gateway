"""D7: what counts as "the same call".

The stake is asymmetric and that shapes every test here. A missed duplicate makes
R1 fire less often -- the numbers get smaller and the project looks worse, but
every number it prints is still true. An invented duplicate makes the gateway
halt a healthy run and call two different questions one -- a wrong decision,
recorded as if it were right.

So each test comes in a pair: one asserting that noise is folded away, and one
asserting that meaning is not. A test that only proved the first half would pass
against a normalizer that returned ``{}`` for everything.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.stop.normalize import (  # noqa: E402
    DEFAULT_SPEC,
    NormalizeSpec,
    normalize_args,
    spec_for,
)
from gateway.trace.canonical import digest_of  # noqa: E402


def identify(tool, args, spec=None):
    """The same path the gateway uses: normalize, then hash."""
    return digest_of(normalize_args(args, spec or spec_for(tool)))


class KeyOrderTests(unittest.TestCase):
    def test_key_order_does_not_change_identity(self):
        self.assertEqual(
            identify("sqlite.query", {"limit": 5, "sql": "select 1"}),
            identify("sqlite.query", {"sql": "select 1", "limit": 5}),
        )

    def test_key_order_inside_a_nested_object(self):
        a = {"filters": {"table": "incidents", "limit": 5}}
        b = {"filters": {"limit": 5, "table": "incidents"}}
        self.assertEqual(identify("sqlite.query", a), identify("sqlite.query", b))


class VolatileKeyTests(unittest.TestCase):
    def test_provenance_keys_are_dropped(self):
        base = {"sql": "select 1"}
        for key in ("request_id", "requestId", "REQUEST-ID", "nonce", "trace_id", "uuid"):
            with self.subTest(key=key):
                self.assertEqual(
                    identify("sqlite.query", {**base, key: "aaaaaaaa"}),
                    identify("sqlite.query", {**base, key: "bbbbbbbb"}),
                )

    def test_a_uuid_under_a_semantic_key_is_kept(self):
        # ``id`` may be exactly what is being looked up. Dropping it would merge
        # two lookups for two different rows.
        self.assertNotEqual(
            identify("sqlite.query", {"id": "2f1c6b1e-0000-4000-8000-000000000001"}),
            identify("sqlite.query", {"id": "2f1c6b1e-0000-4000-8000-000000000002"}),
        )

    def test_non_string_keys_are_an_error_not_a_silent_stringification(self):
        with self.assertRaises(TypeError):
            normalize_args({1: "x"})


class ClockKeyTests(unittest.TestCase):
    def test_iso_timestamps_under_clock_names_are_dropped(self):
        self.assertEqual(
            identify("sqlite.query", {"sql": "q", "ts": "2026-11-09T12:00:00Z"}),
            identify("sqlite.query", {"sql": "q", "ts": "2026-11-09T12:00:07Z"}),
        )

    def test_date_only_and_zone_offsets_are_recognised(self):
        self.assertEqual(
            identify("x.y", {"ts": "2026-11-09"}),
            identify("x.y", {"ts": "2026-11-08"}),
        )
        self.assertEqual(
            identify("x.y", {"ts": "2026-11-09T12:00:00+08:00"}),
            identify("x.y", {"ts": "2026-11-09T12:00:00+00:00"}),
        )

    def test_a_clock_named_key_holding_something_else_is_kept(self):
        # ``time`` is a filter here, not a clock reading.
        self.assertNotEqual(
            identify("x.y", {"time": "business_hours"}),
            identify("x.y", {"time": "night"}),
        )

    def test_epoch_seconds_are_dropped_but_small_numbers_are_not(self):
        self.assertEqual(
            identify("x.y", {"timestamp": 1762689600}),
            identify("x.y", {"timestamp": 1762689601}),
        )
        self.assertNotEqual(
            identify("x.y", {"time": 30}), identify("x.y", {"time": 60})
        )


class ScalarTests(unittest.TestCase):
    def test_surrounding_whitespace_is_irrelevant(self):
        self.assertEqual(
            identify("sqlite.query", {"sql": "  select 1  "}),
            identify("sqlite.query", {"sql": "select 1"}),
        )

    def test_float_noise_is_folded(self):
        self.assertEqual(
            identify("x.y", {"x": 0.1 + 0.2}), identify("x.y", {"x": 0.3})
        )

    def test_integral_floats_and_ints_agree(self):
        self.assertEqual(identify("x.y", {"limit": 5}), identify("x.y", {"limit": 5.0}))

    def test_negative_zero_is_zero(self):
        self.assertEqual(identify("x.y", {"x": -0.0}), identify("x.y", {"x": 0}))

    def test_large_ids_are_not_truncated(self):
        self.assertNotEqual(
            identify("x.y", {"id": 9007199254740993}),
            identify("x.y", {"id": 9007199254740992}),
        )

    def test_booleans_are_not_integers(self):
        # ``True == 1`` in Python; a normalizer that forgot this would fold a flag
        # into a count.
        self.assertNotEqual(identify("x.y", {"x": True}), identify("x.y", {"x": 1}))

    def test_list_order_is_preserved(self):
        # Sorting would fold ``in ('a','b')`` into ``in ('b','a')`` -- but also
        # reorder a column list, which is semantic. Not worth the risk.
        self.assertNotEqual(
            identify("x.y", {"columns": ["a", "b"]}),
            identify("x.y", {"columns": ["b", "a"]}),
        )


class CaseFoldingTests(unittest.TestCase):
    def test_declared_keys_fold_for_that_tool_family(self):
        # SQLite identifiers are case-insensitive, so these are the same request.
        self.assertEqual(
            identify("sqlite.describe_table", {"table": "INCIDENTS"}),
            identify("sqlite.describe_table", {"table": "incidents"}),
        )

    def test_undeclared_tools_keep_case(self):
        # No spec says this tool is case-insensitive, so folding would be a guess.
        self.assertNotEqual(
            identify("dirty.describe_table", {"table": "INCIDENTS"}),
            identify("dirty.describe_table", {"table": "incidents"}),
        )

    def test_folding_does_not_reach_into_list_elements(self):
        a = {"table": "T", "columns": ["Mixed", "Case"]}
        b = {"table": "t", "columns": ["mixed", "case"]}
        self.assertNotEqual(identify("sqlite.query", a), identify("sqlite.query", b))


class MeaningIsPreservedTests(unittest.TestCase):
    def test_different_arguments_stay_different(self):
        self.assertNotEqual(
            identify("sqlite.query", {"sql": "select 1"}),
            identify("sqlite.query", {"sql": "select 2"}),
        )

    def test_a_null_is_not_an_absent_key(self):
        # ``{"x": null}`` asks for null; ``{}`` does not ask at all.
        self.assertNotEqual(identify("x.y", {"x": None}), identify("x.y", {}))

    def test_sql_is_treated_as_opaque(self):
        # Normalizing SQL needs a parser; a wrong normalization here invents
        # duplicates. The policy emits SQL deterministically, so an exact repeat
        # really is byte-identical.
        self.assertNotEqual(
            identify("sqlite.query", {"sql": "select  *  from t"}),
            identify("sqlite.query", {"sql": "select * from t"}),
        )


class SpecTests(unittest.TestCase):
    def test_unknown_tool_falls_back_to_the_conservative_default(self):
        self.assertIs(spec_for("nope.query"), DEFAULT_SPEC)
        self.assertIs(spec_for(None), DEFAULT_SPEC)

    def test_path_keys_use_posix_rules_regardless_of_host(self):
        spec = NormalizeSpec(path_keys=frozenset({"path"}))
        self.assertEqual(
            normalize_args({"path": "a//b/./c"}, spec)["path"], "a/b/c"
        )

    def test_normalization_is_pure(self):
        args = {"sql": " select 1 ", "ts": "2026-11-09T12:00:00Z"}
        before = dict(args)
        normalize_args(args)
        self.assertEqual(args, before, "the caller's dict must not be mutated")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
