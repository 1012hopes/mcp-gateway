"""The criteria. One module each, no registry object.

Every rule implements the same four-attribute contract (``name``, ``priority``,
``stage``, ``check``), so the controller can run a rule it has never heard of.
The rules are instantiated in ``gateway/__main__.py`` -- the single assembly
point -- rather than collected here, because a module-level tuple would become a
second place where "which rules are running" is decided, and the trace's
``rules_version`` must come from exactly one.

Priorities so far (spaced by 10, gaps at 10/15/25 left for insertions):

    R1  20  pre   duplicate call
    R4  30  post  evidence complete          (the project's thesis)
    R6  35  pre   evidence unreachable -> DEGRADED
    R2  40  post  no progress (state fingerprint unchanged)
    R3  50  post  budget exhausted

R5 (dangerous action, 10) slots in without renumbering anything.

R6 is the odd one out and needs saying: it is not a sixth stop criterion, it is
the classification of a stop that could not be avoided -- "cannot finish" as
opposed to "has finished" (R4) or "is wasting time" (R1/R2/R3). It sits between
R4 and R2 so a run that is both complete and stuck is reported as complete.
"""

from .budget import BudgetExhausted
from .duplicate import DuplicateCall
from .evidence_complete import EvidenceComplete
from .no_progress import NoProgress
from .unreachable import EvidenceUnreachable

__all__ = [
    "BudgetExhausted",
    "DuplicateCall",
    "EvidenceComplete",
    "EvidenceUnreachable",
    "NoProgress",
]
