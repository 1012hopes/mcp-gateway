"""R4 -- the run has what the task asked for. Priority 30, post-stage.

This is the criterion the project is built around. The other three rules stop a
run that is going wrong; this one stops a run that has gone *right*, and it is
the only one that can say "done" instead of "enough". It is also the one that
cannot live in the agent: a prompt that says "stop when you are finished" is a
request, not a criterion, and there is no way to check afterwards whether it was
followed. Here the answer is a set comparison -- ``required ⊆ evidence`` -- that
a reviewer can recompute from the trace.

Why it must run before R2 and R3
--------------------------------
A run whose evidence just completed also often looks like a run that is about to
repeat itself, and it may very well be on its last unit of budget. Ordering by
priority (30 < 40 < 50) makes the specific, informative verdict win: "finished"
beats "you were spinning" beats "you ran out". Get this wrong and the metrics
report a completion as a failure.

Why the empty-required guard is not defensive padding
-----------------------------------------------------
The empty set is a subset of everything, so a rule written as a bare
``required <= evidence`` fires on the first call of every run that declared no
evidence -- including the adversarial task t02, whose *whole point* is to be
stopped by R2. The guard is the difference between "this criterion is installed"
and "this criterion is installed and correct".
"""

from __future__ import annotations

from typing import Optional

from ..stop.controller import POST, StopContext, StopVerdict

__all__ = ["EvidenceComplete"]


class EvidenceComplete:
    """Halt when every fact type the task requires has been obtained."""

    name = "R4"
    priority = 30
    stage = POST

    def check(self, ctx: StopContext) -> Optional[StopVerdict]:
        if not ctx.required:
            # Nothing was asked for, so nothing can be complete. See the module
            # docstring: without this, every evidence-free run halts at step 1.
            return None
        missing = ctx.required - ctx.evidence
        if missing:
            return None
        obtained = sorted(ctx.required & ctx.evidence)
        return StopVerdict(
            rule=self.name,
            decision="halt_evidence_complete",
            state="HALTED_COMPLETE",
            reason=(
                f"all {len(obtained)} required evidence types obtained "
                f"({', '.join(obtained)}); the task is complete, so further calls "
                f"would only re-verify what the trace already proves"
            ),
        )
