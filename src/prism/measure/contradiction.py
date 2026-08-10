"""The pair ledger and the denominator arithmetic.

The ledger is the primary evidence. Every public aggregate is computed from it, and the
report can be reconstructed from it exactly. Projection for display happens later and
cannot touch these numbers.

Denominator, stated once::

    pairs_total              all cross-candidate claim-unit pairs
    relevant_pairs           pairs at or above the frozen relevance floor
    scope_divergent_count    relevant pairs excluded by an explicit scope difference
    contradiction_denominator = relevant_pairs - scope_divergent_count
    nli_coverage             = pairs_scored_by_nli / contradiction_denominator
    contradiction_rate       = contradiction_count / contradiction_denominator

A zero denominator yields ``null``, never ``0.0``. "Nothing was comparable" and "nothing
disagreed" are different findings, and collapsing them would be the most flattering
possible lie.

Both directions are scored and the maximum taken: NLI is not symmetric, and "A
contradicts B" can score far lower than "B contradicts A" for the same disagreement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Final, Protocol

from ..canonical import canonical_digest
from ..contracts import InternalConflict, InternalConflictKind, ScopeResult
from ..limits import MAX_INTERNAL_PAIRS
from .calibration import contradiction_threshold
from .pair import ClaimPair, PairSet, apply_relevance
from .scope import classify_scope
from .segment import ClaimUnit, NormalizedCandidate


class Encoders(Protocol):
    """The inference surface the ledger needs. Kept narrow so pure-arithmetic tests can
    substitute a deterministic stand-in without touching ONNX Runtime."""

    def embed(self, texts: list[str]) -> object: ...

    def contradiction_probabilities(self, pairs: list[tuple[str, str]]) -> object: ...


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One fully described pair. This is the unit of evidence."""

    pair_id: str
    candidate_a_id: str
    candidate_b_id: str
    claim_a_id: str
    claim_b_id: str
    relevance: float
    scope: ScopeResult
    scope_dimension: str | None
    scope_marker_a: str | None
    scope_marker_b: str | None
    in_denominator: bool
    score_a_to_b: float | None
    score_b_to_a: float | None

    @property
    def contradiction_score(self) -> float | None:
        if self.score_a_to_b is None or self.score_b_to_a is None:
            return None
        return max(self.score_a_to_b, self.score_b_to_a)

    def is_contradiction(self, threshold: float) -> bool:
        score = self.contradiction_score
        return score is not None and score >= threshold


@dataclass(frozen=True, slots=True)
class PairLedger:
    """The complete internal record. Aggregates are derived, never stored separately."""

    entries: tuple[LedgerEntry, ...]
    pairs_total: int
    threshold: float
    internal_entries: tuple[LedgerEntry, ...] = field(default=())

    # -- aggregates --------------------------------------------------------------------

    @property
    def relevant_pairs(self) -> int:
        return len(self.entries)

    @property
    def scope_divergent_count(self) -> int:
        return sum(1 for e in self.entries if e.scope is ScopeResult.SCOPE_DIVERGENT)

    @property
    def scope_uncertain_count(self) -> int:
        return sum(1 for e in self.entries if e.scope is ScopeResult.UNCERTAIN)

    @property
    def denominator_entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(e for e in self.entries if e.in_denominator)

    @property
    def contradiction_denominator(self) -> int:
        return len(self.denominator_entries)

    @property
    def pairs_scored_by_nli(self) -> int:
        return sum(1 for e in self.denominator_entries if e.contradiction_score is not None)

    @property
    def pairs_inferred_not_contradictory(self) -> int:
        """Denominator pairs an experimental speed profile skipped. Zero in production:
        the production profile scores every denominator pair."""
        return self.contradiction_denominator - self.pairs_scored_by_nli

    @property
    def nli_coverage(self) -> float | None:
        if self.contradiction_denominator == 0:
            return None
        return self.pairs_scored_by_nli / self.contradiction_denominator

    @property
    def contradiction_count(self) -> int | None:
        if self.contradiction_denominator == 0:
            return None
        return sum(1 for e in self.denominator_entries if e.is_contradiction(self.threshold))

    @property
    def contradiction_rate(self) -> float | None:
        count = self.contradiction_count
        if count is None or self.contradiction_denominator == 0:
            return None
        return count / self.contradiction_denominator

    @property
    def digest(self) -> str:
        """Digest over the complete ledger, so a bounded report stays inspectable."""
        return canonical_digest(
            [
                {
                    "pair_id": e.pair_id,
                    "relevance": e.relevance,
                    "scope": e.scope.value,
                    "in_denominator": e.in_denominator,
                    "score_a_to_b": e.score_a_to_b,
                    "score_b_to_a": e.score_b_to_a,
                }
                for e in sorted(self.entries, key=lambda entry: entry.pair_id)
            ]
        )


