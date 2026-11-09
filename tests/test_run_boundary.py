"""D10: where one run ends and the next begins -- declared, never inferred (FR-12).

The stake is a false halt, and it comes in two shapes that look identical in a
trace and have nothing in common as causes:

* **False completion.** The previous topic's facts are still in the evidence set,
  so ``required <= evidence`` holds before the new topic has read anything.
* **False no-progress.** The first answer of the new topic repeats the last answer
  of the old one, the state hash does not move, and R2 halts a healthy run on
  step one.

Neither is detectable from tool calls, because "the topic changed" is a fact
about the caller's intent. So it is declared -- and the first half of this file
is about *parsing* that declaration strictly enough that a typo cannot be
mistaken for silence, while the second half is about the fork itself: what a
follow-up keeps and what a new topic must not.

The counterfactual is tested rather than described (``WithoutBoundaryTests``):
the same two calls, without the declaration, really do produce a
``HALTED_DUPLICATE``. A test that only showed the declared case would pass
against a gateway that never halts at all.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import mcp  # noqa: F401

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

from gateway.boundary import Declaration, DeclarationError, parse_declaration  # noqa: E402
from gateway.limits import CallSignature  # noqa: E402
from gateway.session import Budget, RunSession  # noqa: E402
from gateway.trace.sink import JsonlSink  # noqa: E402

if HAS_MCP:
    from mcp import types

    from gateway.config import GatewayConfig
    from gateway.evidence import load_evidence_model
    from gateway.registry import RawTool
    from gateway.rules import DuplicateCall, EvidenceComplete
    from gateway.stop import StopController
    from gateway.transport import Gateway
    from gateway.wire import KIND_HALT, parse_notice


def _records(path: Path) -> list:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class DeclarationParsingTests(unittest.TestCase):
    """No SDK needed: this is a parser over a dict."""

    def test_no_meta_is_no_declaration(self):
        for params in ({}, {"name": "x"}, {"_meta": {}}, {"_meta": {"other": 1}}):
            with self.subTest(params=params):
                self.assertFalse(parse_declaration(params).declared)

    def test_a_task_declaration_is_read(self):
        decl = parse_declaration({"_meta": {"gateway": {"task": "t01", "inherit": True}}})
        self.assertEqual(decl.task, "t01")
        self.assertTrue(decl.inherit)

    def test_require_is_normalized_to_a_tuple(self):
        # The list comes off the wire; tuples are what the session compares
        # against, and a list would make two otherwise equal declarations unequal.
        decl = parse_declaration(
            {"_meta": {"gateway": {"task": "a", "require": [" sql.schema "]}}}
        )
        self.assertEqual(decl.require, ("sql.schema",))

    def test_a_repeated_declaration_is_not_a_boundary(self):
        # Load-bearing: a stateless client attaches the same declaration to every
        # request, so if a repeat forked, every call would start a new run.
        decl = Declaration(task="t01")
        self.assertFalse(decl.is_boundary("t01"))
        self.assertTrue(decl.is_boundary(None), "nothing declared yet is a change")

    def test_an_undeclared_request_is_never_a_boundary(self):
        self.assertFalse(Declaration().is_boundary(None))
        self.assertFalse(Declaration().is_boundary("t01"))

    def test_a_changed_task_is_a_boundary(self):
        self.assertTrue(Declaration(task="t02").is_boundary("t01"))
        self.assertTrue(Declaration(task="t01").is_boundary(None))

    def test_an_unknown_key_is_rejected(self):
        # ``inhert`` would otherwise be dropped and the caller would believe it
        # had declared an inheritance it never got -- a false halt with no cause.
        with self.assertRaises(DeclarationError) as caught:
            parse_declaration({"_meta": {"gateway": {"task": "a", "inhert": True}}})
        self.assertIn("inhert", str(caught.exception))

    def test_inherit_without_a_task_is_rejected(self):
        with self.assertRaises(DeclarationError):
            parse_declaration({"_meta": {"gateway": {"inherit": True}}})

    def test_a_bare_string_require_is_rejected(self):
        # A string would iterate into characters and silently declare nothing.
        with self.assertRaises(DeclarationError):
            parse_declaration({"_meta": {"gateway": {"task": "a", "require": "sql.schema"}}})

    def test_an_empty_task_is_rejected(self):
        for task in ("", "   ", 7, []):
            with self.subTest(task=task):
                with self.assertRaises(DeclarationError):
                    parse_declaration({"_meta": {"gateway": {"task": task}}})

    def test_a_non_bool_inherit_is_rejected(self):
        with self.assertRaises(DeclarationError):
            parse_declaration({"_meta": {"gateway": {"task": "a", "inherit": "yes"}}})

    def test_a_non_object_declaration_is_rejected(self):
        with self.assertRaises(DeclarationError):
            parse_declaration({"_meta": {"gateway": "t01"}})


class ForkTests(unittest.TestCase):
    """``RunSession.next_run``: what a follow-up keeps, what a new topic drops."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.session = RunSession(
            sink=JsonlSink(self.dir / "parent.jsonl", run_id="run-parent"),
            run_id="run-parent",
            session_id="sess-1",
            task_id="t01",
            budget=Budget(max_calls=4),
            required={"sql.schema", "sql.rows.repos"},
            evidence={"sql.schema": "sqlite.list_tables", "sql.rows.repos": "sqlite.query"},
            history=[CallSignature("sqlite.query", "abc")],
            result_digests=["d1", "d2"],
            state_trail=["h1"],
        )
        self.session.records_written = 3
        self.session.results_delivered = 3
        self.session.turn = 3

    def tearDown(self) -> None:
        # Windows refuses to remove a directory whose file is still open.
        self.session.sink.close()

    def test_a_fork_gets_its_own_file_and_run_id(self):
        child = self.session.next_run(task_id="t02", inherit=False)
        self.assertNotEqual(child.run_id, self.session.run_id)
        self.assertNotEqual(child.trace_path, self.session.trace_path)
        self.assertEqual(child.trace_path.parent, self.session.trace_path.parent)
        child.sink.close()

    def test_the_follow_up_inherits_the_evidence_and_names_its_parent(self):
        child = self.session.next_run(task_id="t01-followup", inherit=True)
        self.assertEqual(child.parent_run_id, self.session.run_id)
        self.assertEqual(child.evidence, self.session.evidence)
        child.sink.close()

    def test_a_topic_change_starts_empty_and_has_no_parent(self):
        child = self.session.next_run(task_id="t02", inherit=False)
        self.assertIsNone(child.parent_run_id, "a new topic does not continue a run")
        self.assertEqual(child.evidence, {}, "the previous topic's facts must not leak")
        child.sink.close()

    def test_this_runs_activity_is_always_reset(self):
        for inherit in (True, False):
            with self.subTest(inherit=inherit):
                child = self.session.next_run(task_id="t02", inherit=inherit)
                self.assertEqual(child.history, [])
                self.assertEqual(child.result_digests, [])
                self.assertEqual(child.state_trail, [])
                self.assertEqual(child.records_written, 0)
                self.assertEqual(child.results_delivered, 0)
                self.assertEqual(child.turn, 1)
                self.assertEqual(child.state, "RUNNING")
                child.sink.close()

    def test_the_session_id_survives(self):
        # One conversation, several runs: the grouping must not depend on
        # guessing from filenames or timestamps.
        child = self.session.next_run(task_id="t02", inherit=False)
        self.assertEqual(child.session_id, self.session.session_id)
        child.sink.close()

    def test_required_is_replaced_not_merged(self):
        child = self.session.next_run(
            task_id="t03", inherit=True, required=("sql.rows.incidents",)
        )
        self.assertEqual(child.required, {"sql.rows.incidents"})
        child.sink.close()

    def test_required_defaults_to_the_parents(self):
        child = self.session.next_run(task_id="t02", inherit=True)
        self.assertEqual(child.required, self.session.required)
        child.sink.close()

    def test_the_boundary_is_auditable_from_the_two_files(self):
        """The inheritance claim must be derivable, not asserted.

        Child record: parent id + inherited evidence. Parent file: the record
        that produced that evidence. Nothing needs an ``inherited_evidence``
        field -- a second copy of a fact the chain already proves is a copy that
        can drift.
        """
        child = self.session.next_run(task_id="t01-followup", inherit=True)
        child.record_call(
            offered=["sqlite.count_rows"],
            chosen="sqlite.count_rows",
            args={"table": "repos"},
            result={"count": 5},
            latency_ms=1,
        )
        child.sink.close()

        record = _records(child.trace_path)[0]
        self.assertEqual(record["parent_run_id"], self.session.run_id)
        self.assertEqual(record["task_id"], "t01-followup")
        # The parent run's own id is on disk, so a reader can open its file and
        # see where `sql.rows.repos` came from.
        self.assertTrue(self.session.evidence["sql.rows.repos"])


