"""Environment determinism.

Identical fixtures are executed under at least two locales, two time zones, three
``PYTHONHASHSEED`` values, and fresh processes, and their canonical SHA-256 digests are
compared.

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

#: Dotted and dotless i in the input itself, so the casefolding condition is generated
#: by the fixture rather than left to whichever locale the runner happens to have.
#: ``classify`` casefolds the task (src/prism/preflight/classify.py:56); under Turkish
#: rules "I" folds to "ı" and "İ" to "i", which would route this text differently.
TURKISH_TASK = (
    "İNCELEME: IŞIK servisinin bileşen sınırları, servisler arası bağımlılık "
    "ve ığdır ölçeklenebilirlik dengesi değerlendirilsin."
)

TURKISH_LOCALE = "tr_TR.UTF-8"

#: The environment variable alone does not change a Python child's locale: the
#: interpreter starts in the "C" locale for everything but ``LC_CTYPE`` and never calls
#: ``setlocale`` on its own. The probe therefore applies the request itself and reports
#: evidence of what took effect, so a permutation cannot silently do nothing.
#:
#: The evidence is behavioural, not ``setlocale``'s return value. The Windows CRT accepts
#: any well-formed locale name — ``xx_XX.UTF-8`` is returned verbatim without error — so
#: the returned string proves only that the name parsed. Locale-rendered month and day
#: names come from the C library, so comparing them against the "C" locale's shows
#: whether the request actually reached it.
PROBE = """
import json
import locale
import os
import sys
import time

STAMP = time.struct_time((2026, 8, 10, 0, 0, 0, 0, 222, 0))


def rendered() -> str:
    return time.strftime("%A|%B", STAMP)


locale.setlocale(locale.LC_ALL, "C")
neutral = rendered()

requested = os.environ.get("PRISM_TEST_LOCALE")
if not requested:
    active, evidence = "NOT-REQUESTED", neutral
else:
    try:
        locale.setlocale(locale.LC_ALL, requested)
    except locale.Error:
        active, evidence = "UNAVAILABLE", neutral
    else:
        evidence = rendered()
        active = requested if evidence != neutral else "INERT"

from prism.canonical import canonical_digest
from prism.contracts import PreflightRequest, PrismMode
from prism.preflight.contract import build_preflight_contract
from prism.preflight.registry import PerspectiveRegistry

registry = PerspectiveRegistry.load()
digests = {
    "registry": registry.content_hash,
    "locale": active,
    "locale_evidence": evidence,
}
for label, task in (("", sys.argv[1]), ("turkish-", sys.argv[2])):
    for mode in ("lite", "standard", "critical"):
        report = build_preflight_contract(
            PreflightRequest(task=task, mode=PrismMode(mode)), registry
        )
        digests[label + mode] = canonical_digest(report)
print(json.dumps(digests))
"""

#: Environment permutations. Time zone is read by C libraries and by datetime;
#: PYTHONHASHSEED changes set and dict iteration order for str keys. The Turkish locale
#: is not here: it needs to be applied inside the child and is not installed everywhere,
#: so it gets its own test that either proves activation or skips loudly.
MATRIX: list[dict[str, str]] = [
    {},
    {"PYTHONHASHSEED": "0"},
    {"PYTHONHASHSEED": "1"},
    {"PYTHONHASHSEED": "4294967295"},
    {"TZ": "UTC"},
    {"TZ": "Asia/Kolkata"},
    {"LC_ALL": "C"},
    {"PYTHONUTF8": "1"},
]


def run_probe(overrides: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(overrides)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    completed = subprocess.run(  # fixed argv, no shell
        [sys.executable, "-c", PROBE, TASK, TURKISH_TASK],
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
def test_the_turkish_locale_is_proven_active_or_skipped_with_a_reason() -> None:
    """The dotless-i trap, with the locale actually applied.

    The previous form set ``LC_ALL`` in the child's environment and asserted nothing
    about it. Python ignores that for everything but ``LC_CTYPE``, and the locale is not
    installed on every runner, so the case could pass without ever having run.
    """
    result = run_probe({"PRISM_TEST_LOCALE": TURKISH_LOCALE})
    active = result["locale"]
    if active in {"UNAVAILABLE", "INERT"}:
        pytest.skip(
            f"{TURKISH_LOCALE} is "
            + ("not installed on" if active == "UNAVAILABLE" else "accepted but inert on")
            + " this runner, so the Turkish casefolding case was NOT exercised here. "
            "The dotted/dotless-i input in TURKISH_TASK still runs under every other "
            "permutation."
        )

    assert active == TURKISH_LOCALE
    assert result["locale_evidence"] != run_probe({})["locale_evidence"]

    baseline = run_probe({})
    ignored = {"locale", "locale_evidence"}
    assert {k: v for k, v in result.items() if k not in ignored} == {
        k: v for k, v in baseline.items() if k not in ignored
    }, f"canonical digest changed under an active {TURKISH_LOCALE}"


@pytest.mark.slow
def test_a_fresh_process_reproduces_the_registry_hash() -> None:
    """The registry hash appears in every report, so a per-process value would make two
    identical runs look like different configurations."""
    first = run_probe({"PYTHONHASHSEED": "0"})["registry"]
    second = run_probe({"PYTHONHASHSEED": "12345"})["registry"]
    assert first == second
    assert first.startswith("sha256:")
