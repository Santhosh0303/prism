"""Claim normalisation and segmentation.

Structured claim packets are the supported input; they bypass segmentation entirely,
which is most of the reason they exist (ADR-003). A constrained plain-text path is kept
for compatibility with candidates produced elsewhere.

The rule that governs this module: **content loss is never silent**. Every removed
region, dropped fragment, truncation, and duplicate is counted and reported through
``normalization_warnings``, with a digest of what was removed rather than the removed
text itself (invariant A18). A caller who cannot see what was discarded cannot judge
whether the measurement covered their argument.

Parsing is bounded and linear. There is no OCR, no markup rendering, no AST evaluation,
and no regex that can backtrack catastrophically (design section 6.7).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from ..canonical import canonical_digest
from ..contracts import (
    CandidatePacket,
    EvidenceStatus,
    NormalizationWarning,
    NormalizationWarningCode,
)
from ..limits import (
    MAX_CLAIMS_PER_CANDIDATE,
    MAX_WORDS_PER_CLAIM,
    MIN_CONTENT_WORDS,
)

# Anchored, non-nesting patterns. Each can match at most once per position, so the total
# work is linear in the length of the input.
_FENCED_CODE: Final[re.Pattern[str]] = re.compile(r"^```[^\n]*\n.*?^```[ \t]*$", re.M | re.S)
_INDENTED_CODE: Final[re.Pattern[str]] = re.compile(r"(?:^[ ]{4,}\S[^\n]*\n?)+", re.M)
_TABLE_ROW: Final[re.Pattern[str]] = re.compile(r"(?:^\|[^\n]*\|[ \t]*\n?)+", re.M)
_BLOCK_QUOTE: Final[re.Pattern[str]] = re.compile(r"(?:^>[^\n]*\n?)+", re.M)
_SENTENCE_SPLIT: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])[ \t]+(?=[A-Z(\"'])")
_ALPHABETIC: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]")

#: Phrases that carry no proposition. A fragment consisting only of these is dropped with
#: a reason rather than being scored as if it asserted something.
_BOILERPLATE_PREFIXES: Final[tuple[str, ...]] = (
    "here is",
    "here are",
    "in summary",
    "to summarise",
    "to summarize",
    "in conclusion",
    "as an ai",
    "i hope this helps",
    "let me know if",
    "sure,",
    "certainly,",
)


@dataclass(frozen=True, slots=True)
class ClaimUnit:
    """One scorable proposition.

    ``text`` is the original, preserved exactly as the host wrote it. ``matching_view`` is
    an NFKC-folded projection used only for comparison, so that normalisation can never
    alter what is reported back to the user (design section 6.7 step 5).
    """

    claim_id: str
    candidate_id: str
    text: str
    matching_view: str
    confidence: int | None
    evidence_status: EvidenceStatus

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True, slots=True)
class NormalizedCandidate:
    candidate_id: str
    source_group_id: str
    units: tuple[ClaimUnit, ...]

    @property
    def is_viable(self) -> bool:
        return len(self.units) > 0


def matching_view(text: str) -> str:
    """The comparison projection: NFKC-folded, whitespace-collapsed, casefolded."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(folded.split())