class _GatewayMixin:
    """One gateway, one fake upstream, calls driven in-process."""

    rules: tuple = (DuplicateCall(),)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.upstream = _FakeUpstream()
        self.evidence = load_evidence_model(ROOT / "config" / "evidence.json")
        self.session = RunSession(
            sink=JsonlSink(self.dir / "first.jsonl", run_id="run-first"),
            run_id="run-first",
            session_id="sess-test",
            budget=Budget(max_calls=10),
        )
        self.gateway = Gateway(
            GatewayConfig(),
            [self.upstream],
            self.session,
            controller=StopController(self.rules),
            evidence=self.evidence,
        )

    def tearDown(self) -> None:
        # Windows will not remove a directory whose file is still open, and a
        # boundary closes its predecessor's sink but never the current one.
        self.gateway.session.sink.close()

    def calls(self, plan):
        """``plan`` is a list of ``(args, meta)``; returns the results."""

        async def scenario():
            out = []
            for args, meta in plan:
                params = types.CallToolRequestParams(
                    name="sqlite.query", arguments=args, meta=meta
                )
                out.append(await self.gateway.handle_call_tool(None, params))
            return out

        return asyncio.run(scenario())

    def all_records(self):
        return {path.name: _records(path) for path in sorted(self.dir.glob("*.jsonl"))}


