"""Pair enumeration and the frozen relevance floor.

This module owns which pairs exist and which of them are comparable. It is deliberately
ignorant of two things:

* **Contradiction.** Relevance says two claims are about the same subject. It never says
  they agree or disagree; that is E2's job and only E2's job (ADR-005).
* **The speed floor.** ``pair.py`` must not import, accept, or reference any speed-floor
  configuration. An optimisation that skipped pairs must never be able to change the
  denominator, because that would silently convert unscored pairs into apparent agreement
  (plan Task 6 Step 4). ``tests/unit/measure/test_pair.py`` asserts this against the
  module source, not against a comment.

The relevance floor itself is a compiled-in constant. It is not exposed through the CLI,
MCP, environment, or config, so no caller can tune their way to a more agreeable result.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Final

from ..errors import ErrorCode, PrismError
from ..limits import MAX_CROSS_CANDIDATE_PAIRS, MAX_INTERNAL_PAIRS
from .segment import ClaimUnit, NormalizedCandidate

#: Cosine similarity below which two claims are treated as being about different subjects
#: and are therefore not comparable. Frozen: documented, mutation-tested, and unreachable
#: from any runtime parameter (design section 6.8).
#:
#: The value is conservative on purpose. Setting it too high would drop genuinely related
#: claims out of the denominator, which flatters the contradiction rate; too low admits
#: unrelated pairs that the NLI model then scores as vacuously non-contradictory.
RELEVANCE_FLOOR: Final[float] = 0.42


@dataclass(frozen=True, slots=True)
class ClaimPair:
    """One comparable pair of claim units."""

    pair_id: str
    a: ClaimUnit
    b: ClaimUnit
    relevance: float | None = None

    def with_relevance(self, relevance: float) -> ClaimPair:
        return ClaimPair(pair_id=self.pair_id, a=self.a, b=self.b, relevance=relevance)

    @property
    def is_relevant(self) -> bool:
        return self.relevance is not None and self.relevance >= RELEVANCE_FLOOR


@dataclass(frozen=True, slots=True)
class PairSet:
    """The complete cross-candidate pair space plus bounded within-candidate diagnostics."""

    cross_pairs: tuple[ClaimPair, ...]
    internal_pairs: tuple[ClaimPair, ...]

    @property
    def pairs_total(self) -> int:
        return len(self.cross_pairs)

    def relevant(self) -> tuple[ClaimPair, ...]:
        return tuple(pair for pair in self.cross_pairs if pair.is_relevant)


def pair_id_for(a: ClaimUnit, b: ClaimUnit) -> str:
    """A stable identifier independent of argument order.

    Order independence matters: the same pair must produce the same ledger entry however
    the candidates were submitted, or the canonical digest would depend on input order.
    """
    left = f"{a.candidate_id}:{a.claim_id}"
    right = f"{b.candidate_id}:{b.claim_id}"
    first, second = sorted((left, right))
    return f"{first}|{second}"


def enumerate_pairs(candidates: tuple[NormalizedCandidate, ...]) -> PairSet:
    """Build every cross-candidate pair, and bounded within-candidate pairs.

    Raises:
        PrismError: ``LIMIT_EXCEEDED`` if the pair space would exceed its cap. The check
            happens here, before any inference, so an oversized workload costs no model
            time (design section 14.8).
    """
    cross: list[ClaimPair] = []
    for left, right in combinations(candidates, 2):
        for unit_a in left.units:
            for unit_b in right.units:
                cross.append(ClaimPair(pair_id=pair_id_for(unit_a, unit_b), a=unit_a, b=unit_b))

    if len(cross) > MAX_CROSS_CANDIDATE_PAIRS:
        raise PrismError(
            code=ErrorCode.LIMIT_EXCEEDED,
            message="The cross-candidate pair space exceeds the supported maximum.",
            diagnostics={"pairs": len(cross), "limit": MAX_CROSS_CANDIDATE_PAIRS},
        )

    internal: list[ClaimPair] = []
    for candidate in candidates:
        for unit_a, unit_b in combinations(candidate.units, 2):
            if len(internal) >= MAX_INTERNAL_PAIRS:
                break
            internal.append(ClaimPair(pair_id=pair_id_for(unit_a, unit_b), a=unit_a, b=unit_b))

    return PairSet(cross_pairs=tuple(cross), internal_pairs=tuple(internal))


def apply_relevance(
    pairs: tuple[ClaimPair, ...], scores: tuple[float, ...]
) -> tuple[ClaimPair, ...]:
    """Attach E1 relevance scores to pairs, in order.

    Raises:
        PrismError: ``INTERNAL_ERROR`` if the score count does not match the pair count.
            A misalignment here would attach one pair's relevance to another, so it fails
            loudly rather than being tolerated.
    """
    if len(pairs) != len(scores):
        raise PrismError(
            code=ErrorCode.INTERNAL_ERROR,
            message="Relevance score count does not match the pair count.",
            diagnostics={"pairs": len(pairs), "scores": len(scores)},
        )
    return tuple(pair.with_relevance(score) for pair, score in zip(pairs, scores, strict=True))
