"""Contract tests — implementation plan Task 3, Step 1.

These are written before ``prism.contracts`` exists. They define the public shape rather
than describe it after the fact.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prism.constants import CALIBRATION_UNCALIBRATED
from prism.contracts import (
    AgreementType,
    CandidatePacket,
    Claim,
    EvidenceStatus,
    MeasureReport,
    MeasureRequest,
    PreflightRequest,
    PrismMode,
    PrismStatus,
    ProvenanceStatus,
    SourceDiversity,
)

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

VALID_TEXT = "The local stdio server avoids remote authentication exposure entirely today."


def claim(
    claim_id: str = "systems-1", text: str = VALID_TEXT, confidence: int | None = 75
) -> Claim:
    return Claim(
        claim_id=claim_id,
        text=text,
        confidence=confidence,
        evidence_status=EvidenceStatus.INFERRED,
    )


def packet(
    candidate_id: str = "systems",
    source_group_id: str = "host-pass-001",
    source_label: str | None = "claude-code",
    claims: tuple[Claim, ...] | None = None,
) -> CandidatePacket:
    return CandidatePacket(
        candidate_id=candidate_id,
        source_group_id=source_group_id,
        source_label=source_label,
        provenance_status=ProvenanceStatus.DECLARED_UNVERIFIED,
        perspective=candidate_id,
        claims=claims if claims is not None else (claim(),),
    )


def words(n: int) -> str:
    """Build a claim body of exactly ``n`` alphabetic words."""
    return " ".join(f"word{i}" for i in range(n))


# --------------------------------------------------------------------------------------
# confidence
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 50, 99, 100, None])
def test_confidence_accepts_full_declared_range(value: int | None) -> None:
    assert claim(confidence=value).confidence == value


@pytest.mark.parametrize("value", [-1, 101, 1000])
def test_confidence_rejects_out_of_range(value: int) -> None:
    with pytest.raises(ValidationError):
        claim(confidence=value)


@pytest.mark.parametrize("value", ["75", 75.5, True])
def test_confidence_is_not_coerced(value: object) -> None:
    """Design section 6.1: no coercive confidence parsing."""
    with pytest.raises(ValidationError):
        Claim(
            claim_id="c-1",
            text=VALID_TEXT,
            confidence=value,  # type: ignore[arg-type]
            evidence_status=EvidenceStatus.INFERRED,
        )


# --------------------------------------------------------------------------------------
# claim and candidate cardinality
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [8, 40, 80])
def test_claim_word_count_within_bounds_is_accepted(count: int) -> None:
    assert claim(text=words(count)).text.split().__len__() == count


@pytest.mark.parametrize("count", [0, 1, 7, 81, 200])
def test_claim_word_count_outside_bounds_is_rejected(count: int) -> None:
    with pytest.raises(ValidationError):
        claim(text=words(count))


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_candidate_accepts_one_to_four_claims(count: int) -> None:
    claims = tuple(claim(claim_id=f"systems-{i}") for i in range(count))
    assert len(packet(claims=claims).claims) == count


@pytest.mark.parametrize("count", [0, 5, 9])
def test_candidate_rejects_claim_count_outside_bounds(count: int) -> None:
    claims = tuple(claim(claim_id=f"systems-{i}") for i in range(count))
    with pytest.raises(ValidationError):
        packet(claims=claims)


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_measure_request_accepts_two_to_five_candidates(count: int) -> None:
    candidates = tuple(packet(candidate_id=f"cand-{i}") for i in range(count))
    request = MeasureRequest(question="Review this architecture.", candidates=candidates)
    assert len(request.candidates) == count


@pytest.mark.parametrize("count", [0, 1, 6, 12])
def test_measure_request_rejects_candidate_count_outside_bounds(count: int) -> None:
    candidates = tuple(packet(candidate_id=f"cand-{i}") for i in range(count))
    with pytest.raises(ValidationError):
        MeasureRequest(question="Review this architecture.", candidates=candidates)


# --------------------------------------------------------------------------------------
# identifier uniqueness and bounds
# --------------------------------------------------------------------------------------


def test_duplicate_claim_ids_within_candidate_are_rejected() -> None:
    with pytest.raises(ValidationError):
        packet(claims=(claim(claim_id="dup"), claim(claim_id="dup")))


def test_duplicate_candidate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        MeasureRequest(
            question="Review this architecture.",
            candidates=(packet(candidate_id="same"), packet(candidate_id="same")),
        )


def test_source_group_id_is_required() -> None:
    with pytest.raises(ValidationError):
        CandidatePacket(
            candidate_id="systems",
            source_label="claude-code",
            provenance_status=ProvenanceStatus.DECLARED_UNVERIFIED,
            perspective="systems",
            claims=(claim(),),
        )  # type: ignore[call-arg]


def test_identifier_length_is_bounded() -> None:
    from prism.limits import MAX_IDENTIFIER_CHARS

    assert packet(candidate_id="a" * MAX_IDENTIFIER_CHARS) is not None
    with pytest.raises(ValidationError):
        packet(candidate_id="a" * (MAX_IDENTIFIER_CHARS + 1))


def test_source_label_length_is_bounded() -> None:
    from prism.limits import MAX_SOURCE_LABEL_CHARS

    assert packet(source_label="l" * MAX_SOURCE_LABEL_CHARS) is not None
    with pytest.raises(ValidationError):
        packet(source_label="l" * (MAX_SOURCE_LABEL_CHARS + 1))


# --------------------------------------------------------------------------------------
# provenance: invariant A15 / gate G18
# --------------------------------------------------------------------------------------


def test_distinct_labels_in_one_source_group_stay_single_source() -> None:
    """Five display labels from one host pass must not manufacture source diversity."""
    candidates = tuple(
        packet(candidate_id=f"cand-{i}", source_group_id="host-pass-001", source_label=f"model-{i}")
        for i in range(5)
    )
    request = MeasureRequest(question="Review this.", candidates=candidates)
    assert request.source_diversity() is SourceDiversity.SINGLE_SOURCE
    assert request.distinct_source_count() == 1


def test_distinct_source_groups_are_multi_source() -> None:
    candidates = (
        packet(candidate_id="a", source_group_id="host-pass-001", source_label="same-label"),
        packet(candidate_id="b", source_group_id="host-pass-002", source_label="same-label"),
    )
    request = MeasureRequest(question="Review this.", candidates=candidates)
    assert request.source_diversity() is SourceDiversity.MULTI_SOURCE
    assert request.distinct_source_count() == 2


# --------------------------------------------------------------------------------------
# hostile text: control and bidirectional characters
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    ["host‮pass", "host​pass", "host\x00pass", "host⁦pass", "host\npass"],
)
def test_control_and_bidirectional_characters_rejected_in_identifiers(hostile: str) -> None:
    with pytest.raises(ValidationError):
        packet(source_group_id=hostile)
    with pytest.raises(ValidationError):
        packet(candidate_id=hostile)


@pytest.mark.parametrize("hostile", ["‮", "⁦", "‏"])
def test_bidirectional_characters_rejected_in_claim_text(hostile: str) -> None:
    with pytest.raises(ValidationError):
        claim(text=f"{words(10)} {hostile} tail")


# --------------------------------------------------------------------------------------
# report invariants
# --------------------------------------------------------------------------------------


def test_zero_denominator_forces_null_rate() -> None:
    """Plan global constraint: a zero denominator produces null, never 0.0."""
    with pytest.raises(ValidationError):
        MeasureReport.model_validate(
            {
                **_minimal_report_payload(),
                "contradiction_denominator": 0,
                "contradiction_rate": 0.0,
            }
        )


def test_zero_denominator_null_rate_is_accepted() -> None:
    report = MeasureReport.model_validate(
        {
            **_minimal_report_payload(),
            "status": PrismStatus.INSUFFICIENT,
            "contradiction_denominator": 0,
            "contradiction_rate": None,
            "nli_coverage": None,
        }
    )
    assert report.contradiction_rate is None
    assert report.nli_coverage is None


def test_contradiction_count_cannot_exceed_denominator() -> None:
    with pytest.raises(ValidationError):
        MeasureReport.model_validate(
            {
                **_minimal_report_payload(),
                "contradiction_denominator": 3,
                "contradiction_count": 4,
                "contradiction_rate": 1.0,
            }
        )


def test_status_and_source_diversity_are_independent_fields() -> None:
    """Phase-1 root fix: status must not encode provenance."""
    report = MeasureReport.model_validate({**_minimal_report_payload(), "status": PrismStatus.OK})
    assert report.status is PrismStatus.OK
    assert report.source_diversity is SourceDiversity.SINGLE_SOURCE
    assert "source_diversity" not in {f.value for f in PrismStatus}


def test_reports_are_immutable() -> None:
    report = MeasureReport.model_validate(_minimal_report_payload())
    with pytest.raises(ValidationError):
        report.contradiction_count = 99


def test_reports_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MeasureReport.model_validate({**_minimal_report_payload(), "surprise": 1})


# --------------------------------------------------------------------------------------
# calibration gating — authoritative fields stay empty until human validation
# --------------------------------------------------------------------------------------


def test_uncalibrated_report_cannot_publish_authoritative_contradiction_fields() -> None:
    """An uncalibrated threshold may prove the machinery runs. It may not produce
    authoritative-looking counts that a later reader mistakes for calibrated evidence."""
    with pytest.raises(ValidationError):
        MeasureReport.model_validate(
            {
                **_minimal_report_payload(),
                "calibration_status": CALIBRATION_UNCALIBRATED,
                "contradiction_denominator": 4,
                "contradiction_count": 2,
                "contradiction_rate": 0.5,
            }
        )


def test_uncalibrated_report_forces_unclear_agreement() -> None:
    with pytest.raises(ValidationError):
        MeasureReport.model_validate(
            {
                **_minimal_report_payload(),
                "calibration_status": CALIBRATION_UNCALIBRATED,
                "agreement_type": AgreementType.SINGLE_SOURCE_AGREEMENT,
            }
        )


def test_uncalibrated_report_carries_experimental_fields_instead() -> None:
    report = MeasureReport.model_validate(
        {
            **_minimal_report_payload(),
            "calibration_status": CALIBRATION_UNCALIBRATED,
            "contradiction_denominator": 4,
            "contradiction_count": None,
            "contradiction_rate": None,
            "agreement_type": AgreementType.UNCLEAR,
            "experimental_contradiction_count": 2,
            "experimental_contradiction_rate": 0.5,
            "experimental_threshold": 0.5,
        }
    )
    assert report.contradiction_count is None
    assert report.contradiction_rate is None
    assert report.agreement_type is AgreementType.UNCLEAR
    assert report.experimental_contradiction_count == 2
    assert report.experimental_threshold == 0.5


# --------------------------------------------------------------------------------------
# preflight request
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(PrismMode))
def test_preflight_request_accepts_every_mode(mode: PrismMode) -> None:
    assert PreflightRequest(task="Review this release plan.", mode=mode).mode is mode


@pytest.mark.parametrize("cap", [3, 4, 5])
def test_preflight_max_perspectives_within_hard_maximum(cap: int) -> None:
    assert PreflightRequest(task="Review this.", max_perspectives=cap).max_perspectives == cap


@pytest.mark.parametrize("cap", [0, 2, 6, 50])
def test_preflight_max_perspectives_outside_bounds_rejected(cap: int) -> None:
    with pytest.raises(ValidationError):
        PreflightRequest(task="Review this.", max_perspectives=cap)


def test_empty_task_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PreflightRequest(task="   ")


# --------------------------------------------------------------------------------------
# shared fixture payload
# --------------------------------------------------------------------------------------


def _minimal_report_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": PrismStatus.OK,
        "calibration_status": "HUMAN_VALIDATED",
        "source_diversity": SourceDiversity.SINGLE_SOURCE,
        "provenance_status": ProvenanceStatus.DECLARED_UNVERIFIED,
        "pairs_total": 8,
        "relevant_pairs": 4,
        "scope_divergent_count": 0,
        "scope_uncertain_count": 0,
        "contradiction_denominator": 4,
        "pairs_scored_by_nli": 4,
        "pairs_inferred_not_contradictory": 0,
        "nli_coverage": 1.0,
        "contradiction_count": 0,
        "contradiction_rate": 0.0,
        "agreement_type": AgreementType.SINGLE_SOURCE_AGREEMENT,
        "sources_distinct": 1,
        "pair_ledger_digest": "sha256:" + "0" * 64,
    }
