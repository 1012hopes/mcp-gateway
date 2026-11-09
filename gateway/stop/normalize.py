"""Parameter normalization: the step that decides whether R1 and R2 can fire at all.

The failure this module prevents is silent. Hash the raw arguments and two calls
that look identical to a human -- one carrying a fresh ``ts``, one with the keys
in a different order -- produce different digests forever. R1 then never fires,
the compression numbers become zero, and **nothing raises an error**. It is the
first risk in the plan's register (R1) for that reason.

The failure this module must not cause is worse. Normalize too eagerly and two
genuinely different calls collapse into one: the gateway halts a healthy run and
calls it a duplicate. A false duplicate is a wrong decision in the trace, so the
defaults here are deliberately narrow -- *miss a duplicate rather than invent
one* (DESIGN A2.1, last paragraph).

Three consequences of that choice, each visible in the code:

1. **Volatile keys are named, not guessed.** A key is dropped only when its name
   is provenance noise (``request_id``, ``nonce``). A UUID-shaped *value* under a
   semantic key (``{"id": "<uuid>"}``) is left alone: it may well be the thing
   being looked up.
2. **Clock-like keys need clock-like values.** ``{"ts": "2026-11-09T12:00:00Z"}``
   is dropped; ``{"time": "business_hours"}`` is not. Key name alone would
   confuse a timestamp with a mode.
3. **Case folding is declared per tool, never global.** ``sqlite.query`` may fold
   ``table`` because SQLite identifiers are case-insensitive; a tool that is
   case-sensitive must not, or two different calls would collide.

What is *not* normalized, and why:

* **SQL text.** Whitespace collapsing inside a string literal changes the query;
  doing it properly needs a parser. ``sql`` is compared byte-for-byte after a
  trim, which is enough because the policy emits SQL from a deterministic
  template -- an "identical" repeat is genuinely byte-identical.
* **List order.** ``in ('a','b')`` and ``in ('b','a')`` may be the same query,
  but sorting a list silently reorders arguments whose order is semantic
  (``steps``, ``columns``). Left as given.
* **Keys.** JSON keys come from the tool's schema; folding them would make
  ``{"Table": ...}`` and ``{"table": ...}`` the same call, which no schema says.

The output is plain JSON (dict/list/str/int/float/bool/None) so that
``trace.canonical.digest_of`` can hash it deterministically. That is the only
reason this module may not return, say, a sorted tuple of pairs.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from typing import Any, FrozenSet, Mapping, Optional

__all__ = [
    "DEFAULT_SPEC",
    "NormalizeSpec",
    "SPECS_BY_PREFIX",
    "SQLITE_SPEC",
    "key_token",
    "normalize_args",
    "spec_for",
]

#: Six decimals: enough to keep a measured value distinguishable, few enough that
#: ``0.1 + 0.2`` and ``0.3`` land on the same digest. Chosen to match the
#: float rule in ``trace/canonical.py`` -- two precisions would make a value hash
#: one way in the trace and another in the call identity.
FLOAT_PRECISION = 6

#: Below this, a number under a clock-named key is more likely a duration or a
#: count than an epoch. 2001-09-09 in seconds; a tool taking "time" as seconds
#: since the epoch will always be above it.
EPOCH_FLOOR = 1_000_000_000

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

#: Provenance noise: it says *when/for whom* the call was made, never *what* it
#: asks. Compared as tokens, so ``request_id``, ``requestId`` and ``Request-Id``
#: are one entry. Adding to this set is a semantic decision, not a convenience:
#: every name here is a promise that two different values mean the same query.
VOLATILE_TOKENS: FrozenSet[str] = frozenset(
    {
        "nonce",
        "uuid",
        "guid",
        "requestid",
        "requestuuid",
        "traceid",
        "spanid",
        "correlationid",
        "idempotencykey",
        "clienttime",
        "clientts",
        "cachebuster",
        "_",
    }
)

#: Ambiguous by name alone -- these need a clock-shaped value to qualify.
CLOCK_TOKENS: FrozenSet[str] = frozenset(
    {"timestamp", "ts", "time", "now", "clock", "walltime", "calledat"}
)

_CLOCK_SHAPED = re.compile(
    r"^\d{4}-\d{2}-\d{2}"  # a date is the anchor
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"  # optional time
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$"  # optional zone
)


def key_token(key: str) -> str:
    """``request_id`` / ``requestId`` / ``X-Request-ID`` -> ``requestid``."""
    return _TOKEN_SPLIT.sub("", key.lower())


@dataclass(frozen=True)
class NormalizeSpec:
    """One tool family's normalization policy.

    All key sets hold *tokens* (see ``key_token``), not raw key names.
    """

    volatile_keys: FrozenSet[str] = frozenset(VOLATILE_TOKENS)
    clock_keys: FrozenSet[str] = frozenset(CLOCK_TOKENS)
    case_insensitive_keys: FrozenSet[str] = frozenset()
    path_keys: FrozenSet[str] = frozenset()
    float_precision: int = FLOAT_PRECISION

    def merge(self, **changes: Any) -> "NormalizeSpec":
        """Derive a spec from this one. Used to build per-tool families."""
        return replace(self, **changes)


DEFAULT_SPEC = NormalizeSpec()

#: SQLite identifiers are case-insensitive, so ``table`` may be folded. Nothing
#: else is: string *values* in SQLite compare case-sensitively by default, and
#: folding them would merge ``'Foo'`` with ``'foo'``.
SQLITE_SPEC = DEFAULT_SPEC.merge(case_insensitive_keys=frozenset({"table", "column", "columns"}))

#: Keyed by the namespace prefix of the normalized tool name (``sqlite.query`` ->
#: ``sqlite``). A tool family not listed here gets ``DEFAULT_SPEC``, i.e. the
#: conservative policy -- an unknown tool is exactly where a wrong guess is most
#: likely. This dict is the extension point for a new upstream (DESIGN 2.8).
SPECS_BY_PREFIX = {"sqlite": SQLITE_SPEC}


def spec_for(tool: Optional[str]) -> NormalizeSpec:
    if not tool:
        return DEFAULT_SPEC
    return SPECS_BY_PREFIX.get(tool.split(".", 1)[0], DEFAULT_SPEC)


def normalize_args(value: Any, spec: NormalizeSpec = DEFAULT_SPEC) -> Any:
    """Return a JSON-shaped copy with provenance noise removed."""
    return _normalize(value, spec, key=None)


def _normalize(value: Any, spec: NormalizeSpec, *, key: Optional[str]) -> Any:
    if isinstance(value, Mapping):
        out = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                # JSON objects have string keys. A non-string key here means the
                # caller built the args in Python, and silently stringifying it
                # would hide the bug behind a stable-looking digest.
                raise TypeError(f"argument keys must be strings, got {type(raw_key).__name__}")
            token = key_token(raw_key)
            if token in spec.volatile_keys:
                continue
            if token in spec.clock_keys and _looks_like_clock(item):
                continue
            out[raw_key] = _normalize(item, spec, key=token)
        return out

    if isinstance(value, (list, tuple)):
        # No sorting, and no key context: ``{"tags": ["A"]}`` must not lowercase
        # the element just because the list also happens to be case-insensitive.
        return [_normalize(item, spec, key=None) for item in value]

    if isinstance(value, bool) or value is None:
        return value

    if isinstance(value, float):
        rounded = round(value, spec.float_precision)
        if rounded == 0:
            return 0  # folds -0.0, and 0.0 -> 0 so ``0`` and ``0.0`` agree
        if rounded.is_integer() and abs(rounded) < 2**53:
            # ``{"limit": 5.0}`` and ``{"limit": 5}`` are the same request. A
            # JSON encoder may have produced either for the same number.
            return int(rounded)
        return rounded

    if isinstance(value, int):
        # Large integers are ids, and an id is semantic. No truncation here.
        return value

    if isinstance(value, str):
        text = value.strip()
        if key is not None and key in spec.case_insensitive_keys:
            text = text.lower()
        if key is not None and key in spec.path_keys:
            # POSIX rules, not os.path: the upstream may not be on this machine,
            # and a digest that depends on the client's OS is not reproducible.
            text = posixpath.normpath(text)
        return text

    return value


def _looks_like_clock(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(_CLOCK_SHAPED.match(value.strip()))
    if isinstance(value, (int, float)):
        return abs(value) >= EPOCH_FLOOR
    return False
