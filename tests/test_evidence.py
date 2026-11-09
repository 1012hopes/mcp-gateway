"""D9: the evidence model, tested where it can actually be wrong.

The end-to-end half (does R4 fire on a real run, does the answer check out) lives
in ``test_agent_e2e.py``. Here the subject is the predicate itself, because every
mistake in this file has the same shape: it is silent. An extractor that never
matches turns every compression number into zero without raising; one that
matches too much declares a task complete before it is done. Both outcomes look
like a working gateway, which is why the cases below are written in pairs --
"this counts" next to "this looks like it and does not".
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.evidence import (  # noqa: E402
    EVIDENCE_VERSION,
    EvidenceModel,
    load_evidence_model,
)
from agent.tasks import load_tasks  # noqa: E402

CONFIG = ROOT / "config" / "evidence.json"


def envelope(body, *, is_error: bool = False) -> dict:
    """The shape the transport actually passes: a CallToolResult dump."""
    return {
        "content": [{"type": "text", "text": json.dumps(body)}],
        "is_error": is_error,
    }


class ShippedModelTests(unittest.TestCase):
    """The model in the repository must be loadable and internally consistent."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_evidence_model(CONFIG)

    def test_version_is_the_supported_one(self):
        self.assertEqual(self.model.version, EVIDENCE_VERSION)

    def test_every_declared_type_has_a_producer_and_a_shape(self):
        for name in self.model.types:
            spec = self.model.spec(name)
            self.assertTrue(spec.producers, name)
            self.assertTrue(spec.shapes, name)
            self.assertTrue(spec.why, f"{name}: the 'why' is what a reviewer reads")

    def test_every_task_requirement_is_declared(self):
        # A task naming an undeclared type makes the gateway refuse to start
        # (loudly, in __main__), but catching it here is cheaper than starting a
        # subprocess to find out.
        declared = set(self.model.types)
        for task in load_tasks():
            for ev_type in task.required_evidence:
                self.assertIn(ev_type, declared, f"{task.id} requires an unknown type")

    def test_producers_are_namespaced(self):
        # Upstream names are part of the tool name (registry.py). A producer
        # without the prefix would never appear in ``offered``, so the fact would
        # be permanently "unreachable" and every run would end DEGRADED.
        for name in self.model.types:
            for producer in self.model.producers(name):
                self.assertIn(".", producer, f"{name}: {producer} is not namespaced")


class ExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_evidence_model(CONFIG)

    def extract(self, body, *, tool, args=None, truncated=False, is_error=False):
        return self.model.extract(
            envelope(body, is_error=is_error),
            tool=tool,
            args=args or {},
            truncated=truncated,
        )

    # -- what counts -------------------------------------------------------

    def test_table_list_is_schema_evidence(self):
        self.assertEqual(
            self.extract({"tables": ["incidents", "repos"]}, tool="sqlite.list_tables"),
            {"sql.schema": "sqlite.list_tables"},
        )

    def test_column_list_is_also_schema_evidence(self):
        # Two shapes, one fact: "the structure was read" is true either way.
        self.assertEqual(
            self.extract(
                {"table": "incidents", "columns": [{"name": "id"}]},
                tool="sqlite.describe_table",
                args={"table": "incidents"},
            ),
            {"sql.schema": "sqlite.describe_table"},
        )

    def test_an_empty_row_set_is_evidence(self):
        # The t02 rule: asking and finding nothing is knowledge. If this failed,
        # "the repo does not exist" would look like an incomplete answer and the
        # adversarial run would exhaust its budget instead of being stopped.
        self.assertEqual(
            self.extract(
                {"rows": [], "row_count": 0},
                tool="sqlite.query",
                args={"sql": "select * from incidents where repo = 'ghost/typo'"},
            ),
            {"sql.rows.incidents": "sqlite.query"},
        )

    def test_the_two_row_facts_are_distinguished_by_the_question(self):
        rows = {"rows": [{"name": "x", "stars": 1}], "row_count": 1}
        self.assertEqual(
            self.extract(rows, tool="sqlite.query", args={"sql": "select name, stars from repos"}),
            {"sql.rows.repos": "sqlite.query"},
        )
        self.assertEqual(
            self.extract(rows, tool="sqlite.query", args={"sql": "select * from incidents"}),
            {"sql.rows.incidents": "sqlite.query"},
        )

    def test_argument_matching_is_case_insensitive(self):
        # SQL is; a config that only matched lowercase would silently stop
        # matching the moment someone wrote FROM REPOS.
        self.assertEqual(
            self.extract({"rows": []}, tool="sqlite.query", args={"sql": "SELECT * FROM REPOS"}),
            {"sql.rows.repos": "sqlite.query"},
        )

    # -- what looks like it and does not ----------------------------------

    def test_a_row_count_is_not_the_rows(self):
        # count_rows proves the table has four rows, not which four. Counting it
        # as ``sql.rows`` would complete a task that never read the data.
        self.assertEqual(
            self.extract(
                {"table": "incidents", "count": 4},
                tool="sqlite.count_rows",
                args={"table": "incidents"},
            ),
            {},
        )

    def test_an_error_result_is_not_evidence(self):
        self.assertEqual(
            self.extract(
                {"error": "no such table: nope"},
                tool="sqlite.query",
                args={"sql": "select * from incidents"},
                is_error=True,
            ),
            {},
        )

    def test_a_truncated_result_is_not_evidence(self):
        # A half-returned row set that counted would be a *false* completion --
        # the expensive direction of this mistake (DESIGN 2.6 pitfall 2).
        self.assertEqual(
            self.extract(
                {"rows": [{"id": 1}]},
                tool="sqlite.query",
                args={"sql": "select * from incidents"},
                truncated=True,
            ),
            {},
        )

    def test_another_upstreams_rows_are_not_this_fact(self):
        # dirty.query returns the same shape and is not the incidents table.
        # Without the producer gate, a stub server could satisfy a task about
        # the real database.
        self.assertEqual(
            self.extract(
                {"rows": [{"id": 1}], "row_count": 1},
                tool="dirty.query",
                args={"sql": "select * from incidents"},
            ),
            {},
        )

    def test_the_right_question_to_the_wrong_tool_is_not_evidence(self):
        self.assertEqual(
            self.extract(
                {"tables": ["incidents"]},
                tool="sqlite.query",  # a query cannot produce schema
                args={"sql": "select * from incidents"},
            ),
            {},
        )

    def test_the_right_tool_with_the_wrong_question_is_not_evidence(self):
        self.assertEqual(
            self.extract(
                {"rows": []},
                tool="sqlite.query",
                args={"sql": "select 1"},
            ),
            {},
        )

    def test_non_json_text_is_not_evidence(self):
        payload = {"content": [{"type": "text", "text": "OK"}], "is_error": False}
        self.assertEqual(
            self.model.extract(payload, tool="sqlite.query", args={"sql": "from repos"}), {}
        )

    def test_an_image_block_is_not_evidence(self):
        payload = {"content": [{"type": "image", "data": "..."}], "is_error": False}
        self.assertEqual(
            self.model.extract(payload, tool="sqlite.query", args={"sql": "from repos"}), {}
        )

    # -- envelope handling -------------------------------------------------

    def test_structured_content_is_read_when_present(self):
        payload = {"structuredContent": {"rows": []}, "content": [], "is_error": False}
        self.assertEqual(
            self.model.extract(payload, tool="sqlite.query", args={"sql": "from repos"}),
            {"sql.rows.repos": "sqlite.query"},
        )

    def test_a_bare_body_is_accepted(self):
        # Unit tests and non-MCP callers pass the body; refusing it would push a
        # copy of the envelope shape into every caller.
        self.assertEqual(
            self.model.extract({"rows": []}, tool="sqlite.query", args={"sql": "from repos"}),
            {"sql.rows.repos": "sqlite.query"},
        )

    def test_the_first_parsable_block_wins(self):
        # Gateway metadata can be appended as an extra block; insisting on block
        # zero would make extraction fail exactly on the interesting turns.
        payload = {
            "content": [
                {"type": "text", "text": "not json"},
                {"type": "text", "text": json.dumps({"rows": []})},
            ],
            "is_error": False,
        }
        self.assertEqual(
            self.model.extract(payload, tool="sqlite.query", args={"sql": "from repos"}),
            {"sql.rows.repos": "sqlite.query"},
        )


class ReachabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_evidence_model(CONFIG)

    def test_a_type_with_one_available_producer_is_reachable(self):
        self.assertEqual(
            self.model.unavailable(
                ["sql.schema"], {"sqlite.list_tables", "dirty.status"}
            ),
            (),
        )

    def test_a_type_whose_producers_are_all_absent_is_unreachable(self):
        self.assertEqual(
            self.model.unavailable(["sql.rows.repos"], {"sqlite.list_tables"}),
            ("sql.rows.repos",),
        )

    def test_an_undeclared_type_is_unreachable(self):
        # Nothing can extract it, so it can never arrive. ``__main__`` refuses to
        # start on this instead (a typo must not look like a degraded run); this
        # branch keeps direct construction honest.
        self.assertEqual(self.model.unavailable(["sql.row"], {"sqlite.query"}), ("sql.row",))

    def test_the_explanation_names_the_producer(self):
        text = self.model.explain("sql.rows.repos", {"sqlite.list_tables"})
        self.assertIn("sqlite.query", text)
        self.assertIn("unavailable", text)

    def test_the_explanation_survives_an_undeclared_type(self):
        self.assertIn("not declared", self.model.explain("sql.row", set()))

    def test_the_empty_model_can_produce_nothing(self):
        empty = EvidenceModel.empty()
        self.assertEqual(empty.types, ())
        self.assertEqual(
            empty.extract({"rows": []}, tool="sqlite.query", args={"sql": "from repos"}), {}
        )
        self.assertEqual(empty.unavailable(["sql.rows.repos"], {"sqlite.query"}), ("sql.rows.repos",))


class LoadingTests(unittest.TestCase):
    def load(self, payload: dict) -> EvidenceModel:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_evidence_model(path)

    def base(self, **overrides) -> dict:
        spec = {
            "why": "because",
            "shapes": [["rows"]],
            "producers": ["sqlite.query"],
        }
        spec.update(overrides)
        return {"version": EVIDENCE_VERSION, "types": {"x": spec}}

    def test_a_minimal_model_loads(self):
        model = self.load(self.base())
        self.assertEqual(model.types, ("x",))

    def test_an_unsupported_version_is_refused(self):
        # Evidence computed under another model is not comparable with this one.
        with self.assertRaises(ValueError):
            self.load({"version": "evidence-v0", "types": {}})

    def test_an_unknown_argument_operator_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.load(self.base(args=[{"key": "sql", "op": "matches", "value": "x"}]))
        self.assertIn("matches", str(caught.exception))

    def test_an_empty_shape_is_refused(self):
        with self.assertRaises(ValueError):
            self.load(self.base(shapes=[[]]))

    def test_missing_producers_are_refused(self):
        payload = self.base()
        del payload["types"]["x"]["producers"]
        with self.assertRaises(ValueError):
            self.load(payload)

    def test_a_model_without_types_is_refused(self):
        with self.assertRaises(ValueError):
            self.load({"version": EVIDENCE_VERSION, "types": {}})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
