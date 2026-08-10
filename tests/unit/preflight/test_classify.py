"""Deterministic classification — implementation plan Task 5.

Classification is a table lookup, so these tests are about routing behaviour and about
what happens when the signal is weak, which is the case that matters: guessing a specific
profile on thin evidence silently narrows the review.
"""

from __future__ import annotations

import pytest

from prism.contracts import ClassificationConfidence
from prism.preflight.classify import classify_task, normalise, score_profiles
from prism.preflight.profiles import TaskProfile


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        (
            "Review this pull request: the refactor changes the retry code and adds a "
            "regression test for the bugfix.",
            TaskProfile.SOFTWARE_CHANGE,
        ),
        (
            "Assess the system design: component boundaries, coupling between services, "
            "and the scalability tradeoff of the proposed architecture.",
            TaskProfile.ARCHITECTURE,
        ),
        (
            "Security review of the upload endpoint: threat model the attack surface, "
            "check authentication and the injection vulnerability we suspect.",
            TaskProfile.SECURITY_REVIEW,
        ),
        (
            "Draft the research question and check the literature: does the study "
            "replicate, and is the citation for that hypothesis sound?",
            TaskProfile.RESEARCH,
        ),
        (
            "Build the business case for the vendor contract: pricing, budget impact, "
            "revenue effect, and which stakeholder signs it off.",
            TaskProfile.BUSINESS_DECISION,
        ),
        (
            "The dataset shows a correlation; check the sample size, the confidence "
            "interval, and whether an outlier drives the statistical result.",
            TaskProfile.DATA_ANALYSIS,
        ),
        (
            "We had an outage last night. Write the postmortem: root cause, the "
            "rollback we ran, and why the alert fired late during the downtime.",
            TaskProfile.INCIDENT,
        ),
    ],
)
def test_representative_tasks_route_to_their_profile(task: str, expected: TaskProfile) -> None:
    assert classify_task(task).profile is expected


@pytest.mark.parametrize(
    "task",
    [
        "Help me with this.",
        "What do you think about the thing we discussed yesterday afternoon?",
        "Please take a look and let me know if anything stands out to you.",
    ],
)
def test_ambiguous_tasks_fall_back_to_general_with_low_confidence(task: str) -> None:
    result = classify_task(task)
    assert result.profile is TaskProfile.GENERAL
    assert result.confidence is ClassificationConfidence.LOW


def test_a_single_weak_signal_does_not_win() -> None:
    """One incidental keyword must not route the whole review."""
    result = classify_task("Add a code comment explaining the parameter.")
    assert result.profile is TaskProfile.GENERAL
    assert result.confidence is ClassificationConfidence.LOW


def test_classification_is_repeatable() -> None:
    task = "Threat model the authentication flow and check the injection attack surface."
    results = {classify_task(task).profile for _ in range(50)}
    assert len(results) == 1


def test_scores_are_returned_highest_first() -> None:
    scores = score_profiles("Security review: vulnerability, exploit, threat model.")
    values = [score for _, score in scores]
    assert values == sorted(values, reverse=True)
    assert scores[0][0] is TaskProfile.SECURITY_REVIEW


def test_confidence_rises_with_signal_density() -> None:
    weak = classify_task("Look at the architecture diagram and the component list.")
    strong = classify_task(
        "Architectural review of the system design: component boundaries, coupling, "
        "topology, scalability and the monolith to microservice tradeoff."
    )
    assert strong.confidence is ClassificationConfidence.HIGH
    assert weak.confidence in {ClassificationConfidence.MEDIUM, ClassificationConfidence.LOW}


def test_normalisation_folds_compatibility_forms() -> None:
    """A full-width or styled character must not evade an indicator.

    The fixture is constructed rather than pasted: embedding ambiguous homoglyphs in
    source would trip the same static-analysis rule that exists to catch them.
    """

    def fullwidth(word: str) -> str:
        return "".join(chr(ord(ch) - 0x20 + 0xFF00) if " " < ch <= "~" else ch for ch in word)

    _, tokens = normalise(f"{fullwidth('SECURITY')} review of the {fullwidth('vulnerability')}")
    assert "security" in tokens
    assert "vulnerability" in tokens


def test_token_signals_do_not_match_inside_longer_words() -> None:
    """'cost' must not fire on 'costume'; that is why single tokens match the token set
    rather than the raw string."""
    scores = dict(score_profiles("The costume budget for the theatre production."))
    business = dict(score_profiles("The cost and budget for the production."))
    assert business[TaskProfile.BUSINESS_DECISION] > scores[TaskProfile.BUSINESS_DECISION]


def test_incident_outranks_software_change_on_a_tie() -> None:
    """The declared tie-break prefers the higher-consequence reading."""
    from prism.preflight.profiles import TIE_BREAK_ORDER

    assert TIE_BREAK_ORDER.index(TaskProfile.INCIDENT) < TIE_BREAK_ORDER.index(
        TaskProfile.SOFTWARE_CHANGE
    )
