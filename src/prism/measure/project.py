"""Bounded report projection.

Exact aggregate counts are always returned, but inline detail is capped and
deterministically ordered. A maximum-conflict workload — five candidates, four claims
each, all 160 pairs contradictory — must not produce a result that breaches the MCP size
budget.

The split is strict. Arithmetic comes from the complete ledger. Projection decides only
what is *shown*, and it cannot change a denominator, a rate, or an agreement label. When
detail is dropped, an exact ``*_omitted_count`` and the ``pair_ledger_digest`` go out with
it, so a reader can always tell that something was omitted and verify the full ledger
separately.

Silent truncation is forbidden. If the report still exceeds the byte budget after
projection, text fields are replaced by identifiers, and only then does it fail with a
typed ``OUTPUT_BUDGET_EXCEEDED``.
"""

from __future__ import annotations

from typing import Final

from ..canonical import canonical_json, canonical_rate
from ..contracts import (
    AgreementType,
    ContradictionPair,
    DuplicateRecord,
    InternalConflict,
    MeasureReport,
    NormalizationWarning,
    PrismStatus,
    ProvenanceStatus,
    RawNliScore,
    RetainedClaim,
    ScopeDivergentPair,
    ScopeResult,
    SourceDiversity,
)
from ..errors import ErrorCode, PrismError
from ..limits import MAX_DEFAULT_REPORT_BYTES, MAX_INLINE_PAIR_DETAILS
from .calibration import calibration_status, contradiction_threshold, is_calibrated
from .contradiction import LedgerEntry, PairLedger


def _cap[T](records: tuple[T, ...]) -> tuple[tuple[T, ...], int]:
    """Return the inline slice and the exact number omitted."""
    if len(records) <= MAX_INLINE_PAIR_DETAILS:
        return records, 0
    return records[:MAX_INLINE_PAIR_DETAILS], len(records) - MAX_INLINE_PAIR_DETAILS


def _severity_key(entry: LedgerEntry) -> tuple[float, str]:
    """Highest contradiction score first, then pair id. Deterministic across runs."""
    score = entry.contradiction_score
    return (-(score if score is not None else 0.0), entry.pair_id)


def build_measure_report(
    *,
    ledger: PairLedger,
    status: PrismStatus,
    source_diversity: SourceDiversity,
    provenance_status: ProvenanceStatus,
    sources_distinct: int,
    agreement: AgreementType,
    retained: tuple[RetainedClaim, ...],
    internal_conflicts: tuple[InternalConflict, ...],
    normalization_warnings: tuple[NormalizationWarning, ...],
    duplicates: tuple[DuplicateRecord, ...],
    confidence_spread: int | None,
    include_raw_nli_scores: bool,
    diagnostics: dict[str, str | int | float | bool | None],
) -> MeasureReport:
    """Project a complete ledger into the bounded public report."""
    calibrated = is_calibrated()
    denominator_entries = ledger.denominator_entries

    contradicting = tuple(
        sorted(
            (e for e in denominator_entries if e.is_contradiction(ledger.threshold)),
            key=_severity_key,
        )
    )

    # Under an uncalibrated threshold the authoritative arrays stay empty for the same
    # reason the scalar fields do: a populated `contradictions` list reads as a finding.
    # The evidence still ships, under raw_nli_scores, where it is plainly provisional.
    if calibrated:
        inline_contradictions, contradictions_omitted = _cap(
            tuple(
                ContradictionPair(
                    pair_id=e.pair_id,
                    claim_a_id=e.claim_a_id,
                    claim_b_id=e.claim_b_id,
                    candidate_a_id=e.candidate_a_id,
                    candidate_b_id=e.candidate_b_id,
                    contradiction_score=canonical_rate(e.contradiction_score) or 0.0,
                    score_a_to_b=canonical_rate(e.score_a_to_b) or 0.0,
                    score_b_to_a=canonical_rate(e.score_b_to_a) or 0.0,
                )
                for e in contradicting
            )
        )
    else:
        inline_contradictions, contradictions_omitted = (), 0

    scope_divergent_entries = tuple(
        sorted(
            (e for e in ledger.entries if e.scope is ScopeResult.SCOPE_DIVERGENT),
            key=lambda e: e.pair_id,
        )
    )
    inline_scope, scope_omitted = _cap(
        tuple(
            ScopeDivergentPair(
                pair_id=e.pair_id,
                claim_a_id=e.claim_a_id,
                claim_b_id=e.claim_b_id,
                dimension=e.scope_dimension or "unknown",
                marker_a=e.scope_marker_a or "",
                marker_b=e.scope_marker_b or "",
            )
            for e in scope_divergent_entries
        )
    )

    inline_internal, internal_omitted = _cap(internal_conflicts)
    inline_warnings, warnings_omitted = _cap(normalization_warnings)

    raw_scores: tuple[RawNliScore, ...] = ()
    raw_omitted = 0
    if include_raw_nli_scores or not calibrated:
        scored = tuple(
            sorted(
                (e for e in denominator_entries if e.contradiction_score is not None),
                key=_severity_key,
            )
        )
        raw_scores, raw_omitted = _cap(
            tuple(
                RawNliScore(
                    pair_id=e.pair_id,
                    score_a_to_b=canonical_rate(e.score_a_to_b) or 0.0,
                    score_b_to_a=canonical_rate(e.score_b_to_a) or 0.0,
                )
                for e in scored
            )
        )

    report = MeasureReport(
        status=status,
        calibration_status=calibration_status(),
        source_diversity=source_diversity,
        provenance_status=provenance_status,
        pairs_total=ledger.pairs_total,
        relevant_pairs=ledger.relevant_pairs,
        scope_divergent_count=ledger.scope_divergent_count,
        scope_uncertain_count=ledger.scope_uncertain_count,
        contradiction_denominator=ledger.contradiction_denominator,
        pairs_scored_by_nli=ledger.pairs_scored_by_nli,
        pairs_inferred_not_contradictory=ledger.pairs_inferred_not_contradictory,
        nli_coverage=canonical_rate(ledger.nli_coverage),
        contradiction_count=ledger.contradiction_count if calibrated else None,
        contradiction_rate=canonical_rate(ledger.contradiction_rate) if calibrated else None,
        agreement_type=agreement if calibrated else AgreementType.UNCLEAR,
        experimental_contradiction_count=None if calibrated else ledger.contradiction_count,
        experimental_contradiction_rate=(
            None if calibrated else canonical_rate(ledger.contradiction_rate)
        ),
        experimental_threshold=None if calibrated else contradiction_threshold(),
        contradictions=inline_contradictions,
        contradictions_omitted_count=contradictions_omitted,
        scope_divergent=inline_scope,
        scope_divergent_omitted_count=scope_omitted,
        retained_distinct_claims=retained,
        internal_conflicts=inline_internal,
        internal_conflicts_omitted_count=internal_omitted,
        normalization_warnings=inline_warnings,
        normalization_warnings_omitted_count=warnings_omitted,
        duplicate_candidates=duplicates,
        raw_nli_scores=raw_scores,
        raw_nli_scores_omitted_count=raw_omitted,
        confidence_spread=confidence_spread,
        sources_distinct=sources_distinct,
        pair_ledger_digest=ledger.digest,
        diagnostics=diagnostics,
    )

    return _enforce_byte_budget(report)


