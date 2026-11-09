"""Gateway configuration.

JSON rather than YAML on purpose: the config must be diffable and machine
readable ("configuration belongs to the machine, prose belongs to the wiki" --
DESIGN 2.7 rule 2), and JSON needs no dependency. If YAML is wanted later, this
is the only file that changes.

Two substitutions keep the file portable between machines and virtualenvs:

* ``{python}`` -> the interpreter running the gateway. Hard-coding a venv path
  would break the moment someone uses a different one.
* relative paths -> resolved against the repository root, not the CWD, so the
  gateway behaves the same however it is launched.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

__all__ = [
    "GatewayConfig",
    "UpstreamSpec",
    "load_config",
    "DEFAULT_CONFIG_PATH",
    "repo_root",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "gateway.json"


def repo_root() -> Path:
    return REPO_ROOT


@dataclass(frozen=True)
class UpstreamSpec:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    env: Mapping[str, str] = field(default_factory=dict)
    call_timeout_s: float = 30.0


@dataclass(frozen=True)
class GatewayConfig:
    name: str = "mcp-gateway"
    trace_dir: Path = REPO_ROOT / "traces" / "runs"
    model_id: str = "unlabeled-agent"
    schema_manifest: Path = REPO_ROOT / "config" / "schema-manifest.json"
    upstreams: List[UpstreamSpec] = field(default_factory=list)

    @property
    def trace_root(self) -> Path:
        return self.trace_dir


def _resolve(value: str) -> str:
    value = value.replace("{python}", sys.executable)
    value = value.replace("{root}", str(REPO_ROOT))
    return value


def _path(value: str) -> Path:
    candidate = Path(_resolve(value))
    return candidate if candidate.is_absolute() else (REPO_ROOT / candidate)


def load_config(path: Optional[str] = None) -> GatewayConfig:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    data: Dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))

    gateway = data.get("gateway", {})
    specs: List[UpstreamSpec] = []
    for raw in data.get("upstreams", []):
        cwd = raw.get("cwd")
        specs.append(
            UpstreamSpec(
                name=raw["name"],
                command=_resolve(raw["command"]),
                args=[_resolve(str(a)) for a in raw.get("args", [])],
                cwd=str(_path(cwd)) if cwd else str(REPO_ROOT),
                env={k: _resolve(str(v)) for k, v in raw.get("env", {}).items()},
                call_timeout_s=float(raw.get("call_timeout_s", 30.0)),
            )
        )

    if not specs:
        raise ValueError(f"no upstreams configured in {config_path}")

    return GatewayConfig(
        name=gateway.get("name", "mcp-gateway"),
        trace_dir=_path(gateway.get("trace_dir", "traces/runs")),
        model_id=gateway.get("model_id", "unlabeled-agent"),
        schema_manifest=_path(gateway.get("schema_manifest", "config/schema-manifest.json")),
        upstreams=specs,
    )
