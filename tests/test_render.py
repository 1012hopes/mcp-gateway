"""The replay view: statuses, the diff, the R4 highlight, and escaping.

Everything here is a pure function of a record list, so these tests need no
SDK, no subprocess and no server -- which is the point of keeping the renderer
free of the gateway runtime. The live half (a real governed run, a real
tampered copy, the CLI) is ``scripts/replay_demo.py``.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from gateway.trace.record import StopCheck, TraceRecord, genesis_hash
from gateway.trace.render import (
    STATUS_AFTER,
    STATUS_BAD,
    STATUS_OK,
    Replay,
    Step,
    load_replay,
    render_html,
    render_path,
)

RUN_ID = "run-render-001"


def _record(seq: int, prev: TraceRecord | None = None, **overrides) -> TraceRecord:
    kwargs = dict(
        seq=seq,
        run_id=RUN_ID,
        session_id="sess-render",
        turn=seq,
        model_id="stub-model",
        gateway_version="0.6.0",
        rules_version="rules-v1",
        offered=["sqlite.query", "sqlite.count_rows"],
        chosen="sqlite.query",
        args_hash="a" * 64,
        result_digest="b" * 64,
        decision="forward",
        state="RUNNING",
        latency_ms=10,
        prev_hash=prev.hash if prev is not None else genesis_hash(RUN_ID),
    )
    kwargs.update(overrides)
    return TraceRecord(**kwargs).seal()


def _write(records, directory: Path, name: str = "run.jsonl") -> Path:
    path = directory / name
    path.write_text(
        "".join(r.to_json_line() + "\n" for r in records), encoding="utf-8"
    )
    return path


def _replay_of(records) -> Replay:
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(records, Path(tmp))
        return load_replay(path)


def _cards(html: str):
    return re.findall(r"<article\b.*?</article>", html, re.S)


class StatusTests(unittest.TestCase):
    """A break marks one record red and the rest *unverified* -- not green."""

    def test_an_intact_chain_is_all_ok(self):
        first = _record(1)
        replay = _replay_of([first, _record(2, first), _record(3, _record(2, first))])
        self.assertTrue(replay.verdict.ok)
        self.assertEqual([s.status for s in replay.steps], [STATUS_OK] * 3)

    def test_the_first_bad_line_is_the_only_bad_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = _record(1)
            second = _record(2, first)
            third = _record(3, second)
            path = _write([first, second, third], directory)

            lines = path.read_text(encoding="utf-8").splitlines()
            lines[1] = lines[1].replace('"latency_ms":10', '"latency_ms":11')
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            replay = load_replay(path)

        self.assertFalse(replay.verdict.ok)
        self.assertEqual(
            [s.status for s in replay.steps], [STATUS_OK, STATUS_BAD, STATUS_AFTER]
        )
        self.assertIn("hash_mismatch", replay.steps[1].error or "")

    def test_the_bad_record_is_rendered_red_and_later_ones_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = _write([_record(1), _record(2, _record(1))], directory)
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[0] = lines[0].replace('"latency_ms":10', '"latency_ms":99')
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            replay = load_replay(path)

        cards = _cards(render_html(replay))
        self.assertIn("status-bad", cards[0])
        self.assertNotIn("status-bad", cards[1])
        self.assertIn("status-after", cards[1])
        self.assertIn("hash_mismatch", cards[0])

    def test_a_line_that_is_not_json_still_gets_a_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            good = _record(1).to_json_line()
            path.write_text(good + "\n{not json\n", encoding="utf-8")
            replay = load_replay(path)

        self.assertEqual(len(replay.steps), 2)
        broken = replay.steps[1]
        self.assertTrue(broken.parse_failed)
        self.assertEqual(broken.status, STATUS_BAD)
        html = render_html(replay)
        self.assertIn("{not json", html)
        self.assertIn("无法解析", html)

    def test_an_empty_file_renders_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            replay = load_replay(path)

        self.assertEqual(replay.steps, [])
        self.assertFalse(replay.verdict.ok)
        self.assertIn("没有任何", render_html(replay))

    def test_crlf_files_keep_the_verifier_s_line_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = _write([_record(1), _record(2, _record(1))], directory)
            text = path.read_text(encoding="utf-8").replace("\n", "\r\n")
            path.write_bytes(text.encode("utf-8"))
            replay = load_replay(path)

        self.assertTrue(replay.verdict.ok)
        self.assertEqual([s.line for s in replay.steps], [1, 2])


class DiffTests(unittest.TestCase):
    """The offered/chosen difference is stated in words, not implied."""

    def test_a_chosen_tool_inside_the_window(self):
        replay = _replay_of([_record(1)])
        html = render_html(replay)
        self.assertIn("调用的是其中之一", html)
        self.assertIn("展示了但没选：1 个", html)
        self.assertEqual(html.count('class="picked"'), 1)

    def test_a_call_outside_the_window_says_so(self):
        first = _record(1)
        outside = _record(2, first, chosen="wiki.search", decision="forward_outside")
        html = render_html(_replay_of([first, outside]))
        self.assertIn("不在其中", html)
        self.assertIn("forward_outside", html)
        self.assertIn("窗口外", html)

    def test_the_number_of_offered_tools_is_printed(self):
        offered = [f"tool.{i}" for i in range(30)]
        replay = Replay(
            source="synthetic",
            steps=[
                Step(
                    line=1,
                    data={
                        "seq": 1,
                        "offered": offered,
                        "chosen": "tool.3",
                        "decision": "forward",
                        "state": "RUNNING",
                    },
                )
            ],
        )
        html = render_html(replay)
        self.assertIn("展示了 30 个工具", html)
        self.assertIn("展示了但没选：29 个", html)

    def test_evidence_accumulates_across_steps(self):
        first = _record(1, evidence_added=["sql.schema"])
        second = _record(2, first, evidence_added=["sql.rows.repos"])
        replay = _replay_of([first, second])
        cards = _cards(render_html(replay))
        self.assertIn("到此累计（1 项）", cards[0])
        self.assertIn("到此累计（2 项）", cards[1])
        self.assertIn("sql.rows.repos", cards[1])


class R4HighlightTests(unittest.TestCase):
    """Only the step that actually fired R4 gets the accent colour."""

    def _html(self, **stop):
        records = [_record(1), _record(2, _record(1), **stop)]
        return _replay_of(records), render_html(_replay_of(records))

    def test_the_completing_step_is_marked(self):
        replay, html = self._html(
            decision="halt_evidence_complete",
            state="HALTED_COMPLETE",
            stop_check=StopCheck(rule="R4", hit=True, evidence=["sql.schema"]),
        )
        r4 = replay.r4_step
        self.assertIsNotNone(r4)
        cards = _cards(html)
        self.assertIn("is-r4", cards[-1])
        self.assertIn("R4 证据完备", cards[-1])
        self.assertNotIn("is-r4", cards[0])

    def test_a_rule_that_was_evaluated_but_did_not_fire_is_not_highlighted(self):
        replay, html = self._html(
            stop_check=StopCheck(rule="R4", hit=False, missing=["sql.rows.repos"])
        )
        self.assertIsNone(replay.r4_step)
        # The stylesheet always contains `.card.is-r4`, so the assertion has to
        # look at elements: a bare `assertNotIn("is-r4", html)` passes or fails
        # depending on whether a CSS rule exists, which is not the claim.
        self.assertEqual([c for c in _cards(html) if "is-r4" in c], [])
        self.assertIn("这条轨迹里没有 R4 命中", html)
        # The rule still shows up as having been *evaluated* -- a different fact
        # from having fired, and the one that explains why the run continued.
        self.assertIn("R4 已评估 · 未命中", html)
        self.assertNotIn('badge-rule">R4 命中', html)
        self.assertIn("sql.rows.repos", html)


class SafetyTests(unittest.TestCase):
    """Trace fields are upstream text; they must never become markup."""

    def test_hostile_fields_are_escaped(self):
        payload = '<script>alert("x")</script>'
        replay = Replay(
            source="synthetic",
            steps=[
                Step(
                    line=1,
                    data={
                        "seq": 1,
                        "run_id": "run-xss",
                        "offered": [payload],
                        "chosen": payload,
                        "decision": payload,
                        "state": "RUNNING",
                        "state_hash": payload,
                    },
                )
            ],
        )
        html = render_html(replay)
        self.assertNotIn(payload, html)
        self.assertIn("&lt;script&gt;", html)

    def test_no_external_reference_is_emitted(self):
        html = render_html(_replay_of([_record(1)]))
        self.assertNotRegex(html, r"<script[^>]+\bsrc=")
        self.assertNotIn("<link", html)
        self.assertNotIn("@import", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_rendering_is_deterministic(self):
        replay = _replay_of([_record(1), _record(2, _record(1))])
        self.assertEqual(render_html(replay), render_html(replay))
        self.assertNotRegex(render_html(replay), r"\b20\d\d-\d\d-\d\dT")


class EvidenceBandTests(unittest.TestCase):
    """The bottom band reconstructs ``required`` from the trace alone."""

    def _html(self, **stop):
        first = _record(1)
        return render_html(_replay_of([first, _record(2, first, **stop)]))

    def test_all_facts_lit_when_nothing_is_missing(self):
        html = self._html(
            stop_check=StopCheck(
                rule="R4", hit=True, evidence=["sql.schema", "sql.rows.repos"]
            )
        )
        self.assertIn('class="band"', html)
        self.assertIn('class="lit"', html)
        self.assertNotIn('class="dim"', html)
        self.assertIn("2/2", html)
        self.assertIn("齐了", html)

    def test_a_missing_fact_is_one_dim_chip(self):
        html = self._html(
            stop_check=StopCheck(
                rule="R4", hit=False, evidence=["sql.schema"], missing=["sql.rows.repos"]
            )
        )
        self.assertEqual(html.count('class="dim"'), 1)
        self.assertIn("1/2", html)
        self.assertIn("缺 1 项", html)

    def test_no_post_rule_means_no_band(self):
        # A v1 record (or a run stopped by a pre rule) has no evidence to show.
        # Rendering an empty band would suggest "nothing was required".
        html = self._html()
        self.assertNotIn('class="band"', html)


class OutputTests(unittest.TestCase):
    def test_render_path_writes_next_to_the_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write([_record(1)], Path(tmp))
            out = render_path(path)
            self.assertEqual(out.name, "run.html")
            self.assertIn("<article", out.read_text(encoding="utf-8"))

    def test_an_explicit_target_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = _write([_record(1)], directory)
            out = render_path(path, directory / "nested" / "page.html")
            self.assertTrue(out.exists())

    def test_the_title_is_escaped_too(self):
        replay = _replay_of([_record(1)])
        html = render_html(replay, title="<b>pwn</b>")
        self.assertNotIn("<b>pwn</b>", html)
        self.assertIn("&lt;b&gt;pwn&lt;/b&gt;", html)


if __name__ == "__main__":
    unittest.main()
