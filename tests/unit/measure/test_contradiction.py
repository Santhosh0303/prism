"""Denominator arithmetic, ledger reconstruction, and bounded projection.

Synthetic-vector arithmetic is exact and no encoder is invoked here. These use the fake
encoder so every input is known and every assertion can be exact.

Includes the denominator property tests and the maximum-conflict output test.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from prism.canonical import canonical_json
from prism.contracts import (
    AgreementType,
    MeasureReport,
    PrismStatus,
    ProvenanceStatus,
    ScopeResult,
    SourceDiversity,
)
from prism.limits import MAX_DEFAULT_REPORT_BYTES, MAX_INLINE_PAIR_DETAILS
from prism.measure.contradiction import LedgerEntry, PairLedger, build_ledger
from prism.measure.pair import enumerate_pairs
from prism.measure.project import build_measure_report

from .conftest import FILLER, FakeEncoders, make_packet, normalized


def ledger_from(texts_a: list[str], texts_b: list[str], encoders: FakeEncoders) -> PairLedger:
    candidates = normalized([make_packet("a", texts_a), make_packet("b", texts_b)])
    return build_ledger(enumerate_pairs(candidates), encoders)


# --------------------------------------------------------------------------------------
# denominator identity
# --------------------------------------------------------------------------------------


def test_denominator_is_relevant_pairs_minus_scope_divergent(encoders: FakeEncoders) -> None:
    ledger = ledger_from(
        [f"alpha {FILLER}", f"beta {FILLER}"],
        [f"alpha NOT-{FILLER}", f"gamma {FILLER}"],
        encoders,
    )
    assert ledger.contradiction_denominator == ledger.relevant_pairs - ledger.scope_divergent_count


def test_zero_denominator_yields_null_not_zero(encoders: FakeEncoders) -> None:
    """The single most important arithmetic rule in the system."""
    ledger = ledger_from([f"alpha {FILLER}"], [f"zulu {FILLER}"], encoders)
    if ledger.contradiction_denominator == 0:
        assert ledger.contradiction_rate is None
        assert ledger.nli_coverage is None
        assert ledger.contradiction_count is None


def test_production_coverage_is_total(encoders: FakeEncoders) -> None:
    """Every denominator pair is scored; nothing is inferred non-contradictory."""
    ledger = ledger_from([f"alpha {FILLER}"], [f"alpha NOT-{FILLER}"], encoders)
    assert ledger.contradiction_denominator > 0
    assert ledger.nli_coverage == 1.0
    assert ledger.pairs_inferred_not_contradictory == 0


def test_both_directions_are_scored_and_the_maximum_is_used(encoders: FakeEncoders) -> None:
    entry = LedgerEntry(
        pair_id="p",
        candidate_a_id="a",
        candidate_b_id="b",
        claim_a_id="a1",
        claim_b_id="b1",
        relevance=0.9,
        scope=ScopeResult.SAME_SCOPE,
        scope_dimension=None,
        scope_marker_a=None,
        scope_marker_b=None,
        in_denominator=True,
        score_a_to_b=0.2,
        score_b_to_a=0.8,
    )
    assert entry.contradiction_score == 0.8


def test_candidate_order_does_not_change_aggregates(encoders: FakeEncoders) -> None:
    forward = ledger_from([f"alpha {FILLER}"], [f"alpha NOT-{FILLER}"], encoders)
    reverse = ledger_from([f"alpha NOT-{FILLER}"], [f"alpha {FILLER}"], FakeEncoders())
    assert forward.contradiction_denominator == reverse.contradiction_denominator
    assert forward.contradiction_count == reverse.contradiction_count


def test_repeated_measurement_is_deterministic(encoders: FakeEncoders) -> None:
    digests = {
        ledger_from([f"alpha {FILLER}"], [f"alpha NOT-{FILLER}"], FakeEncoders()).digest
        for _ in range(10)
    }
    assert len(digests) == 1


# --------------------------------------------------------------------------------------
# property tests — the denominator identities that must hold for any ledger
# --------------------------------------------------------------------------------------


def entries(count: int, contradicting: int, in_denominator: int) -> tuple[LedgerEntry, ...]:
    built = []
    for index in range(count):
        built.append(
            LedgerEntry(
                pair_id=f"p{index:03d}",
                candidate_a_id="a",
                candidate_b_id="b",
                claim_a_id=f"a{index}",
                claim_b_id=f"b{index}",
                relevance=0.9,
                scope=(
                    ScopeResult.SAME_SCOPE
                    if index < in_denominator
                    else ScopeResult.SCOPE_DIVERGENT
                ),
                scope_dimension=None if index < in_denominator else "lifecycle",
                scope_marker_a=None if index < in_denominator else "prototype",
                scope_marker_b=None if index < in_denominator else "enterprise",
                in_denominator=index < in_denominator,
                score_a_to_b=0.99 if index < contradicting else 0.01,
                score_b_to_a=0.99 if index < contradicting else 0.01,
            )
        )
    return tuple(built)


@settings(max_examples=60, deadline=None)
@given(
    total=st.integers(min_value=0, max_value=40),
    contradicting=st.integers(min_value=0, max_value=40),
    denominator=st.integers(min_value=0, max_value=40),
)
def test_rate_invariants_hold_for_any_ledger(
    total: int, contradicting: int, denominator: int
) -> None:
    denominator = min(denominator, total)
    contradicting = min(contradicting, denominator)
    ledger = PairLedger(
        entries=entries(total, contradicting, denominator),
        pairs_total=total,
        threshold=0.5,
    )
    assert ledger.contradiction_denominator >= 0
    if ledger.contradiction_count is not None:
        assert 0 <= ledger.contradiction_count <= ledger.contradiction_denominator
    rate = ledger.contradiction_rate
    assert rate is None or 0.0 <= rate <= 1.0
    if ledger.contradiction_denominator == 0:
        assert rate is None


# --------------------------------------------------------------------------------------
# bounded projection — aggregates exact, inline detail capped
# --------------------------------------------------------------------------------------


def report_from(
    ledger: PairLedger,
    *,
    status: PrismStatus = PrismStatus.OK,
    source_diversity: SourceDiversity = SourceDiversity.SINGLE_SOURCE,
    agreement: AgreementType = AgreementType.UNCLEAR,
) -> MeasureReport:
    return build_measure_report(
        ledger=ledger,
        status=status,
        source_diversity=source_diversity,
        provenance_status=ProvenanceStatus.DECLARED_UNVERIFIED,
        sources_distinct=1,
        agreement=agreement,
        retained=(),
        internal_conflicts=(),
        normalization_warnings=(),
        duplicates=(),
        confidence_spread=None,
        include_raw_nli_scores=False,
        diagnostics={},
    )


def test_maximum_conflict_report_stays_within_every_budget() -> None:
    """160 pairs, all contradictory: the worst legal workload.

    Exact counts survive, inline detail is capped, omitted counts are exact, the ledger
    digest is present, and the serialised result stays under 12 KB.
    """
    ledger = PairLedger(entries=entries(160, 160, 160), pairs_total=160, threshold=0.5)
    report = report_from(ledger)

    assert report.contradiction_denominator == 160
    assert report.pairs_total == 160
    assert len(report.raw_nli_scores) <= MAX_INLINE_PAIR_DETAILS
    assert report.raw_nli_scores_omitted_count == 160 - len(report.raw_nli_scores)
    assert report.pair_ledger_digest.startswith("sha256:")
    size = len(canonical_json(report).encode("utf-8"))
    assert size < MAX_DEFAULT_REPORT_BYTES, size
    assert report.report_bytes == size


def test_projection_cannot_alter_the_arithmetic() -> None:
    ledger = PairLedger(entries=entries(100, 40, 90), pairs_total=100, threshold=0.5)
    report = report_from(ledger)
    assert report.contradiction_denominator == ledger.contradiction_denominator
    assert report.scope_divergent_count == ledger.scope_divergent_count
    assert report.experimental_contradiction_count == ledger.contradiction_count


def test_inline_detail_ordering_is_deterministic() -> None:
    ledger = PairLedger(entries=entries(60, 60, 60), pairs_total=60, threshold=0.5)
    first = [s.pair_id for s in report_from(ledger).raw_nli_scores]
    second = [s.pair_id for s in report_from(ledger).raw_nli_scores]
    assert first == second


def test_uncalibrated_report_routes_findings_to_experimental_fields() -> None:
    ledger = PairLedger(entries=entries(10, 4, 10), pairs_total=10, threshold=0.5)
    report = report_from(ledger)
    assert report.contradiction_count is None
    assert report.contradiction_rate is None
    assert report.agreement_type is AgreementType.UNCLEAR
    assert report.experimental_contradiction_count == 4
    assert report.experimental_contradiction_rate == pytest.approx(0.4)
    assert report.experimental_threshold == 0.5
    assert report.contradictions == ()


def test_report_can_be_reconstructed_from_the_raw_ledger() -> None:
    """The published aggregates must be recomputable from primary evidence."""
    ledger = PairLedger(entries=entries(50, 12, 45), pairs_total=50, threshold=0.5)
    report = report_from(ledger)

    recomputed_denominator = sum(1 for e in ledger.entries if e.in_denominator)
    recomputed_count = sum(
        1 for e in ledger.entries if e.in_denominator and e.is_contradiction(0.5)
    )
    assert report.contradiction_denominator == recomputed_denominator
    assert report.experimental_contradiction_count == recomputed_count
    assert report.pair_ledger_digest == ledger.digest
