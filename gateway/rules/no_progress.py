"""R2 -- the run is busy and learning nothing.

Priority 40, post-stage: the question "did this call teach the run anything" can
only be answered after the answer arrives.

What "progress" means here is a set, not a feeling
--------------------------------------------------
The run's information state is

    state_t = ( evidence_t , { result_digest_1 ... result_digest_t } )

and ``state_hash`` is its fingerprint (``RunSession.state_hash``). Both parts
only ever grow, so the hash changes **iff** the run learned something new. R2
fires when the fingerprint has been identical for ``window`` consecutive calls:
two calls in a row that returned nothing the run did not already have.

This is a deliberate departure from DESIGN's first draft, which folded
``last3(tool, args_hash)`` into the hash. That formula cannot detect the
behaviour this rule exists for: a pseudo-progress call *changes* the tool or the
arguments, so it changes the hash by construction, and the rule would only ever
see literal cycles -- which R1 already refuses one stage earlier. Including the
question in the state measures activity; excluding it measures knowledge. The
observed consequence is in ``docs/progress.md`` (D8): under the draft formula the
adversarial task ends on budget or on ``max_turns``, never on R2.

Why ``window=2``
----------------
One no-news call is normal: metadata first, content second, or a probe that
returns an empty set the agent then routes around. Two in a row, with the second
answer byte-identical to the first, means rephrasing is the whole strategy. The
parameter is per-instance so the experiment can calibrate it, and the README
records what the calibration was measured on instead of asserting "2 is right".

The honest limit
----------------
Byte-identical is the test, so a server that puts a timestamp, a request id or a
nonce in every response defeats R2 completely. That is why R2 is the *passive*
backstop and R4 (D9) is the active one: the completeness criterion does not care
whether answers differ, only whether they completed the task.
"""

from __future__ import annotations

from typing import Optional

from ..stop.controller import POST, StopContext, StopVerdict

__all__ = ["NoProgress"]


class NoProgress:
    """Halt when consecutive calls leave the run's information state unchanged."""

    name = "R2"
    priority = 40
    stage = POST

    def __init__(self, window: int = 2) -> None:
        if window < 2:
            # window=1 would halt on the first call that merely repeats an answer
            # the run already had, before the agent could use it to route around
            # a dead end. Keep the guard explicit rather than surprising.
            raise ValueError("window must be >= 2; one quiet call is not stagnation")
        self.window = window

    def check(self, ctx: StopContext) -> Optional[StopVerdict]:
        if ctx.state_hash is None:
            # No result was seen (pre-stage, or a hand-built context in a test).
            # Guessing here would fabricate the one judgement R2 is supposed to
            # make from evidence.
            return None
        needed = self.window - 1
        if len(ctx.state_trail) < needed:
            return None
        if any(previous != ctx.state_hash for previous in ctx.state_trail[-needed:]):
            return None
        return StopVerdict(
            rule=self.name,
            decision="halt_no_progress",
            state="HALTED_NO_PROGRESS",
            reason=(
                f"{self.window} consecutive calls left the run's information state "
                f"unchanged (state {ctx.state_hash[:12]}); the last result repeated "
                f"what the run already had, so rephrasing is not progress"
            ),
        )
