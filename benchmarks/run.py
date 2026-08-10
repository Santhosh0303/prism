"""Benchmark runner.

Records p50/p95/p99/max, CPU-seconds, and peak RSS for the reference workload, plus the
hardware it ran on. A latency number without its hardware is not evidence.

    uv run python benchmarks/run.py --profile release --output benchmarks/out/run.json

The release profile is at least 100 preflight calls and 30 measurements after warm-up
(design section 12.2). The default smoke profile is smaller and is not release evidence.
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
    CandidatePacket,
    Claim,
    EvidenceStatus,
    MeasureRequest,
    PreflightRequest,
    PrismMode,
    ProvenanceStatus,
)
from prism.service import PrismService  # noqa: E402
from prism.version import PACKAGE_VERSION  # noqa: E402

TASK = (
    "Assess the system design: component boundaries, coupling between services, "
    "and the scalability tradeoff of the proposed architecture under enterprise load."
)

CLAIM_TEXTS = [
    "The service is ready for production deployment across all supported regions today.",
    "A single failed dependency in the payment path takes down the entire checkout flow.",
    "Latency stays under one second at the current prototype load levels we have tested.",
    "The retry path drops messages silently whenever the downstream queue rejects them.",
]


def reference_request(candidates: int = 5, claims: int = 4) -> MeasureRequest:
    """The declared reference workload: five perspectives, four claims each."""
    packets = tuple(
        CandidatePacket(
            candidate_id=f"lens{index}",
            source_group_id="host-pass-001",
            source_label=f"lens{index}",
            provenance_status=ProvenanceStatus.DECLARED_UNVERIFIED,
            perspective=f"lens{index}",
            claims=tuple(
                Claim(
                    claim_id=f"lens{index}-{position}",
                    text=f"{CLAIM_TEXTS[position % len(CLAIM_TEXTS)]} Variant {index}.",
                    confidence=60 + index,
                    evidence_status=EvidenceStatus.INFERRED,
                )
                for position in range(claims)
            ),
        )
        for index in range(candidates)
    )
    return MeasureRequest(question=TASK, candidates=packets)


def distribution(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    if not ordered:
        return {}

    def percentile(fraction: float) -> float:
        index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
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
    args = parser.parse_args()

    preflight_samples = 100 if args.profile == "release" else 20
    measure_samples = 30 if args.profile == "release" else 5

    process = psutil.Process()
    service = PrismService.from_default_bundle()
    request = reference_request()

    # Warm-up is excluded: cold start is reported separately, never folded into p95.
    cold_started = time.perf_counter()
    service.measure(request)
    cold_start_ms = (time.perf_counter() - cold_started) * 1000

    preflight_latencies: list[float] = []
    for _ in range(preflight_samples):
        started = time.perf_counter()
        service.preflight(PreflightRequest(task=TASK, mode=PrismMode.CRITICAL))
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
