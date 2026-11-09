"""Registry contracts: normalization, naming, hashing, drift.

These tests are the D5 acceptance criteria written so they cannot quietly stop
being true:

* two upstreams coexist and same-named tools do not collide
* ``schema_hash`` is stable across restarts (same input -> same digest, no
  dependence on dict or list order in the source)
* a ``$ref``/``oneOf`` schema is not empty after normalization

No MCP SDK needed: the registry is pure data in, pure data out. That is the
point of keeping it out of the transport.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.registry import (  # noqa: E402
    MAX_EXPOSED_NAME,
    RawTool,
    RegistryError,
    build_registry,
    load_baseline,
    normalize_description,
    normalize_schema,
    write_baseline,
)


def raw(
    name: str,
    schema=None,
    *,
    upstream: str = "u",
    description: str = "",
) -> RawTool:
    return RawTool(
        upstream=upstream,
        name=name,
        description=description,
        input_schema=schema if schema is not None else {"type": "object", "properties": {}},
    )


def one(entry: RawTool):
    return build_registry([("u", [entry])]).entries[0]


class RefResolutionTests(unittest.TestCase):
    def test_nested_refs_are_inlined_and_defs_are_dropped(self):
        schema = {
            "type": "object",
            "properties": {"where": {"$ref": "#/$defs/Filter"}},
            "required": ["where"],
            "$defs": {
                "Filter": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "op": {"$ref": "#/$defs/Op"},
                    },
                },
                "Op": {"type": "string", "enum": ["eq", "lt"]},
            },
        }
        entry = one(raw("search", schema))

        where = entry.input_schema["properties"]["where"]
        self.assertEqual(where["properties"]["op"]["enum"], ["eq", "lt"])
        self.assertNotIn("$defs", entry.input_schema)
        self.assertIn("resolved-refs:2", entry.notes)
        self.assertIn("stripped-defs:$defs", entry.notes)

    def test_unresolvable_ref_degrades_instead_of_raising(self):
        entry = one(raw("t", {"type": "object", "properties": {"a": {"$ref": "#/nope"}}}))
        self.assertEqual(entry.input_schema["properties"]["a"], {})
        self.assertIn("unresolved-ref:#/nope", entry.notes)

    def test_ref_cycle_is_cut_and_reported(self):
        schema = {
            "type": "object",
            "properties": {"node": {"$ref": "#/$defs/Node"}},
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/$defs/Node"}},
                }
            },
        }
        entry = one(raw("t", schema))
        self.assertIn("ref-cycle:#/$defs/Node", entry.notes)


class VariantFlatteningTests(unittest.TestCase):
    def _mode_schema(self):
        return {
            "type": "object",
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "mode": {"const": "fast"},
                        "budget_ms": {"type": "integer"},
                    },
                    "required": ["mode"],
                },
                {
                    "type": "object",
                    "properties": {
                        "mode": {"const": "exact"},
                        "statement": {"type": "string"},
                    },
                    "required": ["mode", "statement"],
                },
            ],
        }

    def test_arguments_hidden_inside_variants_become_visible(self):
        entry = one(raw("set_mode", self._mode_schema()))
        properties = entry.input_schema["properties"]

        self.assertEqual(sorted(properties), ["budget_ms", "mode", "statement"])
        # The two variants disagree about ``mode``, so both are kept.
        self.assertEqual(len(properties["mode"]["anyOf"]), 2)
        self.assertIn("flattened-oneOf:2-branches", entry.notes)

    def test_required_is_the_intersection_not_the_union(self):
        entry = one(raw("set_mode", self._mode_schema()))
        # ``statement`` is required by only one variant; claiming it is required
        # by the tool would be a lie the model acts on.
        self.assertEqual(entry.input_schema["required"], ["mode"])

    def test_original_one_of_is_preserved(self):
        entry = one(raw("set_mode", self._mode_schema()))
        self.assertEqual(len(entry.input_schema["oneOf"]), 2)

    def test_existing_properties_are_not_second_guessed(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "oneOf": [{"type": "object", "properties": {"b": {"type": "integer"}}}],
        }
        entry = one(raw("t", schema))
        self.assertEqual(list(entry.input_schema["properties"]), ["a"])


class SchemaRepairTests(unittest.TestCase):
    def test_required_name_missing_from_properties_is_added(self):
        schema = {
            "type": "object",
            "properties": {},
            "required": ["flag"],
            "additionalProperties": False,
        }
        entry = one(raw("set_flag", schema))
        self.assertIn("flag", entry.input_schema["properties"])
        self.assertIn("required-not-in-properties:flag", entry.notes)

    def test_missing_type_is_inferred_from_properties(self):
        entry = one(raw("t", {"properties": {"a": {"type": "string"}}}))
        self.assertEqual(entry.input_schema["type"], "object")
        self.assertIn("type-inferred:object", entry.notes)

    def test_empty_schema_becomes_an_object_with_no_arguments(self):
        entry = one(raw("t", {}))
        self.assertEqual(entry.input_schema, {"type": "object", "properties": {}})
        self.assertIn("assumed-empty-object", entry.notes)

    def test_object_without_properties_gets_an_empty_map(self):
        entry = one(raw("t", {"type": "object", "additionalProperties": False}))
        self.assertEqual(entry.input_schema["properties"], {})

    def test_non_object_schema_is_replaced_not_crashed(self):
        entry = one(raw("t", ["not", "a", "schema"]))
        self.assertEqual(entry.input_schema["type"], "object")
        self.assertIn("schema-not-an-object", entry.notes)


class HashStabilityTests(unittest.TestCase):
    def test_key_order_does_not_change_the_hash(self):
        a = one(
            raw(
                "t",
                {
                    "type": "object",
                    "properties": {"x": {"type": "string"}, "y": {"type": "integer"}},
                },
            )
        )
        b = one(
            raw(
                "t",
                {
                    "properties": {"y": {"type": "integer"}, "x": {"type": "string"}},
                    "type": "object",
                },
            )
        )
        self.assertEqual(a.schema_hash, b.schema_hash)

    def test_required_and_enum_order_do_not_change_the_hash(self):
        a = one(
            raw(
                "t",
                {
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "b": {"type": "string", "enum": ["x", "y"]},
                    },
                    "required": ["a", "b"],
                },
            )
        )
        b = one(
            raw(
                "t",
                {
                    "type": "object",
                    "properties": {
                        "b": {"enum": ["y", "x"], "type": "string"},
                        "a": {"type": "string"},
                    },
                    "required": ["b", "a"],
                },
            )
        )
        self.assertEqual(a.schema_hash, b.schema_hash)

    def test_a_real_contract_change_does_change_the_hash(self):
        a = one(raw("t", {"type": "object", "properties": {"x": {"type": "string"}}}))
        b = one(raw("t", {"type": "object", "properties": {"x": {"type": "integer"}}}))
        self.assertNotEqual(a.schema_hash, b.schema_hash)

    def test_description_whitespace_is_normalized_but_words_are_kept(self):
        a = one(raw("t", description="Line one.\n  Line two."))
        b = one(raw("t", description="Line one. Line two."))
        self.assertEqual(a.schema_hash, b.schema_hash)
        self.assertEqual(a.description, "Line one. Line two.")

    def test_description_change_changes_the_hash(self):
        a = one(raw("t", description="does X"))
        b = one(raw("t", description="does Y"))
        self.assertNotEqual(a.schema_hash, b.schema_hash)

    def test_long_description_is_kept_whole_and_flagged(self):
        text = "word " * 400
        description, notes = normalize_description(text)
        self.assertEqual(len(description.split()), 400)
        self.assertTrue(any(note.startswith("long-description:") for note in notes))

    def test_hash_is_stable_across_two_builds(self):
        """A restart must not change any hash -- that is the D5 criterion."""
        schema = {
            "type": "object",
            "properties": {
                "where": {"$ref": "#/$defs/F"},
                "limit": {"type": "integer"},
            },
            "$defs": {"F": {"type": "object", "properties": {"a": {"type": "string"}}}},
        }
        first = build_registry([("u", [raw("q", schema)])]).manifest()["tools"]
        second = build_registry([("u", [raw("q", schema)])]).manifest()["tools"]
        self.assertEqual(first, second)


class NamingTests(unittest.TestCase):
    def test_tools_are_namespaced_by_upstream(self):
        registry = build_registry(
            [("sqlite", [raw("query", upstream="sqlite")]), ("dirty", [raw("query", upstream="dirty")])]
        )
        self.assertEqual(registry.offered_names(), ["sqlite.query", "dirty.query"])

    def test_same_short_name_in_two_upstreams_does_not_collide(self):
        registry = build_registry(
            [("sqlite", [raw("query", upstream="sqlite")]), ("dirty", [raw("query", upstream="dirty")])]
        )
        self.assertIsNotNone(registry.get("sqlite.query"))
        self.assertIsNotNone(registry.get("dirty.query"))
        self.assertNotEqual(registry.get("sqlite.query"), registry.get("dirty.query"))

    def test_non_ascii_name_is_escaped_deterministically(self):
        first = one(raw("工具查询"))
        second = one(raw("工具查询"))
        self.assertEqual(first.exposed_name, second.exposed_name)
        self.assertRegex(first.exposed_name, r"^u\.[A-Za-z0-9_.-]+$")
        self.assertIn("name-escaped:工具查询", first.notes)

    def test_overlong_name_is_truncated_within_the_limit(self):
        long_name = "tool_" + "x" * 100
        entry = one(raw(long_name))
        self.assertLessEqual(len(entry.exposed_name), MAX_EXPOSED_NAME)
        self.assertTrue(entry.exposed_name.startswith("u."))
        self.assertTrue(entry.exposed_name.rsplit("-", 1)[-1])
        self.assertTrue(any(n.startswith("name-truncated:") for n in entry.notes))

    def test_truncation_is_deterministic(self):
        long_name = "tool_" + "x" * 100
        self.assertEqual(one(raw(long_name)).exposed_name, one(raw(long_name)).exposed_name)

    def test_escaping_collision_is_resolved_not_dropped(self):
        # ``a b`` escapes to ``a_u0020b``, which is also a legal literal name.
        registry = build_registry([("u", [raw("a b"), raw("a_u0020b")])])
        names = registry.offered_names()
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2)
        self.assertEqual(registry.entries[0].exposed_name, "u.a_u0020b")
        self.assertTrue(
            any(note.startswith("name-collision-resolved:") for note in registry.notes)
        )

    def test_duplicate_listing_of_the_same_tool_is_deduped(self):
        registry = build_registry([("u", [raw("q"), raw("q")])])
        self.assertEqual(len(registry.entries), 1)
        self.assertIn("duplicate-listed:u.q", registry.notes)

    def test_dotted_upstream_name_is_rejected(self):
        with self.assertRaises(RegistryError):
            build_registry([("a.b", [raw("q")])])

    def test_exposed_name_keeps_the_original_for_the_call(self):
        entry = one(raw("工具查询"))
        self.assertEqual(entry.original_name, "工具查询")
        self.assertNotEqual(entry.exposed_name, entry.original_name)


class DriftTests(unittest.TestCase):
    def _registry(self, schema, baseline=None):
        return build_registry([("u", [raw("q", schema)])], baseline=baseline)

    def test_matching_baseline_freezes_nothing(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        hash_ = self._registry(schema).manifest()["tools"]["u.q"]
        registry = self._registry(schema, {"u.q": hash_})
        self.assertEqual(registry.frozen, ())
        self.assertFalse(registry.warnings())

    def test_changed_schema_freezes_the_tool(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        registry = self._registry(schema, {"u.q": "0" * 64})
        entry = registry.get("u.q")
        self.assertTrue(entry.frozen)
        self.assertEqual(registry.offered_names(), [])
        self.assertIn("schema-drift:u.q", registry.warnings())
        self.assertTrue(entry.drift.startswith("000000000000->"))

    def test_new_tool_is_offered_but_reported(self):
        schema = {"type": "object", "properties": {}}
        registry = self._registry(schema, {})
        self.assertFalse(registry.get("u.q").frozen)
        self.assertIn("new-tool:u.q", registry.warnings())

    def test_vanished_tool_is_reported(self):
        schema = {"type": "object", "properties": {}}
        registry = self._registry(schema, {"u.gone": "0" * 64})
        self.assertIn("removed-tool:u.gone", registry.warnings())

    def test_no_baseline_means_no_drift_check(self):
        registry = self._registry({"type": "object", "properties": {}}, None)
        self.assertFalse(registry.baseline_present)
        self.assertFalse(registry.get("u.q").frozen)


class BaselineFileTests(unittest.TestCase):
    def test_round_trip(self):
        registry = build_registry(
            [("u", [raw("q", {"type": "object", "properties": {}})])]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema-manifest.json"
            write_baseline(path, registry)
            self.assertEqual(load_baseline(path), registry.manifest()["tools"])

            rebuilt = build_registry(
                [("u", [raw("q", {"type": "object", "properties": {}})])],
                baseline=load_baseline(path),
            )
            self.assertFalse(rebuilt.warnings())

    def test_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_baseline(Path(tmp) / "nope.json"))

    def test_manifest_is_sorted_and_stable(self):
        registry = build_registry(
            [("u", [raw("b"), raw("a")])],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            write_baseline(path, registry)
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
            self.assertEqual(list(data["tools"]), ["u.a", "u.b"])
            self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