def _digest_of(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _is_boilerplate(text: str) -> bool:
    lowered = text.strip().casefold()
    return any(lowered.startswith(prefix) for prefix in _BOILERPLATE_PREFIXES)


def has_content(text: str) -> bool:
    """A unit must carry at least the content floor in words and one alphabetic token."""
    return len(text.split()) >= MIN_CONTENT_WORDS and bool(_ALPHABETIC.search(text))


# --------------------------------------------------------------------------------------
# structured path
# --------------------------------------------------------------------------------------


def normalize_claims(
    packet: CandidatePacket,
) -> tuple[NormalizedCandidate, tuple[NormalizationWarning, ...]]:
    """Normalise an already-structured claim packet.

    Contract validation has already enforced the word bounds and identifier rules, so the
    only work here is building the matching view and removing exact duplicates.
    """
    warnings: list[NormalizationWarning] = []
    units: list[ClaimUnit] = []
    seen: dict[str, str] = {}

    for claim in packet.claims:
        view = matching_view(claim.text)
        if view in seen:
            warnings.append(
                NormalizationWarning(
                    candidate_id=packet.candidate_id,
                    code=NormalizationWarningCode.DUPLICATE_UNIT_REMOVED,
                    count=1,
                    removed_digest=_digest_of(view),
                )
            )
            continue
        seen[view] = claim.claim_id
        units.append(
            ClaimUnit(
                claim_id=claim.claim_id,
                candidate_id=packet.candidate_id,
                text=claim.text,
                matching_view=view,
                confidence=claim.confidence,
                evidence_status=claim.evidence_status,
            )
        )

    return (
        NormalizedCandidate(
            candidate_id=packet.candidate_id,
            source_group_id=packet.source_group_id,
            units=tuple(units),
        ),
        tuple(warnings),
    )


# --------------------------------------------------------------------------------------
# plain-text compatibility path
# --------------------------------------------------------------------------------------


def segment_plain_text(
    candidate_id: str,
    text: str,
) -> tuple[tuple[str, ...], tuple[NormalizationWarning, ...]]:
    """Split free text into claim units, reporting every removal.

    Unsupported structure is removed rather than parsed, because a table or code block
    scored as a proposition produces confident nonsense. What matters is that the caller
    is told it happened.
    """
    warnings: list[NormalizationWarning] = []
    working = text

    for pattern, code in (
        (_FENCED_CODE, NormalizationWarningCode.CODE_BLOCK_REMOVED),
        (_INDENTED_CODE, NormalizationWarningCode.CODE_BLOCK_REMOVED),
        (_TABLE_ROW, NormalizationWarningCode.TABLE_REMOVED),
        (_BLOCK_QUOTE, NormalizationWarningCode.QUOTED_PROMPT_REMOVED),
    ):
        matches = pattern.findall(working)
        if matches:
            warnings.append(
                NormalizationWarning(
                    candidate_id=candidate_id,
                    code=code,
                    count=len(matches),
                    removed_digest=canonical_digest([str(match) for match in matches]),
                )
            )
            working = pattern.sub("\n", working)

    units: list[str] = []
    dropped_low_content = 0
    dropped_boilerplate = 0
    truncated = 0
    seen: set[str] = set()

    for paragraph in (block.strip() for block in working.split("\n\n")):
        if not paragraph:
            continue
        for sentence in _SENTENCE_SPLIT.split(paragraph):
            candidate_text = " ".join(sentence.split())
            if not candidate_text:
                continue
            if _is_boilerplate(candidate_text):
                dropped_boilerplate += 1
                continue
            words = candidate_text.split()
            if len(words) > MAX_WORDS_PER_CLAIM:
                candidate_text = " ".join(words[:MAX_WORDS_PER_CLAIM])
                truncated += 1
            if not has_content(candidate_text):
                dropped_low_content += 1
                continue
            view = matching_view(candidate_text)
            if view in seen:
                warnings.append(
                    NormalizationWarning(
                        candidate_id=candidate_id,
                        code=NormalizationWarningCode.DUPLICATE_UNIT_REMOVED,
                        count=1,
                        removed_digest=_digest_of(view),
                    )
                )
                continue
            seen.add(view)
            units.append(candidate_text)

    if dropped_low_content:
        warnings.append(
            NormalizationWarning(
                candidate_id=candidate_id,
                code=NormalizationWarningCode.UNIT_BELOW_CONTENT_FLOOR,
                count=dropped_low_content,
            )
        )
    if dropped_boilerplate:
        warnings.append(
            NormalizationWarning(
                candidate_id=candidate_id,
                code=NormalizationWarningCode.BOILERPLATE_REMOVED,
                count=dropped_boilerplate,
            )
        )
    if truncated:
        warnings.append(
            NormalizationWarning(
                candidate_id=candidate_id,
                code=NormalizationWarningCode.UNIT_TRUNCATED,
                count=truncated,
            )
        )

    if len(units) > MAX_CLAIMS_PER_CANDIDATE:
        warnings.append(
            NormalizationWarning(
                candidate_id=candidate_id,
                code=NormalizationWarningCode.UNIT_TRUNCATED,
                count=len(units) - MAX_CLAIMS_PER_CANDIDATE,
            )
        )
        units = units[:MAX_CLAIMS_PER_CANDIDATE]

    return tuple(units), tuple(warnings)


# --------------------------------------------------------------------------------------
# duplicate candidates
# --------------------------------------------------------------------------------------


def find_duplicate_candidates(
    candidates: tuple[NormalizedCandidate, ...],
) -> tuple[tuple[str, str], ...]:
    """Identify candidates whose claim set is exactly another's.

    Submitting the same answer twice must not look like two agreeing reviewers. Duplicates
    are excluded from aggregate scoring and reported (design section 6.12).
    """
    duplicates: list[tuple[str, str]] = []
    fingerprints: dict[str, str] = {}
    for candidate in candidates:
        fingerprint = canonical_digest(sorted(unit.matching_view for unit in candidate.units))
        original = fingerprints.get(fingerprint)
        if original is not None:
            duplicates.append((candidate.candidate_id, original))
        else:
            fingerprints[fingerprint] = candidate.candidate_id
    return tuple(duplicates)
