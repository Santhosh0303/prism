"""Benchmark runner.

Records p50/p95/p99/max, CPU-seconds, and peak RSS for the reference workload, plus the
hardware it ran on. A latency number without its hardware is not evidence.

    uv run python benchmarks/run.py --profile release --output benchmarks/out/run.json

The release profile is at least 100 preflight calls and 30 measurements after warm-up.
The default smoke profile is smaller and is not release evidence.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import psutil  # noqa: E402

from prism.contracts import (  # noqa: E402
    MeasureRequest,
    PreflightRequest,
    PrismMode,
)
from prism.service import PrismService  # noqa: E402
from prism.version import PACKAGE_VERSION  # noqa: E402

#: The declared reference workload, held as data rather than built in code so that the
#: benchmark, the offline-network check, and any future comparison all measure the same
#: input. A latency series is only comparable against another run of the identical
#: workload; regenerating it from code that has drifted silently invalidates the baseline.
WORKLOADS = REPO_ROOT / "benchmarks" / "workloads"
REFERENCE_WORKLOAD = WORKLOADS / "reference.json"


def load_workload(path: Path | None = None) -> MeasureRequest:
    """Load a workload file as a validated request. Five perspectives, four claims each."""
    source = path if path is not None else REFERENCE_WORKLOAD
    return MeasureRequest.model_validate_json(source.read_text(encoding="utf-8"))


def workload_task(path: Path | None = None) -> str:
    return load_workload(path).question


def distribution(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    if not ordered:
        return {}

    def percentile(fraction: float) -> float:
        index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
        return ordered[index]

    return {
        "count": float(len(ordered)),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_ms": ordered[-1],
        "median_absolute_deviation_ms": statistics.median(
            [abs(value - statistics.median(ordered)) for value in ordered]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("smoke", "release"), default="smoke")
    parser.add_argument("--output", default=None)
    parser.add_argument("--workload", default=None, help="workload file; defaults to reference")
    args = parser.parse_args()

    preflight_samples = 100 if args.profile == "release" else 20
    measure_samples = 30 if args.profile == "release" else 5

    process = psutil.Process()
    service = PrismService.from_default_bundle()
    workload_path = Path(args.workload) if args.workload else REFERENCE_WORKLOAD
    request = load_workload(workload_path)
    task = request.question

    # Warm-up is excluded: cold start is reported separately, never folded into p95.
    cold_started = time.perf_counter()
    service.measure(request)
    cold_start_ms = (time.perf_counter() - cold_started) * 1000

    preflight_latencies: list[float] = []
    for _ in range(preflight_samples):
        started = time.perf_counter()
        service.preflight(PreflightRequest(task=task, mode=PrismMode.CRITICAL))
        preflight_latencies.append((time.perf_counter() - started) * 1000)

    cpu_before = process.cpu_times()
    measure_latencies: list[float] = []
    report = None
    for _ in range(measure_samples):
        started = time.perf_counter()
        report = service.measure(request)
        measure_latencies.append((time.perf_counter() - started) * 1000)
    cpu_after = process.cpu_times()

    cpu_seconds = (cpu_after.user - cpu_before.user) + (cpu_after.system - cpu_before.system)

    result: dict[str, Any] = {
        "package_version": PACKAGE_VERSION,
        "profile": args.profile,
        "workload": workload_path.name,
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "logical_cores": psutil.cpu_count(logical=True),
            "python": platform.python_version(),
        },
        "preflight": distribution(preflight_latencies),
        "measurement": distribution(measure_latencies),
        "cold_start_ms": cold_start_ms,
        "cpu_seconds_per_measurement": cpu_seconds / max(1, measure_samples),
        "peak_rss_bytes": process.memory_info().rss,
        "default_report_bytes": report.report_bytes if report else None,
    }

    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
