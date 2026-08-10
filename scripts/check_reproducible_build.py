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
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Final

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


def run() -> GateResult:
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

    detail = {"artifacts": len(digests), "digests": digests}
    if findings:
        return failed(GATE, findings, **detail)
    return passed(GATE, **detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    args = parser.parse_args()
    return report(run(), as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
