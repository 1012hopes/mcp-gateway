"""R3 -- the hard stop. Priority 50, post-stage, last in line.

It runs after the other criteria for a reason: if evidence was already complete,
the honest verdict is "done", not "ran out of budget". R3 exists for the runs
where nothing else could decide -- and a run that ends on R3 is a *failure to
finish*, not a success. That distinction lives in ``state``
(``HALTED_BUDGET``), so a metrics table can count budget halts separately
instead of folding them into completions (DESIGN A6).

Post-stage, though the count does not need the result: the rule fires on the call
that consumed the last unit, and that call's result is legitimate evidence which
must still be delivered. Running it pre-stage would throw the final result away
to save a call that was already paid for.
"""

from __future__ import annotations

from typing import Optional

from ..stop.controller import POST, StopContext, StopVerdict

__all__ = ["BudgetExhausted"]


class BudgetExhausted:
    """Halt when the call or wall-clock budget is spent."""

    name = "R3"
    priority = 50
    stage = POST

    def check(self, ctx: StopContext) -> Optional[StopVerdict]:
        why = ctx.budget.why_exhausted(calls_used=ctx.calls_used, wall_ms=ctx.wall_ms)
        if why is None:
            return None
        return StopVerdict(
            rule=self.name,
            decision="halt_budget",
            state="HALTED_BUDGET",
            reason=why,
        )
