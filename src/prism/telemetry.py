"""Content-free diagnostics.

Invariant A18: raw task and candidate logging is not implemented, **including in debug
mode**. There is no verbosity level that turns it on, because a debug flag that leaks user
content is the same leak with an excuse attached.

What may be recorded: request identifiers, hashes, counts, durations, statuses, versions,
warning counts. What may never be recorded: task text, claim text, source labels,
environment values, home directories, user-project paths, or model paths.

Output is JSON Lines to stderr, and only when explicitly enabled. There is no telemetry
exporter and no outbound connection (design section 16).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

from .constants import CANONICAL_JSON_SEPARATORS

DIAGNOSTICS_ENV_VAR: Final[str] = "PRISM_DIAGNOSTICS"

#: Keys that would carry user content. Any attempt to emit one is dropped and counted,
#: rather than trusted not to happen.
_FORBIDDEN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "task",
        "text",
        "claim",
        "claims",
        "question",
        "candidate",
        "candidates",
        "source_label",
        "content",
        "prompt",
        "path",
        "paths",
        "home",
        "cwd",
        "env",
        "environment",
        "model_path",
        "traceback",
        "stack",
    }
)

_logger: Final[logging.Logger] = logging.getLogger("prism")


def diagnostics_enabled() -> bool:
    return os.environ.get(DIAGNOSTICS_ENV_VAR, "") not in {"", "0", "false", "False"}


def new_request_id() -> str:
    """A random identifier with no relationship to request content."""
    return str(uuid.uuid4())


def scrub(payload: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    """Drop any key that could carry content, and any value that is not a scalar.

    This is a belt-and-braces control. Call sites are already expected not to pass
    content; this makes a mistake at a call site non-fatal.
    """
    clean: dict[str, str | int | float | bool | None] = {}
    for key, value in payload.items():
        if key.casefold() in _FORBIDDEN_KEYS:
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            clean[key] = value
    return clean


def emit(event: str, **fields: Any) -> None:
    """Write one JSON Lines diagnostic record to stderr, if enabled."""
    if not diagnostics_enabled():
        return
    record = {"event": event, **scrub(fields)}
    print(
        json.dumps(record, separators=CANONICAL_JSON_SEPARATORS, sort_keys=True),
        file=sys.stderr,
    )


@contextmanager
def timed(stage: str, request_id: str) -> Iterator[dict[str, float]]:
    """Time a stage and emit its duration. Durations are not content."""
    started = time.perf_counter()
    holder: dict[str, float] = {}
    try:
        yield holder
    finally:
        duration_ms = (time.perf_counter() - started) * 1000.0
        holder["duration_ms"] = duration_ms
        emit("stage", stage=stage, request_id=request_id, duration_ms=round(duration_ms, 3))
