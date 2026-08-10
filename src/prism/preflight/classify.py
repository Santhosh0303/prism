"""Deterministic task classification.

No LLM, no embedding, no probability. The same text always produces the same profile, on
every platform, in every locale, under any hash seed.

The output deliberately reports raw integer scores rather than a normalised "confidence"
number, because a fabricated probability would imply a calibration that does not exist.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from ..contracts import ClassificationConfidence
from .profiles import (
    HIGH_CONFIDENCE_SCORE,
    MIN_WINNING_MARGIN,
    MIN_WINNING_SCORE,
    PROFILE_INDICATORS,
    TIE_BREAK_ORDER,
    TaskProfile,
)

_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Classification:
    """The routing decision plus the evidence for it."""

    profile: TaskProfile
    confidence: ClassificationConfidence
    scores: tuple[tuple[TaskProfile, int], ...]

    @property
    def top_score(self) -> int:
        return self.scores[0][1] if self.scores else 0

    @property
    def margin(self) -> int:
        if len(self.scores) < 2:
            return self.top_score
        return self.scores[0][1] - self.scores[1][1]


def normalise(task: str) -> tuple[str, frozenset[str]]:
    """Produce the matching view of a task.

    The original text is never mutated; this view exists only for signal matching.
    NFKC folds compatibility forms so that a full-width or styled character cannot evade
    an indicator.
    """
    folded = unicodedata.normalize("NFKC", task).casefold()
    tokens = frozenset(_TOKEN_PATTERN.findall(folded))
    return folded, tokens


def score_profiles(task: str) -> tuple[tuple[TaskProfile, int], ...]:
    """Score every profile. Returned highest-first, with the declared tie-break applied."""
    folded, tokens = normalise(task)
    scores: dict[TaskProfile, int] = dict.fromkeys(TIE_BREAK_ORDER, 0)

    for profile, indicators in PROFILE_INDICATORS.items():
        total = 0
        for signal, weight in indicators:
            if " " in signal:
                if signal in folded:
                    total += weight
            elif signal in tokens:
                total += weight
        scores[profile] = total

    tie_break_rank = {profile: rank for rank, profile in enumerate(TIE_BREAK_ORDER)}
    ordered = sorted(
        scores.items(),
        key=lambda item: (-item[1], tie_break_rank[item[0]]),
    )
    return tuple(ordered)


def classify_task(task: str) -> Classification:
    """Map a task to exactly one profile.

    An ambiguous task becomes :attr:`TaskProfile.GENERAL` with LOW confidence. That is a
    real answer, not a failure: the general lens set is balanced and safe, and pretending
    to a specific profile on weak evidence would silently narrow the review.
    """
    scores = score_profiles(task)
    best_profile, best_score = scores[0]
    runner_up_score = scores[1][1] if len(scores) > 1 else 0
    margin = best_score - runner_up_score

    if best_profile is TaskProfile.GENERAL:
        # GENERAL has no indicators of its own; reaching the top means nothing else scored.
        return Classification(
            profile=TaskProfile.GENERAL,
            confidence=ClassificationConfidence.LOW,
            scores=scores,
        )

    if best_score < MIN_WINNING_SCORE or margin < MIN_WINNING_MARGIN:
        return Classification(
            profile=TaskProfile.GENERAL,
            confidence=ClassificationConfidence.LOW,
            scores=scores,
        )

    confidence = (
        ClassificationConfidence.HIGH
        if best_score >= HIGH_CONFIDENCE_SCORE and margin >= MIN_WINNING_MARGIN
        else ClassificationConfidence.MEDIUM
    )
    return Classification(profile=best_profile, confidence=confidence, scores=scores)
