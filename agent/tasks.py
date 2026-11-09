"""Task specs: what the agent is trying to finish.

Deliberately data, not code. The gateway needs ``required_evidence`` to judge
completeness, the agent needs ``goal`` to plan, and an experiment needs both plus
a stable id -- one file answers all three, and a task added in D11 requires no
Python.

JSON rather than YAML for the same reason as ``config.py``: zero dependencies and
a diff that a reviewer can read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

__all__ = ["PlanStep", "TaskSpec", "load_task", "load_tasks", "TASK_DIR"]

TASK_DIR = Path(__file__).resolve().parents[1] / "tasks"


@dataclass(frozen=True)
class PlanStep:
    """One call the agent intends to make.

    Declared rather than derived for most tasks. The alternative -- one
    hand-written policy class per question -- would put the planner's quality
    inside the measurement: a task set where "9 steps" partly means "this
    closure happened to re-query" cannot support "the gateway cut it to 4".

    What stays emergent is the *part that must be*: ``ReactivePolicy`` still
    builds its enrichment query from the previous result (t01), and the
    overshoot tail (``DeclaredPolicy._tail``) is a program, not a list.
    """

    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStep":
        tool = data.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError(f"plan step needs a tool name, got {tool!r}")
        args = data.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"plan step args must be an object, got {type(args).__name__}")
        return cls(tool=tool, args=dict(args))



@dataclass(frozen=True)
class TaskSpec:
    id: str
    goal: str
    required_evidence: Tuple[str, ...] = ()
    max_turns: int = 12
    #: The gateway's call budget for this task. ``None`` keeps the gateway
    #: default; a task that is expected to finish in four calls can say so, and
    #: then R3 firing is a property of the task, not of the harness's flags.
    max_calls: Optional[int] = None
    policy: str = "reactive_sqlite"
    #: Filled by D11/D12; absent here rather than faked.
    optimal_steps: Optional[int] = None
    #: How that number was arrived at, in words. Required by D11 for every task:
    #: a step count with no stated method is a number a reviewer has to trust.
    optimal_path: str = ""
    #: The declared plan (``policy: "declared"``). Empty for the reactive task,
    #: whose plan depends on what the previous call returned.
    plan: Tuple[PlanStep, ...] = ()
    #: For ``policy: "futile_search"``: the rephrasings the adversary cycles
    #: through. At least ``max_turns`` of them, so the spin never repeats a call
    #: exactly -- a repeat would hand the job to R1 and the task would stop
    #: measuring R2 (see ``tests/test_taskset.py``).
    futile_sql: Tuple[str, ...] = ()
    checker: Optional[str] = None
    source: Optional[Path] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], source: Optional[Path] = None) -> "TaskSpec":
        required = data.get("required_evidence") or ()
        if isinstance(required, str):
            raise ValueError("required_evidence must be a list, not a string")
        plan = tuple(PlanStep.from_dict(item) for item in (data.get("plan") or ()))
        futile = data.get("futile_sql") or ()
        if isinstance(futile, str):
            raise ValueError("futile_sql must be a list of statements, not a string")
        return cls(
            id=str(data["id"]),
            goal=str(data.get("goal", "")),
            required_evidence=tuple(str(item) for item in required),
            max_turns=int(data.get("max_turns", 12)),
            max_calls=(int(data["max_calls"]) if data.get("max_calls") is not None else None),
            policy=str(data.get("policy", "reactive_sqlite")),
            optimal_steps=data.get("optimal_steps"),
            optimal_path=str(data.get("optimal_path", "")),
            plan=plan,
            futile_sql=tuple(str(item) for item in futile),
            checker=data.get("checker"),
            source=source,
        )

    def gateway_args(self) -> List[str]:
        args: List[str] = []
        for ev_type in self.required_evidence:
            args.extend(["--require", ev_type])
        if self.max_calls is not None:
            args.extend(["--max-calls", str(self.max_calls)])
        return args


def load_task(path: Path | str) -> TaskSpec:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    return TaskSpec.from_dict(data, source=source)


def load_tasks(directory: Path | str = TASK_DIR) -> List[TaskSpec]:
    """All tasks, sorted by file name -- stable order matters for experiments."""
    root = Path(directory)
    if not root.exists():
        return []
    return [load_task(path) for path in sorted(root.glob("*.json"))]
