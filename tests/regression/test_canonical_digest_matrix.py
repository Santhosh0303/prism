"""Environment determinism — plan Task 18, Step 2 (gate G22).

"Execute identical fixtures ... under at least two locales, two time zones, three
PYTHONHASHSEED values, and fresh processes ... compare canonical SHA-256."

Each case runs in a **separate process**, which is the point: a digest that is stable
within one interpreter proves nothing about set iteration order, locale-dependent
casefolding, or hash randomisation. Only a fresh process exercises those.
"""

from __future__ import annotations

import json
import os
import subprocess  # the harness deliberately spawns fresh interpreters
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

TASK = (
    "Assess the system design: component boundaries, coupling between services, "
    "and the scalability tradeoff of the proposed architecture."
)

PROBE = """
import sys
from prism.canonical import canonical_digest
from prism.contracts import PreflightRequest, PrismMode
from prism.preflight.contract import build_preflight_contract
from prism.preflight.registry import PerspectiveRegistry

registry = PerspectiveRegistry.load()
digests = {
    "registry": registry.content_hash,
}
for mode in ("lite", "standard", "critical"):
    report = build_preflight_contract(
        PreflightRequest(task=sys.argv[1], mode=PrismMode(mode)), registry
    )
    digests[mode] = canonical_digest(report)
print(__import__("json").dumps(digests))
"""

#: Environment permutations. Locale and time zone are read by C libraries and by
#: datetime; PYTHONHASHSEED changes set and dict iteration order for str keys.
MATRIX: list[dict[str, str]] = [
    {},
    {"PYTHONHASHSEED": "0"},
    {"PYTHONHASHSEED": "1"},
    {"PYTHONHASHSEED": "4294967295"},
    {"TZ": "UTC"},
    {"TZ": "Asia/Kolkata"},
    {"LC_ALL": "C"},
    {"LC_ALL": "tr_TR.UTF-8"},  # dotless-i locale: the classic casefolding trap
    {"PYTHONUTF8": "1"},
]


def run_probe(overrides: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(overrides)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    completed = subprocess.run(  # fixed argv, no shell
        [sys.executable, "-c", PROBE, TASK],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-800:]
    return dict(json.loads(completed.stdout.strip().splitlines()[-1]))


@pytest.mark.slow
def test_canonical_digests_are_identical_across_the_environment_matrix() -> None:
    baseline = run_probe({})
    for overrides in MATRIX[1:]:
        result = run_probe(overrides)
        assert result == baseline, (
            f"canonical digest changed under {overrides or 'default environment'}:\n"
            f"  baseline: {baseline}\n"
            f"  actual:   {result}"
        )


@pytest.mark.slow
def test_a_fresh_process_reproduces_the_registry_hash() -> None:
    """The registry hash appears in every report, so a per-process value would make two
    identical runs look like different configurations."""
    first = run_probe({"PYTHONHASHSEED": "0"})["registry"]
    second = run_probe({"PYTHONHASHSEED": "12345"})["registry"]
    assert first == second
    assert first.startswith("sha256:")
