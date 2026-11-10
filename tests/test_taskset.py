"""D11 acceptance: the task set is a data set with invariants, not twenty files.

Why these are tests and not a checklist in a doc: every invariant here is one
that, if broken, produces numbers that look fine. A task whose ``plan`` cannot
reach its own ``required_evidence`` would be halted as ``DEGRADED`` and still
report "1 step"; an adversarial task with fewer rephrasings than ``max_turns``
would wrap around, repeat itself exactly, and be stopped by R1 while claiming to
measure R2. Neither failure raises anything at run time.

The one property that cannot be tested without spawning the gateway -- that
``baseline >= optimal * 3`` on the *measured* numbers -- lives in
``scripts/task_sweep.py``; the static half (the plan is long enough that it could
hold) is checked here.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.policy import (  # noqa: E402
    FUTILE_SQL,
    Action,
    AgentContext,
    DeclaredPolicy,
    Observation,
)
from agent.tasks import TaskSpec, load_tasks  # noqa: E402
from gateway.evidence import load_evidence_model  # noqa: E402

EVIDENCE_PATH = ROOT / "config" / "evidence.json"
SERVER_PATH = ROOT / "mcp_servers" / "sqlite_server.py"

TASKS = load_tasks()
BY_ID = {task.id: task for task in TASKS}


def server_tools() -> set:
    """The tool names the upstream actually exposes, read from its source.

    Parsed rather than imported: the server module needs the MCP SDK, and this
    test must run on a bare interpreter (see ``agent/__init__.py``). Parsing also
    makes it a *drift* check -- a renamed tool breaks it, which is the point.
    """
    source = SERVER_PATH.read_text(encoding="utf-8")
    return {f"sqlite.{name}" for name in re.findall(r'name="([a-z_]+)"', source)}


class ShippedSetTests(unittest.TestCase):
    def test_the_set_has_twenty_tasks_with_contiguous_ids(self):
        self.assertEqual(len(TASKS), 20, [task.id for task in TASKS])
        self.assertEqual([task.id for task in TASKS], [f"t{n:02d}" for n in range(1, 21)])

    def test_every_task_states_what_it_is_for(self):
        for task in TASKS:
            with self.subTest(task=task.id):
                self.assertTrue(task.goal.strip())
                self.assertGreaterEqual(task.max_turns, 2)

    def test_every_task_has_a_hand_counted_optimum(self):
        # Absent would mean "not done yet" -- D11's completion criterion is that
        # all twenty are marked, so a None here is a missing deliverable, not a
        # gap to fill later.
        for task in TASKS:
            with self.subTest(task=task.id):
                self.assertIsNotNone(task.optimal_steps)
                self.assertGreaterEqual(int(task.optimal_steps), 1)
                self.assertTrue(
                    task.optimal_path.strip(),
                    "a step count without a stated method is a number on faith",
                )

    def test_the_six_adversarial_tasks_are_exactly_the_empty_requirement_ones(self):
        adversarial = [task for task in TASKS if not task.required_evidence]
        self.assertEqual(len(adversarial), 6, [task.id for task in adversarial])
        for task in adversarial:
            with self.subTest(task=task.id):
                self.assertEqual(task.policy, "futile_search")
                self.assertEqual(task.optimal_steps, 1, "one question, one empty answer")


class EvidenceCoverageTests(unittest.TestCase):
    """Every required fact must be reachable by the task's own declared plan."""

    def setUp(self):
        self.model = load_evidence_model(EVIDENCE_PATH)
        self.tools = server_tools()

    def test_required_types_are_declared(self):
        declared = set(self.model.types)
        for task in TASKS:
            with self.subTest(task=task.id):
                unknown = set(task.required_evidence) - declared
                self.assertEqual(unknown, set(), f"undeclared evidence type(s): {unknown}")

    def test_no_task_asks_for_more_facts_than_its_plan_can_produce(self):
        # The failure this prevents is quiet: a plan that never produces one of
        # its own required types leaves the run incomplete, R6 may or may not
        # catch it, and the sweep reports a small step count that means
        # "gave up", not "finished".
        for task in TASKS:
            if not task.plan:
                continue
            obtainable = set()
            for step in task.plan:
                for ev_type in self.model.types:
                    spec = self.model.spec(ev_type)
                    if step.tool in spec.producers and spec.matches_args(step.args):
                        obtainable.add(ev_type)
            with self.subTest(task=task.id):
                self.assertEqual(
                    set(task.required_evidence) - obtainable,
                    set(),
                    f"plan cannot produce {set(task.required_evidence) - obtainable}",
                )

    def test_no_plan_calls_a_tool_that_does_not_exist(self):
        for task in TASKS:
            for step in task.plan:
                with self.subTest(task=task.id, tool=step.tool):
                    self.assertIn(step.tool, self.tools)


