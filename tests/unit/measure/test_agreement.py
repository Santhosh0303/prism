"""The agreement truth table — implementation plan Task 8, Step 4.

Every path that could produce an agreement label without the evidence to support it gets
its own test. The suppressing conditions are the point of the table; the two agreement
labels are almost an afterthought.
"""

from __future__ import annotations

import pytest

from prism.contracts import AgreementType, PrismStatus, SourceDiversity
from prism.measure import agreement as agreement_module
from prism.measure.agreement import MAX_UNCERTAIN_SCOPE_SHARE, agreement_type


def call(
    *,
    status: PrismStatus = PrismStatus.OK,
    contradiction_denominator: int = 10,
    contradiction_count: int | None = 0,
    nli_coverage: float | None = 1.0,
    scope_uncertain_count: int = 0,
    source_diversity: SourceDiversity = SourceDiversity.SINGLE_SOURCE,
) -> AgreementType:
    return agreement_type(
        status=status,
        contradiction_denominator=contradiction_denominator,
        contradiction_count=contradiction_count,
        nli_coverage=nli_coverage,
        scope_uncertain_count=scope_uncertain_count,
        source_diversity=source_diversity,
    )


@pytest.fixture
def calibrated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most of the table is unreachable while the threshold is uncalibrated, which is
    itself the first row of the table. These tests exercise the rest of it."""
    monkeypatch.setattr(agreement_module, "is_calibrated", lambda: True)


# --------------------------------------------------------------------------------------
# suppression
# --------------------------------------------------------------------------------------


def test_uncalibrated_threshold_suppresses_every_label() -> None:
    """First row, and the reason the others rarely fire today."""
    assert call(contradiction_count=0) is AgreementType.UNCLEAR
    assert call(contradiction_count=5) is AgreementType.UNCLEAR


@pytest.mark.parametrize("status", [PrismStatus.ERROR, PrismStatus.INSUFFICIENT])
def test_error_or_insufficient_status_is_unclear(calibrated: None, status: PrismStatus) -> None:
    assert call(status=status) is AgreementType.UNCLEAR


def test_zero_denominator_is_unclear(calibrated: None) -> None:
    """No comparable pairs is not agreement."""
    assert call(contradiction_denominator=0, contradiction_count=None) is AgreementType.UNCLEAR


def test_missing_count_is_unclear(calibrated: None) -> None:
    assert call(contradiction_count=None) is AgreementType.UNCLEAR


@pytest.mark.parametrize("coverage", [None, 0.0, 0.5, 0.99])
def test_incomplete_nli_coverage_is_unclear(calibrated: None, coverage: float | None) -> None:
    """Unscored pairs are not evidence of compatibility."""
    assert call(nli_coverage=coverage) is AgreementType.UNCLEAR


def test_excess_scope_uncertainty_is_unclear(calibrated: None) -> None:
    """When the scope heuristic is carrying most of the result, the result is not a
    measurement of agreement."""
    over = int(10 * MAX_UNCERTAIN_SCOPE_SHARE) + 1
    assert call(scope_uncertain_count=over) is AgreementType.UNCLEAR


# --------------------------------------------------------------------------------------
# the labels themselves
# --------------------------------------------------------------------------------------


def test_any_contradiction_is_contested(calibrated: None) -> None:
    assert call(contradiction_count=1) is AgreementType.CONTESTED


def test_clean_single_source_is_single_source_agreement(calibrated: None) -> None:
    assert call() is AgreementType.SINGLE_SOURCE_AGREEMENT


def test_clean_multi_source_is_multi_source_agreement(calibrated: None) -> None:
    assert (
        call(source_diversity=SourceDiversity.MULTI_SOURCE) is AgreementType.MULTI_SOURCE_AGREEMENT
    )


def test_no_label_claims_independence_or_convergence() -> None:
    """Fitness function: these labels must never reappear. PRISM cannot establish either."""
    names = {member.name for member in AgreementType}
    assert "INDEPENDENT" not in names
    assert "CONVERGENT" not in names
    assert names == {
        "MULTI_SOURCE_AGREEMENT",
        "SINGLE_SOURCE_AGREEMENT",
        "CONTESTED",
        "UNCLEAR",
    }
