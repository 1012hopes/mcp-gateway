"""Version stamps that end up inside every trace record.

These are not decoration. ``gateway_version`` and ``rules_version`` are what
makes an old trace replayable: during replay the gateway must be able to say
"this record was produced by rules-v0, so evaluate it with rules-v0" (DESIGN
A5). They are bumped whenever the meaning of a field changes, never when a
field is merely added -- adding a field is covered by ``schema_version``.
"""

from __future__ import annotations

GATEWAY_VERSION = "0.6.0"

#: ``rules-v0`` means: the *versioning scheme*, not "no rules". The actual set is
#: appended by ``StopController.version`` (``rules-v0(R1,R3)`` at D7). A trace
#: stamped with one set cannot be compared with a run under another -- which is
#: exactly why the stamp exists.
#:
#: This constant is only the default on a session that no controller has been
#: attached to yet; ``Gateway.__init__`` overwrites it from the controller. It is
#: set to ``(none)`` rather than to the current rule names on purpose: a second
#: place listing the installed rules would be a second thing to forget to update.
RULES_VERSION = "rules-v0(none)"

#: Used when the caller does not declare which model drove the loop. Keeping a
#: placeholder beats leaving it null: a null would be indistinguishable from
#: "field not recorded", and the whole point of the reserved fields (DESIGN 2.8)
#: is that absence must never be ambiguous.
UNKNOWN_MODEL_ID = "unlabeled-agent"

__all__ = ["GATEWAY_VERSION", "RULES_VERSION", "UNKNOWN_MODEL_ID"]
