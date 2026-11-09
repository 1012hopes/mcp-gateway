"""R1 -- the same call, twice.

Priority 20, pre-stage: the value of this rule is that the second call does not
happen, so it must run before the upstream is touched.

What "the same call" means is the whole difficulty. It is ``(tool, args_hash)``,
not ``args_hash``: ``count_rows({"table": "incidents"})`` and
``describe_table({"table": "incidents"})`` hash the same arguments and ask
different questions. Comparing digests alone reports a duplicate where the agent
merely changed the tool -- and that behaviour is *pseudo-progress*, which is R2's
target, not R1's. The two rules would then fire on each other's evidence and the
``attempted`` log could not explain either.

The verdict asks for a replay rather than an error. A duplicated read is not a
mistake worth punishing: the data is already in the transcript, and returning it
again is what a human assistant would do. The transport owns that replay because
only it holds the delivered results.

One assumption, stated because it is load-bearing: **the upstream is a function
of its arguments within a run.** Replaying a call whose result has since changed
would serve stale data. The gateway's first upstream is read-only by
construction (``mode=ro``), which is what makes the assumption true here; against
a mutable upstream R1 must degrade to "blocked, explained" instead of replaying.
"""

from __future__ import annotations

from typing import Optional

from ..stop.controller import PRE, StopContext, StopVerdict

__all__ = ["DuplicateCall"]


class DuplicateCall:
    """Halt when this exact call already ran in this run."""

    name = "R1"
    priority = 20
    stage = PRE

    def check(self, ctx: StopContext) -> Optional[StopVerdict]:
        for position, seen in enumerate(ctx.history, start=1):
            if seen.tool != ctx.tool or seen.args_hash != ctx.args_hash:
                continue
            return StopVerdict(
                rule=self.name,
                decision="halt_duplicate",
                state="HALTED_DUPLICATE",
                reason=(
                    f"{ctx.tool} with identical arguments already ran at step "
                    f"{position}; repeating it cannot add evidence this run lacks"
                ),
                replay_last=True,
            )
        return None
