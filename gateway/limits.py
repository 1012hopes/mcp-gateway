"""The two plain values a run is bounded by: its budget and its call identity.

They live in their own leaf module for one structural reason. ``session`` needs
``normalize_args`` (identity is computed after normalization), ``stop.normalize``
belongs to the stop layer, and the stop controller needs ``Budget`` and
``CallSignature`` -- so with both types defined in ``session`` the import graph
became a cycle, and the only ways out were a lazy import inside a method or a
``TYPE_CHECKING`` lie. Both hide the shape of the dependency rather than fix it.

Neither type depends on anything here. Moving them down one level makes
``session -> stop.normalize`` and ``stop.controller -> limits`` two acyclic
edges, and ``session`` re-exports both so nothing that already imported them has
to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Optional

__all__ = ["Budget", "CallSignature"]


@dataclass(frozen=True)
class Budget:
    """What the gateway can actually count.

    Calls and wall-clock time, not tokens: the gateway never sees the prompt, so
    a token budget here would be a number nobody measured. Token accounting
    belongs to the agent side, where the prompt exists. Stating the limit of the
    measurement is cheaper than inventing a plausible number.
    """

    max_calls: int = 24
    max_wall_ms: int = 600_000

    def why_exhausted(self, *, calls_used: int, wall_ms: int) -> Optional[str]:
        """Which limit was reached, in words -- or ``None`` while there is room.

        One predicate, one reason: R3 has to write *which* budget ran out, and a
        second ``if`` computing that would eventually disagree with this one.
        ``max_wall_ms <= 0`` disables the wall clock, so a test can drive the
        call budget alone without racing a timer.
        """
        if calls_used >= self.max_calls:
            return f"calls={calls_used} reached max_calls={self.max_calls}"
        if self.max_wall_ms > 0 and wall_ms >= self.max_wall_ms:
            return f"wall={wall_ms}ms reached max_wall_ms={self.max_wall_ms}"
        return None

    def exhausted(self, *, calls_used: int, wall_ms: int) -> bool:
        return self.why_exhausted(calls_used=calls_used, wall_ms=wall_ms) is not None


class CallSignature(NamedTuple):
    """What R1 hashes and R2 slots into its rolling state hash.

    ``tool`` is part of the identity. ``count_rows({"table": "x"})`` and
    ``describe_table({"table": "x"})`` hash the same arguments and are different
    calls; comparing the digest alone reports a duplicate where there is none.
    """

    tool: str
    args_hash: str