def _entry_for(
    pair: ClaimPair, scored: bool, score_ab: float | None, score_ba: float | None
) -> LedgerEntry:
    verdict = classify_scope(pair)
    in_denominator = verdict.result is not ScopeResult.SCOPE_DIVERGENT
    return LedgerEntry(
        pair_id=pair.pair_id,
        candidate_a_id=pair.a.candidate_id,
        candidate_b_id=pair.b.candidate_id,
        claim_a_id=pair.a.claim_id,
        claim_b_id=pair.b.claim_id,
        relevance=pair.relevance if pair.relevance is not None else 0.0,
        scope=verdict.result,
        scope_dimension=verdict.dimension,
        scope_marker_a=verdict.marker_a,
        scope_marker_b=verdict.marker_b,
        in_denominator=in_denominator,
        score_a_to_b=score_ab if scored else None,
        score_b_to_a=score_ba if scored else None,
    )


def build_ledger(pair_set: PairSet, encoders: Encoders) -> PairLedger:
    """Score a pair set into a complete ledger.

    Order of operations is fixed and matters: relevance first so that the NLI model never
    sees pairs about different subjects (empirically it will call unrelated sentences
    contradictory), then scope, then NLI over every surviving denominator pair.
    """
    import numpy as np

    threshold = contradiction_threshold()
    cross = pair_set.cross_pairs

    if not cross:
        return PairLedger(entries=(), pairs_total=0, threshold=threshold)

    # -- E1: relevance ----------------------------------------------------------------
    units = {}
    for pair in cross:
        units[(pair.a.candidate_id, pair.a.claim_id)] = pair.a
        units[(pair.b.candidate_id, pair.b.claim_id)] = pair.b
    keys = sorted(units)
    embeddings = np.asarray(encoders.embed([units[key].text for key in keys]))
    index = {key: position for position, key in enumerate(keys)}

    relevance_scores = tuple(
        float(
            embeddings[index[(pair.a.candidate_id, pair.a.claim_id)]]
            @ embeddings[index[(pair.b.candidate_id, pair.b.claim_id)]]
        )
        for pair in cross
    )
    scored_pairs = apply_relevance(cross, relevance_scores)
    relevant = tuple(pair for pair in scored_pairs if pair.is_relevant)

    # -- scope, then E2 over the denominator ------------------------------------------
    denominator_pairs = [
        pair for pair in relevant if classify_scope(pair).result is not ScopeResult.SCOPE_DIVERGENT
    ]

    forward = [(pair.a.text, pair.b.text) for pair in denominator_pairs]
    backward = [(pair.b.text, pair.a.text) for pair in denominator_pairs]
    forward_scores = np.asarray(encoders.contradiction_probabilities(forward))
    backward_scores = np.asarray(encoders.contradiction_probabilities(backward))

    by_pair_id = {
        pair.pair_id: (float(forward_scores[i]), float(backward_scores[i]))
        for i, pair in enumerate(denominator_pairs)
    }

    entries = tuple(
        _entry_for(
            pair,
            scored=pair.pair_id in by_pair_id,
            score_ab=by_pair_id.get(pair.pair_id, (None, None))[0],
            score_ba=by_pair_id.get(pair.pair_id, (None, None))[1],
        )
        for pair in relevant
    )

    return PairLedger(
        entries=entries,
        pairs_total=len(cross),
        threshold=threshold,
        internal_entries=(),
    )


