"""What the synthesis contract is allowed to say about the report it was handed.

The contract narrates a measurement to a host that will not see the measurement itself.
Two ways that goes wrong, both found by the audit:

* narrating a report against *runtime* state, so an imported or cross-version report is
  described under a calibration it was never produced with;
* promising to disclose "every unresolved contradiction" when the report is bounded, its
  inline detail capped, and the remainder reported only as a count.

The second is why the promise now says what the contract can actually deliver.
"""

from __future__ import annotations

import pytest

from prism.canonical import canonical_digest
from prism.constants import CALIBRATION_HUMAN_VALIDATED, CALIBRATION_UNCALIBRATED
from prism.contracts import (
    AgreementType,
    ContradictionPair,
    InternalConflict,
    InternalConflictKind,
    MeasureReport,
    PrismStatus,
    ProvenanceStatus,
    ScopeDivergentPair,
    SourceDiversity,
)
from prism.measure.calibration import is_calibrated
from prism.synthesis.contract import FINAL_ANSWER_STRUCTURE, build_synthesis_contract


def report(**overrides: object) -> MeasureReport:
    """A minimal OK report; every field the tests vary is an explicit override."""
    base: dict[str, object] = {
        "status": PrismStatus.OK,
        "calibration_status": CALIBRATION_UNCALIBRATED,
        "source_diversity": SourceDiversity.MULTI_SOURCE,
        "provenance_status": ProvenanceStatus.DECLARED_UNVERIFIED,
        "pairs_total": 10,
        "relevant_pairs": 10,
        "scope_divergent_count": 0,
        "scope_uncertain_count": 0,
        "contradiction_denominator": 10,
        "pairs_scored_by_nli": 10,
        "pairs_inferred_not_contradictory": 0,
        "nli_coverage": 1.0,
        "sources_distinct": 2,
        "pair_ledger_digest": canonical_digest([]),
    }
    base.update(overrides)
    return MeasureReport(**base)  # type: ignore[arg-type]


def joined(values: tuple[str, ...]) -> str:
    return " ".join(values)


# --------------------------------------------------------------------------------------
# calibration is read off the report, not off this process
# --------------------------------------------------------------------------------------


def test_the_narration_follows_the_report_not_the_runtime() -> None:
    """A report produced under a validated threshold must not be narrated as provisional.

    This build is uncalibrated, so the old code — which asked ``is_calibrated()`` about
    the running process — attached the uncalibrated caveat to every report it was ever
    shown, including one that says otherwise.
    """
    assert not is_calibrated(), "fixture assumes this build is uncalibrated"

    contract = build_synthesis_contract(
        None,
        report(
            calibration_status=CALIBRATION_HUMAN_VALIDATED,
            contradiction_count=2,
            contradiction_rate=0.2,
            agreement_type=AgreementType.CONTESTED,
        ),
    )

    assert "not yet calibrated" not in joined(contract.required_disclosures)
    assert "provisional" not in joined(contract.limitations)


def test_an_uncalibrated_report_still_carries_the_caveat() -> None:
    """The rule must not collapse into silence about calibration."""
    contract = build_synthesis_contract(None, report())

    assert "not yet calibrated" in joined(contract.required_disclosures)
    assert "provisional" in joined(contract.limitations)


def test_experimental_findings_are_narrated_from_the_reports_state() -> None:
    """The provisional-threshold warning is keyed to the report, like the caveat is."""
    contract = build_synthesis_contract(
        None, report(experimental_contradiction_count=3, experimental_contradiction_rate=0.3)
    )
    assert "provisional threshold" in joined(contract.unresolved_conflicts)


# --------------------------------------------------------------------------------------
# every omitted category is disclosed, not only contradictions
# --------------------------------------------------------------------------------------


def test_all_three_omitted_counts_reach_the_host() -> None:
    """Contradictions were disclosed; scope divergences and internal conflicts were not.

    A host told about 20 of 23 contradicting pairs, with no mention of the other three,
    cannot write an honest answer no matter how carefully it follows the rest.
    """
    contract = build_synthesis_contract(
        None,
        report(
            contradictions=(
                ContradictionPair(
                    pair_id="p-1",
                    claim_a_id="a-1",
                    claim_b_id="b-1",
                    candidate_a_id="lens-a",
                    candidate_b_id="lens-b",
                    contradiction_score=0.91,
                    score_a_to_b=0.91,
                    score_b_to_a=0.88,
                ),
            ),
            contradictions_omitted_count=3,
            scope_divergent=(
                ScopeDivergentPair(
                    pair_id="p-2",
                    claim_a_id="a-2",
                    claim_b_id="b-2",
                    dimension="lifecycle",
                    marker_a="prototype",
                    marker_b="enterprise",
                ),
            ),
            scope_divergent_count=1,
            scope_divergent_omitted_count=5,
            internal_conflicts=(
                InternalConflict(
                    candidate_id="lens-a",
                    claim_a_id="a-3",
                    claim_b_id="a-4",
                    kind=InternalConflictKind.BOOLEAN_CONFLICT,
                    detail="claim a-3 says enabled; claim a-4 says disabled",
                ),
            ),
            internal_conflicts_omitted_count=7,
        ),
    )

    assert "3 further contradicting pairs" in joined(contract.unresolved_conflicts)
    assert "5 further scope-divergent pairs" in joined(contract.scope_differences)
    assert "7 further internal conflicts" in joined(contract.required_disclosures)


@pytest.mark.parametrize(
    "field",
    [
        "contradictions_omitted_count",
        "scope_divergent_omitted_count",
        "internal_conflicts_omitted_count",
    ],
)
def test_a_zero_omitted_count_says_nothing(field: str) -> None:
    """Silence is correct when nothing was omitted; a "0 further" line would be noise."""
    contract = build_synthesis_contract(None, report(**{field: 0}))
    everything = joined(
        contract.unresolved_conflicts + contract.scope_differences + contract.required_disclosures
    )
    assert "further" not in everything


def test_the_disclosure_promise_matches_what_a_bounded_report_can_deliver() -> None:
    """D3: the promise was weakened deliberately rather than left unmeetable."""
    structure = joined(FINAL_ANSWER_STRUCTURE)
    assert "every unresolved contradiction" not in structure
    assert "omitted counts" in structure
