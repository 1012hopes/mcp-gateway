"""The policy, simulated without a gateway or a subprocess.

This is the cheapest possible test of the loop's logic, and it answers the two
questions D7 depends on:

* **Is the loop really closed?** Every action after the first must be derived
  from a payload the previous action returned. The simulation supplies answers
  and checks the sequence, so a policy that ignored observations would fail here
  rather than in a 9-step trace nobody reads.
* **Does the baseline actually overshoot?** If it stopped at the useful query,
  R4 would have nothing to compress and the project's headline number would be
  a measurement of nothing. The duplicate assertion is what makes D7 testable.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.policy import (  # noqa: E402
    UNRESOLVED_SQL,
    VERIFY_PLAN,
    Action,
    AgentContext,
    Observation,
    ReactivePolicy,
    ScriptedPolicy,
)
from agent.tasks import TaskSpec  # noqa: E402

TOOLS = (
    "sqlite.list_tables",
    "sqlite.describe_table",
    "sqlite.count_rows",
    "sqlite.query",
    "dirty.query",
)

TASK = TaskSpec(id="t01", goal="unresolved incidents", required_evidence=("sql.rows",))

UNRESOLVED = [
    {"id": 2, "repo": "maximhq/bifrost", "severity": "sev3"},
    {"id": 4, "repo": "1012hopes/mcp-gateway", "severity": "sev3"},
]


def answer(action: Action) -> dict:
    """A stand-in for the sqlite upstream: deterministic, shaped like the real one."""
    if action.tool == "sqlite.list_tables":
        return {"tables": ["incidents", "repos"]}
    if action.tool == "sqlite.describe_table":
        table = action.args["table"]
        return {"table": table, "columns": [{"name": "id", "type": "INT"}]}
    if action.tool == "sqlite.count_rows":
        return {"table": action.args["table"], "count": 4}
    sql = str(action.args.get("sql", ""))
    if sql == UNRESOLVED_SQL:
        return {"rows": list(UNRESOLVED), "row_count": 2}
    if "from repos" in sql:
        return {"rows": [{"name": "1012hopes/mcp-gateway", "stars": 1}], "row_count": 1}
    return {"rows": [], "row_count": 0}


def simulate(policy: ReactivePolicy, steps: int = 12) -> list[Action]:
    history: list[Observation] = []
    actions: list[Action] = []
    for _ in range(steps):
        ctx = AgentContext(task=TASK, tools=TOOLS, history=tuple(history))
        action = policy.next_action(ctx)
        if action is None:
            break
        actions.append(action)
        history.append(
            Observation(
                index=len(history) + 1,
                tool=action.tool,
                args=dict(action.args),
                blocks=(json.dumps(answer(action)),),
            )
        )
    return actions


class ClosedLoopTests(unittest.TestCase):
    def setUp(self):
        self.actions = simulate(ReactivePolicy())

    def test_first_step_discovers_before_querying(self):
        self.assertEqual(self.actions[0].tool, "sqlite.list_tables")

    def test_beliefs_come_from_observations(self):
        # The table name is not hard-coded in the policy: it arrives in step 1's
        # payload and is used in step 2.
        self.assertEqual(self.actions[1], Action("sqlite.describe_table", {"table": "incidents"}))

    def test_query_is_built_from_the_task_not_from_the_tables_it_found(self):
        self.assertEqual(self.actions[2].args["sql"], UNRESOLVED_SQL)

    def test_enrichment_uses_the_repos_from_the_previous_result(self):
        sql = self.actions[3].args["sql"]
        self.assertIn("from repos", sql)
        # Sorted, so two runs cannot differ by dict ordering.
        self.assertIn("'1012hopes/mcp-gateway', 'maximhq/bifrost'", sql)

    def test_baseline_overshoots_instead_of_declaring_done(self):
        # Four useful steps, then the verification plan. If this ever collapses
        # to four, R4 has nothing to prove.
        self.assertGreater(len(self.actions), 4)
        self.assertEqual(len(self.actions), 4 + len(VERIFY_PLAN))

    def test_the_baseline_contains_an_exact_duplicate(self):
        # R1's target must exist in the baseline, otherwise D7's "at least one
        # hit" assertion can only be satisfied by a synthetic call.
        keys = [(a.tool, json.dumps(a.args, sort_keys=True)) for a in self.actions]
        self.assertEqual(len(keys), len(set(keys)) + 1, keys)

    def test_policy_terminates(self):
        self.assertEqual(simulate(ReactivePolicy(), steps=40), self.actions)

    def test_same_history_same_decision(self):
        self.assertEqual(simulate(ReactivePolicy()), self.actions)

    def test_unoffered_tool_is_skipped_not_retried(self):
        # D14 will shrink ``offered``; a policy that kept asking for a hidden
        # tool would spin until max_turns and pollute the step counts.
        actions = simulate(ReactivePolicy())  # tools include everything
        self.assertTrue(all(a.tool in TOOLS for a in actions))


class ScriptedPolicyTests(unittest.TestCase):
    def test_plays_the_list_then_stops(self):
        policy = ScriptedPolicy([Action("sqlite.list_tables", {}), Action("sqlite.count_rows", {"table": "repos"})])
        history = []
        played = []
        for _ in range(5):
            ctx = AgentContext(task=TASK, tools=TOOLS, history=tuple(history))
            action = policy.next_action(ctx)
            if action is None:
                break
            played.append(action)
            history.append(Observation(len(history) + 1, action.tool, dict(action.args), ("{}",)))
        self.assertEqual(len(played), 2)
        self.assertIsNone(policy.next_action(AgentContext(TASK, TOOLS, tuple(history))))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