# --------------------------------------------------------------------------------------
# within-candidate diagnostics
# --------------------------------------------------------------------------------------

#: Exact-conflict patterns. These are deterministic checks, not model output: a claim that
#: says "supported" and one that says "not supported" in the same candidate is a conflict
#: whatever an encoder thinks. They are reported as diagnostics and never mixed into the
#: cross-candidate contradiction rate.
_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bv?(\d+\.\d+(?:\.\d+)?)\b")
_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_NUMBER_UNIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(ms|s|seconds?|minutes?|hours?|kb|mb|gb|%|percent)\b"
)
_BOOLEAN_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("supported", "not supported"),
    ("enabled", "disabled"),
    ("required", "optional"),
    ("safe", "unsafe"),
    ("available", "unavailable"),
    ("reversible", "irreversible"),
)


def detect_internal_conflicts(
    candidates: tuple[NormalizedCandidate, ...],
) -> tuple[InternalConflict, ...]:
    """Find exact conflicts between claims inside a single candidate.

    A candidate that contradicts itself is a quality signal about that candidate. Folding
    it into the cross-candidate rate would misattribute it as disagreement between
    reviewers, so it is reported separately and never changes the denominator.
    """
    conflicts: list[InternalConflict] = []
    for candidate in candidates:
        pairs = list(combinations(candidate.units, 2))[:MAX_INTERNAL_PAIRS]
        for unit_a, unit_b in pairs:
            view_a, view_b = unit_a.matching_view, unit_b.matching_view

            for positive, negative in _BOOLEAN_PAIRS:
                if (negative in view_a and positive in view_b and negative not in view_b) or (
                    negative in view_b and positive in view_a and negative not in view_a
                ):
                    conflicts.append(
                        _conflict(
                            candidate,
                            unit_a,
                            unit_b,
                            InternalConflictKind.BOOLEAN_CONFLICT,
                            f"{positive!r} versus {negative!r}",
                        )
                    )
                    break

            for pattern, kind in (
                (_VERSION_PATTERN, InternalConflictKind.VERSION_CONFLICT),
                (_DATE_PATTERN, InternalConflictKind.DATE_CONFLICT),
            ):
                found_a = set(pattern.findall(view_a))
                found_b = set(pattern.findall(view_b))
                if found_a and found_b and not (found_a & found_b):
                    conflicts.append(
                        _conflict(
                            candidate,
                            unit_a,
                            unit_b,
                            kind,
                            f"{sorted(found_a)[0]} versus {sorted(found_b)[0]}",
                        )
                    )

            # Numeric conflicts are only claimed when the unit matches, which is the
            # conservative reading: 10 ms and 10 GB are not in disagreement.
            numbers_a = {unit: value for value, unit in _NUMBER_UNIT_PATTERN.findall(view_a)}
            numbers_b = {unit: value for value, unit in _NUMBER_UNIT_PATTERN.findall(view_b)}
            for unit in sorted(set(numbers_a) & set(numbers_b)):
                if numbers_a[unit] != numbers_b[unit]:
                    conflicts.append(
                        _conflict(
                            candidate,
                            unit_a,
                            unit_b,
                            InternalConflictKind.NUMERIC_CONFLICT,
                            f"{numbers_a[unit]}{unit} versus {numbers_b[unit]}{unit}",
                        )
                    )
                    break

    return tuple(conflicts[:MAX_INTERNAL_PAIRS])


def _conflict(
    candidate: NormalizedCandidate,
    unit_a: ClaimUnit,
    unit_b: ClaimUnit,
    kind: InternalConflictKind,
    detail: str,
) -> InternalConflict:
    return InternalConflict(
        candidate_id=candidate.candidate_id,
        claim_a_id=unit_a.claim_id,
        claim_b_id=unit_b.claim_id,
        kind=kind,
        detail=detail,
    )
