"""The evidence model: what counts as having learned a fact.

The question this module answers is not "does the answer look right" but "has
the run obtained this *kind* of fact". That is a deliberate narrowing, because
the only honest alternative is an LLM judging completeness -- using something
uncertain to check something uncertain, which is where "verifiable" stops
meaning anything (DESIGN A3).

Three inputs decide one ev_type
-------------------------------
An evidence item is a deterministic predicate over the *call* and its *result*:

    tool in producers          who is allowed to produce this fact
    args match the predicates   which question was asked
    result matches a shape      what came back

All three are required. The first is what stops a stub server's fake row set
from satisfying a task about the real database. The second is what stops "some
rows came back" from being confused with "the rows the task needs"; D9 started
with shapes only, and t01's checker caught the difference immediately -- R4
declared the task complete at step 3, before the star counts had been read at
all. That is the failure this project exists to make visible, so the argument
predicate is not a refinement, it is the fix.

An empty row set is evidence
----------------------------
``sql.rows.incidents`` matches ``{"rows": []}``. Asking a question and getting
nothing back *is* knowledge, and the adversarial task ``t02`` depends on it: a
model that required non-empty results would call "the repo does not exist" an
incomplete answer and let the run burn its whole budget. The distinction lives
in the config, per type, rather than as a global rule (``shapes`` names the keys
that must be present, never that they must be non-empty).

What this cannot do
-------------------
It proves the shape and subject of knowledge, not its correctness. A query
``where repo = 'ghost/typo'`` returns a row set with the right shape and subject
and is still the wrong answer; only the task checker (D12) can tell. And a
truncated result is treated as no evidence at all: a half-returned row set that
counted would be a *false* completion, which is the expensive direction of that
mistake (DESIGN 2.6 pitfall 2).

Determinism is a requirement, not a virtue
------------------------------------------
Extractors are pure functions of (payload, tool, args). Replay depends on it: a
trace is re-judged by re-running the stop rules, and an extractor that consulted
a clock or a random value would let the same bytes yield a different evidence
set the second time. That is why the model is data (``config/evidence.json``)
plus a fixed interpreter, instead of a registry that lets extractors be plugged
in at import time (DESIGN EP-3 wants exactly that, and this is the shape it
should take when it arrives).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

__all__ = [
    "EVIDENCE_VERSION",
    "EvidenceModel",
    "EvidenceType",
    "load_evidence_model",
]

EVIDENCE_VERSION = "evidence-v1"

#: The predicates the config may use on a call's arguments. Kept tiny on
#: purpose: every operator here is one a reviewer can check by hand against the
#: call in the trace, and an operator that needed a parser would make the
#: evidence model as hard to trust as the answer it is judging.
_OPS = ("contains", "equals")


@dataclass(frozen=True)
class EvidenceType:
    """One fact type: who can produce it, for which question, in what shape."""

    name: str
    why: str
    shapes: Tuple[Tuple[str, ...], ...]
    producers: Tuple[str, ...]
    args: Tuple[Tuple[str, str, str], ...] = ()

    def matches_shape(self, body: Mapping[str, Any]) -> bool:
        """Any declared shape, all of its keys present. Values are not inspected."""
        return any(all(key in body for key in shape) for shape in self.shapes)

    def matches_args(self, args: Mapping[str, Any]) -> bool:
        for key, op, value in self.args:
            actual = args.get(key)
            if actual is None:
                return False
            if op == "contains":
                if str(value).casefold() not in str(actual).casefold():
                    return False
            else:  # "equals"; validated at load time
                if str(actual) != str(value):
                    return False
        return True


class EvidenceModel:
    """The frozen set of fact types for this deployment.

    Loading is strict and loud. A typo in a task's ``required_evidence`` must
    fail at startup, not degrade into "the run never completes" -- the gateway
    refuses to start on an unknown type (``__main__``), and ``unavailable``
    treats one as unreachable as a second line of defence for callers that build
    a model directly.
    """

    def __init__(
        self, types: Mapping[str, EvidenceType], *, version: str = EVIDENCE_VERSION
    ) -> None:
        self._types = dict(types)
        self.version = version

    @classmethod
    def empty(cls) -> "EvidenceModel":
        """No extractor registered: no record may claim evidence (D6 behaviour)."""
        return cls({})

    # -- introspection -----------------------------------------------------

    @property
    def types(self) -> Tuple[str, ...]:
        return tuple(sorted(self._types))

    def spec(self, ev_type: str) -> Optional[EvidenceType]:
        return self._types.get(ev_type)

    def producers(self, ev_type: str) -> Tuple[str, ...]:
        spec = self._types.get(ev_type)
        return spec.producers if spec else ()

    def subjects_for(self, tool: str) -> Tuple[str, ...]:
        """The fact types a tool is declared able to produce.

        Read by the selector (D14) to build its index. The gateway knows two
        things about a tool that its description may not say: which upstream
        serves it and what kind of fact it can deliver. Only the second is
        useful for ranking, and it is already written down here -- a selector
        that had to infer "this ``query`` tool can answer questions about
        incidents" from the sentence "run a read-only SELECT" would be guessing.
        """
        return tuple(
            name for name in sorted(self._types) if tool in self._types[name].producers
        )

    # -- reachability ------------------------------------------------------

    def unavailable(
        self, required: Iterable[str], available: Iterable[str]
    ) -> Tuple[str, ...]:
        """Required types no available tool is allowed to produce.

        ``available`` is the namespaced set of tools the agent may call *now* --
        frozen and missing tools are already absent from it, which is precisely
        the meaning wanted here: the gateway's own admission decision, not a
        guess about the agent's intentions.

        An undeclared type is unreachable by definition: nothing can extract it.
        ``__main__`` rejects those at startup; this branch keeps the direct
        construction path honest instead of silently passing.
        """
        offered = set(available)
        out = []
        for ev_type in sorted(set(required)):
            producers = self.producers(ev_type)
            if not any(producer in offered for producer in producers):
                out.append(ev_type)
        return tuple(out)

    # -- extraction --------------------------------------------------------

    def extract(
        self,
        payload: Any,
        *,
        tool: str,
        args: Mapping[str, Any],
        truncated: bool = False,
    ) -> Dict[str, str]:
        """``payload -> {ev_type: producing tool}``. Pure; never raises on data."""
        if truncated:
            # A partial payload is not a complete fact. Counting it would end
            # the run early on evidence that was cut in half.
            return {}
        if isinstance(payload, Mapping) and (
            payload.get("is_error") is True or payload.get("isError") is True
        ):
            # An error result is the absence of an answer, not evidence of one.
            return {}
        body = _body_of(payload)
        if body is None:
            return {}
        out: Dict[str, str] = {}
        for name in sorted(self._types):
            spec = self._types[name]
            if tool not in spec.producers:
                continue
            if not spec.matches_args(args):
                continue
            if not spec.matches_shape(body):
                continue
            out[name] = tool
        return out

    def explain(self, ev_type: str, available: Iterable[str]) -> str:
        """Why a type is unreachable, in the words a reviewer needs."""
        spec = self._types.get(ev_type)
        if spec is None:
            return f"{ev_type} is not declared in {self.version}"
        offered = set(available)
        absent = [p for p in spec.producers if p not in offered]
        if not spec.producers:
            return f"{ev_type} declares no producer"
        return (
            f"{ev_type} can only be produced by {', '.join(spec.producers)}; "
            f"unavailable: {', '.join(absent)}"
        )


def _body_of(payload: Any) -> Optional[Mapping[str, Any]]:
    """The upstream's JSON body, out of the MCP envelope or a bare dict.

    Two shapes are accepted because two callers exist: the gateway passes
    ``CallToolResult.model_dump()`` (envelope with text blocks, or the newer
    structured content), and unit tests pass the body directly. Parsing the
    *first parseable* text block rather than the first block matters -- gateway
    metadata (the halt notice) is appended as an extra block, and insisting on
    block zero would make extraction fail exactly on the interesting turns.
    """
    if not isinstance(payload, Mapping):
        return None

    struct = payload.get("structuredContent") or payload.get("structured_content")
    if isinstance(struct, Mapping):
        return struct

    if "content" not in payload:
        # Already a body. No envelope, no blocks to unwrap.
        return payload

    for block in payload.get("content") or ():
        if not isinstance(block, Mapping) or block.get("type") != "text":
            continue
        try:
            data = json.loads(block.get("text") or "")
        except (TypeError, ValueError):
            continue
        if isinstance(data, Mapping):
            return data
    return None


def load_evidence_model(path: Path | str) -> EvidenceModel:
    """Read and validate ``config/evidence.json``. Raises ``ValueError`` loudly."""
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("evidence model must be a JSON object")

    version = str(data.get("version", ""))
    if version != EVIDENCE_VERSION:
        # The version is part of the contract: an evidence set computed under a
        # different model is not comparable with this one, and silently reading
        # it anyway is how two numbers end up in one table that mean different
        # things.
        raise ValueError(
            f"unsupported evidence model version {version!r} (expected {EVIDENCE_VERSION!r})"
        )

    raw_types = data.get("types")
    if not isinstance(raw_types, Mapping) or not raw_types:
        raise ValueError("evidence model has no types")

    types: Dict[str, EvidenceType] = {}
    for name, raw in raw_types.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"evidence type {name!r} must be an object")
        shapes = _shapes(name, raw.get("shapes"))
        producers = _strings(name, "producers", raw.get("producers"), required=True)
        predicates = _predicates(name, raw.get("args") or [])
        types[str(name)] = EvidenceType(
            name=str(name),
            why=str(raw.get("why", "")),
            shapes=shapes,
            producers=producers,
            args=predicates,
        )
    return EvidenceModel(types, version=version)


def _strings(
    name: str, field: str, value: Any, *, required: bool
) -> Tuple[str, ...]:
    if value is None:
        if required:
            raise ValueError(f"evidence type {name!r} is missing {field!r}")
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"evidence type {name!r}: {field!r} must be a list")
    items = tuple(str(item) for item in value)
    if required and not items:
        raise ValueError(f"evidence type {name!r}: {field!r} must not be empty")
    return items


def _shapes(name: str, value: Any) -> Tuple[Tuple[str, ...], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError(f"evidence type {name!r}: 'shapes' must be a non-empty list")
    shapes = []
    for shape in value:
        if (
            not isinstance(shape, Sequence)
            or isinstance(shape, (str, bytes))
            or not shape
        ):
            raise ValueError(
                f"evidence type {name!r}: each shape is a non-empty list of keys"
            )
        shapes.append(tuple(str(key) for key in shape))
    return tuple(shapes)


def _predicates(name: str, value: Any) -> Tuple[Tuple[str, str, str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"evidence type {name!r}: 'args' must be a list")
    out = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError(f"evidence type {name!r}: each arg predicate is an object")
        key = str(raw.get("key", ""))
        op = str(raw.get("op", ""))
        if not key:
            raise ValueError(f"evidence type {name!r}: arg predicate needs 'key'")
        if op not in _OPS:
            raise ValueError(
                f"evidence type {name!r}: unknown arg op {op!r} (known: {', '.join(_OPS)})"
            )
        out.append((key, op, str(raw.get("value", ""))))
    return tuple(out)
