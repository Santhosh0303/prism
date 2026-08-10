"""Latency and size budgets that need no model bundle.

These are guardrails, not benchmarks. `benchmarks/run.py` produces the release evidence on
named hardware; this suite catches the regression that turns a sub-millisecond operation
into a hundred-millisecond one, on whatever machine happens to run the tests.

The thresholds are therefore the **hard limits**, not the targets. A CI runner is shared,
throttled, and noisy: asserting a 15 ms target there would produce a flaky test, and a flaky
blocking test gets ignored, which is worse than not having it.

Measurement latency is deliberately absent. It depends on the encoders, so it belongs in the
benchmark against a fixed workload on recorded hardware.
"""

from __future__ import annotations

import time

import pytest

from prism.contracts import PreflightRequest, PrismMode
from prism.service import PrismService

pytestmark = pytest.mark.benchmark

TASK = "Review the payment service release plan for security and reliability risk."

#: Hard limits from the success criteria.
PREFLIGHT_P95_HARD_MS = 50.0
SYNTHESIS_P95_HARD_MS = 30.0

#: Enough samples for a p95 to mean something, few enough to stay fast.
SAMPLES = 60


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
    return ordered[index]


@pytest.fixture(scope="module")
def service() -> PrismService:
    instance = PrismService.from_default_bundle()
    instance.preflight(PreflightRequest(task=TASK, mode=PrismMode.CRITICAL))  # warm-up
    return instance


def test_preflight_warm_p95_is_within_the_hard_limit(service: PrismService) -> None:
    latencies = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        service.preflight(PreflightRequest(task=TASK, mode=PrismMode.CRITICAL))
        latencies.append((time.perf_counter() - started) * 1000)

    observed = _p95(latencies)
    assert observed < PREFLIGHT_P95_HARD_MS, f"preflight p95 {observed:.2f} ms"


def test_synthesis_contract_warm_p95_is_within_the_hard_limit(service: PrismService) -> None:
    preflight = service.preflight(PreflightRequest(task=TASK, mode=PrismMode.CRITICAL))

    latencies = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        service.synthesis_contract(preflight, None)
        latencies.append((time.perf_counter() - started) * 1000)

    observed = _p95(latencies)
    assert observed < SYNTHESIS_P95_HARD_MS, f"synthesis p95 {observed:.2f} ms"


def test_the_preflight_contract_stays_within_its_size_budget(service: PrismService) -> None:
    """Instruction size is host token cost. It is charged to the user on every call, which
    makes it a budget rather than a preference."""
    report = service.preflight(PreflightRequest(task=TASK, mode=PrismMode.CRITICAL))
    rendered = report.model_dump_json()

    # 1,400 tokens hard, at a conservative 4 bytes per token.
    assert len(rendered.encode("utf-8")) < 1_400 * 4


def test_every_mode_stays_within_its_perspective_bound(service: PrismService) -> None:
    """Perspective count drives claim count, which drives pair count quadratically. An
    off-by-one here is a latency regression everywhere downstream."""
    expected = {PrismMode.LITE: 3, PrismMode.STANDARD: 4, PrismMode.CRITICAL: 5}
    for mode, count in expected.items():
        report = service.preflight(PreflightRequest(task=TASK, mode=mode))
        assert len(report.perspectives) == count
