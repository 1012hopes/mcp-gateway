"""Stop criteria plumbing: the controller, the context, the verdict.

Import surface is narrow on purpose -- the transport needs a controller, a
context and a verdict; it must never need to know which rules exist. The rules
themselves live in ``gateway/rules/`` and are instantiated in ``__main__.py``.

``normalize`` is not re-exported here: it is imported by ``session`` to compute
call identity, and re-exporting it through this package would re-create the
import cycle that moving ``limits`` to a leaf module removed.
"""

from .controller import (
    POST,
    PRE,
    Rule,
    StopContext,
    StopController,
    StopOutcome,
    StopVerdict,
    merge_checks,
)

__all__ = [
    "POST",
    "PRE",
    "Rule",
    "StopContext",
    "StopController",
    "StopOutcome",
    "StopVerdict",
    "merge_checks",
]
