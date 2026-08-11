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
    InternalConflict,
    InternalConflictKind,
    MeasureReport,
    PrismStatus,
    ProvenanceStatus,
    ScopeResult,
    SourceDiversity,
)
from prism.errors import ErrorCode, PrismError
from prism.limits import MAX_DEFAULT_REPORT_BYTES, MAX_INLINE_PAIR_DETAILS
from prism.measure.contradiction import (
    LedgerEntry,
    PairLedger,
    build_ledger,
    detect_internal_conflicts,
)
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
    diagnostics: dict[str, str | int | float | bool | None] | None = None,
    include_raw_nli_scores: bool = False,
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
        include_raw_nli_scores=include_raw_nli_scores,
        diagnostics=diagnostics if diagnostics is not None else {},
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


# --------------------------------------------------------------------------------------
# internal conflicts accuse one candidate, so they need the same subject gate
# --------------------------------------------------------------------------------------


def internal_conflicts_for(
    texts: list[str], encoders: FakeEncoders
) -> tuple[InternalConflict, ...]:
    """Run the real pipeline for one candidate's claims, with a second candidate present.

    Two candidates are required for cross pairs to exist, which is what produces the
    embeddings the internal subject gate reuses.
    """
    ledger = ledger_from(texts, [f"zulu {FILLER}"], encoders)
    return detect_internal_conflicts(ledger.internal_pairs)


def test_two_unrelated_components_are_not_a_self_contradiction(encoders: FakeEncoders) -> None:
    """The audit's case: differing versions about different subjects.

    "the parser is at v1.2" and "the scheduler is at v2.0" are both true and describe
    different things. Without a subject gate the version pattern fired on any two claims
    carrying different version strings, and the candidate was told it contradicted itself.
    """
    ledger = ledger_from(
        [f"parser reached v1.2 {FILLER}", f"scheduler reached v2.0 {FILLER}"],
        [f"zulu {FILLER}"],
        encoders,
    )
    # The pair is enumerated and both claims do carry version strings, so the pattern
    # would fire. The subject gate is the only thing standing between them.
    internal = [p for p in ledger.internal_pairs if p.a.candidate_id == "a"]
    assert internal, "the within-candidate pair must exist for the gate to be what stops it"
    assert not any(pair.is_relevant for pair in internal)
    assert detect_internal_conflicts(ledger.internal_pairs) == ()


def test_the_same_subject_at_two_versions_is_still_a_conflict(encoders: FakeEncoders) -> None:
    """The gate must not silence the real finding it was added to protect."""
    conflicts = internal_conflicts_for(
        [f"parser reached v1.2 {FILLER}", f"parser reached v2.0 {FILLER}"], encoders
    )
    assert [c.kind for c in conflicts] == [InternalConflictKind.VERSION_CONFLICT]
    assert conflicts[0].candidate_id == "a"


def test_unrelated_numerics_in_the_same_unit_are_not_a_conflict(encoders: FakeEncoders) -> None:
    """Two components can take different amounts of time without disagreeing."""
    conflicts = internal_conflicts_for(
        [f"parser needs 10 ms {FILLER}", f"scheduler needs 40 ms {FILLER}"], encoders
    )
    assert conflicts == ()


# --------------------------------------------------------------------------------------
# the digest covers the whole ledger, not the part that survived
# --------------------------------------------------------------------------------------


def test_pairs_total_is_inside_the_digest() -> None:
    """Two ledgers with identical entries but different pair counts are different ledgers.

    Under the old envelope they hashed identically, so pairs vanishing before the
    relevance floor left the digest untouched.
    """
    shared = entries(10, 4, 10)
    assert (
        PairLedger(entries=shared, pairs_total=10, threshold=0.5).digest
        != PairLedger(entries=shared, pairs_total=40, threshold=0.5).digest
    )


def test_an_irrelevant_pair_changes_the_digest() -> None:
    """A pair dropped at the relevance floor is still evidence about what was compared."""
    shared = entries(4, 2, 4)
    base = PairLedger(entries=shared, pairs_total=6, threshold=0.5)
    with_dropped = PairLedger(
        entries=shared,
        pairs_total=6,
        threshold=0.5,
        irrelevant_pairs=(("p900", 0.11), ("p901", 0.07)),
    )
    renamed = PairLedger(
        entries=shared,
        pairs_total=6,
        threshold=0.5,
        irrelevant_pairs=(("p900", 0.11), ("p902", 0.07)),
    )

    assert base.digest != with_dropped.digest
    assert with_dropped.digest != renamed.digest


