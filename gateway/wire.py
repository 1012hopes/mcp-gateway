"""The one module the gateway and the agent are allowed to share.

Everything else crosses the process boundary as MCP JSON-RPC. This file exists
because "the gateway has stopped your run" is information the protocol does not
have a field for -- and putting it in the *text* of a tool result is a decision
with consequences, so it gets written down instead of being a string literal in
two places that can drift.

Two questions the format has to answer:

1. **Can a cooperative agent notice?** Yes: the notice is an extra ``TextContent``
   block that starts with ``[gateway]``. A crash-free agent can parse it and stop.
2. **Does an uncooperative agent get away with ignoring it?** No, and that is the
   point of the whole project: the notice is a courtesy, the *refusal* of every
   later call is the enforcement. A marker that only works on good behaviour
   would be a prompt, not a control.

Deliberately stdlib-only: the agent side imports this without pulling the MCP
SDK into a unit test, and the gateway imports it without pulling the agent.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

__all__ = ["NOTICE_PREFIX", "format_notice", "parse_notice", "KIND_HALT", "KIND_CLOSED"]

NOTICE_PREFIX = "[gateway]"

#: The run stopped on its own terms; the result in this block is still valid.
KIND_HALT = "halt"

#: The run is already over (aborted or halted) and this call was refused.
KIND_CLOSED = "closed"

_SEPARATOR = re.compile(r"\s\|\s")


def format_notice(kind: str, **fields: Any) -> str:
    """Render one single-line, greppable, parseable notice.

    ``reason`` is emitted last and absorbs the remainder of the line, so a
    reason containing the separator cannot corrupt the other fields.
    """
    reason = fields.pop("reason", None)
    parts = [f"{NOTICE_PREFIX} {kind}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    if reason is not None:
        parts.append(f"reason={reason}")
    return " | ".join(str(part) for part in parts)


def parse_notice(text: str) -> Optional[Dict[str, str]]:
    """Find the first gateway notice in ``text``, or ``None``.

    Scans every line rather than only the first: a notice is appended to a
    result that may already contain several text blocks, and requiring it to be
    line one would make the contract depend on block ordering.
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith(NOTICE_PREFIX):
            continue
        body = stripped[len(NOTICE_PREFIX) :].strip()
        if not body:
            # A bare prefix is a malformed notice, not a notice of nothing.
            return None
        pieces = _SEPARATOR.split(body)
        kind, _, head = pieces[0].partition(" ")
        kind = kind.strip()
        if not kind:
            return None
        fields: Dict[str, str] = {}
        tail = ([head.strip()] if head.strip() else []) + pieces[1:]
        for index, piece in enumerate(tail):
            key, sep, value = piece.partition("=")
            if not sep:
                continue
            if key == "reason":
                fields["reason"] = " | ".join([value] + tail[index + 1 :])
                break
            fields[key] = value
        return {"kind": kind, **fields}
    return None