class DeclaredPlanTests(unittest.TestCase):
    def setUp(self):
        self.model = load_evidence_model(EVIDENCE_PATH)

    def test_productive_optimum_is_the_number_of_required_facts(self):
        for task in TASKS:
            if not task.required_evidence:
                continue
            with self.subTest(task=task.id):
                self.assertEqual(task.optimal_steps, len(task.required_evidence))

    def test_plans_are_non_empty_and_distinct(self):
        # Distinctness is load-bearing twice: ``_next_tail`` anchors on the first
        # occurrence of the plan's last action, and the tail opens with an exact
        # repeat of it. A plan that repeated a step would make the anchor land on
        # the wrong call and the run would never advance past its first re-check.
        #
        # Non-emptiness only applies to declared tasks: a reactive task has no
        # plan by design (its plan depends on what the last call returned). The
        # first version of this test skipped every empty plan -- including
        # declared ones -- so it checked *part* of what its name promised.
        for task in TASKS:
            if task.policy in ("declared", "plan"):
                with self.subTest(task=task.id):
                    self.assertTrue(task.plan, "a declared task must plan something")
            if not task.plan:
                continue
            keys = [(step.tool, json.dumps(dict(step.args), sort_keys=True)) for step in task.plan]
            with self.subTest(task=task.id):
                self.assertEqual(len(keys), len(set(keys)), "plan repeats a step")


class DeclaredTaskWithoutAPlanTests(unittest.TestCase):
    """A baseline run that makes zero calls must not look like an efficient one.

    ``DeclaredPolicy`` returns ``None`` on turn 1 when the plan is empty, so the
    run ends with zero calls and zero records -- and in ``results/table.md`` it
    contributes 0 to the *denominator* of "calls saved". A typo in one task file
    would therefore improve the headline number while removing a task from the
    experiment (measured in the D17 audit: 0 records, no error, ``steps=0``).
    """

    def test_a_declared_task_with_no_plan_is_refused_at_load(self):
        with self.assertRaises(ValueError) as caught:
            TaskSpec.from_dict({"id": "t-empty", "goal": "g", "policy": "declared"})
        self.assertIn("empty plan", str(caught.exception))

    def test_an_empty_plan_list_is_refused_too(self):
        # ``[]`` and a missing key are the same mistake written two ways.
        with self.assertRaises(ValueError):
            TaskSpec.from_dict(
                {"id": "t-empty", "goal": "g", "policy": "declared", "plan": []}
            )

    def test_the_reactive_policy_may_still_have_no_plan(self):
        task = TaskSpec.from_dict({"id": "t", "goal": "g"})
        self.assertEqual(task.plan, ())
        self.assertEqual(task.policy, "reactive_sqlite")

    def test_a_declared_task_with_a_plan_still_loads(self):
        task = TaskSpec.from_dict(
            {
                "id": "t",
                "goal": "g",
                "policy": "declared",
                "plan": [{"tool": "sqlite.query", "args": {"sql": "select 1"}}],
            }
        )
        self.assertEqual(len(task.plan), 1)


class AdversarialTests(unittest.TestCase):
    def test_the_spin_never_wraps_around(self):
        # If the rephrasings ran out before the turn budget, the adversary would
        # start issuing byte-identical calls. R1 would then stop it at that point
        # and the task would be measuring the wrong rule -- while still producing
        # a plausible-looking "11 -> 3".
        for task in TASKS:
            if task.policy != "futile_search":
                continue
            statements = task.futile_sql or FUTILE_SQL
            with self.subTest(task=task.id):
                self.assertGreaterEqual(len(statements), task.max_turns)
                self.assertEqual(len(statements), len(set(statements)))

    def test_every_rephrasing_asks_the_same_unanswerable_question(self):
        # Same table, different wording: that is what makes the answers identical
        # and the state a plateau. A statement against another table would return
        # a different (still empty) answer -- a different digest, no plateau.
        for task in TASKS:
            if task.policy != "futile_search":
                continue
            statements = task.futile_sql or FUTILE_SQL
            tables = {re.search(r"\bfrom\s+([a-z_]+)", sql, re.I).group(1) for sql in statements}
            with self.subTest(task=task.id):
                self.assertEqual(len(tables), 1, tables)


