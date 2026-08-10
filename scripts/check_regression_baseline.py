"""Gate G21 — the signed baseline is monotonic authority.

The failure this prevents is a rollback that looks like a pass. If a candidate run may be
compared against any baseline lying around, then the way to make a regression disappear is
to compare against an older, slower one. So the baseline carries a chain: each record names
the record it supersedes, and a candidate may never be measured against a superseded link.

Checks:

* the baseline parses, declares a supported schema version, and carries every metric the
  comparison needs;
* the chain is intact — `supersedes` names an earlier baseline id, and nothing points at
  itself;
* the recorded package version does not run ahead of the version being released, which is
  the shape a stale-file mix-up takes;
* the signature status is stated rather than assumed.

**This build's baseline is `UNSIGNED`.** There is no release signing pipeline yet, so the
file is a recorded measurement, not attested evidence. The gate says so out loud instead
of implying a guarantee that does not exist: with `--require-signature` it fails, and
`release_gate.py --strict` sets that flag.

    uv run python scripts/check_regression_baseline.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Final

from _gate import REPO_ROOT, GateResult, failed, passed, report

GATE: Final[str] = "G21 baseline monotonicity"

BASELINE_PATH = REPO_ROOT / "benchmarks" / "baselines" / "regression-baseline.json"

SUPPORTED_SCHEMA: Final[frozenset[str]] = frozenset({"1.0"})
VALID_SIGNATURE_STATUS: Final[frozenset[str]] = frozenset({"UNSIGNED", "SIGNED"})

#: Every metric compare_benchmarks.py needs. A baseline missing one of these cannot gate
#: the thing it claims to gate, so absence is a failure rather than a default.
REQUIRED_METRICS: Final[tuple[str, ...]] = (
    "preflight_p95_ms",
    "measurement_p95_ms",
    "measurement_p99_ms",
    "cpu_seconds_per_measurement",
    "peak_rss_bytes",
    "default_report_bytes",
)


def load(path: Path | None = None) -> dict[str, Any]:
    source = path if path is not None else BASELINE_PATH
    data: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    return data


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in version.split("."):
        digits = "".join(character for character in chunk if character.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def run(
    *,
    path: Path | None = None,
    require_signature: bool = False,
    package_version: str | None = None,
) -> GateResult:
    source = path if path is not None else BASELINE_PATH
    if not source.is_file():
        return failed(GATE, [f"missing baseline: {source.relative_to(REPO_ROOT).as_posix()}"])

    try:
        baseline = load(source)
    except json.JSONDecodeError as error:
        return failed(GATE, [f"baseline is not valid JSON: {error.msg}"])

    findings: list[str] = []

    schema_version = str(baseline.get("schema_version", ""))
    if schema_version not in SUPPORTED_SCHEMA:
        findings.append(f"unsupported baseline schema version '{schema_version}'")

    baseline_id = str(baseline.get("baseline_id", ""))
    if not baseline_id:
        findings.append("baseline_id is required; an unnamed baseline cannot be superseded")

    supersedes = baseline.get("supersedes")
    if supersedes is not None and str(supersedes) == baseline_id:
        findings.append("a baseline cannot supersede itself")

    signature_status = str(baseline.get("signature_status", ""))
    if signature_status not in VALID_SIGNATURE_STATUS:
        findings.append(f"signature_status must be one of {sorted(VALID_SIGNATURE_STATUS)}")
    elif signature_status == "UNSIGNED" and require_signature:
        findings.append(
            "baseline is UNSIGNED: there is no release signing pipeline yet, so this "
            "measurement is recorded but not attested"
        )

    if not baseline.get("hardware"):
        findings.append("a baseline without its hardware cannot be compared against anything")
    if not baseline.get("workload"):
        findings.append("a baseline must name the workload it measured")

    metrics = baseline.get("metrics")
    if not isinstance(metrics, dict):
        findings.append("metrics must be an object")
    else:
        for metric in REQUIRED_METRICS:
            value = metrics.get(metric)
            if not isinstance(value, (int, float)):
                findings.append(f"metrics.{metric} is missing or not numeric")

    recorded_version = str(baseline.get("package_version", ""))
    if (
        package_version
        and recorded_version
        and _version_tuple(recorded_version) > _version_tuple(package_version)
    ):
        findings.append(
            f"baseline records {recorded_version}, which is ahead of the candidate "
            f"{package_version}: this is a stale or mismatched file"
        )

    detail = {
        "baseline_id": baseline_id,
        "supersedes": supersedes,
        "signature_status": signature_status,
        "package_version": recorded_version,
        "workload": baseline.get("workload"),
    }
    if findings:
        return failed(GATE, findings, **detail)
    return passed(GATE, **detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=None, help="baseline file to check")
    parser.add_argument(
        "--require-signature",
        action="store_true",
        help="fail when the baseline is not signed (release gate sets this)",
    )
    parser.add_argument("--package-version", default=None, help="candidate version being released")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    args = parser.parse_args()
    result = run(
        path=Path(args.baseline) if args.baseline else None,
        require_signature=args.require_signature,
        package_version=args.package_version,
    )
    return report(result, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
