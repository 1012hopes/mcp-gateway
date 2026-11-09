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
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from gateway.wire import parse_notice

from .tasks import TaskSpec

__all__ = [
    "Action",
    "AgentContext",
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
        position = (len(ctx.history) - 1) % len(FUTILE_SQL)
        if "sqlite.query" not in ctx.tools:
            return None
        return Action("sqlite.query", {"sql": FUTILE_SQL[position], "limit": 20})


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

        done = self._verify_done(ctx, plan)
        verify = [action for action in VERIFY_PLAN if self._offered(ctx, action)]
        return verify[done] if done < len(verify) else None

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

    def _verify_done(self, ctx: AgentContext, plan: Sequence[Action]) -> int:
        """How many verification rounds already happened.

        Anchored to the *last* useful action (the enrichment query), which never
        appears in ``VERIFY_PLAN``. Anchoring to "the last call that looks
        useful" would not work: the first verification round is byte-identical
        to an earlier useful call, so the anchor would keep moving forward and
        the loop would never advance.
        """
        if len(plan) < 4:
            return 0
        anchor = plan[-1]
        index = None
        for position, obs in enumerate(ctx.history):
            if obs.asked(anchor.tool, anchor.args):
                index = position
        if index is None:
            return 0
        return len(ctx.history) - index - 1

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
    raise ValueError(f"unknown policy {name!r}")
