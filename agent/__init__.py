"""The agent side of the harness -- a client of the gateway, never a part of it.

Kept out of the ``gateway`` package on purpose: the gateway must be able to
control an agent it does not own. If the loop lived inside the gateway, "the
gateway stops the run" would be a statement about one program's internals rather
than about a process boundary that a real agent would have to respect.
"""

from .loop import RunResult, gateway_params, run_task, run_task_sync
from .policy import Action, AgentContext, Observation, Policy, ReactivePolicy, ScriptedPolicy
from .tasks import TaskSpec, load_task, load_tasks

__all__ = [
    "Action",
    "AgentContext",
    "Observation",
    "Policy",
    "ReactivePolicy",
    "RunResult",
    "ScriptedPolicy",
    "TaskSpec",
    "gateway_params",
    "load_task",
    "load_tasks",
    "run_task",
    "run_task_sync",
]
