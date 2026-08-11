"""What a report is allowed to say about its own sources.

The audit's finding was that the report described the *request* while the measurement
described the deduplicated, viable subset of it. Two copies of one answer arriving under
two source group ids then read as corroboration from two sources, which is the exact claim
PRISM exists to refuse to make.

These run through the real service on the fake encoder pair: the source fields are decided
before any inference, so the encoder only has to be deterministic, not real.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from prism.contracts import (
    MeasureRequest,
    ProvenanceStatus,
    SourceDiversity,
)
from prism.measure.models import ModelSessions
from prism.preflight.registry import PerspectiveRegistry
from prism.service import PrismService

from .conftest import FILLER, FakeEncoders, make_packet

QUESTION = "Is the payment release ready to ship this week?"

#: Same leading token, so the fake encoder puts every claim on one relevance axis.
CLAIM_X = f"release readiness {FILLER}"
CLAIM_Y = f"release NOT-readiness {FILLER}"


class FakeSessions(FakeEncoders):
    """The encoder pair plus the one attribute the service reads off a session."""

    manifest = SimpleNamespace(digest=f"sha256:{'0' * 64}")


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> PrismService:
    sessions = FakeSessions()
    monkeypatch.setattr(ModelSessions, "get", staticmethod(lambda root=None: sessions))
    return PrismService(registry=PerspectiveRegistry.load())


def test_a_removed_duplicate_does_not_raise_source_diversity(service: PrismService) -> None:
    """The audit's counterexample: A(g1, X), B(g2, X) duplicate of A, C(g1, Y).

    B is dropped as a duplicate, so the measured set is A and C — one source group. A
    report that said MULTI_SOURCE here would be crediting a source whose only contribution
    was removed before scoring.
    """
    report = service.measure(
        MeasureRequest(
            question=QUESTION,
            candidates=(
                make_packet("lens-a", [CLAIM_X], source_group_id="host-pass-001"),
                make_packet("lens-b", [CLAIM_X], source_group_id="host-pass-002"),
                make_packet("lens-c", [CLAIM_Y], source_group_id="host-pass-001"),
            ),
        )
    )

    assert [record.removed_id for record in report.duplicate_candidates] == ["lens-b"]
    assert report.sources_distinct == 1
    assert report.source_diversity is SourceDiversity.SINGLE_SOURCE


def test_mixed_provenance_never_reports_as_fully_attested(service: PrismService) -> None:
    """The attested candidate is first, which is exactly what the old code read."""
    report = service.measure(
        MeasureRequest(
            question=QUESTION,
            candidates=(
                make_packet("lens-a", [CLAIM_X], provenance=ProvenanceStatus.EXTERNALLY_ATTESTED),
                make_packet("lens-b", [CLAIM_Y], provenance=ProvenanceStatus.DECLARED_UNVERIFIED),
            ),
        )
    )

    assert report.provenance_status is ProvenanceStatus.DECLARED_UNVERIFIED


def test_uniform_attestation_still_reports_as_attested(service: PrismService) -> None:
    """The degrade rule must not collapse into "always unverified"."""
    report = service.measure(
        MeasureRequest(
            question=QUESTION,
            candidates=(
                make_packet("lens-a", [CLAIM_X], provenance=ProvenanceStatus.EXTERNALLY_ATTESTED),
                make_packet("lens-b", [CLAIM_Y], provenance=ProvenanceStatus.EXTERNALLY_ATTESTED),
            ),
        )
    )

    assert report.provenance_status is ProvenanceStatus.EXTERNALLY_ATTESTED


def test_the_insufficient_path_uses_the_same_effective_set(service: PrismService) -> None:
    """Both submissions are the same answer, so one survives and nothing is measured.

    The insufficient report is still a report, and it described the request too.
    """
    report = service.measure(
        MeasureRequest(
            question=QUESTION,
            candidates=(
                make_packet(
                    "lens-a",
                    [CLAIM_X],
                    source_group_id="host-pass-001",
                    provenance=ProvenanceStatus.EXTERNALLY_ATTESTED,
                ),
                make_packet(
                    "lens-b",
                    [CLAIM_X],
                    source_group_id="host-pass-002",
                    provenance=ProvenanceStatus.DECLARED_UNVERIFIED,
                ),
            ),
        )
    )

    assert report.sources_distinct == 1
    assert report.source_diversity is SourceDiversity.SINGLE_SOURCE
    assert report.provenance_status is ProvenanceStatus.EXTERNALLY_ATTESTED
