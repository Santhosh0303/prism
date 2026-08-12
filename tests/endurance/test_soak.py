"""Endurance probes — resource plateau over a sustained call sequence.

**Excluded from the default run.** `pyproject.toml` deselects the `endurance` marker, so
these run only when asked for:

    uv run pytest tests/endurance -m endurance                    # probes only
    uv run pytest tests/endurance -m "endurance and models"       # the real soak

Two different things live here.

* The two short probes catch an obvious per-call leak — a session rebuilt every time, a
  list that grows forever — in seconds. They load no model.
* `test_measurement_reaches_a_resource_plateau` is the release soak: 500 measurements or
  30 minutes of real inference, whichever comes first, sampling RSS, handles, threads,
  workers, permits and encoder sessions. It writes `benchmarks/out/soak.json` and then
  reads that file back and asserts against what is on disk, because starting a soak is not
  passing a soak and a run whose record was never opened is not evidence.

**Ambient load is the thing that makes a soak lie.** Preflight is pure Python and loads no
model, so its p95 cannot move because of anything this soak does; if it moves, the machine
moved. Measured on the development box: 0.110-0.144 ms idle against 0.306 ms under load,
while the same workload's measurement p95 went from 9,421 to 14,340 ms. A 30-minute run
that samples RSS on a busy machine will read that ambient load as a memory slope. So
preflight p95 is recorded per window and a window whose p95 has moved is discarded rather
than believed; if the windows the verdict depends on are the discarded ones, the run is
`INCONCLUSIVE` and says so instead of reporting a leak or a plateau it cannot see.
"""

from __future__ import annotations

import gc
import itertools
import json
import os
import platform
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any, Final

import pytest

from prism.contracts import MeasureRequest, PreflightRequest, PrismMode
from prism.limits import MAX_CONCURRENT_MEASUREMENTS
from prism.measure.models import ModelSessions, manifest_present, measurement_disabled
from prism.preflight.registry import PerspectiveRegistry
from prism.service import PrismService
from prism.version import PACKAGE_VERSION

pytestmark = pytest.mark.endurance

psutil = pytest.importorskip("psutil")

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARKS = REPO_ROOT / "benchmarks"
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))

import run as benchmark_run  # noqa: E402  - the benchmark runner, imported as a library

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


# --------------------------------------------------------------------------------------
# measurement soak
# --------------------------------------------------------------------------------------

#: The declared budget: whichever limit is reached first ends the run. On the development
#: box a reference measurement costs about 3.4 s, so the two land within minutes of each
#: other by construction — neither one alone would be a stated duration.
SOAK_MEASUREMENTS: Final[int] = int(os.environ.get("PRISM_SOAK_MEASUREMENTS", "500"))
SOAK_SECONDS: Final[float] = float(os.environ.get("PRISM_SOAK_SECONDS", "1800"))
SOAK_ARTIFACT: Final[Path] = Path(
    os.environ.get("PRISM_SOAK_ARTIFACT", str(BENCHMARKS / "out" / "soak.json"))
)

