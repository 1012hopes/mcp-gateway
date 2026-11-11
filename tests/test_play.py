"""Tests for the one page that runs something.

Three groups, and the split matters:

* ``BuildStoryTests`` -- pure. A fake run in, a story out. No SDK, no
  subprocess, no disk. This is where the frame contract is pinned: which fields
  the page reads, what the repeat marker means, and what counts as a stop.
* ``RenderTests`` -- the shell. Options, escaping, empty states.
* ``HttpTests`` -- refusals only. The success path spawns a real gateway and
  takes eight seconds; asserting it here would make the suite depend on a
  machine's timing. ``scripts/agent_demo.py`` is where that belongs.
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from gateway.trace.play import build_story, render_play
from gateway.trace.serve import make_server

TOKEN = "0123456789abcdef0123456789abcdef"


class _Turn:
    def __init__(self, tool, args=None, blocks=("{}",), is_error=False):
        self.tool = tool
        self.args = args or {}
        self.blocks = blocks
        self.is_error = is_error


class _Run:
    """The handful of ``RunResult`` fields ``build_story`` touches."""

    def __init__(self, turns, visible, tools, *, halt=None, stop="policy_exhausted"):
        self.turns = turns
        self.visible = visible
        self.tools = tools
        self.halt = halt
        self.stop_reason = stop
        self.trace_path = Path("traces/play/play-t01-abcd.jsonl")

    @property
    def steps(self):
        return len(self.turns)


def _task(required=("sql.schema", "sql.rows.incidents"), optimal=3):
    return SimpleNamespace(
        id="t01",
        goal="List every unresolved incident",
        optimal_steps=optimal,
        required_evidence=tuple(required),
    )


def _record(evidence=(), missing=(), rule="", hit=False, decision="forward"):
    return {
        "stop_check": {
            "evidence": list(evidence),
            "missing": list(missing),
            "rule": rule or None,
            "hit": hit,
        },
        "decision": decision,
    }


ALL = tuple(f"tool.{index}" for index in range(30))


class BuildStoryTests(unittest.TestCase):
    def test_one_frame_per_turn_and_the_order_is_the_call_order(self):
        run = _Run(
            [_Turn("sqlite.list_tables"), _Turn("sqlite.query", {"sql": "select 1"})],
            [ALL, ALL[:5]],
            ALL,
        )
        story = build_story(run, [_record(), _record()], task=_task(), rules=True)
        self.assertEqual([f["n"] for f in story["frames"]], [1, 2])
        self.assertEqual([f["tool"] for f in story["frames"]], ["sqlite.list_tables", "sqlite.query"])
        self.assertEqual(story["steps"], 2)

    def test_the_window_comes_from_what_the_agent_was_shown(self):
        run = _Run([_Turn("a"), _Turn("b")], [ALL, ALL[:5]], ALL)
        story = build_story(run, [_record(), _record()], task=_task(), rules=True)
        self.assertEqual(len(story["frames"][0]["window"]), 30)
        self.assertEqual(len(story["frames"][1]["window"]), 5)
        self.assertEqual(story["total_tools"], 30)

    def test_evidence_progress_is_read_from_the_sealed_record_not_the_turn(self):
        # The turn list says what was called; only the trace says what the
        # gateway concluded. Mixing them would let the agent narrate its own
        # compliance.
        run = _Run([_Turn("a"), _Turn("b")], [ALL, ALL], ALL)
        records = [
            _record(evidence=["sql.schema"], missing=["sql.rows.incidents"]),
            _record(evidence=["sql.schema", "sql.rows.incidents"], missing=[]),
        ]
        story = build_story(run, records, task=_task(), rules=True)
        self.assertEqual(story["frames"][0]["missing"], ["sql.rows.incidents"])
        self.assertEqual(story["frames"][1]["missing"], [])

    def test_an_exact_repeat_names_the_step_it_repeats(self):
        # Without a gateway the agent re-issues the same query; that is the
        # "long way round" the page is supposed to make visible, and a bare
        # step count cannot show it.
        same = {"sql": "select 1", "limit": 50}
        run = _Run(
            [_Turn("sqlite.query", same), _Turn("sqlite.query", dict(same))],
            [ALL, ALL],
            ALL,
        )
        story = build_story(run, [_record(), _record()], task=_task(), rules=False)
        self.assertIsNone(story["frames"][0]["repeat_of"])
        self.assertEqual(story["frames"][1]["repeat_of"], 1)

    def test_a_different_limit_is_not_a_repeat(self):
        run = _Run(
            [
                _Turn("sqlite.query", {"sql": "select 1", "limit": 50}),
                _Turn("sqlite.query", {"sql": "select 1", "limit": 5}),
            ],
            [ALL, ALL],
            ALL,
        )
        story = build_story(run, [_record(), _record()], task=_task(), rules=False)
        self.assertIsNone(story["frames"][1]["repeat_of"])

    def test_a_repeat_is_measured_against_the_first_occurrence(self):
        same = {"sql": "select 1"}
        run = _Run([_Turn("q", same)] * 3, [ALL] * 3, ALL)
        story = build_story(run, [_record()] * 3, task=_task(), rules=False)
        self.assertEqual([f["repeat_of"] for f in story["frames"]], [None, 1, 1])

    def test_long_results_are_cut_to_one_line(self):
        run = _Run([_Turn("q", blocks=("line one\nline two\n" + "x" * 900,))], [ALL], ALL)
        story = build_story(run, [_record()], task=_task(), rules=True)
        frame = story["frames"][0]
        self.assertNotIn("\n", frame["result"])
        self.assertLessEqual(len(frame["result"]), 260)
        self.assertTrue(frame["result"].endswith("…"))

    def test_arguments_are_shown_as_json_not_as_python(self):
        run = _Run([_Turn("q", {"table": "incidents"})], [ALL], ALL)
        story = build_story(run, [_record()], task=_task(), rules=True)
        self.assertEqual(story["frames"][0]["args"], '{"table": "incidents"}')

    def test_a_rule_that_fired_says_so_and_the_run_says_complete(self):
        run = _Run(
            [_Turn("q")],
            [ALL],
            ALL,
            halt={
                "state": "HALTED_COMPLETE",
                "rule": "R4",
                "decision": "halt_evidence_complete",
                "reason": "all required evidence obtained",
            },
            stop="halted",
        )
        story = build_story(
            run,
            [_record(evidence=["sql.schema"], rule="R4", hit=True, decision="halt_evidence_complete")],
            task=_task(),
            rules=True,
        )
        self.assertTrue(story["frames"][0]["hit"])
        self.assertEqual(story["frames"][0]["rule"], "R4")
        self.assertEqual(story["verdict"]["kind"], "ok")
        self.assertIn("第 1 步", story["verdict"]["title"])

    def test_a_rule_that_fired_without_completing_is_not_called_success(self):
        run = _Run(
            [_Turn("q")],
            [ALL],
            ALL,
            halt={"state": "HALTED_NO_PROGRESS", "rule": "R2", "reason": "no new evidence"},
            stop="halted",
        )
        story = build_story(
            run, [_record(rule="R2", hit=True)], task=_task(), rules=True
        )
        self.assertEqual(story["verdict"]["kind"], "stop")
        self.assertIn("走不通", story["verdict"]["title"])
        self.assertIn("R2", story["verdict"]["detail"])

    def test_running_out_of_turns_is_reported_as_the_guard_not_an_ending(self):
        run = _Run([_Turn("q")], [ALL], ALL, stop="max_turns")
        story = build_story(run, [_record()], task=_task(), rules=False)
        self.assertEqual(story["verdict"]["kind"], "bad")
        self.assertIn("还没停", story["verdict"]["title"])

    def test_the_agent_stopping_itself_is_neither_success_nor_failure(self):
        run = _Run([_Turn("q")], [ALL], ALL, stop="policy_exhausted")
        story = build_story(run, [_record()], task=_task(), rules=False)
        self.assertEqual(story["verdict"]["kind"], "open")
        self.assertIn("自己停了", story["verdict"]["title"])

    def test_an_unanswerable_task_is_named_as_such_in_the_verdict(self):
        # t02 has no required evidence: the point of the run is that it should
        # give up, so a verdict that says "no one can vouch for it" is the
        # honest one -- there was never anything to vouch *for*.
        run = _Run([_Turn("q")], [ALL], ALL, stop="policy_exhausted")
        story = build_story(run, [_record()], task=_task(required=()), rules=False)
        self.assertIn("没人能替它背书", story["verdict"]["detail"])

    def test_missing_records_do_not_crash_the_story(self):
        # A trace can be shorter than the turn list if writing failed; the page
        # must still draw what happened rather than 500 on the last frame.
        run = _Run([_Turn("a"), _Turn("b")], [ALL, ALL], ALL)
        story = build_story(run, [_record()], task=_task(), rules=True)
        self.assertEqual(len(story["frames"]), 2)
        self.assertEqual(story["frames"][1]["evidence"], [])

    def test_the_trace_name_is_the_basename_not_a_path(self):
        run = _Run([_Turn("q")], [ALL], ALL)
        story = build_story(run, [_record()], task=_task(), rules=True)
        self.assertEqual(story["trace"], "play-t01-abcd.jsonl")


CHOICES = [
    {
        "id": "t01",
        "goal": "List every unresolved incident",
        "optimal": 3,
        "required": ["sql.schema"],
        "max_turns": 10,
        "adversarial": False,
    },
    {
        "id": "t02",
        "goal": "Find the incident for a repo that does not exist",
        "optimal": None,
        "required": [],
        "max_turns": 12,
        "adversarial": True,
    },
]


class RenderTests(unittest.TestCase):
    def test_every_task_becomes_an_option(self):
        page = render_play(choices=CHOICES, token=TOKEN)
        self.assertEqual(page.count("<option"), 2)
        self.assertIn("t01", page)
        self.assertIn("t02", page)

    def test_the_page_says_what_it_does_before_offering_the_button(self):
        page = render_play(choices=CHOICES, token=TOKEN)
        self.assertIn("亲手跑一次", page)
        self.assertIn("判停规则", page)
        self.assertIn("/api/play", page)

    def test_a_token_that_closes_the_script_tag_cannot(self):
        page = render_play(choices=CHOICES, token='"><script>alert(1)</script>')
        self.assertNotIn('"><script>alert(1)', page)
        self.assertIn("\\u003c", page)

    def test_a_task_goal_with_markup_is_escaped_in_the_option(self):
        choices = [dict(CHOICES[0], goal='<img src=x onerror="alert(1)">')]
        page = render_play(choices=choices, token=TOKEN)
        self.assertNotIn("<img src=x", page)

    def test_no_tasks_is_an_empty_menu_not_a_crash(self):
        page = render_play(choices=[], token=TOKEN)
        self.assertEqual(page.count("<option"), 0)
        self.assertIn("<select", page)

    def test_same_input_same_bytes(self):
        self.assertEqual(
            render_play(choices=CHOICES, token=TOKEN),
            render_play(choices=CHOICES, token=TOKEN),
        )


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.server = make_server(cls.root, token=TOKEN)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    def request(self, path, *, method="GET", payload=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8", "replace")
        connection.close()
        return response.status, text

    def test_the_page_is_served(self):
        status, body = self.request("/play")
        self.assertEqual(status, 200)
        self.assertIn("亲手跑一次", body)

    def test_playing_without_a_token_is_refused(self):
        status, _ = self.request("/api/play", method="POST", payload={"task": "t01"})
        self.assertEqual(status, 403)

    def test_a_wrong_token_is_refused(self):
        status, _ = self.request(
            "/api/play?token=deadbeef", method="POST", payload={"task": "t01"}
        )
        self.assertEqual(status, 403)

    def test_an_unknown_task_is_a_404_before_anything_is_spawned(self):
        # The id is looked up in tasks/*.json before the SDK import, so this
        # answers even on an interpreter with no MCP installed -- and it never
        # reaches the code that starts a process.
        status, body = self.request(
            f"/api/play?token={TOKEN}", method="POST", payload={"task": "t99"}
        )
        self.assertEqual(status, 404)
        self.assertIn("t99", json.loads(body)["error"])

    def test_a_missing_task_field_is_a_400(self):
        status, _ = self.request(f"/api/play?token={TOKEN}", method="POST", payload={})
        self.assertEqual(status, 400)

    def test_a_body_that_is_not_an_object_is_a_400(self):
        status, _ = self.request(f"/api/play?token={TOKEN}", method="POST", payload=[1])
        self.assertEqual(status, 400)

    def test_a_foreign_host_header_is_refused_on_every_path(self):
        for path in ("/play", "/api/play"):
            status, _ = self.request(path, method="GET", host="evil.example")
            self.assertEqual(status, 403, path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
