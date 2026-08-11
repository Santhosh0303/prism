"""Baseline integrity.

The implementation was built against three governing documents whose digests were
recorded before any source file existed. This re-checks them on every run.

The control exists because it was already needed once: ``ruff format .`` at the
repository root reformatted the fenced Python blocks inside two of the three documents,
changing their digests. Configuration now excludes Markdown from the formatter; this is
the backstop for the day that configuration is relaxed.

The documents are gitignored, so a CI checkout does not contain them. A missing document
is reported as a skip with a reason and never as a pass — the difference between "checked
and correct" and "not present to check" has to stay visible, because a control that
quietly succeeds on an empty set is worse than no control.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Recorded in the verification ledger, section 1, at document version 1.3. These are the
#: values the implementation was built against; they are not refreshed from whatever the
#: file currently contains.
BASELINE_DOCUMENTS: dict[str, str] = {
    "PRISM_ARCHITECTURE(2).md": (
        "7f6371dd1cc7705c618e2c4639087c8fed6df86bdbfb1da2fedd7486ffa2b809"
    ),
    "PRISM_IMPLEMENTATION_PLAN(2).md": (
        "b7c67531efd346fbfd0e9c5510858d1c3e3946bab13d3f5662855f5517a68a8c"
    ),
    "PRISM_SYSTEM_DESIGN_TECH_STACK(2).md": (
        "840a678aba9635c7dc38221eae2cfb8d85091d8afc86c14dc7afcc67fe665e62"
    ),
}


def test_every_recorded_digest_is_a_well_formed_sha256() -> None:
    """A truncated or mistyped constant would fail only where the documents exist, which
    is not where most runs happen. This catches it everywhere."""
    assert len(BASELINE_DOCUMENTS) == 3
    for filename, digest in BASELINE_DOCUMENTS.items():
        assert len(digest) == 64, filename
        assert all(character in "0123456789abcdef" for character in digest), filename


@pytest.mark.parametrize(("filename", "expected"), sorted(BASELINE_DOCUMENTS.items()))
def test_a_baseline_document_still_hashes_to_its_recorded_digest(
    filename: str, expected: str
) -> None:
    path = REPO_ROOT / filename
    if not path.is_file():
        pytest.skip(
            f"{filename} is not in this checkout — it is gitignored, so baseline "
            "integrity was NOT verified here. It is verified where the document exists."
        )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, (
        f"{filename} no longer matches the digest recorded before implementation began. "
        "Restore the document; do not update this constant."
    )
