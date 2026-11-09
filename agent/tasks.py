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
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["TaskSpec", "load_task", "load_tasks", "TASK_DIR"]

TASK_DIR = Path(__file__).resolve().parents[1] / "tasks"


@dataclass(frozen=True)
class TaskSpec:
    id: str
    goal: str
    required_evidence: Tuple[str, ...] = ()
    max_turns: int = 12
    policy: str = "reactive_sqlite"
    #: Filled by D11/D12; absent here rather than faked.
    optimal_steps: Optional[int] = None
    checker: Optional[str] = None
    source: Optional[Path] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], source: Optional[Path] = None) -> "TaskSpec":
        required = data.get("required_evidence") or ()
        if isinstance(required, str):
            raise ValueError("required_evidence must be a list, not a string")
        return cls(
            id=str(data["id"]),
            goal=str(data.get("goal", "")),
            required_evidence=tuple(str(item) for item in required),
            max_turns=int(data.get("max_turns", 12)),
            policy=str(data.get("policy", "reactive_sqlite")),
            optimal_steps=data.get("optimal_steps"),
            checker=data.get("checker"),
            source=source,
        )

    def gateway_args(self) -> List[str]:
        args: List[str] = []
        for ev_type in self.required_evidence:
            args.extend(["--require", ev_type])
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
