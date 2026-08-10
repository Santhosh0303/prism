"""Endurance probe — resource plateau over a sustained call sequence.

**Excluded from the default run.** `pyproject.toml` deselects the `endurance` marker, so
this runs only when asked for:

    uv run pytest tests/endurance -m endurance

**What this is not.** The success criteria call for a 30-minute, 500-call soak with a
resource slope inside 3%. This is not that. It is a short probe that catches an obvious
per-call leak — a session rebuilt every time, a list that grows forever — in seconds rather
than half an hour. A real soak is release evidence and belongs on a dedicated machine;
running one inside the test suite would produce a slow test that people learn to skip.

The gap is recorded in `docs/performance.md` under "Not measured" rather than papered over
by a shorter run wearing the same name.
"""

from __future__ import annotations

import gc

import pytest

from prism.contracts import PreflightRequest, PrismMode
from prism.preflight.registry import PerspectiveRegistry
from prism.service import PrismService

pytestmark = pytest.mark.endurance

psutil = pytest.importorskip("psutil")

ITERATIONS = 500
WARM_UP = 50

#: Growth allowed between the warm baseline and the final window. Generous, because this is
#: a leak detector, not a precision measurement: a genuine per-call leak over 500 calls
#: shows up as multiples, not percentages.
MAX_GROWTH_RATIO = 1.10


def test_preflight_reaches_a_resource_plateau() -> None:
    process = psutil.Process()
    service = PrismService.from_default_bundle()
    request = PreflightRequest(task="Review the release plan.", mode=PrismMode.CRITICAL)

    for _ in range(WARM_UP):
        service.preflight(request)

    gc.collect()
    baseline_rss = process.memory_info().rss

    for _ in range(ITERATIONS):
        service.preflight(request)

    gc.collect()
    final_rss = process.memory_info().rss

    growth = final_rss / baseline_rss
    assert growth < MAX_GROWTH_RATIO, (
        f"RSS grew {growth:.2f}x over {ITERATIONS} calls ({baseline_rss:,} -> {final_rss:,} bytes)"
    )


def test_repeated_registry_loads_do_not_accumulate() -> None:
    """The registry is re-read on every load. If a load retained anything, this is where a
    long-running host would notice."""
    process = psutil.Process()

    for _ in range(WARM_UP):
        PerspectiveRegistry.load()

    gc.collect()
    baseline_rss = process.memory_info().rss

    digests = set()
    for _ in range(ITERATIONS):
        digests.add(PerspectiveRegistry.load().content_hash)

    gc.collect()
    growth = process.memory_info().rss / baseline_rss

    assert len(digests) == 1, "the registry hash is not stable across loads"
    assert growth < MAX_GROWTH_RATIO, f"RSS grew {growth:.2f}x over {ITERATIONS} loads"