class _FakeUpstream:
    name = "sqlite"

    def __init__(self) -> None:
        self.calls: list = []

    async def list_tools(self, *, refresh: bool = False) -> list:
        return [
            RawTool(
                upstream="sqlite",
                name="query",
                description="run a select",
                input_schema={"type": "object", "properties": {}},
            ),
            RawTool(
                upstream="sqlite",
                name="count_rows",
                description="count rows",
                input_schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, original_name: str, arguments):
        self.calls.append((original_name, dict(arguments or {})))
        return types.CallToolResult(
            content=[types.TextContent(type="text", text='{"rows": [{"id": 1}]}')]
        )


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class WithoutBoundaryTests(_GatewayMixin, unittest.TestCase):
    """The counterfactual: no declaration, so the second topic's first call is a duplicate."""

    def test_the_same_call_produces_a_false_duplicate(self):
        meta = None
        results = self.calls(
            [
                ({"sql": "select name from repos"}, meta),
                ({"sql": "select name from repos"}, meta),
            ]
        )
        self.assertEqual(self.session.state, "HALTED_DUPLICATE")
        # R1 answers a repeat with the earlier result rather than an error; the
        # halt is what a reviewer reads off the notice and the record.
        self.assertEqual(parse_notice(results[1].content[-1].text)["kind"], KIND_HALT)
        self.assertEqual(len(self.all_records()), 1, "one process, one run, one file")

    def test_the_second_topic_leaks_the_first_topics_evidence(self):
        # The other half of the false halt: facts carry over because nothing
        # declared that they should not.
        self.calls([({"sql": "select name from repos"}, None)])
        self.assertIn("sql.rows.repos", self.session.evidence)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class WithBoundaryTests(_GatewayMixin, unittest.TestCase):
    rules = (DuplicateCall(), EvidenceComplete())

    def test_a_declared_task_change_starts_a_new_run(self):
        results = self.calls(
            [
                ({"sql": "select name from repos"}, {"gateway": {"task": "t01"}}),
                ({"sql": "select name from repos"}, {"gateway": {"task": "t02"}}),
            ]
        )
        self.assertFalse(any(r.is_error for r in results))
        records = self.all_records()
        self.assertEqual(len(records), 2, "one file per run")
        for path, lines in records.items():
            self.assertEqual(len(lines), 1, path)

    def test_the_new_run_carries_its_declaration_into_the_trace(self):
        self.calls(
            [
                ({"sql": "select 1"}, {"gateway": {"task": "t01"}}),
                ({"sql": "select 2"}, {"gateway": {"task": "t02"}}),
            ]
        )
        tasks = sorted(line["task_id"] for lines in self.all_records().values() for line in lines)
        self.assertEqual(tasks, ["t01", "t02"])

    def test_a_repeated_declaration_does_not_fork(self):
        decl = {"gateway": {"task": "t01"}}
        self.calls([({"sql": "select 1"}, decl), ({"sql": "select 2"}, decl)])
        self.assertEqual(len(self.all_records()), 1)
        self.assertEqual(self.session.records_written, 2)

    def test_a_new_topic_relearns_what_the_old_one_knew(self):
        self.calls(
            [
                ({"sql": "select name from repos"}, {"gateway": {"task": "t01"}}),
                ({"sql": "select name from repos"}, {"gateway": {"task": "t02"}}),
            ]
        )
        lines = [line for lines in self.all_records().values() for line in lines]
        added = sorted(tuple(line["evidence_added"]) for line in lines)
        self.assertEqual(
            added,
            [("sql.rows.repos",), ("sql.rows.repos",)],
            "each topic had to read the rows itself",
        )

    def test_a_follow_up_inherits_and_completes_without_reading_again(self):
        self.calls(
            [
                (
                    {"sql": "select name from repos"},
                    {"gateway": {"task": "t01"}},
                ),
                (
                    {"sql": "select 1"},
                    {
                        "gateway": {
                            "task": "t01-followup",
                            "inherit": True,
                            "require": ["sql.rows.repos"],
                        }
                    },
                ),
            ]
        )
        follow = [
            line
            for lines in self.all_records().values()
            for line in lines
            if line.get("task_id") == "t01-followup"
        ][0]
        self.assertEqual(follow["state"], "HALTED_COMPLETE")
        self.assertEqual(follow["stop_check"]["missing"], [], "the fact was inherited")
        self.assertEqual(
            follow["evidence_added"], [], "a follow-up that already knows must not re-read"
        )

    def test_a_bad_declaration_is_refused_but_recorded(self):
        results = self.calls(
            [
                (
                    {"sql": "select 1"},
                    {"gateway": {"task": "t01", "require": ["sql.rows.nope"]}},
                )
            ]
        )
        self.assertTrue(results[0].is_error)
        self.assertIn("sql.rows.nope", results[0].content[0].text)
        records = list(self.all_records().values())[0]
        self.assertEqual(records[0]["decision"], "block_declaration")
        self.assertEqual(self.session.state, "RUNNING", "a typo does not end the run")

    def test_a_declaration_is_served_even_after_the_run_halted(self):
        # Otherwise the boundary feature would only work for runs that did not
        # need it: a halted run refuses every call, including a new topic's.
        self.calls([({"sql": "select 1"}, {"gateway": {"task": "t01"}})])
        self.gateway.session.halt("HALTED_BUDGET", rule="R3", reason="test")
        results = self.calls([({"sql": "select 2"}, {"gateway": {"task": "t02"}})])
        self.assertFalse(results[0].is_error, "the new topic starts in a fresh run")
        self.assertEqual(self.gateway.session.state, "RUNNING")
        self.assertEqual(self.gateway.session.task_id, "t02")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
