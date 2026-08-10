"""Scope classification — implementation plan Task 8, Step 5.

The asymmetry in these tests is deliberate. A missed scope difference costs a slightly
pessimistic contradiction rate. A false scope difference *removes a real contradiction
from the denominator* and reports agreement that was never measured. So the tests lean
hard on the false-positive direction.
"""

from __future__ import annotations

import pytest

from prism.contracts import EvidenceStatus, ScopeResult
from prism.measure.pair import ClaimPair
from prism.measure.scope import DIVERGENCE_CAPABLE_DIMENSIONS, classify_scope, detect_markers
from prism.measure.segment import ClaimUnit, matching_view


def unit(claim_id: str, text: str, candidate: str = "cand") -> ClaimUnit:
    return ClaimUnit(
        claim_id=claim_id,
        candidate_id=candidate,
        text=text,
        matching_view=matching_view(text),
        confidence=None,
        evidence_status=EvidenceStatus.INFERRED,
    )


def pair_of(text_a: str, text_b: str) -> ClaimPair:
    return ClaimPair(pair_id="p1", a=unit("a", text_a, "ca"), b=unit("b", text_b, "cb"))


# --------------------------------------------------------------------------------------
# genuine divergence
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text_a", "text_b", "dimension"),
    [
        (
            "Latency stays under one second at the current prototype load levels tested.",
            "Latency exceeds ten seconds once enterprise scale traffic hits the endpoint.",
            "lifecycle",
        ),
        (
            "The local deployment needs no authentication layer for the single developer.",
            "The cloud deployment requires a full authentication layer for every request.",
            "environment",
        ),
        (
            "On windows the path handling requires explicit drive letter normalisation here.",
            "On linux the path handling requires no drive letter normalisation whatsoever.",
            "platform",
        ),
    ],
)
def test_explicit_same_dimension_difference_is_divergent(
    text_a: str, text_b: str, dimension: str
) -> None:
    verdict = classify_scope(pair_of(text_a, text_b))
    assert verdict.result is ScopeResult.SCOPE_DIVERGENT
    assert verdict.dimension == dimension


# --------------------------------------------------------------------------------------
# false positives — the regression that matters
# --------------------------------------------------------------------------------------


def test_tense_difference_is_not_a_scope_difference() -> None:
    """Regression, found during live integration testing.

    "is ready today" versus "is not ready and will fail" is a direct contradiction about
    one system. An earlier version read `today` as time:current and `will` as time:future
    and excluded the pair, which silently removed the only real contradiction in the
    workload and turned the result into INSUFFICIENT. Tense is not scope.
    """
    verdict = classify_scope(
        pair_of(
            "The service is ready for production deployment across all regions today.",
            "The service is not ready for production deployment and will fail under load.",
        )
    )
    assert verdict.result is not ScopeResult.SCOPE_DIVERGENT
    assert verdict.result is ScopeResult.UNCERTAIN


def test_only_high_confidence_dimensions_can_exclude_a_pair() -> None:
    """Time and certainty describe tense and modality, not two different worlds."""
    assert "time" not in DIVERGENCE_CAPABLE_DIMENSIONS
    assert "certainty" not in DIVERGENCE_CAPABLE_DIMENSIONS
    assert {"lifecycle", "environment", "scale", "platform"} == DIVERGENCE_CAPABLE_DIMENSIONS


def test_one_sided_marker_is_uncertain_not_divergent() -> None:
    """One scoped claim and one unscoped claim is exactly where a keyword rule guesses.
    Uncertain keeps the pair in the denominator."""
    verdict = classify_scope(
        pair_of(
            "The production system handles the retry path without any data loss at all.",
            "The retry path drops messages whenever the downstream queue rejects them.",
        )
    )
    assert verdict.result is ScopeResult.UNCERTAIN


def test_no_markers_is_same_scope() -> None:
    verdict = classify_scope(
        pair_of(
            "The retry path drops messages whenever the downstream queue rejects them.",
            "The retry path preserves every message even when the queue rejects them.",
        )
    )
    assert verdict.result is ScopeResult.SAME_SCOPE


def test_identical_markers_are_same_scope() -> None:
    verdict = classify_scope(
        pair_of(
            "In production the service rejects malformed requests before any model loads.",
            "In production the service accepts malformed requests and loads the model first.",
        )
    )
    assert verdict.result is ScopeResult.SAME_SCOPE


def test_overlapping_markers_do_not_diverge() -> None:
    """Claims that share a scope are not talking past each other, even if one names an
    extra scope as well."""
    verdict = classify_scope(
        pair_of(
            "The production and enterprise tiers both require the audit log to be enabled.",
            "The production tier requires no audit log because the data never leaves it.",
        )
    )
    assert verdict.result is not ScopeResult.SCOPE_DIVERGENT


# --------------------------------------------------------------------------------------
# negation
# --------------------------------------------------------------------------------------


def test_negated_marker_is_not_believed() -> None:
    """ "not in production" must not register the claim as production-scoped."""
    markers = detect_markers(matching_view("This path is not in production and never will be."))
    assert "production" not in markers.get("lifecycle", set())


def test_marker_does_not_fire_inside_a_longer_word() -> None:
    markers = detect_markers(matching_view("The delivery pipeline moves artifacts nightly."))
    assert "live" not in markers.get("lifecycle", set())


def test_classification_is_deterministic() -> None:
    pair = pair_of(
        "Latency stays under one second at the current prototype load levels tested.",
        "Latency exceeds ten seconds once enterprise scale traffic hits the endpoint.",
    )
    results = {classify_scope(pair).result for _ in range(50)}
    assert len(results) == 1
