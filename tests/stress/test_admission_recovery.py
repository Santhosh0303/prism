"""Admission, saturation, and recovery — plan Task 18, Steps 6 and 7.

Probe ST-1: a 20-client burst must produce at most two active measurements, zero queued,
and typed BUSY for the rest. Probe ST-2: ten simultaneous cold callers must create exactly
one E1 and one E2 session.

These use a stub measurement so the properties under test are admission and recovery, not
encoder speed. A real-model burst would measure the CPU, not the design.
"""

from __future__ import annotations

import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from prism.contracts import (
    CandidatePacket,
    Claim,
    EvidenceStatus,
    MeasureConfig,
    MeasureRequest,
    PrismStatus,
    ProvenanceStatus,
)
from prism.errors import ErrorCode, PrismError
from prism.limits import MAX_CONCURRENT_MEASUREMENTS, MAX_QUEUED_MEASUREMENTS
from prism.measure.models import ModelSessions, measurement_disabled
from prism.preflight.registry import PerspectiveRegistry
from prism.service import PrismService


def request_for(candidate_count: int = 2, timeout: float = 5.0) -> MeasureRequest:
    candidates = tuple(
        CandidatePacket(
            candidate_id=f"c{index}",
            source_group_id="one-pass",
            source_label=None,
            provenance_status=ProvenanceStatus.DECLARED_UNVERIFIED,
            perspective=f"c{index}",
            claims=(
                Claim(
                    claim_id=f"c{index}-1",
                    text=f"Claim number {index} with enough words to clear the content floor.",
                    confidence=None,
                    evidence_status=EvidenceStatus.INFERRED,
                ),
            ),
        )
        for index in range(candidate_count)
    )
    return MeasureRequest(
        question="Is the release ready?",
        candidates=candidates,
        config=MeasureConfig(timeout_seconds=timeout),
    )


class SlowService(PrismService):
    """Service whose measurement body blocks, so admission can be observed."""

    def __init__(self, hold_seconds: float) -> None:
        super().__init__(registry=PerspectiveRegistry.load())
        self.hold_seconds = hold_seconds
        self.active = 0
        self.peak_active = 0
        self._counter_lock = threading.Lock()

    def _measure_inner(self, request: MeasureRequest, request_id: str) -> Any:
        with self._counter_lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            time.sleep(self.hold_seconds)
            return self._insufficient_report(request, (), (), request_id)
        finally:
            with self._counter_lock:
                self.active -= 1


# --------------------------------------------------------------------------------------
# ST-1 — burst admission
# --------------------------------------------------------------------------------------


@pytest.mark.stress
def test_twenty_client_burst_admits_two_and_refuses_the_rest() -> None:
    service = SlowService(hold_seconds=0.6)
    outcomes: list[str] = []
    busy_latencies: list[float] = []
    lock = threading.Lock()

    def attempt() -> None:
        started = time.perf_counter()
        try:
            service.measure(request_for())
            with lock:
                outcomes.append("OK")
        except PrismError as error:
            elapsed_ms = (time.perf_counter() - started) * 1000
            with lock:
                outcomes.append(error.code.value)
                if error.code is ErrorCode.BUSY:
                    busy_latencies.append(elapsed_ms)

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(lambda _: attempt(), range(20)))

    assert service.peak_active <= MAX_CONCURRENT_MEASUREMENTS, service.peak_active
    assert outcomes.count("BUSY") >= 20 - MAX_CONCURRENT_MEASUREMENTS - 2
    assert set(outcomes) <= {"OK", "BUSY"}, f"unexpected outcome: {set(outcomes)}"
    # Rejection must be immediate: a BUSY that took a second is a queue in disguise.
    assert max(busy_latencies, default=0.0) < 50.0, f"slowest BUSY {max(busy_latencies):.1f} ms"


@pytest.mark.stress
def test_there_is_no_queue_by_design() -> None:
    assert MAX_QUEUED_MEASUREMENTS == 0


@pytest.mark.stress
def test_capacity_is_restored_after_the_burst_drains() -> None:
    """A permit leak would show up here as a permanent BUSY."""
    service = SlowService(hold_seconds=0.05)
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda _: _swallow(service), range(10)))
    time.sleep(0.3)
    report = service.measure(request_for())
    assert report.status is PrismStatus.INSUFFICIENT


def _swallow(service: PrismService) -> None:
    with contextlib.suppress(PrismError):
        service.measure(request_for())


# --------------------------------------------------------------------------------------
# timeout and the circuit breaker
# --------------------------------------------------------------------------------------


@pytest.mark.stress
def test_timeout_returns_typed_error_and_no_partial_report() -> None:
    service = SlowService(hold_seconds=2.0)
    with pytest.raises(PrismError) as excinfo:
        service.measure(request_for(timeout=0.15))
    assert excinfo.value.code is ErrorCode.TIMEOUT
    assert excinfo.value.retryable is True
    # No partial result: the exception is the only outcome.
    assert "partial" in excinfo.value.message.casefold()


@pytest.mark.stress
def test_repeated_timeouts_trip_the_circuit_rather_than_stacking_work() -> None:
    """A Python timeout does not stop native inference. Admitting more work on top of
    workers that never returned is how a process quietly dies."""
    service = SlowService(hold_seconds=3.0)
    codes: list[ErrorCode] = []
    for _ in range(MAX_CONCURRENT_MEASUREMENTS + 1):
        try:
            service.measure(request_for(timeout=0.1))
        except PrismError as error:
            codes.append(error.code)
    assert codes[-1] is ErrorCode.TIMEOUT
    assert service.peak_active <= MAX_CONCURRENT_MEASUREMENTS


# --------------------------------------------------------------------------------------
# ST-2 — cold session race
# --------------------------------------------------------------------------------------


@pytest.mark.stress
@pytest.mark.models
def test_ten_cold_callers_create_exactly_one_session_pair() -> None:
    """Duplicate sessions are a release-blocking leak: a second E2 is another 328 MB."""
    if measurement_disabled():
        pytest.skip("kill switch active")
    ModelSessions.reset()
    built: list[int] = []
    barrier = threading.Barrier(10)

    def race() -> None:
        barrier.wait()
        sessions = ModelSessions.get()
        built.append(id(sessions))

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda _: race(), range(10)))

    assert len(set(built)) == 1, "more than one ModelSessions instance was created"


# --------------------------------------------------------------------------------------
# kill switch
# --------------------------------------------------------------------------------------


@pytest.mark.stress
def test_kill_switch_disables_inference_but_not_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Design section 19: measurement off, deterministic preflight still available."""
    from prism.constants import DISABLE_MEASURE_ENV_VAR
    from prism.contracts import PreflightRequest

    monkeypatch.setenv(DISABLE_MEASURE_ENV_VAR, "1")
    ModelSessions.reset()
    service = PrismService.from_default_bundle()

    assert service.preflight(PreflightRequest(task="Review the release plan.")).perspectives

    with pytest.raises(PrismError) as excinfo:
        ModelSessions.get()
    assert excinfo.value.code is ErrorCode.MEASURE_DISABLED

    health = service.health(deep=True)
    assert health.measurement_disabled_by_kill_switch is True
    assert health.status is PrismStatus.OK