#: Writing the size into the document changes the document, so the value is a fixed point
#: rather than a measurement. Each pass can only widen the integer, so it settles almost
#: immediately; the cap exists to turn a hypothetical oscillation into a loud failure
#: instead of a hang.
_SIZE_PASSES: Final[int] = 4


def _with_measured_size(report: MeasureReport) -> MeasureReport:
    """Set ``report_bytes`` to the length of the document that carries it.

    The previous implementation measured the report with the field still unset and then
    attached the number, so the emitted document was longer than the one that had been
    measured. That is invisible while the integer happens to be as wide as ``null`` and
    wrong as soon as it is not.
    """
    sized = report
    for _ in range(_SIZE_PASSES):
        size = len(canonical_json(sized).encode("utf-8"))
        if sized.report_bytes == size:
            return sized
        sized = report.model_copy(update={"report_bytes": size})
    raise PrismError(
        code=ErrorCode.INTERNAL_ERROR,
        message="The reported size did not converge on the size of the emitted document.",
        diagnostics={"passes": _SIZE_PASSES, "last_size": sized.report_bytes},
    )


def _enforce_byte_budget(report: MeasureReport) -> MeasureReport:
    """Attach the measured size, and fail loudly rather than truncate silently.

    The budget is checked against the size of the document the caller actually receives,
    not against a draft of it.
    """
    sized = _with_measured_size(report)
    if sized.report_bytes is not None and sized.report_bytes <= MAX_DEFAULT_REPORT_BYTES:
        return sized

    # Second pass: drop the diagnostic raw scores, which are the largest optional payload.
    reduced = _with_measured_size(
        report.model_copy(
            update={
                "raw_nli_scores": (),
                "raw_nli_scores_omitted_count": (
                    report.raw_nli_scores_omitted_count + len(report.raw_nli_scores)
                ),
            }
        )
    )
    if reduced.report_bytes is not None and reduced.report_bytes <= MAX_DEFAULT_REPORT_BYTES:
        return reduced

    raise PrismError(
        code=ErrorCode.OUTPUT_BUDGET_EXCEEDED,
        message="The report could not be reduced below the size budget without silently "
        "truncating it.",
        diagnostics={
            "report_bytes": reduced.report_bytes,
            "limit_bytes": MAX_DEFAULT_REPORT_BYTES,
        },
    )