def _answer(action: Action) -> dict:
    """A stand-in upstream: shapes matter here, values do not."""
    if action.tool == "sqlite.list_tables":
        return {"tables": ["incidents", "releases"]}
    if action.tool == "sqlite.describe_table":
        return {"table": action.args["table"], "columns": [{"name": "id", "type": "INT"}]}
    if action.tool == "sqlite.count_rows":
        return {"table": action.args["table"], "count": 0}
    return {"rows": [], "row_count": 0}


def _simulate(task, tools, steps: int = 40):
    policy = DeclaredPolicy()
    history = []
    actions = []
    for _ in range(steps):
        action = policy.next_action(AgentContext(task=task, tools=tools, history=tuple(history)))
        if action is None:
            return actions
        actions.append(action)
        history.append(
            Observation(
                index=len(history) + 1,
                tool=action.tool,
                args=dict(action.args),
                blocks=(json.dumps(_answer(action)),),
            )
        )
    raise AssertionError("policy never stopped -- the tail anchor is broken")


class DeclaredPolicyBehaviourTests(unittest.TestCase):
    """The overshoot, simulated: it must advance, and it must add no facts."""

    def setUp(self):
        self.task = BY_ID["t10"]
        self.tools = ("sqlite.list_tables", "sqlite.describe_table", "sqlite.count_rows", "sqlite.query")

    def test_the_plan_runs_first_and_in_order(self):
        actions = _simulate(self.task, self.tools)
        plan = [Action(step.tool, dict(step.args)) for step in self.task.plan]
        self.assertEqual(actions[: len(plan)], plan)

    def test_the_tail_opens_with_an_exact_repeat(self):
        # R1's target has to exist in the baseline, otherwise "R1 fired" can only
        # be demonstrated with a synthetic call.
        actions = _simulate(self.task, self.tools)
        plan_len = len(self.task.plan)
        self.assertEqual(actions[plan_len], actions[plan_len - 1])

    def test_the_tail_never_stops_advancing(self):
        # The trap this guards: the first tail call is an exact repeat of the
        # plan's last action, so a last-occurrence anchor would report "0 tail
        # calls done" forever and the loop would hang on one re-check.
        actions = _simulate(self.task, self.tools)
        tail = actions[len(self.task.plan) :]
        self.assertEqual(len(tail), len(set(
            (a.tool, json.dumps(dict(a.args), sort_keys=True)) for a in tail
        )), tail)

    def test_the_overshoot_adds_no_new_facts(self):
        # "Adds no evidence" would be the wrong assertion -- a re-read *does*
        # produce a payload that satisfies the same types again. What matters is
        # that the set does not grow: everything the tail re-reads, the plan
        # already had.
        model = load_evidence_model(EVIDENCE_PATH)
        actions = _simulate(self.task, self.tools)
        plan_len = len(self.task.plan)

        def evidence_of(batch):
            found = set()
            for action in batch:
                found |= set(model.extract(_answer(action), tool=action.tool, args=action.args))
            return found

        before = evidence_of(actions[:plan_len])
        after = evidence_of(actions)
        self.assertEqual(after, before, f"the overshoot learned {after - before}")

    def test_the_plan_alone_satisfies_the_requirement(self):
        # The other half of the same claim: what the tail cannot add, the plan
        # must already have. Together these say "the run is complete before the
        # overshoot begins", which is exactly what R4 acts on.
        model = load_evidence_model(EVIDENCE_PATH)
        evidence = set()
        for step in self.task.plan:
            evidence |= set(model.extract(_answer(Action(step.tool, step.args)), tool=step.tool, args=step.args))
        self.assertLessEqual(set(self.task.required_evidence), evidence)

    def test_an_unoffered_plan_step_is_skipped_but_the_tail_is_not(self):
        # Two different rules, deliberately (D14):
        # * the *plan* is what a model chooses, and a model cannot choose a tool
        #   it was not shown -- so a hidden plan step is skipped, not retried;
        # * the *tail* is the fixed program that measures post-completion waste,
        #   and it is the denominator of "saved calls", so it must not shrink
        #   when the window narrows. Filtering it made the baseline fall from
        #   182 calls to 175 for no reason but the gate.
        actions = _simulate(self.task, ("sqlite.query",))
        plan_queries = [
            Action(step.tool, dict(step.args))
            for step in self.task.plan
            if step.tool == "sqlite.query"
        ]
        self.assertEqual(actions[: len(plan_queries)], plan_queries)
        self.assertTrue(
            any(action.tool != "sqlite.query" for action in actions[len(plan_queries) :]),
            actions,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
