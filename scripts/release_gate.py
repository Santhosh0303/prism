"""The release gate: run every blocking check and return one verdict.

This is the single command Task 19 gates on. It runs the in-repo gate scripts in-process
and the external toolchain as subprocesses, then reports each result with the same three
verdicts. Nothing here invents a result: a tool that is not installed reports `SKIP`, and
under `--strict` a skip blocks exactly like a failure. A gate that cannot run has not
passed, and the difference between "clean" and "not checked" is the whole point of having
a gate.

    uv run python scripts/release_gate.py --strict

Expected on a release candidate:

    {"verdict": "PASS", "blocking_failures": 0}

**This build does not reach that.** The evaluation corpus does not exist and the baseline
is unsigned, so `--strict` fails by design. That is the honest state of the project, and
the gate is written to report it rather than to be satisfiable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

import build_sbom
import check_architecture
import check_links
import check_regression_baseline
import check_reproducible_build
import check_seed_lock
import compare_benchmarks
import verify_offline
from _gate import REPO_ROOT, GateResult, failed, passed, skipped

#: External commands, each stated as the gate it satisfies. `uv run` is deliberately not
#: used: the gate runs under the interpreter that invoked it, so the versions checked are
#: the versions present.
ExternalGate = tuple[str, list[str]]

EXTERNAL_GATES: Final[tuple[ExternalGate, ...]] = (
    ("G1 test suite", [sys.executable, "-m", "pytest", "-q"]),
    ("G13 lint", [sys.executable, "-m", "ruff", "check", "."]),
    ("G13 format", [sys.executable, "-m", "ruff", "format", "--check", "."]),
    ("G13 types", [sys.executable, "-m", "mypy", "src", "tests"]),
    ("G9 static security", [sys.executable, "-m", "bandit", "-r", "src", "-q"]),
    ("G13 dead code", [sys.executable, "-m", "vulture", "src", "tests", "--min-confidence", "90"]),
    ("G13 dependencies", [sys.executable, "-m", "deptry", "."]),
)

#: Import boundaries. import-linter ships a console script rather than a `-m` entry point.
#: On Windows it sits beside the interpreter rather than on PATH, so the interpreter's own
#: directory is searched before PATH — otherwise the gate reports SKIP on a machine where
#: the tool is installed and working.
IMPORT_LINTER: Final[str] = "lint-imports"

TIMEOUT_SECONDS: Final[int] = 1800


def _console_script(name: str) -> str | None:
    """Locate a console script, preferring the environment this interpreter belongs to."""
    scripts_dir = Path(sys.executable).parent
    for candidate in (scripts_dir / name, scripts_dir / f"{name}.exe"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def _advisories_gate() -> GateResult:
    """Audit the resolved lock, not the installed environment.

    Auditing the environment tries to look up the local project itself, which is not on any
    index, and `--strict` correctly refuses to call that a clean scan. The lock is also the
    more honest subject: it is what a user will actually install, and it is what the SBOM
    is generated from, so both controls describe the same dependency set.
    """
    name = "G10 advisories"
    if shutil.which("uv") is None:
        return skipped(name, "uv is not on PATH, so the lock could not be exported")

    with tempfile.TemporaryDirectory(prefix="prism-audit-") as workspace:
        requirements = Path(workspace) / "requirements.txt"
        export = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [  # noqa: S607 - uv is resolved from PATH by design
                "uv",
                "export",
                "--format",
                "requirements-txt",
                "--no-emit-project",
                "--no-dev",
                "-o",
                str(requirements),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        if export.returncode != 0:
            return failed(name, ["uv export failed", *export.stderr.strip().splitlines()[-2:]])

        return _run_external(
            name,
            [sys.executable, "-m", "pip_audit", "--strict", "-r", str(requirements)],
        )


def _run_external(name: str, command: list[str]) -> GateResult:
    executable = command[0]
    if executable != sys.executable and shutil.which(executable) is None:
        return skipped(name, f"{executable} is not installed")

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv from a constant, no shell
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return skipped(name, f"{executable} is not installed")
    except subprocess.TimeoutExpired:
        return failed(name, [f"timed out after {TIMEOUT_SECONDS}s"])

    if completed.returncode == 0:
        return passed(name, command=" ".join(Path(part).name for part in command[:3]))

    output = (completed.stdout + completed.stderr).strip().splitlines()
    decisive = [line for line in output if line.strip()][-4:]
    return failed(name, decisive, exit_code=completed.returncode)


def _in_process_gates(*, strict: bool) -> list[Callable[[], GateResult]]:
    return [
        check_links.run,
        check_architecture.run,
        check_seed_lock.run,
        lambda: check_regression_baseline.run(require_signature=strict),
        compare_benchmarks.run,
        build_sbom.run,
        verify_offline.run,
        check_reproducible_build.run,
    ]


def run_all(*, strict: bool, skip_slow: bool) -> list[GateResult]:
    results: list[GateResult] = []

    for name, command in EXTERNAL_GATES:
        results.append(_run_external(name, command))

    results.append(_advisories_gate())

    linter = _console_script(IMPORT_LINTER)
    if linter is None:
        results.append(skipped("G13 import boundaries", f"{IMPORT_LINTER} is not installed"))
    else:
        results.append(_run_external("G13 import boundaries", [linter]))

    for gate in _in_process_gates(strict=strict):
        if skip_slow and gate in (check_reproducible_build.run, verify_offline.run):
            continue
        try:
            results.append(gate())
        except Exception as error:
            results.append(failed(getattr(gate, "__module__", "gate"), [repr(error)]))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat SKIP as blocking and require a signed baseline",
    )
    parser.add_argument(
        "--skip-slow",
        action="store_true",
        help="omit the double build and the offline measurement run",
    )
    parser.add_argument("--text", action="store_true", help="human-readable output")
    args = parser.parse_args()

    results = run_all(strict=args.strict, skip_slow=args.skip_slow)

    failures = [result for result in results if result.verdict == "FAIL"]
    skips = [result for result in results if result.verdict == "SKIP"]
    blocking = len(failures) + (len(skips) if args.strict else 0)

    summary = {
        "verdict": "PASS" if blocking == 0 else "FAIL",
        "blocking_failures": blocking,
        "failed": len(failures),
        "skipped": len(skips),
        "passed": len(results) - len(failures) - len(skips),
        "strict": args.strict,
        "gates": [result.as_dict() for result in results],
    }

    if args.text:
        for result in results:
            print(f"{result.verdict:5} {result.gate}")
            for finding in result.findings:
                print(f"      - {finding}")
        print(f"\nverdict: {summary['verdict']}  blocking: {blocking}")
    else:
        print(json.dumps(summary, indent=2, sort_keys=True))

    return 0 if blocking == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
