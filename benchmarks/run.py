"""Benchmark runner.

Records p50/p95/p99/max, CPU-seconds, and peak RSS for a workload, plus the hardware it
ran on. A latency number without its hardware is not evidence.

    uv run python benchmarks/run.py --profile release --output benchmarks/out/run.json

The release profile is at least 100 preflight calls and 30 measurements after warm-up.
The default smoke profile is smaller and is not release evidence.

Two workloads, and the difference between them is the point:

* ``reference.json`` is the declared comparable workload. It submits the legal maximum
  *shape* — five candidates, four claims each, 160 cross-candidate pairs — but its claims
  are about four different subjects, so E1 drops 100 of those pairs and only 60 reach the
  NLI model.
* ``adversarial.json`` puts every claim on one subject and one scope, so all 160 pairs
  survive E1 and every one of them is scored in both directions. That is the maximum NLI
  *work* the contract permits, and it is what a published worst case has to describe.

Running the adversarial workload therefore gates the measurement deadline where it means
something. On current hardware it lands on both sides of that deadline from one run to the
next, and whichever way a run falls is recorded rather than tuned away.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any, Final

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import psutil  # noqa: E402

from prism.contracts import (  # noqa: E402
    MeasureReport,
    MeasureRequest,
    PreflightRequest,
    PrismMode,
)
from prism.limits import MAX_CROSS_CANDIDATE_PAIRS  # noqa: E402
from prism.service import PrismService  # noqa: E402
from prism.version import PACKAGE_VERSION  # noqa: E402

#: The declared workloads, held as data rather than built in code so that the benchmark,
#: the offline-network check, and any future comparison all measure the same input. A
#: latency series is only comparable against another run of the identical workload;
#: regenerating it from code that has drifted silently invalidates the baseline.
WORKLOADS = REPO_ROOT / "benchmarks" / "workloads"
REFERENCE_WORKLOAD = WORKLOADS / "reference.json"
ADVERSARIAL_WORKLOAD = WORKLOADS / "adversarial.json"

#: The latency series is measured under this deadline rather than the workload's own, so
#: that a workload which breaches its deadline still produces a distribution instead of
#: nothing. This is instrumentation, not a threshold: the deadline gate below is evaluated
#: against the workload's declared timeout, which is unchanged, and both numbers are
#: reported side by side. A ceiling cannot be recorded honestly without observing past it.
CEILING_OBSERVATION_TIMEOUT_SECONDS: Final[float] = 60.0

#: How often the RSS sampler wakes. Fast enough to catch a peak that lives inside a single
#: measurement, cheap enough that sampling does not meaningfully perturb what it measures.
RSS_SAMPLE_INTERVAL_SECONDS: Final[float] = 0.05


class PeakRssSampler:
    """Continuous peak RSS across the process tree.

    One reading at the end of a run records whatever happened to be resident when the last
    measurement returned, which is not the peak: allocation during inference is released
    between calls, so the maximum sits *inside* a measurement and a final reading walks
    straight past it. Sampling on a background thread records the maximum that actually
    occurred. Children are included because memory a worker process needed is memory this
    workload required.
    """

    def __init__(self, interval_seconds: float = RSS_SAMPLE_INTERVAL_SECONDS) -> None:
        self._interval = interval_seconds
        self._process = psutil.Process()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._sample_until_stopped, name="prism-rss-sampler", daemon=True
        )
        self.peak_bytes = 0
        self.samples = 0

    def tree_rss_bytes(self) -> int:
        """Resident memory of this process plus every descendant, as one number."""
        total: int = 0
        try:
            total = self._process.memory_info().rss
            children = self._process.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0
        for child in children:
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # A child that exited between enumeration and reading contributed to an
                # earlier sample; skipping it here cannot lower the recorded maximum.
                continue
        return total

    def sample(self) -> int:
        """Take one reading now, fold it into the maximum, and return it.

        Every reading goes through here, including the ones a caller asks for directly.
        A reading taken outside the maximum can exceed it, and a peak that is smaller than
        a number reported beside it is not a peak.
        """
        reading = self.tree_rss_bytes()
        self.peak_bytes = max(self.peak_bytes, reading)
        self.samples += 1
        return reading

    def _sample_until_stopped(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self._interval)

    def __enter__(self) -> PeakRssSampler:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)
        # One last sample after the thread has stopped, so the record covers the whole run
        # including whatever the final measurement left resident.
        self.sample()


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


def full_pair_space_findings(report: MeasureReport) -> list[str]:
    """Check that the run really exercised the maximum NLI work, not the maximum input.

    Without this the published p95 describes the maximum input *shape*. The reference
    workload submits the same 5x4 shape and reaches 60 scored pairs, because E1 correctly
    drops the pairs whose claims are about different subjects — a perfectly good comparable
    workload, and a quarter of the inference the contract permits.

    Both directions are covered by ``pairs_scored_by_nli``: a pair counts as scored only
    when both ``score_a_to_b`` and ``score_b_to_a`` exist, so 160 here is 320 NLI calls.
    """
    expected = MAX_CROSS_CANDIDATE_PAIRS
    findings: list[str] = []
    if report.pairs_total != expected:
        findings.append(
            f"pairs_total is {report.pairs_total}, not the {expected}-pair legal maximum: "
            "this workload does not have the adversarial shape"
        )
    if report.relevant_pairs != expected:
        findings.append(
            f"E1 kept {report.relevant_pairs} of {expected} pairs: the claims are not all "
            "about one subject, so this measures the maximum input shape, not the maximum "
            "NLI work"
        )
    if report.scope_divergent_count != 0:
        findings.append(
            f"{report.scope_divergent_count} pairs left the denominator as scope divergent: "
            "the claims are not all in one scope"
        )
    if report.contradiction_denominator != expected:
        findings.append(
            f"contradiction_denominator is {report.contradiction_denominator}, not {expected}"
        )
    if report.pairs_scored_by_nli != expected:
        findings.append(
            f"E2 scored {report.pairs_scored_by_nli} of {expected} pairs in both directions"
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("smoke", "release"), default="smoke")
    parser.add_argument("--output", default=None)
    parser.add_argument("--workload", default=None, help="workload file; defaults to reference")
    args = parser.parse_args()

    preflight_samples = 100 if args.profile == "release" else 20
    measure_samples = 30 if args.profile == "release" else 5

    workload_path = Path(args.workload) if args.workload else REFERENCE_WORKLOAD
    adversarial = workload_path.resolve() == ADVERSARIAL_WORKLOAD.resolve()
    request = load_workload(workload_path)
    task = request.question

    declared_deadline_seconds = request.config.timeout_seconds
    observation_deadline_seconds = max(
        declared_deadline_seconds, CEILING_OBSERVATION_TIMEOUT_SECONDS
    )
    observed_request = request.model_copy(
        update={
            "config": request.config.model_copy(
                update={"timeout_seconds": observation_deadline_seconds}
            )
        }
    )

    process = psutil.Process()

    with PeakRssSampler() as rss:
        service = PrismService.from_default_bundle()

        # Warm-up is excluded: cold start is reported separately, never folded into p95.
        # Its report is kept only so that every field derived from a report has one before
        # the timed loop begins; the loop overwrites it.
        cold_started = time.perf_counter()
        report = service.measure(observed_request)
        cold_start_ms = (time.perf_counter() - cold_started) * 1000

        preflight_latencies: list[float] = []
        for _ in range(preflight_samples):
            started = time.perf_counter()
            service.preflight(PreflightRequest(task=task, mode=PrismMode.CRITICAL))
            preflight_latencies.append((time.perf_counter() - started) * 1000)

        cpu_before = process.cpu_times()
        measure_latencies: list[float] = []
        for _ in range(measure_samples):
            started = time.perf_counter()
            report = service.measure(observed_request)
            measure_latencies.append((time.perf_counter() - started) * 1000)
        cpu_after = process.cpu_times()
        # The same end-of-run reading the sampler replaced, over the same process tree the
        # sampler covers. Reading only this process here would have made the pair describe
        # two different populations, so the difference between them would no longer isolate
        # the one thing it exists to show: when the sample was taken, not what was counted.
        final_rss_bytes = rss.sample()

    cpu_seconds = (cpu_after.user - cpu_before.user) + (cpu_after.system - cpu_before.system)
    worst_observed_ms = max(measure_latencies)
    deadline_met = worst_observed_ms <= declared_deadline_seconds * 1000
    findings = full_pair_space_findings(report) if adversarial else []

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
        "peak_rss_bytes": rss.peak_bytes,
        "rss_sampling": {
            "method": "maximum over the process tree, sampled on a background thread",
            "samples": rss.samples,
            "interval_seconds": RSS_SAMPLE_INTERVAL_SECONDS,
            "final_rss_bytes": final_rss_bytes,
        },
        "deadline": {
            "declared_seconds": declared_deadline_seconds,
            "series_measured_under_seconds": observation_deadline_seconds,
            "worst_observed_ms": worst_observed_ms,
            "gated_on": "warm measurement series",
            "verdict": "PASS" if deadline_met else "FAIL",
        },
        "pair_work": {
            "pairs_total": report.pairs_total,
            "relevant_pairs": report.relevant_pairs,
            "scope_divergent_count": report.scope_divergent_count,
            "contradiction_denominator": report.contradiction_denominator,
            "pairs_scored_by_nli": report.pairs_scored_by_nli,
            "full_pair_space_asserted": adversarial,
        },
        "default_report_bytes": report.report_bytes,
    }

    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

    # The record is written before anything fails, because the failure is the evidence.
    for finding in findings:
        print(f"FAIL adversarial workload: {finding}", file=sys.stderr)
    if not deadline_met:
        print(
            f"FAIL measurement deadline: worst warm measurement {worst_observed_ms:,.0f} ms "
            f"exceeds the workload's declared {declared_deadline_seconds:,.1f} s deadline; "
            "the series above was measured under "
            f"{observation_deadline_seconds:,.1f} s so that the ceiling could be recorded",
            file=sys.stderr,
        )
    return 1 if findings or not deadline_met else 0


if __name__ == "__main__":
    raise SystemExit(main())
