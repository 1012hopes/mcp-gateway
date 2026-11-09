"""Canonical serialization: one logical value, exactly one byte string."""

import unittest

from gateway.trace.canonical import (
    NonCanonicalValue,
    canonical_bytes,
    canonical_text,
    normalize,
)


class KeyOrderTests(unittest.TestCase):
    def test_dict_insertion_order_does_not_matter(self):
        a = {"b": 2, "a": 1, "c": {"z": 26, "y": 25}}
        b = {"c": {"y": 25, "z": 26}, "a": 1, "b": 2}
        self.assertEqual(canonical_bytes(a), canonical_bytes(b))

    def test_key_order_matters_when_not_sorted(self):
        # Guard against the "sorted" flag being dropped by accident.
        text = canonical_text({"b": 2, "a": 1})
        self.assertEqual(text, '{"a":1,"b":2}')

    def test_no_whitespace(self):
        self.assertEqual(canonical_text({"a": 1, "b": [1, 2]}), '{"a":1,"b":[1,2]}')


class FloatTests(unittest.TestCase):
    def test_rounding_makes_near_equal_floats_hash_equal(self):
        self.assertEqual(
            canonical_bytes({"latency": 1.0000001}),
            canonical_bytes({"latency": 1.0000002}),
        )

    def test_sum_of_parts_equals_whole_after_rounding(self):
        self.assertEqual(
            canonical_bytes({"x": 0.1 + 0.2}), canonical_bytes({"x": 0.3})
        )

    def test_negative_zero_folds_into_zero(self):
        self.assertEqual(canonical_bytes({"x": -0.0}), canonical_bytes({"x": 0.0}))

    def test_int_and_float_are_distinguishable(self):
        # 1 and 1.0 serialize differently on purpose: the type is semantic.
        self.assertNotEqual(canonical_bytes({"x": 1}), canonical_bytes({"x": 1.0}))

    def test_nan_is_rejected(self):
        with self.assertRaises(NonCanonicalValue):
            canonical_bytes({"x": float("nan")})

    def test_infinity_is_rejected(self):
        with self.assertRaises(NonCanonicalValue):
            canonical_bytes({"x": float("inf")})


class DropKeyTests(unittest.TestCase):
    def test_drop_keys_removes_volatile_fields(self):
        with_ts = {"tool": "gh.search", "args": {"q": "x", "ts": "2026-11-09T12:00:00Z"}}
        without_ts = {"tool": "gh.search", "args": {"q": "x"}}
        self.assertEqual(
            canonical_bytes(with_ts, drop_keys=frozenset({"ts"})),
            canonical_bytes(without_ts),
        )

    def test_drop_keys_applies_at_every_level(self):
        a = {"a": {"nonce": "1", "v": 1}, "nonce": "2"}
        b = {"a": {"v": 1}}
        self.assertEqual(
            canonical_bytes(a, drop_keys=frozenset({"nonce"})), canonical_bytes(b)
        )

    def test_dropping_a_semantic_key_changes_the_hash(self):
        # Drop-keys must be an explicit allowlist decision, never a default.
        a = {"cursor": "abc", "q": "x"}
        b = {"cursor": "def", "q": "x"}
        self.assertNotEqual(canonical_bytes(a), canonical_bytes(b))


class EncodingTests(unittest.TestCase):
    def test_cjk_is_not_escaped(self):
        text = canonical_text({"note": "证据完备"})
        self.assertIn("证据完备", text)
        self.assertNotIn("\\u", text)

    def test_bytes_are_rejected(self):
        with self.assertRaises(NonCanonicalValue):
            canonical_bytes({"x": b"raw"})

    def test_non_string_keys_are_rejected(self):
        with self.assertRaises(NonCanonicalValue):
            canonical_bytes({1: "a"})

    def test_unsupported_type_is_rejected_loudly(self):
        with self.assertRaises(NonCanonicalValue):
            canonical_bytes({"x": object()})


class NormalizeTests(unittest.TestCase):
    def test_nested_structures_are_normalized_recursively(self):
        value = normalize({"b": [{"y": 2, "x": 1.00000001}], "a": None})
        self.assertEqual(value, {"b": [{"y": 2, "x": 1.0}], "a": None})


if __name__ == "__main__":
    unittest.main()
