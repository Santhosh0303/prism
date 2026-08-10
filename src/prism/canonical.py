"""Canonical serialisation and digests.

Invariant A24: the same request and artifact set must produce the same digest across
process restarts, locales, time zones, Python hash seeds, and supported operating
systems. Adapter-added timestamps are excluded.

Every digest in PRISM — registry hash, pair-ledger digest, golden comparisons — goes
through this module, so there is exactly one definition of "the same".
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from .constants import CANONICAL_JSON_SEPARATORS, RATE_DECIMAL_PLACES

#: Fields adapters may attach that describe *when* a result was produced rather than
#: *what* it contains. They are excluded from canonical comparison by name.
NON_CANONICAL_KEYS: frozenset[str] = frozenset(
    {"timestamp", "generated_at", "duration_ms", "request_id", "elapsed_ms"}
)


def canonical_rate(value: float | None) -> float | None:
    """Round a rate to the fixed serialisation precision.

    Without this, two runs that differ only in floating-point noise would produce
    different digests and a false regression.
    """
    if value is None:
        return None
    return round(value, RATE_DECIMAL_PLACES)


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip(item)
            for key, item in sorted(value.items())
            if key not in NON_CANONICAL_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_strip(item) for item in value]
    if isinstance(value, float):
        return round(value, RATE_DECIMAL_PLACES)
    return value


def canonical_json(payload: Any) -> str:
    """Serialise deterministically: sorted keys, fixed separators, ASCII-escaped.

    ``ensure_ascii`` matters on Windows, where the default console encoding would
    otherwise make the byte stream environment-dependent.
    """
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    return json.dumps(
        _strip(payload),
        separators=CANONICAL_JSON_SEPARATORS,
        sort_keys=True,
        ensure_ascii=True,
    )


def canonical_digest(payload: Any) -> str:
    """SHA-256 over the canonical serialisation, prefixed for use in typed fields."""
    encoded = canonical_json(payload).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
