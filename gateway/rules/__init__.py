"""The criteria. One module each, no registry object.

Every rule implements the same four-attribute contract (``name``, ``priority``,
``stage``, ``check``), so the controller can run a rule it has never heard of.
The rules are instantiated in ``gateway/__main__.py`` -- the single assembly
point -- rather than collected here, because a module-level tuple would become a
second place where "which rules are running" is decided, and the trace's
``rules_version`` must come from exactly one.

Priorities so far (spaced by 10, gaps at 10/15/25/35 left for insertions):

    R1  20  pre   duplicate call
    R2  40  post  no progress (state fingerprint unchanged)
    R3  50  post  budget exhausted

R5 (dangerous action, 10) and R4 (evidence complete, 30) arrive in D9 and slot
into the gaps without renumbering anything.
"""

from .budget import BudgetExhausted
from .duplicate import DuplicateCall
from .no_progress import NoProgress

__all__ = ["BudgetExhausted", "DuplicateCall", "NoProgress"]
