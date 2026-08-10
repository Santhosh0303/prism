"""Gates G7 and G25 — absolute budgets and relative regression against the baseline.

Two kinds of failure, deliberately checked together:

* **Absolute.** A number that exceeds its hard limit blocks regardless of history. Being
  no worse than last time is not a defence when last time was already over budget.
* **Relative.** A number that drifts past its budget compared with the signed baseline
  blocks even while inside the absolute limit, because that is how a system degrades —
  five percent at a time, each step defensible on its own.

Latency, CPU, memory, and output size are compared in one pass so that a speed improvement
bought with memory or a larger report cannot be presented as a straight win.

Comparison across different hardware is refused rather than reported with a caveat. A p95
from a different machine is not a slower or faster version of this one; it is a different
measurement.

    uv run python scripts/compare_benchmarks.py --candidate benchmarks/out/run.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import check_regression_baseline
from _gate import REPO_ROOT, GateResult, failed, passed, report, skipped

GATE: Final[str] = "G7/G25 performance and regression"

CANDIDATE_PATH = REPO_ROOT / "benchmarks" / "out" / "run.json"


@dataclass(frozen=True, slots=True)
class Budget:
    """One metric's absolute ceiling and its allowed drift from the baseline."""

    key: str
    label: str
    hard_limit: float | None
    target: float | None
    relative_budget: float
    unit: str

    def absolute_finding(self, value: float) -> str | None:
        if self.hard_limit is not None and value > self.hard_limit:
            return (
                f"{self.label}: {value:,.1f} {self.unit} exceeds the hard limit "
                f"{self.hard_limit:,.1f} {self.unit}"
            )
        return None

    def relative_finding(self, value: float, baseline: float) -> str | None:
        if baseline <= 0:
            return None
        drift = (value - baseline) / baseline
        if drift > self.relative_budget:
            return (
                f"{self.label}: {drift * 100:.1f}% worse than baseline "
                f"({value:,.1f} vs {baseline:,.1f} {self.unit}), "
                f"budget {self.relative_budget * 100:.0f}%"
            )
        return None


#: Budgets as declared in the success criteria. Relative budgets are the hard column: the
#: target column is reported, but only the hard budget blocks.
BUDGETS: Final[tuple[Budget, ...]] = (
    Budget("preflight_p95_ms", "preflight p95", 50.0, 15.0, 0.10, "ms"),
    Budget("measurement_p95_ms", "measurement p95", 8_000.0, 3_500.0, 0.15, "ms"),
    Budget("measurement_p99_ms", "measurement p99", 10_000.0, 9_000.0, 0.15, "ms"),
    Budget("cpu_seconds_per_measurement", "CPU per measurement", None, None, 0.15, "s"),
    Budget("peak_rss_bytes", "peak RSS", 3 * 1024**3, 2.2 * 1024**3, 0.10, "bytes"),
    Budget("default_report_bytes", "default report size", 12_288.0, 6_144.0, 0.10, "bytes"),
)


def metrics_from_report(report_data: dict[str, Any]) -> dict[str, float]:
    """Flatten a benchmark report into the metric names the baseline uses."""
    preflight = report_data.get("preflight", {})
    measurement = report_data.get("measurement", {})
    values: dict[str, float] = {
        "preflight_p95_ms": float(preflight.get("p95_ms", 0.0)),
        "measurement_p95_ms": float(measurement.get("p95_ms", 0.0)),
        "measurement_p99_ms": float(measurement.get("p99_ms", 0.0)),
        "cpu_seconds_per_measurement": float(report_data.get("cpu_seconds_per_measurement", 0.0)),
        "peak_rss_bytes": float(report_data.get("peak_rss_bytes", 0.0)),
    }
    report_bytes = report_data.get("default_report_bytes")
    if report_bytes is not None:
        values["default_report_bytes"] = float(report_bytes)
    return values


def _describe(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def _hardware_matches(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    keys = ("platform", "processor", "logical_cores")
    return all(str(candidate.get(key)) == str(baseline.get(key)) for key in keys)


def run(*, candidate_path: Path | None = None, baseline_path: Path | None = None) -> GateResult:
    source = candidate_path if candidate_path is not None else CANDIDATE_PATH
    if not source.is_file():
        return skipped(
            GATE,
            "no candidate benchmark run; produce one with "
            "`uv run python benchmarks/run.py --profile release --output benchmarks/out/run.json`",
        )

    candidate = json.loads(source.read_text(encoding="utf-8"))
    try:
        baseline = check_regression_baseline.load(baseline_path)
    except FileNotFoundError:
        return skipped(GATE, "no regression baseline to compare against")

    findings: list[str] = []
    notes: list[str] = []

    if candidate.get("profile") != "release":
        findings.append(
            f"candidate profile is '{candidate.get('profile')}': only the release profile "
            "takes enough samples for a p95 to be evidence"
        )

    candidate_workload = candidate.get("workload")
    if candidate_workload and candidate_workload != baseline.get("workload"):
        findings.append(
            f"workload mismatch: candidate ran '{candidate_workload}', "
            f"baseline recorded '{baseline.get('workload')}'"
        )

    comparable = _hardware_matches(candidate.get("hardware", {}), baseline.get("hardware", {}))
    if not comparable:
        notes.append(
            "hardware differs from the baseline; relative regression was not evaluated "
            "because the comparison would not mean anything"
        )

    candidate_metrics = metrics_from_report(candidate)
    baseline_metrics = baseline.get("metrics", {})

    for budget in BUDGETS:
        value = candidate_metrics.get(budget.key)
        if value is None:
            continue

        absolute = budget.absolute_finding(value)
        if absolute:
            findings.append(absolute)

        if budget.target is not None and value > budget.target and not absolute:
            notes.append(
                f"{budget.label}: {value:,.1f} {budget.unit} misses its "
                f"{budget.target:,.1f} {budget.unit} target but is inside the hard limit"
            )

        if comparable:
            reference = baseline_metrics.get(budget.key)
            if isinstance(reference, (int, float)):
                relative = budget.relative_finding(value, float(reference))
                if relative:
                    findings.append(relative)

    detail: dict[str, Any] = {
        # A candidate produced outside the working tree is a normal case — a CI artifact
        # downloaded to a scratch directory, for one — so the label degrades rather than
        # raising.
        "candidate": _describe(source),
        "baseline_id": baseline.get("baseline_id"),
        "hardware_comparable": comparable,
        "metrics_compared": len([b for b in BUDGETS if b.key in candidate_metrics]),
        "notes": notes,
    }
    if findings:
        return failed(GATE, findings, **detail)
    return passed(GATE, **detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", default=None, help="benchmark report to check")
    parser.add_argument("--baseline", default=None, help="baseline to compare against")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    args = parser.parse_args()
    result = run(
        candidate_path=Path(args.candidate) if args.candidate else None,
        baseline_path=Path(args.baseline) if args.baseline else None,
    )
    return report(result, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
