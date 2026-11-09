"""One task, one run: spawn the gateway, ask the policy, observe, repeat.

The loop is deliberately dumb, and every dumb part is load-bearing:

* **It stops when the gateway says so, or when the policy runs out of ideas.**
  Never because the agent decided the task looked finished -- that judgement is
  what the project moves to the gateway, and a loop that second-guesses it would
  quietly invalidate every step count.
* **It keeps the observations.** The policy is a pure function of them, so the
  same trace replays to the same decisions; nothing is remembered outside
  ``Observations``.
* **It counts turns the same way the gateway does.** Every call the gateway
  accepted becomes one record, so ``len(turns) == records`` is checkable and a
  mismatch is a bug in the harness, not a rounding difference.

The gateway is a subprocess on purpose. Running it in-process would let the loop
reach the upstream directly, and then the choke point would be a convention
rather than a fact.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gateway.config import load_config
from gateway.session import new_run_id
from gateway.wire import KIND_CLOSED, KIND_HALT

from .policy import Action, AgentContext, Observation, Policy, policy_for
from .tasks import TaskSpec

__all__ = ["RunResult", "gateway_params", "run_task"]

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class RunResult:
    run_id: str
    trace_path: Path
    tools: Tuple[str, ...]
    turns: List[Observation] = field(default_factory=list)
    halt: Optional[Dict[str, str]] = None
    stop_reason: str = ""
    protocol: str = ""

    @property
    def steps(self) -> int:
        return len(self.turns)

    def summary(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "steps": self.steps,
            "stop_reason": self.stop_reason,
            "halt": self.halt,
            "trace_path": str(self.trace_path),
        }


def gateway_params(extra: Sequence[str] = ()) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "gateway", *extra],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def _blocks(result: Any) -> Tuple[str, ...]:
    out: List[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        out.append(text if isinstance(text, str) else f"<{getattr(item, 'type', '?')}>")
    return tuple(out)


async def run_task(
    task: TaskSpec,
    *,
    policy: Optional[Policy] = None,
    run_id: Optional[str] = None,
    trace_dir: Optional[Path] = None,
    extra_args: Sequence[str] = (),
    model_id: Optional[str] = None,
) -> RunResult:
    """Run one task to a stop. Never raises on a tool error -- that is data."""
    run_id = run_id or new_run_id()
    trace_dir = Path(trace_dir) if trace_dir else load_config().trace_dir
    trace_path = trace_dir / f"{run_id}.jsonl"

    args = ["--run-id", run_id, "--trace-dir", str(trace_dir), "--task-id", task.id]
    if model_id:
        args += ["--model-id", model_id]
    args += task.gateway_args()
    args += list(extra_args)

    policy = policy or policy_for(task.policy)
    result = RunResult(
        run_id=run_id, trace_path=trace_path, tools=(), stop_reason="not_started"
    )

    async with stdio_client(gateway_params(args)) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            result.protocol = init.protocol_version
            listing = await session.list_tools()
            result.tools = tuple(tool.name for tool in listing.tools)

            while len(result.turns) < task.max_turns:
                ctx = AgentContext(
                    task=task, tools=result.tools, history=tuple(result.turns)
                )
                action = policy.next_action(ctx)
                if action is None:
                    result.stop_reason = "policy_exhausted"
                    break

                observation = await _ask(session, action, index=len(result.turns) + 1)
                result.turns.append(observation)

                notice = observation.notice
                if notice and notice.get("kind") == KIND_HALT:
                    result.halt = notice
                    result.stop_reason = "halted"
                    break
                if notice and notice.get("kind") == KIND_CLOSED:
                    result.halt = notice
                    result.stop_reason = "closed"
                    break
            else:
                result.stop_reason = "max_turns"

    return result


async def _ask(session: ClientSession, action: Action, *, index: int) -> Observation:
    try:
        result = await session.call_tool(action.tool, dict(action.args))
    except Exception as exc:  # noqa: BLE001 - a dead transport is an observation too
        return Observation(
            index=index,
            tool=action.tool,
            args=dict(action.args),
            blocks=(f"{type(exc).__name__}: {exc}",),
            is_error=True,
        )
    return Observation(
        index=index,
        tool=action.tool,
        args=dict(action.args),
        blocks=_blocks(result),
        is_error=bool(getattr(result, "is_error", False)),
    )


def run_task_sync(task: TaskSpec, **kwargs: Any) -> RunResult:
    """Convenience for scripts and tests; the library entry point stays async."""
    return asyncio.run(run_task(task, **kwargs))
