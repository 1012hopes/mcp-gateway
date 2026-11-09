"""R6 -- completion is impossible. Priority 35, pre-stage.

Not a stop criterion in the sense the other five are: it does not answer "is
there enough", it answers "can there ever be enough". When every tool declared
able to produce a required fact is unavailable -- absent from the registry, or
frozen because its schema drifted -- no amount of further calling will satisfy
R4, and the run's remaining budget can only buy steps towards a conclusion that
is already known.

Why halting, when DESIGN said "degrade and keep going"
------------------------------------------------------
Keeping going assumes the missing fact might still arrive. That assumption is
false by construction here: the set of producers is declared in the evidence
model and the set of available tools is known before the first call. The useful
output is the *classification* -- ``DEGRADED`` -- because a metrics table must
be able to exclude these runs from "stopped too late". A run that limps to
``HALTED_BUDGET`` would otherwise be scored as a slow agent when it was a
missing server.

The honest limit: this sees the gateway's own admission decision (what is
offered), not the agent's intentions. An agent that simply never tries a
producer looks identical to a healthy run until the turn budget ends; the trace
then shows the evidence still ``missing``, which is why ``missing`` is recorded
on every line rather than only at the end.

Pre-stage on purpose
--------------------
The fact is known before the call is forwarded, so the call is refused rather
than paid for. That is the same reasoning as R1's pre-stage placement, and it
makes the demo unambiguous: zero upstream calls in a run that cannot complete.
"""

from __future__ import annotations

from typing import Optional

from ..evidence import EvidenceModel
from ..stop.controller import PRE, StopContext, StopVerdict

__all__ = ["EvidenceUnreachable"]


class EvidenceUnreachable:
    """Halt as ``DEGRADED`` when a required fact has no available producer."""

    name = "R6"
    priority = 35
    stage = PRE

    def __init__(self, model: EvidenceModel) -> None:
        # The rule holds the model only to explain *why* -- the verdict itself is
        # computed from the context, so a rule that cannot explain still refuses.
        self._model = model

    def check(self, ctx: StopContext) -> Optional[StopVerdict]:
        if not ctx.required or not ctx.unreachable:
            return None
        names = sorted(ctx.unreachable)
        reasons = [self._model.explain(name, ctx.available) for name in names]
        return StopVerdict(
            rule=self.name,
            decision="halt_evidence_unreachable",
            state="DEGRADED",
            reason=(
                f"completion is impossible: {', '.join(names)} cannot be obtained "
                f"({'; '.join(reasons)}). Stopping in DEGRADED instead of spending "
                f"the remaining budget on a task that cannot finish"
            ),
        )
