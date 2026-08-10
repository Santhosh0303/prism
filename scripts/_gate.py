"""Shared plumbing for the release-gate scripts.

Each gate script exposes ``run() -> GateResult`` so that ``release_gate.py`` can execute
them in one process rather than shelling out to nine interpreters and parsing text. The
same function backs the standalone ``main()``, so a gate cannot behave one way under CI
and another way when an engineer runs it directly.

A gate reports one of three verdicts:

``PASS``  the check ran and found nothing.
``FAIL``  the check ran and found something that blocks release.
``SKIP``  the check could not run because an input it needs is absent. A skip is never
          counted as a pass; ``release_gate.py --strict`` treats it as blocking.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

Verdict = Literal["PASS", "FAIL", "SKIP"]


@dataclass(frozen=True, slots=True)
class GateResult:
    """One gate's outcome. Findings are human-readable and content-free."""

    gate: str
    verdict: Verdict
    findings: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.verdict != "PASS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "verdict": self.verdict,
            "findings": list(self.findings),
            "detail": self.detail,
        }


def passed(gate: str, **detail: Any) -> GateResult:
    return GateResult(gate=gate, verdict="PASS", detail=detail)


def failed(gate: str, findings: list[str], **detail: Any) -> GateResult:
    return GateResult(gate=gate, verdict="FAIL", findings=tuple(findings), detail=detail)


def skipped(gate: str, reason: str, **detail: Any) -> GateResult:
    return GateResult(gate=gate, verdict="SKIP", findings=(reason,), detail=detail)


def report(result: GateResult, as_json: bool) -> int:
    """Print one gate result and return the process exit code."""
    if as_json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"{result.verdict}  {result.gate}")
        for finding in result.findings:
            print(f"  - {finding}")
        for key, value in sorted(result.detail.items()):
            print(f"  {key}: {value}")
    return 0 if result.verdict == "PASS" else 1


def tracked_files(suffix: str | None = None) -> list[Path]:
    """Return files git actually tracks.

    Globbing the working tree would sweep in ignored material — the private design
    documents, the model weights, a stray scratch file — and a gate that inspects
    untracked bytes is measuring the wrong thing.
    """
    completed = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git is resolved from PATH by design
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    names = [name for name in completed.stdout.split("\0") if name]
    paths = [REPO_ROOT / name for name in names]
    if suffix is not None:
        paths = [path for path in paths if path.suffix == suffix]
    return [path for path in paths if path.is_file()]


def add_src_to_path() -> None:
    """Import PRISM from the working tree without requiring an install."""
    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
