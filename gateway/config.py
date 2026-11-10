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
    "GatingSpec",
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
class GatingSpec:
    """A1's window, as configuration.

    Disabled by default, and that default is load-bearing: every trace written
    before D14 records ``offered`` as the whole catalogue, and a config file
    that silently switched to a narrower surface would make those files describe
    a gateway that no longer exists. A deployment opts in, and the config it
    opted in with is on disk.
    """

    enabled: bool = False
    k: int = 5
    m: int = 10
    #: ``name`` / ``description`` / ``enriched`` -- see ``selector.INDEX_VARIANTS``.
    #: Kept configurable because the demo reports all three: the difference
    #: between them is the only honest account of why the gate works.
    index: str = "enriched"

    def __post_init__(self) -> None:
        if self.k < 1:
            raise ValueError(f"gating.k must be >= 1, got {self.k}")
        if self.m < self.k:
            raise ValueError(f"gating.m must be >= gating.k ({self.m} < {self.k})")


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
    #: The evidence model (D9). A path, not a model object: this file describes
    #: *where the deployment keeps its facts*, and the gateway loads it once at
    #: startup so a malformed model is a refusal to start, not a run that
    #: silently never completes.
    evidence_model: Path = REPO_ROOT / "config" / "evidence.json"
    upstreams: List[UpstreamSpec] = field(default_factory=list)
    gating: GatingSpec = field(default_factory=GatingSpec)

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

    raw_gating = gateway.get("gating") or {}
    if not isinstance(raw_gating, dict):
        raise ValueError(f"gateway.gating must be an object in {config_path}")
    gating = GatingSpec(
        enabled=bool(raw_gating.get("enabled", False)),
        k=int(raw_gating.get("k", 5)),
        m=int(raw_gating.get("m", 10)),
        index=str(raw_gating.get("index", "enriched")),
    )

    return GatewayConfig(
        name=gateway.get("name", "mcp-gateway"),
        trace_dir=_path(gateway.get("trace_dir", "traces/runs")),
        model_id=gateway.get("model_id", "unlabeled-agent"),
        schema_manifest=_path(gateway.get("schema_manifest", "config/schema-manifest.json")),
        evidence_model=_path(gateway.get("evidence_model", "config/evidence.json")),
        upstreams=specs,
        gating=gating,
    )
