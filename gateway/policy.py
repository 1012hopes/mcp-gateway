"""Caller-declared policy: redaction today (whitelist, approval, budget later).

This module exists because of one ordering requirement (FR-14):
**redact before hashing.** Two calls that differ only in a credential are the
same call, and if the credential reaches ``args_hash`` they never will be. The
consequence of getting it wrong is the quiet kind this project keeps meeting: the
digest looks fine, R1 never fires, and no error is raised anywhere.

Why redaction belongs in the identity path rather than in the trace writer: the
trace already stores only digests, so a secret's plaintext never lands on disk
either way. What it *does* land in is the digest -- and a digest computed over a
rotating token makes the same question hash differently on every run, which
breaks duplicate detection, the R2 plateau, and replay comparison at once.

Two judgments, both narrow on purpose
-------------------------------------
1. **A credential-named key hides its whole value.** ``{"token": "abc"}`` and
   ``{"token": "xyz"}`` must produce one identity, so the value is replaced by a
   fixed marker rather than masked per character. Replacing the whole subtree
   (a credential object inside a ``credentials`` key) is the same decision one
   level up: there is no part of it worth keeping, and keeping structure would
   only invite a future reader to think the shape is meaningful. The name match
   covers the whole token or its tail, so header-style names (``X-Api-Key``)
   are included -- see :meth:`Redactor.hides`.
2. **A credential-shaped string is scrubbed wherever it appears.** A token pasted
   into a ``message`` or an error tail is still a token. The patterns are
   prefixes that identify a *format* (``ghp_``, ``sk-``, ``AKIA``, ``Bearer``),
   never a generic "long random string" heuristic: that would redact legitimate
   ids and content, and a redactor that over-matches is worse than none -- it
   silently changes the thing being compared.

The asymmetry with ``stop/normalize.py`` is deliberate and worth naming: there,
the rule is *miss a duplicate rather than invent one*; here, the rule is *miss a
secret rather than merge two calls*. So the key list is explicit, the patterns
are anchored, and both are data a reviewer can read and dispute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, FrozenSet, Iterable, Mapping, Pattern, Tuple

from .stop.normalize import key_token

__all__ = [
    "CREDENTIAL_TOKENS",
    "DEFAULT_REDACTOR",
    "REDACTED",
    "Redactor",
    "redact",
]

#: What a hidden value becomes. A constant, not a per-value hash: the point is
#: that two different secrets are the same call, so they must produce the same
#: identity. A per-value marker would preserve distinguishability and defeat FR-14.
REDACTED = "<redacted>"

#: Credential key names, as normalized tokens (so ``api_key``, ``apiKey`` and
#: ``X-Api-Key`` are one entry). Every name here is a promise that the *value*
#: says which credential was used, never what was asked -- the same standard as
#: ``normalize.VOLATILE_TOKENS``, applied to secrecy instead of provenance.
CREDENTIAL_TOKENS: FrozenSet[str] = frozenset(
    {
        "accesstoken",
        "apikey",
        "auth",
        "authorization",
        "authtoken",
        "bearer",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "idtoken",
        "password",
        "passwd",
        "privatekey",
        "pwd",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "setcookie",
        "token",
    }
)

#: Credential *formats*. Anchored on a known prefix and a minimum length so that
#: ordinary words cannot trigger them; applied to every string value regardless
#: of its key.
_CREDENTIAL_SHAPES: Tuple[Pattern[str], ...] = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
)


@dataclass(frozen=True)
class Redactor:
    """A declared redaction policy. Immutable, so it cannot drift mid-run."""

    key_tokens: FrozenSet[str] = CREDENTIAL_TOKENS
    marker: str = REDACTED
    shapes: Tuple[Pattern[str], ...] = _CREDENTIAL_SHAPES

    def redact(self, value: Any) -> Any:
        """Return a copy with credentials replaced. Never mutates its argument."""
        return self._walk(value, key=None)

    def redact_text(self, text: str) -> str:
        """Pattern-only scrubbing, for strings that arrive without a key."""
        out = text
        for shape in self.shapes:
            out = shape.sub(self.marker, out)
        return out

    def hides(self, key: str) -> bool:
        """Is this key name a credential?

        Matched as the whole token **or its tail**, because credentials travel in
        header names: ``X-Api-Key`` and ``X-Auth-Token`` normalize to
        ``xapikey`` / ``xauthtoken``, and an exact-match rule would miss every
        one of them. The tail rule costs a false positive only for a key that
        happens to end in a credential word (``oauth``), which is a credential
        key far more often than not.

        Plural forms are deliberately *not* covered: ``tokens`` and
        ``maxtokens`` are almost always counts, and the cost of missing a secret
        (one missed duplicate) is cheaper than inventing one (a wrong halt).
        """
        token = key_token(key)
        if token in self.key_tokens:
            return True
        return any(token.endswith(known) for known in self.key_tokens)

    # -- internals ---------------------------------------------------------

    def _walk(self, value: Any, *, key: Any) -> Any:
        if isinstance(value, Mapping):
            out = {}
            for raw_key, item in value.items():
                if isinstance(raw_key, str) and self.hides(raw_key):
                    out[raw_key] = self._hide(item)
                    continue
                out[raw_key] = self._walk(item, key=raw_key)
            return out
        if isinstance(value, (list, tuple)):
            # Key context is dropped inside a list for the same reason as in
            # ``normalize``: ``{"tokens": ["a", "b"]}`` is a list whose elements
            # are not themselves the value of the ``tokens`` key.
            return [self._walk(item, key=None) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def _hide(self, value: Any) -> Any:
        """The value of a credential-named key.

        ``None``/empty pass through: there is nothing to hide, and rewriting an
        absent credential into a marker would invent a value where the caller
        sent none -- a difference that shows up in every later comparison.
        """
        if value is None or value == "" or value == {} or value == []:
            return value
        return self.marker


DEFAULT_REDACTOR = Redactor()


def redact(value: Any, redactor: Redactor = DEFAULT_REDACTOR) -> Any:
    """Module-level convenience. Deterministic and idempotent."""
    return redactor.redact(value)


def tokens_of(redactor: Redactor) -> Iterable[str]:
    """The declared key tokens, sorted. For ``--doctor``-style diagnostics."""
    return sorted(redactor.key_tokens)
