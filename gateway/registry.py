"""The tool registry: the one place that decides what the agent can see.

Three jobs, in the order they must happen (DESIGN 2.6 "语义层"):

1. **Normalize the schema.** Real servers ship ``$ref``, ``oneOf``, missing
   ``type``, and ``required`` entries that are absent from ``properties``. If the
   gateway forwards that verbatim, two things break: the model reads a schema it
   cannot parse, and evidence extraction (D9) has no stable field signature to
   match on. Normalizing is therefore not cosmetics -- it is the precondition for
   the evidence rules, which is why D5 does it before any rule exists.

2. **Name deterministically.** Every tool is ``<upstream>.<tool>``. Names are
   sanitized to the MCP-legal character set, escaped rather than dropped, and
   truncated with a hash suffix when they exceed the length limit. Sanitizing
   changes what a trace calls a tool, so it has to be *stable across restarts*:
   a name that depends on iteration order makes two runs incomparable.

3. **Freeze on drift.** ``schema_hash`` covers exactly what the agent saw
   (normalized schema + normalized description). When the baseline in
   ``config/schema-manifest.json`` disagrees, the tool is withdrawn from the
   offering rather than silently served under a different contract -- otherwise
   historical traces and current behaviour are quietly mixed.

What this module deliberately does *not* do: validate arguments. The gateway is
not a validator; the upstream is. The schema here is informational (for the
model) and identificational (for the hash). Adding validation would create a
second source of truth about what a valid call is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .trace.canonical import canonical_text, digest_of

__all__ = [
    "MAX_EXPOSED_NAME",
    "NAMESPACE_SEP",
    "RegistryError",
    "RawTool",
    "ToolEntry",
    "Registry",
    "build_registry",
    "normalize_schema",
    "normalize_description",
    "load_baseline",
    "write_baseline",
]

NAMESPACE_SEP = "."

#: MCP tool names are at most 64 characters. The upstream prefix is part of the
#: name the agent sees, so it eats into the budget -- a 20-character upstream
#: name leaves 43 for the tool.
MAX_EXPOSED_NAME = 64

#: Descriptions longer than this get a note (not a truncation). Dropping text
#: would silently remove constraints the model needs; the context budget is the
#: gating layer's problem (D14), not the registry's.
LONG_DESCRIPTION_CHARS = 512

_SAFE_CHARS = re.compile(r"[A-Za-z0-9_.-]")
_LOCAL_REF = "#/"
_MAX_REF_DEPTH = 32


class RegistryError(RuntimeError):
    """The tool set cannot be assembled coherently. Fail at startup, not later."""


# ---------------------------------------------------------------------------
# input / output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawTool:
    """A tool exactly as the upstream advertised it -- before any normalization."""

    upstream: str
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ToolEntry:
    """What the gateway exposes, plus why it looks that way."""

    upstream: str
    original_name: str
    exposed_name: str
    description: str
    input_schema: Mapping[str, Any]
    schema_hash: str
    notes: Tuple[str, ...] = ()
    frozen: bool = False
    drift: Optional[str] = None

    @property
    def offered(self) -> bool:
        """Frozen tools are kept in the registry but never offered."""
        return not self.frozen

    def to_mcp(self) -> Any:
        from mcp import types

        return types.Tool(
            name=self.exposed_name,
            description=self.description or None,
            input_schema=dict(self.input_schema),
        )


# ---------------------------------------------------------------------------
# schema normalization
# ---------------------------------------------------------------------------


def _lookup_ref(root: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    if not isinstance(ref, str) or not ref.startswith(_LOCAL_REF):
        raise KeyError(f"only local refs are resolvable: {ref!r}")
    node: Any = root
    for raw in ref[len(_LOCAL_REF):].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or token not in node:
            raise KeyError(ref)
        node = node[token]
    return node


def _resolve_refs(
    node: Any,
    root: Mapping[str, Any],
    trail: frozenset,
    notes: List[str],
    depth: int,
) -> Any:
    if depth > _MAX_REF_DEPTH:
        notes.append("ref-depth-exceeded")
        return {}
    if not isinstance(node, Mapping):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref in trail:
            # A cycle is legal in JSON Schema and unusable in a prompt. Cut it,
            # say so, and keep the rest of the schema intact.
            notes.append(f"ref-cycle:{ref}")
            return {}
        try:
            target = _lookup_ref(root, ref)
        except KeyError:
            notes.append(f"unresolved-ref:{ref}")
            return {}
        resolved = _resolve_refs(target, root, trail | {ref}, notes, depth + 1)
        notes.append(f"resolved-ref:{ref}")
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        if siblings:
            notes.append(f"ref-with-siblings:{ref}")
            merged = dict(resolved) if isinstance(resolved, Mapping) else {}
            merged.update(
                {
                    k: _resolve_refs(v, root, trail, notes, depth + 1)
                    for k, v in siblings.items()
                }
            )
            return merged
        return resolved

    out: Dict[str, Any] = {}
    for key, value in node.items():
        if key in ("properties", "patternProperties") and isinstance(value, Mapping):
            out[key] = {
                name: _resolve_refs(sub, root, trail, notes, depth + 1)
                for name, sub in value.items()
            }
        elif key in ("items", "additionalProperties", "not", "contains", "if", "then", "else"):
            out[key] = (
                _resolve_refs(value, root, trail, notes, depth + 1)
                if isinstance(value, Mapping)
                else value
            )
        elif isinstance(value, list):
            out[key] = [
                _resolve_refs(item, root, trail, notes, depth + 1) for item in value
            ]
        else:
            out[key] = value
    return out


def _dedupe(items: Sequence[Any]) -> List[Any]:
    seen: Dict[str, Any] = {}
    for item in items:
        seen.setdefault(canonical_text(item), item)
    return list(seen.values())


def _flatten_variants(
    schema: Mapping[str, Any], notes: List[str]
) -> Mapping[str, Any]:
    """Surface argument names that live inside ``oneOf`` / ``anyOf`` branches.

    This is the case that makes ``$ref``-heavy servers unusable: the tool takes
    an object, every field is described in a variant, and ``properties`` is
    empty -- so the model sees a tool with no arguments and calls it wrong.

    Order matters: this runs *after* ``$ref`` resolution, because the variants
    themselves are usually refs.

    What is merged: property **names**, with each name's per-branch schema kept
    as ``anyOf`` when the branches disagree. What is not merged: required-ness
    beyond the intersection (a field required in one variant is not required by
    the tool). The original ``oneOf`` stays in place, so validation semantics
    are unchanged -- and the gateway does not validate anyway.
    """
    for key in ("oneOf", "anyOf"):
        branches = schema.get(key)
        if not isinstance(branches, list) or not branches:
            continue
        if schema.get("properties"):
            return schema  # already has a visible surface; do not invent a second one
        objects = [
            b
            for b in branches
            if isinstance(b, Mapping) and isinstance(b.get("properties"), Mapping)
        ]
        if len(objects) < 2:
            continue

        collected: Dict[str, List[Any]] = {}
        required: Optional[set] = None
        for branch in objects:
            for name, sub in branch["properties"].items():
                collected.setdefault(name, []).append(sub)
            branch_required = set(branch.get("required") or [])
            required = (
                branch_required if required is None else (required & branch_required)
            )

        properties: Dict[str, Any] = {}
        for name, variants in collected.items():
            unique = _dedupe(variants)
            properties[name] = unique[0] if len(unique) == 1 else {"anyOf": unique}

        merged = dict(schema)
        merged["properties"] = properties
        if required:
            merged["required"] = sorted(required)
        notes.append(f"flattened-{key}:{len(objects)}-branches")
        return merged
    return schema


def _complete_required(
    schema: Mapping[str, Any], notes: List[str]
) -> Mapping[str, Any]:
    """A ``required`` name missing from ``properties`` cannot be passed at all.

    With ``additionalProperties: false`` an argument not listed in ``properties``
    is rejected by a strict client, which makes the tool uncallable by
    construction -- exactly what ``dirty_schema_server`` ships on purpose. Listing
    the name with an unconstrained schema repairs reachability without inventing
    a type.
    """
    required = schema.get("required")
    if required is None:
        return schema

    out = dict(schema)
    if not isinstance(required, list):
        notes.append("required-not-a-list")
        out.pop("required", None)
        return out

    properties = dict(schema.get("properties") or {})
    kept: List[str] = []
    for name in required:
        if not isinstance(name, str):
            notes.append("required-non-string")
            continue
        kept.append(name)
        if name not in properties:
            properties[name] = {}
            notes.append(f"required-not-in-properties:{name}")
    if "properties" in out or properties:
        out["properties"] = properties
    out["required"] = kept
    return out


def _ensure_object(schema: Mapping[str, Any], notes: List[str]) -> Mapping[str, Any]:
    kind = schema.get("type")
    if kind is None:
        if isinstance(schema.get("properties"), Mapping) or schema.get("required"):
            notes.append("type-inferred:object")
            out = dict(schema)
            out["type"] = "object"
            return out
        # A bare ``{}`` is legal and means "anything". A tool argument list is
        # an object, so say so -- otherwise every consumer has to special-case.
        notes.append("assumed-empty-object")
        return {"type": "object", "properties": {}}
    if kind == "object" and "properties" not in schema:
        notes.append("object-without-properties")
        out = dict(schema)
        out["properties"] = {}
        return out
    return schema


def _stabilize(node: Any) -> Any:
    """Order-independent containers get one order, so hashes survive reordering.

    ``required`` and ``enum`` are sets; ``oneOf``/``anyOf``/``allOf`` are
    commutative. A server that reorders them has not changed its contract, and
    the hash must not claim otherwise -- a false drift report is worse than none,
    because it trains you to ignore the alarm.
    """
    if isinstance(node, Mapping):
        out: Dict[str, Any] = {}
        for key, value in node.items():
            if key in ("required", "enum") and isinstance(value, list):
                out[key] = sorted(value, key=canonical_text)
            elif key in ("oneOf", "anyOf", "allOf") and isinstance(value, list):
                out[key] = sorted((_stabilize(v) for v in value), key=canonical_text)
            else:
                out[key] = _stabilize(value)
        return out
    if isinstance(node, list):
        return [_stabilize(v) for v in node]
    return node


def normalize_schema(
    raw: Optional[Mapping[str, Any]],
) -> Tuple[Dict[str, Any], List[str]]:
    """Return ``(schema the agent sees, notes explaining every change)``."""
    notes: List[str] = []
    if not isinstance(raw, Mapping):
        return {"type": "object", "properties": {}}, ["schema-not-an-object"]

    root = dict(raw)
    resolved = _resolve_refs(root, root, frozenset(), notes, 0)
    if not isinstance(resolved, Mapping):
        notes.append("schema-not-an-object")
        resolved = {}

    schema: Mapping[str, Any] = dict(resolved)
    refs = [note for note in notes if note.startswith("resolved-ref:")]
    if refs:
        notes[:] = [note for note in notes if not note.startswith("resolved-ref:")]
        notes.append(f"resolved-refs:{len(refs)}")
    for key in ("$defs", "definitions"):
        if key in schema:
            # Every local ref has been inlined by now, so the definition block is
            # dead weight the model would still have to read -- and its own keys
            # are not arguments.
            schema = {k: v for k, v in schema.items() if k != key}
            notes.append(f"stripped-defs:{key}")
    schema = _flatten_variants(schema, notes)
    schema = _complete_required(schema, notes)
    schema = _ensure_object(schema, notes)
    return dict(_stabilize(schema)), notes


def normalize_description(text: Any) -> Tuple[str, List[str]]:
    """Collapse whitespace; keep every word.

    Wrapping is presentation. A description that differs only in line breaks has
    not changed, so it must not change the hash -- but the text itself is kept
    whole, because dropping the tail of a long description silently removes
    constraints the model needs.
    """
    if not isinstance(text, str):
        return "", ["description-not-a-string"]
    collapsed = " ".join(text.split())
    notes = [] if len(collapsed) <= LONG_DESCRIPTION_CHARS else [
        f"long-description:{len(collapsed)}"
    ]
    return collapsed, notes


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------


def _short4(text: str) -> str:
    return digest_of({"name": text})[:4]


def _fit(upstream: str, tool: str, suffix: str = "") -> Tuple[str, List[str]]:
    """Qualified name, truncated to the limit while keeping the namespace.

    The prefix is load-bearing (it is how a trace says which server ran), so the
    truncation happens on the tool part and the disambiguating suffix is kept.
    """
    body = tool + suffix
    exposed = f"{upstream}{NAMESPACE_SEP}{body}"
    if len(exposed) <= MAX_EXPOSED_NAME:
        return exposed, []

    tail = suffix or f"-{_short4(tool)}"
    budget = MAX_EXPOSED_NAME - len(upstream) - len(NAMESPACE_SEP) - len(tail)
    if budget < 1:
        raise RegistryError(
            f"upstream name {upstream!r} leaves no room for a tool name "
            f"within {MAX_EXPOSED_NAME} characters"
        )
    return (
        f"{upstream}{NAMESPACE_SEP}{tool[:budget]}{tail}",
        [f"name-truncated:{upstream}{NAMESPACE_SEP}{body}"],
    )


def _sanitize(tool: str) -> Tuple[str, List[str]]:
    if not tool:
        return "unnamed", ["empty-tool-name"]
    escaped = "".join(
        ch if _SAFE_CHARS.fullmatch(ch) else f"_u{ord(ch):04x}" for ch in tool
    )
    return escaped, ([] if escaped == tool else [f"name-escaped:{tool}"])


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def build_registry(
    listing: Iterable[Tuple[str, Sequence[RawTool]]],
    *,
    baseline: Optional[Mapping[str, str]] = None,
) -> Registry:
    """Assemble all upstreams into one namespaced, normalized, hashed tool set.

    ``listing`` order is the exposure order, and it comes from the config file --
    so the same config produces the same ``offered`` list on every start.
    """
    entries: List[ToolEntry] = []
    notes: List[str] = []
    seen: Dict[str, ToolEntry] = {}

    for upstream, tools in listing:
        if not upstream or NAMESPACE_SEP in upstream:
            raise RegistryError(
                f"upstream name must be non-empty and dot-free: {upstream!r}"
            )

        for raw in tools:
            description, desc_notes = normalize_description(raw.description)
            schema, schema_notes = normalize_schema(raw.input_schema)
            safe, name_notes = _sanitize(raw.name)
            exposed, fit_notes = _fit(upstream, safe)

            clash = seen.get(exposed)
            if clash is not None:
                if clash.upstream == upstream and clash.original_name == raw.name:
                    notes.append(f"duplicate-listed:{exposed}")
                    continue
                # Two different tools collapsing onto one name -- only possible
                # after escaping or truncation. Disambiguate deterministically
                # instead of failing the whole gateway for one bad upstream.
                exposed, fit_notes = _fit(
                    upstream, safe, suffix=f"-{_short4(raw.name)}"
                )
                if exposed in seen:
                    raise RegistryError(
                        f"tool name collision on {exposed!r}: "
                        f"{clash.original_name!r} and {raw.name!r} both map to it"
                    )
                notes.append(f"name-collision-resolved:{raw.name}->{exposed}")

            schema_hash = digest_of(
                {"description": description, "input_schema": schema}
            )
            entry_notes = tuple(desc_notes + schema_notes + name_notes + fit_notes)

            frozen, drift = False, None
            if baseline is not None:
                prior = baseline.get(exposed)
                if prior is None:
                    notes.append(f"new-tool:{exposed}")
                elif prior != schema_hash:
                    frozen = True
                    drift = f"{prior[:12]}->{schema_hash[:12]}"
                    notes.append(f"schema-drift:{exposed}")

            entry = ToolEntry(
                upstream=upstream,
                original_name=raw.name,
                exposed_name=exposed,
                description=description,
                input_schema=schema,
                schema_hash=schema_hash,
                notes=entry_notes,
                frozen=frozen,
                drift=drift,
            )
            seen[exposed] = entry
            entries.append(entry)

    if baseline is not None:
        for name in sorted(set(baseline) - set(seen)):
            notes.append(f"removed-tool:{name}")

    return Registry(entries, notes, baseline_present=baseline is not None)


class Registry:
    """An immutable view of one run's tool surface."""

    def __init__(
        self,
        entries: Sequence[ToolEntry],
        notes: Sequence[str] = (),
        *,
        baseline_present: bool = False,
    ) -> None:
        self._entries: Tuple[ToolEntry, ...] = tuple(entries)
        self.notes: Tuple[str, ...] = tuple(notes)
        self.baseline_present = baseline_present
        self._by_name = {entry.exposed_name: entry for entry in self._entries}

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterable[ToolEntry]:
        return iter(self._entries)

    @property
    def entries(self) -> Tuple[ToolEntry, ...]:
        return self._entries

    @property
    def offered(self) -> Tuple[ToolEntry, ...]:
        return tuple(e for e in self._entries if e.offered)

    @property
    def frozen(self) -> Tuple[ToolEntry, ...]:
        return tuple(e for e in self._entries if e.frozen)

    def offered_names(self) -> List[str]:
        return [e.exposed_name for e in self.offered]

    def get(self, exposed_name: str) -> Optional[ToolEntry]:
        return self._by_name.get(exposed_name)

    def by_upstream(self) -> Dict[str, List[ToolEntry]]:
        grouped: Dict[str, List[ToolEntry]] = {}
        for entry in self._entries:
            grouped.setdefault(entry.upstream, []).append(entry)
        return grouped

    def manifest(self) -> Dict[str, Any]:
        """The baseline format -- what ``scripts/freeze_schema.py`` writes."""
        return {
            "version": 1,
            "tools": {
                entry.exposed_name: entry.schema_hash
                for entry in sorted(self._entries, key=lambda e: e.exposed_name)
            },
        }

    def warnings(self) -> List[str]:
        return [
            note
            for note in self.notes
            if note.startswith(("schema-drift:", "new-tool:", "removed-tool:"))
        ]


# ---------------------------------------------------------------------------
# baseline persistence
# ---------------------------------------------------------------------------


def load_baseline(path: Path) -> Optional[Dict[str, str]]:
    """Read the frozen hashes. ``None`` means "no baseline yet" (first run)."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    tools = data.get("tools")
    if not isinstance(tools, dict):
        raise RegistryError(f"malformed schema manifest: {path}")
    return {str(k): str(v) for k, v in tools.items()}


def write_baseline(path: Path, registry: Registry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(registry.manifest(), indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
