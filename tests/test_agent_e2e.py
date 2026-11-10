"""D7-D9 acceptance: every rule that is installed must fire in a real run.

The plan's warning for this stage is specific: a rule that never fires raises
nothing, prints nothing, and turns every number downstream into zero. So the
assertions here are not "the rule is correct" (``test_rules.py`` covers that with
a hand-built context) but **the hit rate is greater than zero, in a subprocess,
with the real upstream**.

Seven runs, seven claims:

* ``BaselineRun`` -- t01 with ``--no-rules``. Nine steps, and it ends because the
  policy ran out of ideas. This is the denominator the compression numbers need,
  and it must stay reproducible after the rules exist.
* ``CompleteRun`` -- t01, four steps, halted by **R4** at the exact call where
  the last required fact arrived. The delivered result is checked against the
  task, because "required ⊆ evidence" is only worth anything if the evidence
  actually answers the question (the D9 acceptance criterion).
* ``BudgetRun`` -- t01 with ``--max-calls 3``: R3 halts on the third call, and
  that call's result is still delivered, because it was paid for.
* ``DuplicateRun`` -- t01 driven by a scripted policy that repeats its first
  call: R1 refuses it before the upstream sees it and replays the earlier
  result. Scripted rather than reactive because R4 now ends the reactive run
  before any duplicate can occur -- a task that tests one rule must not
  accidentally test another.
* ``SpinBaselineRun`` -- t02 with ``--no-rules``: eleven calls, no two alike, and
  the same empty answer every time. This is the G2 adversary.
* ``SpinGovernedRun`` -- t02 with the rules: three steps, R2 halts on the second
  identical answer, and the plateau is visible as two adjacent equal
  ``state_hash`` values in the file.
* ``DegradedRun`` -- t01 against a configuration where the only upstream that can
  produce ``sql.rows.repos`` is absent: one step, ``DEGRADED``, and R6 explains
  which producer is missing instead of letting the run burn its budget.

Three more from D11, which took the task set from two tasks to twenty:

* ``MultiFactBaselineRun`` / ``MultiFactRun`` -- t10 (two facts over two tables)
  without and with the rules: ten steps become four. The second fact is the point:
  a shape-only evidence model would call this finished after the first row set.
* ``PagedSpinBaselineRun`` / ``PagedSpinRun`` -- t20, an agent paging through the
  commits of a repo that does not exist. Same ending as t02 by a different route
  (offset pagination instead of rephrasing), which is what makes "the plateau is
  the signal" a claim about a class of behaviour rather than about one script.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import mcp  # noqa: F401

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

if HAS_MCP:
    from agent import load_tasks, run_task_sync
    from agent.policy import Action, ScriptedPolicy
    from tasks.checkers import answers_from_turns, check


def _records(path: Path) -> list:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class _RunMixin:
    """Spawn one run per class; each subclass asserts a different ending.

    A mixin rather than a ``TestCase`` subclass: unittest would collect the base
    class too and spawn a run to assert the same five things an extra time.
    """

    extra_args: tuple = ()
    task_id: str = "t01"
    policy: Optional[object] = None
    #: Calls for a scripted policy, as ``(tool, args)`` pairs. Built inside
    #: ``setUpClass`` rather than in the class body on purpose: a class body runs
    #: at import time, *before* the ``skipUnless`` decorator can skip anything,
    #: so ``ScriptedPolicy(...)`` up there would make collection fail on an
    #: interpreter without the SDK -- with a ``NameError`` that says nothing
    #: about the missing dependency and defeats the guard it sits under.
    script: tuple = ()

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tasks = {task.id: task for task in load_tasks()}
        policy = cls.policy
        if policy is None and cls.script:
            policy = ScriptedPolicy([Action(tool, args) for tool, args in cls.script])
        cls.result = run_task_sync(
            tasks[cls.task_id],
            policy=policy,
            run_id=cls.__name__.lower(),
            trace_dir=Path(cls._tmp.name),
            extra_args=list(cls.extra_args),
        )
        cls.records = _records(cls.result.trace_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    # -- the same claims for every run -------------------------------------

    def test_tools_are_namespaced_and_offered(self):
        self.assertIn("sqlite.query", self.result.tools)

    def test_every_step_produced_a_record(self):
        self.assertEqual(len(self.records), self.result.steps)
        self.assertEqual(
            [r["seq"] for r in self.records], list(range(1, len(self.records) + 1))
        )

    def test_every_record_carries_the_fields_that_make_it_judgeable(self):
        for record in self.records:
            self.assertTrue(record["offered"], record["seq"])
            self.assertIn(record["chosen"], record["offered"])
            self.assertTrue(record["prev_hash"])
            self.assertTrue(record["args_hash"])
            self.assertTrue(record["result_digest"])

    def test_trace_verifies(self):
        from gateway.trace.verify import verify_path

        outcome = verify_path(self.result.trace_path)
        self.assertTrue(outcome.ok, outcome.summary())

    def test_no_result_payload_is_stored(self):
        raw = self.result.trace_path.read_text(encoding="utf-8")
        self.assertNotIn("maximhq/bifrost", raw, "traces must not become data copies")
        self.assertNotIn("severity", raw)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class BaselineRun(_RunMixin, unittest.TestCase):
    """No rules: the overshoot D6 measured, kept as the comparison floor."""

    extra_args = ("--no-rules",)

    def test_the_baseline_is_the_nine_step_overshoot(self):
        self.assertEqual(self.result.steps, 9)
        self.assertEqual(self.result.stop_reason, "policy_exhausted")

    def test_the_baseline_contains_the_duplicate_r1_targets(self):
        # Identity is (tool, args_hash), not args_hash alone: ``count_rows`` and
        # ``describe_table`` can share arguments and are not the same call.
        counts = Counter((r["chosen"], r["args_hash"]) for r in self.records)
        repeated = {key: n for key, n in counts.items() if n > 1}
        self.assertEqual(len(repeated), 1, repeated)
        self.assertEqual(list(repeated.values()), [2])

    def test_no_rule_is_installed_so_attempted_is_empty(self):
        for record in self.records:
            self.assertEqual(record["rules_version"], "rules-v0(none)")
            self.assertEqual(record["stop_check"]["attempted"], [])
            self.assertFalse(record["stop_check"]["hit"])

    def test_the_baseline_still_tracks_evidence(self):
        # The evidence model runs whether or not rules do -- what the baseline
        # lacks is anything acting on it. That separation is what lets a
        # comparison table say "same knowledge, more steps" instead of
        # attributing the compression to a different extraction.
        self.assertEqual(self.records[-1]["stop_check"]["missing"], [])
        for record in self.records:
            self.assertEqual(record["stop_check"]["attempted"], [])


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class CompleteRun(_RunMixin, unittest.TestCase):
    """R4 installed: the run stops where the task is finished, not where it gave up."""

    def test_r4_fires_and_ends_the_run(self):
        self.assertEqual(self.result.stop_reason, "halted")
        self.assertEqual(self.result.halt["rule"], "R4")
        self.assertEqual(self.result.halt["state"], "HALTED_COMPLETE")
        self.assertEqual(self.result.halt["decision"], "halt_evidence_complete")

    def test_nine_steps_become_four(self):
        # Nine without the gateway; four is the shortest run that can finish:
        # schema, incidents, repo stars -- the describe_table call is the only
        # one that adds nothing, and it is the agent's plan, not the gateway's.
        self.assertEqual(self.result.steps, 4)

    def test_the_run_stops_on_the_call_that_completes_the_evidence(self):
        last = self.records[-1]
        self.assertEqual(last["stop_check"]["missing"], [])
        self.assertEqual(last["evidence_added"], ["sql.rows.repos"])
        self.assertEqual(last["stop_check"]["evidence"], ["sql.rows.incidents", "sql.rows.repos", "sql.schema"])
        self.assertEqual(last["stop_check"]["hit"], True)

    def test_earlier_records_show_what_was_still_missing(self):
        # The progress bar the UI draws (DESIGN 2.7) is this field, not a separate
        # computation: every record says what was missing at that point.
        self.assertEqual(
            self.records[0]["stop_check"]["missing"],
            ["sql.rows.incidents", "sql.rows.repos"],
        )
        self.assertEqual(self.records[2]["stop_check"]["missing"], ["sql.rows.repos"])

    def test_the_checker_passes_on_the_delivered_result(self):
        # R4 proves the *shape* of knowledge, not its correctness. This is the
        # other half: the run stopped, and the answer it stopped on is right.
        # Since D12 it is the same checker the metric table uses, so the two
        # cannot drift into disagreeing about what "answered t01" means.
        verdict = check("t01", answers_from_turns(self.result.turns))
        self.assertTrue(verdict.ok, verdict.detail)

    def test_the_rules_version_names_the_installed_set(self):
        for record in self.records:
            self.assertEqual(record["rules_version"], "rules-v0(R1,R4,R6,R2,R3)")


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class BudgetRun(_RunMixin, unittest.TestCase):
    """R3 installed with a three-call budget: the hard stop, on a real run."""

    extra_args = ("--max-calls", "3")

    def test_r3_fires_on_the_call_that_spends_the_budget(self):
        # Three calls is not enough for this task, so completeness cannot fire
        # first -- which is the point: R3 is what remains when nothing else can
        # decide.
        self.assertEqual(self.result.steps, 3)
        self.assertEqual(self.result.stop_reason, "halted")
        self.assertEqual(self.result.halt["rule"], "R3")
        self.assertEqual(self.result.halt["state"], "HALTED_BUDGET")
        self.assertIn("max_calls=3", self.result.halt["reason"])

    def test_the_final_result_is_still_delivered(self):
        # The third call was already paid for. Dropping its result to save a call
        # that already happened would be theatre, not control.
        self.assertFalse(self.result.turns[-1].is_error)
        self.assertEqual(self.records[-1]["decision"], "halt_budget")
        self.assertEqual(self.records[-1]["state"], "HALTED_BUDGET")

    def test_early_calls_are_unaffected(self):
        for record in self.records[:-1]:
            self.assertEqual(record["decision"], "forward")
            self.assertEqual(record["state"], "RUNNING")

    def test_evidence_was_still_partial(self):
        self.assertEqual(self.records[-1]["stop_check"]["missing"], ["sql.rows.repos"])


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class DuplicateRun(_RunMixin, unittest.TestCase):
    """R1 installed: the duplicate is refused before it reaches the upstream."""

    script = (
        ("sqlite.list_tables", {}),
        ("sqlite.list_tables", {}),
        ("sqlite.query", {"sql": "select 1"}),
    )

    def test_r1_fires_before_the_upstream_is_touched(self):
        self.assertEqual(self.result.steps, 2, "the third action is never reached")
        self.assertEqual(self.result.halt["rule"], "R1")
        self.assertEqual(self.result.halt["state"], "HALTED_DUPLICATE")

    def test_the_replayed_result_is_provably_the_earlier_one(self):
        # R1 answers with the result the agent already had instead of an error.
        # Equal digests prove it is the same bytes -- the replay is auditable,
        # not merely asserted in a docstring.
        self.assertEqual(
            self.records[-1]["result_digest"], self.records[0]["result_digest"]
        )
        self.assertFalse(
            self.result.turns[-1].is_error, "a duplicate read is answered, not refused"
        )

    def test_the_second_call_was_blocked_not_forwarded(self):
        self.assertEqual(self.records[-1]["decision"], "halt_duplicate")
        # R1 runs at the pre stage, so the post-stage rules never saw this call.
        self.assertEqual(self.records[-1]["stop_check"]["attempted"], ["R1"])


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class SpinBaselineRun(_RunMixin, unittest.TestCase):
    """t02 without rules: the agent rephrases until the turn budget runs out."""

    task_id = "t02"
    extra_args = ("--no-rules",)

    def test_the_baseline_burns_the_whole_turn_budget(self):
        self.assertEqual(self.result.steps, 11)
        self.assertEqual(self.result.stop_reason, "max_turns")

    def test_no_call_is_a_literal_repeat(self):
        # The spin is *pseudo*-progress: different question, identical answer. If
        # any call repeated exactly, this task would be measuring R1, not R2.
        counts = Counter((r["chosen"], r["args_hash"]) for r in self.records)
        self.assertEqual([n for n in counts.values() if n > 1], [], "not R1's task")

    def test_the_answers_really_are_identical(self):
        # Otherwise R2 would be catching a coincidence rather than stagnation.
        self.assertEqual(len({r["result_digest"] for r in self.records[1:]}), 1)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class SpinGovernedRun(_RunMixin, unittest.TestCase):
    """t02 with the rules installed: R2 halts on the second identical answer."""

    task_id = "t02"

    def test_r2_fires_and_ends_the_run(self):
        self.assertEqual(self.result.stop_reason, "halted")
        self.assertEqual(self.result.halt["rule"], "R2")
        self.assertEqual(self.result.halt["state"], "HALTED_NO_PROGRESS")

    def test_eleven_steps_become_three(self):
        # One look at the schema, then two questions with the same answer. The
        # second identical answer is the proof that rephrasing is the strategy.
        self.assertEqual(self.result.steps, 3)

    def test_the_plateau_is_visible_in_the_trace(self):
        # The evidence R2 acted on is in the file, not only in the verdict: the
        # last two records carry the same fingerprint, the first two do not.
        self.assertEqual(
            self.records[-1]["stop_check"]["state_hash"],
            self.records[-2]["stop_check"]["state_hash"],
        )
        self.assertNotEqual(
            self.records[1]["stop_check"]["state_hash"],
            self.records[0]["stop_check"]["state_hash"],
        )

    def test_the_two_identical_answers_are_provably_identical(self):
        self.assertEqual(
            self.records[-1]["result_digest"], self.records[-2]["result_digest"]
        )

    def test_r4_cannot_fire_on_a_task_that_requires_nothing(self):
        # The empty-set trap: ``required <= evidence`` is true for an empty
        # requirement, so a bare subset test would declare this run complete on
        # its first call and R2 would never be reached.
        first = self.records[0]
        self.assertEqual(first["stop_check"]["missing"], [])
        self.assertFalse(first["stop_check"]["hit"], "R4 did not claim completion")
        self.assertIn("R4", first["stop_check"]["attempted"], "R4 ran and declined")

    def test_the_final_result_is_still_delivered(self):
        # R2 judges after the fact. The result was paid for and is real evidence
        # of absence, so it is handed back along with the halt notice.
        self.assertFalse(self.result.turns[-1].is_error)
        self.assertEqual(self.records[-1]["decision"], "halt_no_progress")
        self.assertEqual(self.records[-1]["stop_check"]["attempted"], ["R1", "R6", "R4", "R2"])


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class DegradedRun(unittest.TestCase):
    """R6: a required fact has no available producer, so the run says so at once.

    The configuration has a single upstream (a stub), while ``sql.rows.repos``
    declares ``sqlite.query`` as its only producer. In production this is a
    server that was never deployed, or a tool frozen by schema drift; the gateway
    cannot tell the difference and does not need to -- both mean "the fact cannot
    arrive".
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        config = {
            "gateway": {"name": "mcp-gateway", "trace_dir": "traces/runs"},
            "upstreams": [
                {
                    "name": "dirty",
                    "command": "{python}",
                    "args": ["-m", "mcp_servers.dirty_schema_server"],
                    "cwd": "{root}",
                }
            ],
        }
        cfg = tmp / "gateway.json"
        cfg.write_text(json.dumps(config), encoding="utf-8")

        task = replace(
            {t.id: t for t in load_tasks()}["t01"],
            required_evidence=("sql.rows.repos",),
            max_turns=5,
        )
        cls.result = run_task_sync(
            task,
            policy=ScriptedPolicy([Action("dirty.status", {})]),
            run_id="degraded",
            trace_dir=tmp,
            extra_args=["--config", str(cfg)],
        )
        cls.records = _records(cls.result.trace_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_the_run_stops_on_the_first_call(self):
        # One record: the call was *refused*, not executed. Zero upstream calls
        # in a run that cannot finish.
        self.assertEqual(len(self.records), 1)
        self.assertEqual(self.result.steps, 1)

    def test_it_ends_degraded_and_not_on_the_budget(self):
        self.assertEqual(self.result.halt["rule"], "R6")
        self.assertEqual(self.result.halt["state"], "DEGRADED")
        self.assertEqual(self.records[-1]["decision"], "halt_evidence_unreachable")

    def test_the_reason_names_the_missing_producer(self):
        reason = self.result.halt["reason"]
        self.assertIn("sql.rows.repos", reason)
        self.assertIn("sqlite.query", reason)

    def test_the_missing_fact_is_recorded(self):
        self.assertEqual(self.records[-1]["stop_check"]["missing"], ["sql.rows.repos"])
        self.assertEqual(self.records[-1]["evidence_added"], [])

    def test_the_offered_surface_explains_the_verdict(self):
        # The unreachability is not an opinion: ``sqlite.query`` is simply not in
        # the tool surface this run was given, and ``offered`` says so on the
        # record the verdict is attached to.
        offered = self.records[-1]["offered"]
        self.assertIn("dirty.status", offered)
        self.assertNotIn("sqlite.query", offered)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class MultiFactBaselineRun(_RunMixin, unittest.TestCase):
    """t10 without rules: the plan plus the double-check tail, then it runs out."""

    task_id = "t10"
    extra_args = ("--no-rules",)

    def test_the_baseline_is_the_ten_step_overshoot(self):
        # Four plan calls (one of which is a useless describe_table) and six
        # re-checks. The task set's acceptance line is baseline >= optimal * 3,
        # and ten is what makes "three required facts" clear it.
        self.assertEqual(self.result.steps, 10)
        self.assertEqual(self.result.stop_reason, "policy_exhausted")

    def test_the_last_six_calls_are_the_overshoot(self):
        # The plan is checkable against the task file; so is the boundary between
        # "useful" and "re-assuring", which is what the compression is measured
        # across.
        self.assertEqual(self.records[-1]["evidence_added"], [])
        self.assertEqual(
            sorted(self.records[-1]["stop_check"]["evidence"]),
            ["sql.rows.incidents", "sql.rows.labels", "sql.schema"],
        )


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class MultiFactRun(_RunMixin, unittest.TestCase):
    """t10 with the rules: the run ends where the *second* fact arrives."""

    task_id = "t10"

    def test_ten_steps_become_four(self):
        self.assertEqual(self.result.steps, 4)
        self.assertEqual(self.result.halt["rule"], "R4")
        self.assertEqual(self.result.halt["state"], "HALTED_COMPLETE")

    def test_the_missing_list_shrinks_one_fact_at_a_time(self):
        # A shape-only evidence model would have declared this complete at step 3
        # (labels had not been read yet) -- that is the D9 lesson, replayed on a
        # second task so it is not a property of t01.
        self.assertEqual(
            self.records[1]["stop_check"]["missing"],
            ["sql.rows.incidents", "sql.rows.labels"],
        )
        self.assertEqual(self.records[2]["stop_check"]["missing"], ["sql.rows.labels"])
        self.assertEqual(self.records[3]["stop_check"]["missing"], [])
        self.assertEqual(self.records[3]["evidence_added"], ["sql.rows.labels"])

    def test_the_delivered_rows_answer_the_question(self):
        incidents = [row["id"] for row in self.result.turns[2].payload["rows"]]
        labels = {
            row["incident_id"]: row["label"]
            for row in self.result.turns[3].payload["rows"]
        }
        self.assertEqual(incidents, [2, 4], "the open incidents")
        self.assertEqual(labels[2], "flaky")


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class PagedSpinBaselineRun(_RunMixin, unittest.TestCase):
    """t20 without rules: pagination past the end of an empty result set."""

    task_id = "t20"
    extra_args = ("--no-rules",)

    def test_the_baseline_burns_the_whole_turn_budget(self):
        self.assertEqual(self.result.steps, 10)
        self.assertEqual(self.result.stop_reason, "max_turns")

    def test_paging_is_not_a_literal_repeat(self):
        # Four different offsets against a repo that does not exist. If any call
        # repeated, the run would be R1's business and the pagination story would
        # be decoration.
        counts = Counter((r["chosen"], r["args_hash"]) for r in self.records)
        self.assertEqual([n for n in counts.values() if n > 1], [])

    def test_every_page_returns_the_same_empty_answer(self):
        self.assertEqual(len({r["result_digest"] for r in self.records[1:]}), 1)


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed (run inside .venv)")
class PagedSpinRun(_RunMixin, unittest.TestCase):
    """t20 with the rules: the third page is refused by R2, not by the budget."""

    task_id = "t20"

    def test_r2_fires_on_the_second_identical_page(self):
        self.assertEqual(self.result.steps, 3)
        self.assertEqual(self.result.halt["rule"], "R2")
        self.assertEqual(self.result.halt["state"], "HALTED_NO_PROGRESS")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
