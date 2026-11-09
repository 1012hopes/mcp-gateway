"""The agent side of the harness -- a client of the gateway, never a part of it.

Kept out of the ``gateway`` package on purpose: the gateway must be able to
control an agent it does not own. If the loop lived inside the gateway, "the
gateway stops the run" would be a statement about one program's internals rather
than about a process boundary that a real agent would have to respect.

The exports are resolved lazily (PEP 562) for one concrete reason: ``loop``
speaks the MCP SDK, while ``tasks`` and ``policy`` are plain data and functions.
Re-exporting eagerly made ``from agent.tasks import load_tasks`` -- which reads a
JSON file and needs nothing at all -- pull in the whole SDK, so a bare
interpreter could not even *collect* the tests for the evidence model. Tests that
cannot be collected are tests that report as errors rather than skips, and an
error says "broken" where the truth is "not installed". Deferring the import
costs one dict and keeps the data layer reachable without the transport layer.
"""

from typing import TYPE_CHECKING

from .tasks import TaskSpec, load_task, load_tasks

if TYPE_CHECKING:  # pragma: no cover - for editors and type checkers only
    from .loop import RunResult, gateway_params, run_task, run_task_sync
    from .policy import (
        Action,
        AgentContext,
        Observation,
        Policy,
        ReactivePolicy,
        ScriptedPolicy,
    )

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

#: Names that come from the two SDK-dependent modules, mapped to their module.
_LAZY = {
    "Action": "policy",
    "AgentContext": "policy",
    "Observation": "policy",
    "Policy": "policy",
    "ReactivePolicy": "policy",
    "ScriptedPolicy": "policy",
    "RunResult": "loop",
    "gateway_params": "loop",
    "run_task": "loop",
    "run_task_sync": "loop",
}


def __getattr__(name: str):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # Imported here, not at module scope: ``loop`` needs the MCP SDK and
    # ``policy`` needs nothing, but both are kept together so one dict answers
    # "which module owns this name".
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache, so the second lookup skips this path
    return value


def __dir__():
    return sorted(__all__)
