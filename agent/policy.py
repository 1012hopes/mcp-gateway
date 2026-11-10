"""The agent's decision function -- the only LLM-shaped seam in the project.

Why a hand-written policy and not a model call
----------------------------------------------
The claim under test is about the *gateway*, not about how smart the planner is.
A hand-written policy gives three properties a model call cannot:

* **Determinism.** The same trace replays to the same decisions, which is what
  makes "3 steps instead of 11" a measurement rather than an anecdote.
* **No key, no bill, no network.** The experiment runs anywhere, forever.
* **A known failure mode.** This policy does not know when to stop -- it finishes
  its useful plan and then keeps verifying. That is not a bug to fix here: it is
  the behaviour R4 exists to cut, and the baseline it produces is the honest
  denominator for the compression numbers (DESIGN A6).

Swapping in a real model means replacing ``ReactivePolicy.next_action`` and
nothing else; the loop, the trace and the rules are all indifferent.

The interface is one pure function of the context: given the tools offered and
the observations so far, return the next call or ``None``. No hidden state --
state would make replay depend on the replay itself.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from gateway.wire import parse_notice

from .tasks import TaskSpec

__all__ = [
    "Action",
    "AgentContext",
    "DeclaredPolicy",
    "FutileSearchPolicy",
    "Observation",
    "Policy",
    "ReactivePolicy",
    "ScriptedPolicy",
    "policy_for",
]


@dataclass(frozen=True)
class Action:
    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """One turn as the agent saw it: what it asked, what came back."""

    index: int
    tool: str
    args: Mapping[str, Any]
    blocks: Tuple[str, ...]
    is_error: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.blocks)

    @property
    def payload(self) -> Optional[Dict[str, Any]]:
        """The upstream's JSON, when the first block is JSON.

        Only the first block: a halt notice is appended as an extra block, and
        parsing the concatenation would fail exactly on the interesting turns.
        """
        if not self.blocks:
            return None
        try:
            data = json.loads(self.blocks[0])
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    @property
    def notice(self) -> Optional[Dict[str, str]]:
        return parse_notice(self.text)

    @property
    def halted(self) -> bool:
        notice = self.notice
        return bool(notice) and notice.get("kind") == "halt"

    def asked(self, tool: str, args: Mapping[str, Any]) -> bool:
        return self.tool == tool and dict(self.args) == dict(args)


@dataclass(frozen=True)
class AgentContext:
    task: TaskSpec
    tools: Tuple[str, ...]
    history: Tuple[Observation, ...]

    @property
    def turn(self) -> int:
        return len(self.history) + 1


class Policy(Protocol):
    name: str

    def next_action(self, ctx: AgentContext) -> Optional[Action]: ...


# ---------------------------------------------------------------------------
# the reactive SQLite triage policy
# ---------------------------------------------------------------------------

UNRESOLVED_SQL = (
    "select id, repo, severity from incidents where resolved_at is null order by id"
)

#: The overshoot phase. Every entry is a plausible-sounding "just to be sure"
#: call that adds no evidence. The first one is an *exact* repeat of the useful
#: query (R1's target); the rest differ in args or only re-read metadata (R2's
#: target). Written down as data so the baseline is auditable, not emergent.
VERIFY_PLAN: Tuple[Action, ...] = (
    Action("sqlite.query", {"sql": UNRESOLVED_SQL, "limit": 50}),
    Action(
        "sqlite.query",
        {
            "sql": "select id, repo, severity from incidents "
            "where resolved_at is null order by severity desc",
            "limit": 50,
        },
    ),
    Action("sqlite.count_rows", {"table": "incidents"}),
    Action("sqlite.describe_table", {"table": "repos"}),
    Action(
        "sqlite.query",
        {"sql": "select name, stars from repos order by stars desc", "limit": 5},
    ),
)


def stars_sql(repos: Iterable[str]) -> str:
    """Deterministic SQL for a set of repo names (sorted: order is identity)."""
    names = sorted({str(repo) for repo in repos})
    if not names:
        return "select name, stars from repos order by stars desc"
    quoted = ", ".join("'" + name.replace("'", "''") + "'" for name in names)
    return f"select name, stars from repos where name in ({quoted}) order by name"


#: Rephrasings of the same unanswerable question. Every one of them returns the
#: same empty row set, which is what makes them *pseudo*-progress: the SQL text
#: differs (so R1 sees a new call every time), the answer does not.
#:
#: More variants than the task's ``max_turns`` on purpose: if the list were
#: shorter the spin would wrap around and start repeating itself exactly, and the
#: baseline would then contain R1's target instead of R2's. A task that tests one
#: rule must not accidentally test another.
FUTILE_SQL: Tuple[str, ...] = (
    "select id, repo, severity from incidents where repo = 'ghost/typo'",
    "select id, repo, severity from incidents where lower(repo) = lower('ghost/typo')",
    "select id, repo, severity from incidents where repo like '%ghost%'",
    "select id, repo, severity from incidents where repo glob 'ghost/*'",
    "select id, repo, severity from incidents where instr(repo, 'typo') > 0",
    "select id, repo, severity from incidents where trim(repo) = 'ghost/typo'",
    "select id, repo, severity from incidents where repo like 'ghost%'",
    "select id, repo, severity from incidents where upper(repo) = upper('ghost/typo')",
    "select id, repo, severity from incidents where repo like '%/typo'",
    "select id, repo, severity from incidents where repo like '_host/typo'",
    "select id, repo, severity from incidents where substr(repo, 1, 5) = 'ghost'",
    "select id, repo, severity from incidents "
    "where repo != 'ghost/typo' and repo = 'ghost/typo'",
)


class FutileSearchPolicy:
    """An agent that cannot accept "it is not there".

    This is the adversarial baseline for G2, written as a policy rather than as a
    scripted list because the point is the *behaviour*, not the calls: after
    learning the schema it keeps asking the same question in slightly different
    words, forever, and every answer is the same empty set.

    It is not a strawman. "Search again, differently" is the single most common
    way an agent burns a budget without learning anything, and no individual call
    is wrong -- which is exactly why the decision cannot live in the agent's
    prompt (DESIGN 0.2). The run must be stopped from outside, on evidence the
    agent itself produced.
    """

    name = "futile_search"

    def next_action(self, ctx: AgentContext) -> Optional[Action]:
        if not ctx.tools:
            return None
        if not ctx.history:
            # One look at the schema, then it is sure it knows what to ask.
            return (
                Action("sqlite.list_tables", {})
                if "sqlite.list_tables" in ctx.tools
                else None
            )
        # The rephrasings are per task: "repo does not exist" and "label was
        # never used" are the same behaviour over different tables, and putting
        # the statements in the task file keeps one policy honest for all six
        # adversarial tasks instead of six near-copies of this method.
        statements = tuple(ctx.task.futile_sql) or FUTILE_SQL
        position = (len(ctx.history) - 1) % len(statements)
        if "sqlite.query" not in ctx.tools:
            return None
        return Action("sqlite.query", {"sql": statements[position], "limit": 20})


class ReactivePolicy:
    """Plan from beliefs, then keep verifying.

    Beliefs are recomputed from ``history`` on every turn rather than stored:
    two sources of truth (memory and history) would diverge the first time a call
    was blocked, and the trace would then disagree with the agent's account.
    """

    name = "reactive_sqlite"

    def next_action(self, ctx: AgentContext) -> Optional[Action]:
        if not ctx.tools:
            return None

        plan = self._plan(ctx)
        for action in plan:
            if not self._offered(ctx, action):
                continue
            if not self._performed(ctx.history, action):
                return action

        return self._next_verify(ctx, plan)

    def _next_verify(self, ctx: AgentContext, plan: Sequence[Action]) -> Optional[Action]:
        """The first "just to be sure" call this run has not made yet.

        Written as a predicate over *performed* actions rather than as a
        position ("how many calls since the anchor"), because a position and a
        list filtered by the current window disagree the moment the window
        changes, and the agent then repeats a call it already made. Measured
        with positional counting plus window filtering (D14): the baseline ran
        its closing query twice and had two byte-identical pairs instead of one,
        which would have made "the baseline contains exactly one duplicate" a
        property of the catalogue rather than of the agent.

        An action the window currently hides is skipped, not waited for: the
        overshoot must terminate even if a tool never comes back.
        """
        anchor = plan[-1]
        index = None
        for position, obs in enumerate(ctx.history):
            if obs.asked(anchor.tool, anchor.args):
                index = position
        after = ctx.history[index + 1 :] if index is not None else ()

        for action in VERIFY_PLAN:
            if not self._offered(ctx, action):
                continue
            if any(obs.asked(action.tool, action.args) for obs in after):
                continue
            return action
        return None

    # -- the useful phase --------------------------------------------------

    def _plan(self, ctx: AgentContext) -> List[Action]:
        tables = self._tables(ctx)
        if tables is None:
            return [Action("sqlite.list_tables", {})]

        plan = [Action("sqlite.list_tables", {})]
        if "incidents" in tables:
            plan.append(Action("sqlite.describe_table", {"table": "incidents"}))
        plan.append(Action("sqlite.query", {"sql": UNRESOLVED_SQL, "limit": 50}))

        rows = self._unresolved(ctx)
        if rows:
            plan.append(
                Action(
                    "sqlite.query",
                    {"sql": stars_sql(row.get("repo", "") for row in rows), "limit": 50},
                )
            )
        return plan

    def _tables(self, ctx: AgentContext) -> Optional[List[str]]:
        payload = self._last(ctx, lambda obs: (obs.payload or {}).get("tables"))
        if payload is None:
            return None
        return [str(name) for name in payload["tables"]]

    def _unresolved(self, ctx: AgentContext) -> List[Dict[str, Any]]:
        payload = self._last(
            ctx,
            lambda obs: obs.args.get("sql") == UNRESOLVED_SQL and (obs.payload or {}).get("rows"),
        )
        if payload is None:
            return []
        rows = payload.get("rows") or []
        return [row for row in rows if isinstance(row, dict)]

    # -- the overshoot phase ----------------------------------------------

    # -- helpers -----------------------------------------------------------

    def _offered(self, ctx: AgentContext, action: Action) -> bool:
        if action.tool in ctx.tools:
            return True
        # A tool that is not offered cannot be called; treat the step as done
        # rather than retrying it forever (D14 will shrink ``offered``).
        return False

    @staticmethod
    def _performed(history: Sequence[Observation], action: Action) -> bool:
        return any(obs.asked(action.tool, action.args) for obs in history)

    @staticmethod
    def _last(
        ctx: AgentContext, predicate: Any
    ) -> Optional[Dict[str, Any]]:
        for obs in reversed(ctx.history):
            if obs.is_error:
                continue
            payload = obs.payload
            if payload is not None and predicate(obs):
                return payload
        return None


class DeclaredPolicy:
    """Run a declared plan, then double-check it until the ideas run out.

    Why a declared plan and not twenty hand-written planners: the quantity under
    test is "how many of these calls were necessary", and a planner that
    re-queries by accident would put its own quality inside that number. The
    plan is in the task file so a reviewer can check it against the goal without
    reading Python -- and so the overshoot below stays the only "behaviour" left.

    The overshoot is deliberately *generic*: the same five or six "just to be
    sure" calls for every task, derived from the plan rather than written per
    task. If it were written per task, "baseline / optimal >= 3" would be true by
    construction and say nothing; as a fixed routine it is a property of the
    agent archetype, and the tasks were selected to be ones where it dominates.
    """

    name = "declared"

    def next_action(self, ctx: AgentContext) -> Optional[Action]:
        plan = [Action(step.tool, dict(step.args)) for step in ctx.task.plan]
        if not plan:
            return None

        for action in plan:
            if action.tool not in ctx.tools:
                # Not offered is not the same as not done: skip it rather than
                # spin (D14 will shrink ``offered`` and this must not become an
                # infinite loop the day it does).
                continue
            if not self._performed(ctx.history, action):
                return action

        tail = self._tail(ctx, plan)
        return self._next_tail(ctx, plan, tail)

    # -- the useful phase --------------------------------------------------

    @staticmethod
    def _performed(history: Sequence[Observation], action: Action) -> bool:
        return any(obs.asked(action.tool, action.args) for obs in history)

    # -- the overshoot ------------------------------------------------------

    def _tail(self, ctx: AgentContext, plan: Sequence[Action]) -> List[Action]:
        """The "am I sure?" phase, built from the plan's last query.

        Every entry here is a call a careful agent really does make after it
        believes it is finished. None of them adds a fact: re-asking a question
        returns an answer it already has, ``count_rows`` returns a number where
        the evidence model wants rows, and ``describe_table`` re-reads structure
        it already read.
        """
        queries = [action for action in plan if action.tool.endswith(".query")]
        if not queries:
            return []
        last = queries[-1]
        table = _table_of(str(last.args.get("sql", "")))

        tail: List[Action] = [last]  # exact repeat: R1's target
        if table:
            tail.append(Action("sqlite.count_rows", {"table": table}))
            tail.append(Action("sqlite.describe_table", {"table": table}))
        tail.append(Action("sqlite.list_tables", {}))
        tail.append(Action("sqlite.query", {**last.args, "limit": 1}))
        if len(queries) > 1:
            # Two facts in, two facts to re-check. One re-read per query is what
            # "double-check the answer" means when the answer has two halves.
            tail.append(Action("sqlite.query", {**queries[0].args, "limit": 10}))
        # Unfiltered, unlike the plan above. The plan is what a *model* chooses,
        # and a model cannot choose what it was not shown; the tail is the fixed
        # program that measures how much an agent wastes after it is done, and
        # that number is the denominator of "saved calls" -- so it must not
        # depend on the width of the window under test. Measured the other way
        # (filtered): the baseline fell from 182 calls to 175, purely because
        # the gate hid four re-checks, and "saved 64.3%" quietly became 62.9%.
        return tail

    def _next_tail(
        self, ctx: AgentContext, plan: Sequence[Action], tail: Sequence[Action]
    ) -> Optional[Action]:
        """The first tail call this run has not made yet.

        Predicate over performed actions, not a position in the list. The tail
        used to be counted positionally, and when a tool dropped out of the
        window the filtered list and the position disagreed: the agent then
        repeated a call it had already made (D14 measured it -- the closing
        query ran twice, so the baseline had two byte-identical pairs where it
        should have had one).

        Anchored to the *first* occurrence of the plan's last action, not the
        last. The tail opens with an exact repeat of that action, so a
        last-occurrence anchor would move forward on every step and the loop
        would never advance past the first re-check. Requiring plan steps to be
        distinct (``tests/test_taskset.py``) is what makes "first occurrence"
        the plan step rather than an earlier identical one.
        """
        anchor = plan[-1]
        index = None
        for position, obs in enumerate(ctx.history):
            if obs.asked(anchor.tool, anchor.args):
                index = position
                break
        after = ctx.history[index + 1 :] if index is not None else ()

        for action in tail:
            if not self._performed(after, action):
                return action
        return None


_TABLE_RE = re.compile(r"\bfrom\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def _table_of(sql: str) -> Optional[str]:
    match = _TABLE_RE.search(sql)
    return match.group(1) if match else None


class ScriptedPolicy:
    """A fixed list of calls. For tests that need the gateway, not the agent."""

    name = "scripted"

    def __init__(self, actions: Sequence[Action]) -> None:
        self._actions = list(actions)

    def next_action(self, ctx: AgentContext) -> Optional[Action]:
        position = len(ctx.history)
        return self._actions[position] if position < len(self._actions) else None


def policy_for(name: str) -> Policy:
    if name in ("reactive_sqlite", "reactive", ""):
        return ReactivePolicy()
    if name in ("futile_search", "futile"):
        return FutileSearchPolicy()
    if name in ("declared", "plan"):
        return DeclaredPolicy()
    raise ValueError(f"unknown policy {name!r}")
