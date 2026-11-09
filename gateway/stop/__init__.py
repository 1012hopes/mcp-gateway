"""Stop criteria plumbing: the controller plus (from D7) the rules themselves.

Import surface is narrow on purpose -- the transport needs a controller, a
context and a verdict; it must never need to know which rules exist.
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
