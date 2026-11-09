"""Trace: canonical serialization + hash-chained records.

D1 scope: ``canonical`` and ``record``. ``sink`` (fail-closed write) and
``verify`` (first-error localisation) land on D2.
"""

from .canonical import canonical_bytes, canonical_text, normalize
from .record import (
    CURRENT_SCHEMA_VERSION,
    DECISIONS,
    PAYLOAD_FIELDS_V1,
    RUN_STATES,
    StopCheck,
    TraceRecord,
    genesis_hash,
)

__all__ = [
    "canonical_bytes",
    "canonical_text",
    "normalize",
    "CURRENT_SCHEMA_VERSION",
    "DECISIONS",
    "PAYLOAD_FIELDS_V1",
    "RUN_STATES",
    "StopCheck",
    "TraceRecord",
    "genesis_hash",
]