def test_the_scope_verdict_is_inside_the_digest() -> None:
    """Scope decides the denominator, so it cannot sit outside the hash of the ledger."""
    same_scope = PairLedger(entries=entries(6, 3, 6), pairs_total=6, threshold=0.5)
    some_divergent = PairLedger(entries=entries(6, 3, 4), pairs_total=6, threshold=0.5)
    assert same_scope.digest != some_divergent.digest


def test_the_digest_is_stable_for_an_unchanged_ledger() -> None:
    """Order of construction must not leak into the hash."""
    forward = PairLedger(
        entries=entries(5, 2, 5),
        pairs_total=7,
        threshold=0.5,
        irrelevant_pairs=(("p800", 0.2), ("p801", 0.3)),
    )
    reversed_inputs = PairLedger(
        entries=tuple(reversed(entries(5, 2, 5))),
        pairs_total=7,
        threshold=0.5,
        irrelevant_pairs=(("p801", 0.3), ("p800", 0.2)),
    )
    assert forward.digest == reversed_inputs.digest


# --------------------------------------------------------------------------------------
# the report states the size of the document it actually is
# --------------------------------------------------------------------------------------


def _padded_report(padding: int) -> MeasureReport:
    """A report whose size can be tuned one byte at a time."""
    return report_from(
        PairLedger(entries=entries(4, 2, 4), pairs_total=4, threshold=0.5),
        diagnostics={"pad": "x" * padding},
    )


def test_report_bytes_equals_the_emitted_length_at_the_budget_edge() -> None:
    """Land the report within one byte of the cap, where a one-byte lie decides the gate.

    The old code measured the document with ``report_bytes`` unset and then attached the
    integer, so the emitted document was longer than the one that had been checked. It
    went unnoticed because ``null`` is four characters wide, exactly like a four-digit
    size, and every report in the suite happened to land there.
    """
    padding = 0
    report = _padded_report(padding)
    for _ in range(8):
        assert report.report_bytes is not None
        shortfall = MAX_DEFAULT_REPORT_BYTES - report.report_bytes
        if shortfall == 0:
            break
        padding += shortfall
        report = _padded_report(padding)

    assert report.report_bytes == MAX_DEFAULT_REPORT_BYTES
    assert len(canonical_json(report).encode("utf-8")) == report.report_bytes
    assert report.report_bytes <= MAX_DEFAULT_REPORT_BYTES


@pytest.mark.parametrize("padding", [0, 500, 9000, 10500])
def test_report_bytes_is_exact_at_every_integer_width(padding: int) -> None:
    """Three-, four- and five-digit sizes must all describe the emitted bytes."""
    report = _padded_report(padding)
    assert len(canonical_json(report).encode("utf-8")) == report.report_bytes


# --------------------------------------------------------------------------------------
# over budget: reduce once, then refuse — never truncate quietly
# --------------------------------------------------------------------------------------


def _oversized(padding: int) -> MeasureReport:
    """A report carrying inline raw scores and tunable diagnostic padding."""
    return report_from(
        PairLedger(entries=entries(40, 20, 40), pairs_total=40, threshold=0.5),
        diagnostics={"pad": "x" * padding},
        include_raw_nli_scores=True,
    )


def test_the_reduction_pass_drops_raw_scores_and_still_measures_itself() -> None:
    """The recovery path was rewritten with the size fix and had no test at all.

    Raw NLI scores are diagnostic, so shedding them is the one reduction allowed before
    refusing. What comes back must still state its own true size.
    """
    with_scores = _oversized(0)
    assert with_scores.raw_nli_scores, "fixture must carry raw scores to shed"
    assert with_scores.report_bytes is not None
    kept = len(with_scores.raw_nli_scores)

    # Push just over the cap, by less than the raw scores are worth.
    over_by = 200
    padding = MAX_DEFAULT_REPORT_BYTES - with_scores.report_bytes + over_by
    reduced = _oversized(padding)

    assert reduced.raw_nli_scores == ()
    assert reduced.raw_nli_scores_omitted_count >= kept
    assert reduced.report_bytes is not None
    assert reduced.report_bytes <= MAX_DEFAULT_REPORT_BYTES
    assert len(canonical_json(reduced).encode("utf-8")) == reduced.report_bytes


def test_an_irreducible_report_is_refused_rather_than_truncated() -> None:
    """A report that cannot fit is an error, not a silently shortened document."""
    with pytest.raises(PrismError) as raised:
        _oversized(MAX_DEFAULT_REPORT_BYTES * 2)

    assert raised.value.code is ErrorCode.OUTPUT_BUDGET_EXCEEDED
    assert raised.value.diagnostics["limit_bytes"] == MAX_DEFAULT_REPORT_BYTES
    # The refused size must be the size after reduction was attempted, not before.
    refused = raised.value.diagnostics["report_bytes"]
    assert isinstance(refused, int)
    assert refused > MAX_DEFAULT_REPORT_BYTES