#: Measurements discarded before the baseline window opens: lazy session creation, the
#: first allocations of the arena, and the tokenizer caches all land here. Proportional so
#: that a deliberately small budget still leaves windows behind.
SOAK_WARM_UP: Final[int] = max(1, min(10, SOAK_MEASUREMENTS // 10))

#: Five windows, so "the final 20%" is one of them.
WINDOW_COUNT: Final[int] = 5

#: The final window must sit within this of the warm baseline window. Applied to RSS,
#: handles and threads alike.
SOAK_MAX_GROWTH_RATIO: Final[float] = 1.05

#: A rise counts as a slope only once it is also this far above the baseline window. RSS on
#: a plateau still drifts upward: an 8-measurement calibration run on this box rose in every
#: one of its five windows and gained 0.16% in total (762,814,464 -> 764,055,552 bytes),
#: which is allocator drift, not a leak. Without a floor the check fires on every healthy
#: run, and a check that always fires is one someone deletes. 1% is five times tighter than
#: the window allowance above and well clear of the observed drift; the measured slope is
#: recorded either way, so a rise below the floor is visible rather than hidden.
MONOTONIC_RISE_FLOOR_RATIO: Final[float] = 1.01

#: How far preflight p95 may move between windows before the window is treated as measuring
#: the machine rather than the code. Idle spread on the development box is 0.110-0.150 ms
#: (1.36x); the same box under load read 0.306 ms (2.2x above the idle floor). 1.5 sits
#: above the idle spread and below the loaded reading.
AMBIENT_P95_TOLERANCE_RATIO: Final[float] = 1.5

#: Wall-clock ceiling for the test itself, over the declared budget so that the run ends by
#: its own budget rather than by pytest's global 120 s timeout.
SOAK_TIMEOUT_SECONDS: Final[float] = SOAK_SECONDS + 600


def _handle_count(process: Any) -> int:
    """Open OS handles, whatever the platform calls them. A leaked file, socket or event
    shows up here long before it shows up in RSS."""
    counter = getattr(process, "num_handles", None) or getattr(process, "num_fds", None)
    if counter is None:  # pragma: no cover - every supported platform has one of the two
        return -1
    try:
        return int(counter())
    except (psutil.AccessDenied, OSError):  # pragma: no cover - not seen on CI or Windows
        return -1


def _measure_worker_threads() -> int:
    """Live threads belonging to the measurement pool. The pool is bounded, so a count that
    climbs is a pool being rebuilt rather than reused."""
    return sum(1 for thread in threading.enumerate() if thread.name.startswith("prism-measure"))


def _p95_ms(values: list[float]) -> float:
    """The benchmark runner's percentile, so the soak and the published p95 agree."""
    return float(benchmark_run.distribution(values).get("p95_ms", 0.0))


def _summarise_window(index: int, samples: list[dict[str, Any]]) -> dict[str, Any]:
    def column(key: str) -> list[float]:
        return [float(sample[key]) for sample in samples]

    return {
        "index": index,
        "samples": len(samples),
        "first_measurement": samples[0]["measurement"],
        "last_measurement": samples[-1]["measurement"],
        "mean_rss_bytes": statistics.fmean(column("rss_bytes")),
        "max_rss_bytes": max(column("rss_bytes")),
        "mean_handles": statistics.fmean(column("handles")),
        "max_handles": max(column("handles")),
        "mean_threads": statistics.fmean(column("threads")),
        "max_threads": max(column("threads")),
        "max_measure_workers": max(column("measure_workers")),
        "min_available_permits": min(column("available_permits")),
        "max_abandoned_workers": max(column("abandoned_workers")),
        "distinct_encoder_sessions": len({sample["encoder_session_id"] for sample in samples}),
        "preflight_p95_ms": _p95_ms(column("preflight_ms")),
        "measure_p95_ms": _p95_ms(column("measure_ms")),
    }


def _windows(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Equal windows over the post-warm-up samples, remainder into the last one."""
    size = len(samples) // WINDOW_COUNT
    bounds = [(index * size, (index + 1) * size) for index in range(WINDOW_COUNT)]
    bounds[-1] = (bounds[-1][0], len(samples))
    return [
        _summarise_window(index, samples[start:stop]) for index, (start, stop) in enumerate(bounds)
    ]


def _ratio(final: float, baseline: float) -> float:
    return final / baseline if baseline else 0.0


def _collect(
    service: PrismService,
    request: MeasureRequest,
    task: str,
    sampler: Any,
    process: Any,
    started: float,
) -> tuple[list[dict[str, Any]], str | None]:
    """Run the budget. Returns the samples taken and the error that stopped the run, if any.

    An exception ends the loop but does not discard what was already measured: the samples
    up to the failure are the record of how the process got there.
    """
    samples: list[dict[str, Any]] = []
    error: str | None = None
    for index in range(SOAK_MEASUREMENTS):
        if time.monotonic() - started >= SOAK_SECONDS:
            break
        try:
            measure_started = time.perf_counter()
            service.measure(request)
            measure_ms = (time.perf_counter() - measure_started) * 1000

            # Preflight loads no model and allocates almost nothing, so its latency here is
            # a reading of the machine, not of the workload. It is the ambient-load control.
            preflight_started = time.perf_counter()
            service.preflight(PreflightRequest(task=task, mode=PrismMode.CRITICAL))
            preflight_ms = (time.perf_counter() - preflight_started) * 1000
        except Exception as exc:  # recorded as the verdict, not swallowed
            error = f"{type(exc).__name__}: {exc}"
            break

        samples.append(
            {
                "measurement": index,
                "elapsed_seconds": time.monotonic() - started,
                "measure_ms": measure_ms,
                "preflight_ms": preflight_ms,
                # Through the sampler, so a reading taken here can never exceed the peak
                # reported beside it.
                "rss_bytes": sampler.sample(),
                "handles": _handle_count(process),
                "threads": process.num_threads(),
                "measure_workers": _measure_worker_threads(),
                # Private state on purpose: a permit that is never released and a worker
                # that is never reclaimed are exactly what a soak exists to catch, and
                # neither has a public reading.
                "available_permits": service._permits._value,
                "abandoned_workers": len(service._abandoned),
                "encoder_session_id": id(ModelSessions._instance),
            }
        )
    return samples, error


def _evaluate(windows: list[dict[str, Any]]) -> tuple[str, list[str], dict[str, Any]]:
    """Verdict, findings, and the numbers they were reached from."""
    baseline, final = windows[0], windows[-1]
    reference_p95 = baseline["preflight_p95_ms"]
    contaminated = [
        window["index"]
        for window in windows
        if reference_p95 > 0
        and window["preflight_p95_ms"] > reference_p95 * AMBIENT_P95_TOLERANCE_RATIO
    ]

    growth = {
        "rss": _ratio(final["mean_rss_bytes"], baseline["mean_rss_bytes"]),
        "handles": _ratio(final["mean_handles"], baseline["mean_handles"]),
        "threads": _ratio(final["mean_threads"], baseline["mean_threads"]),
    }
    retained = [window for window in windows if window["index"] not in contaminated]
    retained_rss = [window["mean_rss_bytes"] for window in retained]
    rising = len(retained_rss) >= 3 and all(
        later > earlier for earlier, later in itertools.pairwise(retained_rss)
    )
    rise_ratio = _ratio(retained_rss[-1], retained_rss[0])
    slope_bytes = statistics.linear_regression(range(len(retained_rss)), retained_rss).slope
    monotonic = rising and rise_ratio > MONOTONIC_RISE_FLOOR_RATIO

    evidence: dict[str, Any] = {
        "growth_ratios": growth,
        "allowed_growth_ratio": SOAK_MAX_GROWTH_RATIO,
        "rss_rose_in_every_retained_window": rising,
        "rss_rise_ratio_across_retained_windows": rise_ratio,
        "rss_slope_bytes_per_window": slope_bytes,
        "monotonic_rise_floor_ratio": MONOTONIC_RISE_FLOOR_RATIO,
        "monotonic_rss_increase_across_windows": monotonic,
        "ambient": {
            "control": "preflight p95 per window; preflight loads no model",
            "baseline_preflight_p95_ms": reference_p95,
            "final_preflight_p95_ms": final["preflight_p95_ms"],
            "tolerance_ratio": AMBIENT_P95_TOLERANCE_RATIO,
            "contaminated_windows": contaminated,
            "retained_windows": [window["index"] for window in retained],
        },
    }

    if baseline["index"] in contaminated or final["index"] in contaminated:
        return (
            "INCONCLUSIVE",
            [
                "ambient load moved preflight p95 from "
                f"{reference_p95:.3f} ms to {final['preflight_p95_ms']:.3f} ms across the "
                "run, so a resource slope here would be a reading of the machine. Re-run on "
                "an idle box."
            ],
            evidence,
        )

    findings: list[str] = []
    for resource, ratio in growth.items():
        if ratio > SOAK_MAX_GROWTH_RATIO:
            findings.append(
                f"{resource} in the final 20% window is {ratio:.3f}x the warm baseline, "
                f"over the {SOAK_MAX_GROWTH_RATIO:.2f}x allowance"
            )
    if monotonic:
        findings.append(
            f"RSS rose in every retained window and gained {(rise_ratio - 1) * 100:.2f}% "
            f"({slope_bytes:,.0f} bytes per window): "
            + " -> ".join(f"{value:,.0f}" for value in retained_rss)
        )
    if final["min_available_permits"] != MAX_CONCURRENT_MEASUREMENTS:
        findings.append(
            f"{final['min_available_permits']:.0f} of {MAX_CONCURRENT_MEASUREMENTS} permits "
            "were available at rest in the final window: a permit was not released"
        )
    if max(window["max_measure_workers"] for window in windows) > MAX_CONCURRENT_MEASUREMENTS:
        findings.append("the measurement pool held more threads than its bound")
    if max(window["max_abandoned_workers"] for window in windows) > 0:
        findings.append("a worker was abandoned: a measurement passed its deadline mid-soak")
    sessions = {window["distinct_encoder_sessions"] for window in windows}
    if sessions != {1}:
        findings.append(f"encoder sessions were rebuilt during the run: {sorted(sessions)}")

    return ("FAIL" if findings else "PASS"), findings, evidence


@pytest.mark.models
@pytest.mark.timeout(SOAK_TIMEOUT_SECONDS)
def test_measurement_reaches_a_resource_plateau() -> None:
    """The release soak: sustained real measurement, not preflight.

    The two probes above exercise pure-Python paths. Everything that actually holds a
    resource — the ONNX sessions, the tokenizers, the thread pool, the admission permits —
    is only touched by `measure`, so a plateau proven without it is a plateau proven on the
    part of the process that was never in question.
    """
    if measurement_disabled():
        pytest.skip("measurement kill switch is active")
    if not manifest_present():
        pytest.skip("no verified model bundle; the soak measures real inference or nothing")

    request = benchmark_run.load_workload(benchmark_run.REFERENCE_WORKLOAD)
    task = request.question
    service = PrismService.from_default_bundle()
    process = psutil.Process()

    started = time.monotonic()
    with benchmark_run.PeakRssSampler() as sampler:
        samples, error = _collect(service, request, task, sampler, process, started)
    elapsed = time.monotonic() - started

    measured = samples[SOAK_WARM_UP:]
    evidence: dict[str, Any]
    if error is not None:
        verdict, findings, evidence = "ABORTED", [f"the run stopped early: {error}"], {}
    elif len(measured) < WINDOW_COUNT:
        verdict, findings, evidence = (
            "ABORTED",
            [
                f"{len(measured)} measurements after {SOAK_WARM_UP} warm-up calls is fewer "
                f"than the {WINDOW_COUNT} windows the verdict is computed over"
            ],
            {},
        )
    else:
        windows = _windows(measured)
        verdict, findings, evidence = _evaluate(windows)
        evidence["windows"] = windows

    artifact = {
        "package_version": PACKAGE_VERSION,
        "workload": benchmark_run.REFERENCE_WORKLOAD.name,
        "hardware": {
            "platform": platform.platform(),
            "logical_cores": psutil.cpu_count(logical=True),
            "python": platform.python_version(),
        },
        "budget": {
            "max_measurements": SOAK_MEASUREMENTS,
            "max_seconds": SOAK_SECONDS,
            "warm_up_measurements": SOAK_WARM_UP,
            "windows": WINDOW_COUNT,
        },
        "measurements_completed": len(samples),
        "measurements_scored": len(measured),
        "elapsed_seconds": elapsed,
        "peak_rss_bytes": sampler.peak_bytes,
        "rss_samples": sampler.samples,
        "verdict": verdict,
        "findings": findings,
        "evidence": evidence,
        "samples": samples,
    }

    # Written before anything is asserted, because the failure is the evidence.
    SOAK_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    SOAK_ARTIFACT.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Read back from disk, and every assertion below is against what is on disk. A soak
    # that was started, or whose record was never opened, is not a soak that passed.
    recorded = json.loads(SOAK_ARTIFACT.read_text(encoding="utf-8"))
    summary = (
        f"soak: {recorded['measurements_scored']} scored measurements over "
        f"{recorded['elapsed_seconds'] / 60:.1f} min -> {recorded['verdict']} "
        f"({SOAK_ARTIFACT})"
    )
    print(summary)

    assert recorded["measurements_scored"] >= WINDOW_COUNT, summary
    assert recorded["verdict"] == "PASS", summary + "\n" + "\n".join(recorded["findings"])
