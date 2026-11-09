"""Deterministic serialization for the trace hash chain.

Why this module exists
----------------------
A hash chain is only as trustworthy as its serialization is deterministic.
If one logical record can serialize to two different byte strings, ``verify``
reports tampering where there is none -- and, worse, real tampering hides in
the resulting noise. So the rule is: **one record, exactly one byte string.**

Enforced here (see DESIGN A2.1 / A4):

1. keys sorted              -> dict insertion order never matters
2. no whitespace            -> separators ``(",", ":")``
3. UTF-8, no ``\\uXXXX``     -> CJK content stays readable in the file
4. floats rounded to 6 dp   -> ``0.1 + 0.2`` and ``0.3`` hash the same
5. ``-0.0`` folded to ``0.0``, NaN/Inf rejected -> fail loudly, never silently
6. ``drop_keys``            -> volatile fields (timestamps, nonces) can be
                               excluded before hashing

Note what is *not* here: string trimming and enum lower-casing. Those are
argument-normalization rules (A2.1 step 4) and belong to the tool-argument
layer, not to the record layer -- trimming a tool *result* or a wiki snippet
would corrupt content that must stay verbatim.
"""

from __future__ import annotations

import json
import math
from typing import Any, FrozenSet, Iterable, Mapping

DEFAULT_FLOAT_NDIGITS = 6

__all__ = [
    "DEFAULT_FLOAT_NDIGITS",
    "NonCanonicalValue",
    "normalize",
    "canonical_bytes",
    "canonical_text",
]


class NonCanonicalValue(TypeError):
    """Raised when a value cannot be serialized deterministically."""


def normalize(
    obj: Any,
    *,
    drop_keys: FrozenSet[str] = frozenset(),
    float_ndigits: int = DEFAULT_FLOAT_NDIGITS,
) -> Any:
    """Return a structurally identical value that serializes deterministically.

    ``drop_keys`` is applied at every nesting level: a key named ``nonce`` is
    removed wherever it appears. That is intentional -- volatile identifiers are
    volatile regardless of which object holds them.
    """
    if obj is None or isinstance(obj, bool):
        return obj

    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise NonCanonicalValue(f"non-finite float is not canonical: {obj!r}")
        rounded = round(obj, float_ndigits)
        # -0.0 == 0.0 in value but hashes differently after formatting.
        return 0.0 if rounded == 0 else rounded

    if isinstance(obj, int):
        return obj

    if isinstance(obj, str):
        return obj

    if isinstance(obj, bytes):
        raise NonCanonicalValue("bytes must be decoded before canonicalization")

    if isinstance(obj, Mapping):
        out = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                raise NonCanonicalValue(f"non-string key is not canonical: {key!r}")
            if key in drop_keys:
                continue
            out[key] = normalize(
                value, drop_keys=drop_keys, float_ndigits=float_ndigits
            )
        return out

    if isinstance(obj, (list, tuple)):
        return [
            normalize(v, drop_keys=drop_keys, float_ndigits=float_ndigits)
            for v in obj
        ]

    # Objects that know how to become a dict (dataclasses, pydantic models).
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return normalize(
            to_dict(), drop_keys=drop_keys, float_ndigits=float_ndigits
        )

    raise NonCanonicalValue(f"unsupported type: {type(obj).__name__}")


def canonical_bytes(
    obj: Any,
    *,
    drop_keys: FrozenSet[str] = frozenset(),
    float_ndigits: int = DEFAULT_FLOAT_NDIGITS,
) -> bytes:
    """Serialize ``obj`` to the single byte string used for hashing."""
    normalized = normalize(
        obj, drop_keys=drop_keys, float_ndigits=float_ndigits
    )
    text = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def canonical_text(
    obj: Any,
    *,
    drop_keys: Iterable[str] = (),
    float_ndigits: int = DEFAULT_FLOAT_NDIGITS,
) -> str:
    """Same as :func:`canonical_bytes` but decoded -- for human inspection."""
    return canonical_bytes(
        obj, drop_keys=frozenset(drop_keys), float_ndigits=float_ndigits
    ).decode("utf-8")
