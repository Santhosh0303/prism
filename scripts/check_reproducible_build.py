"""Gate G28 — two clean builds from one source and lock produce the same artifact.

A wheel is a zip, and a zip records timestamps and file ordering, so comparing raw bytes
reports a difference on every rebuild and proves nothing. What matters is whether the
*content* is identical: same members, same bytes in each member. That is what is compared
here — each archive is normalised to a sorted list of (member name, member digest) and the
list is hashed.

A mismatch means something outside the source is leaking into the artifact: a path, a
timestamp baked into a generated file, a dependency resolved differently between runs. Any
of those breaks the promise that a published artifact can be rebuilt and checked.

    uv run python scripts/check_reproducible_build.py

The build runs in a temporary directory, twice. It needs `uv` on PATH; without it the gate
reports SKIP rather than inventing a result.

**Two builds on one machine is not independence.** They share a filesystem, a Python, a
clock and a `uv`, so every environment-dependent input they could disagree about is held
constant — which is exactly the class of difference the gate exists to detect. The same run
therefore also records what it built, and can be pointed at a record made elsewhere:

    uv run python scripts/check_reproducible_build.py --digest-out dist/build-digest.json
    uv run python scripts/check_reproducible_build.py --compare-with ci-build-digest.json

`--digest-out` is what the CI job writes; `--compare-with` is what a maintainer runs against
the downloaded CI record. The record carries the commit it was built from, so a comparison
across two machines is a comparison of the same source rather than of two moments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Final

from _gate import REPO_ROOT, GateResult, failed, passed, report, skipped

GATE: Final[str] = "G28 reproducible build"

BUILD_TIMEOUT_SECONDS: Final[int] = 600


def _normalised_digest(archive: Path) -> str:
    """Hash the archive's content, ignoring zip metadata that legitimately varies."""
    entries: list[tuple[str, str]] = []
    with zipfile.ZipFile(archive) as bundle:
        for name in sorted(bundle.namelist()):
            with bundle.open(name) as member:
                entries.append((name, hashlib.sha256(member.read()).hexdigest()))
    encoded = "\n".join(f"{name}:{digest}" for name, digest in entries).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_into(destination: Path) -> tuple[Path, ...]:
    subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller-supplied input
        ["uv", "build", "--out-dir", str(destination)],  # noqa: S607 - resolved from PATH
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    return tuple(sorted(destination.glob("*.whl")))


def _source_revision() -> str:
    """The commit the artifact was built from, so two records can be shown to describe one
    source. `GITHUB_SHA` first: on a runner it is the checked-out commit by definition."""
    revision = os.environ.get("GITHUB_SHA")
    if revision:
        return revision
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - resolved from PATH
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _build_record(digests: dict[str, str]) -> dict[str, Any]:
    return {
        "source_revision": _source_revision(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "artifacts": digests,
    }


def cross_machine_findings(local: dict[str, Any], recorded: dict[str, Any]) -> list[str]:
    """Compare this machine's build against a record made on another one.

    Separated from the build so that the comparison can be exercised without one. A
    disagreement here is the finding the same-machine check structurally cannot produce.
    """
    findings: list[str] = []
    if recorded.get("source_revision") != local.get("source_revision"):
        findings.append(
            f"the recorded build is of {recorded.get('source_revision', 'an unstated commit')} "
            f"and this one is of {local.get('source_revision')}: different source, so a "
            "matching digest would prove nothing and a differing one would explain itself"
        )
        return findings

    local_artifacts: dict[str, str] = local.get("artifacts", {})
    recorded_artifacts: dict[str, str] = recorded.get("artifacts", {})
    if not recorded_artifacts:
        findings.append("the recorded build lists no artifacts")
        return findings
    if set(local_artifacts) != set(recorded_artifacts):
        findings.append(
            f"different artifacts: {sorted(local_artifacts)} here, "
            f"{sorted(recorded_artifacts)} in the record"
        )
        return findings

    for name, digest in sorted(local_artifacts.items()):
        if recorded_artifacts[name] != digest:
            findings.append(
                f"{name}: {digest} here, {recorded_artifacts[name]} on "
                f"{recorded.get('platform', 'the other machine')} — the artifact depends on "
                "the machine that built it"
            )
    return findings


def run(digest_out: Path | None = None, compare_with: Path | None = None) -> GateResult:
    if shutil.which("uv") is None:
        return skipped(GATE, "uv is not on PATH, so no build could be attempted")

    with tempfile.TemporaryDirectory(prefix="prism-build-") as workspace:
        root = Path(workspace)
        try:
            first = _build_into(root / "first")
            second = _build_into(root / "second")
        except subprocess.CalledProcessError as error:
            tail = (error.stderr or "").strip().splitlines()[-3:]
            return failed(GATE, ["uv build failed", *tail])
        except subprocess.TimeoutExpired:
            return failed(GATE, [f"uv build exceeded {BUILD_TIMEOUT_SECONDS}s"])

        if not first or not second:
            return failed(GATE, ["the build produced no wheel"])

        first_names = [path.name for path in first]
        second_names = [path.name for path in second]
        if first_names != second_names:
            return failed(
                GATE,
                [f"different artifacts produced: {first_names} then {second_names}"],
            )

        findings: list[str] = []
        digests: dict[str, str] = {}
        for left, right in zip(first, second, strict=True):
            left_digest = _normalised_digest(left)
            right_digest = _normalised_digest(right)
            digests[left.name] = f"sha256:{left_digest}"
            if left_digest != right_digest:
                findings.append(
                    f"{left.name}: normalised content digest differs between two clean builds"
                )

    record = _build_record(digests)
    detail: dict[str, Any] = {"artifacts": len(digests), "digests": digests}

    if digest_out is not None:
        digest_out.parent.mkdir(parents=True, exist_ok=True)
        digest_out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        detail["digest_out"] = str(digest_out)

    if compare_with is not None:
        if not compare_with.is_file():
            findings.append(f"no build record to compare against at {compare_with}")
        else:
            recorded = json.loads(compare_with.read_text(encoding="utf-8"))
            detail["compared_against"] = {
                "path": str(compare_with),
                "platform": recorded.get("platform"),
                "python": recorded.get("python"),
                "source_revision": recorded.get("source_revision"),
            }
            findings.extend(cross_machine_findings(record, recorded))

    if findings:
        return failed(GATE, findings, **detail)
    return passed(GATE, **detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    parser.add_argument(
        "--digest-out",
        default=None,
        help="write this machine's build record here, for comparison from another machine",
    )
    parser.add_argument(
        "--compare-with",
        default=None,
        help="a build record from another machine to compare this build against",
    )
    args = parser.parse_args()
    result = run(
        digest_out=Path(args.digest_out) if args.digest_out else None,
        compare_with=Path(args.compare_with) if args.compare_with else None,
    )
    return report(result, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
