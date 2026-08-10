"""Hard limits, checked before any inference.

Every value here is pinned by design section 6.2. They are a denial-of-service control
(design section 14.8) and a correctness control: they bound the pair space so that a
legal maximum workload has a knowable cost.

Limits reject. They never truncate silently (plan global constraints).
"""

from __future__ import annotations

from typing import Final

from .errors import ErrorCode, PrismError

# --------------------------------------------------------------------------------------
# input size
# --------------------------------------------------------------------------------------

MAX_INPUT_BYTES: Final[int] = 256 * 1024
MAX_TASK_WORDS: Final[int] = 4_000

# --------------------------------------------------------------------------------------
# candidate and claim shape
# --------------------------------------------------------------------------------------

MIN_CANDIDATES: Final[int] = 2
MAX_CANDIDATES: Final[int] = 5
MAX_CLAIMS_PER_CANDIDATE: Final[int] = 4
MAX_WORDS_PER_CLAIM: Final[int] = 80

#: A surviving claim unit needs this many content words. Below it the unit is dropped
#: with an explicit reason rather than scored as if it were a proposition.
MIN_CONTENT_WORDS: Final[int] = 8

# --------------------------------------------------------------------------------------
# pair space
# --------------------------------------------------------------------------------------

#: 5 candidates x 4 claims, every cross-candidate pair = 160. This is the legal maximum
#: workload, not a throttle below it.
MAX_CROSS_CANDIDATE_PAIRS: Final[int] = 160

#: Within-candidate pairs are diagnostic only and never enter the cross-candidate
#: contradiction denominator (architecture section 6.11).
MAX_INTERNAL_PAIRS: Final[int] = 30

# --------------------------------------------------------------------------------------
# identifiers
# --------------------------------------------------------------------------------------

MAX_IDENTIFIER_CHARS: Final[int] = 128
MAX_SOURCE_LABEL_CHARS: Final[int] = 256
MAX_REGISTRY_PERSPECTIVES: Final[int] = 64

# --------------------------------------------------------------------------------------
# output projection (invariant A23)
# --------------------------------------------------------------------------------------

#: Inline records per detailed category in the default public report. Exact aggregate
#: counts are always returned; only presentation is capped.
MAX_INLINE_PAIR_DETAILS: Final[int] = 20
MAX_DEFAULT_REPORT_BYTES: Final[int] = 12 * 1024

# --------------------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0

#: Admission is zero-queue by design (invariant A20). Excess work is rejected with a
#: typed BUSY inside the admission budget; it is never parked in a queue where it could
#: become a stale result or hidden background work.
MAX_CONCURRENT_MEASUREMENTS: Final[int] = 2
MAX_QUEUED_MEASUREMENTS: Final[int] = 0


def validate_input_size(raw_bytes: int) -> None:
    """Reject an oversized or nonsensical payload before it reaches a parser or a model.

    Raises:
        PrismError: with :attr:`ErrorCode.LIMIT_EXCEEDED`. The diagnostics carry sizes,
            never content.
    """
    if raw_bytes < 0:
        raise PrismError(
            code=ErrorCode.LIMIT_EXCEEDED,
            message="Input size is negative, which indicates a malformed request.",
            diagnostics={"input_bytes": raw_bytes, "limit_bytes": MAX_INPUT_BYTES},
        )
    if raw_bytes > MAX_INPUT_BYTES:
        raise PrismError(
            code=ErrorCode.LIMIT_EXCEEDED,
            message="Input exceeds the maximum accepted size.",
            diagnostics={
                "input_bytes": raw_bytes,
                "limit_bytes": MAX_INPUT_BYTES,
                "excess_bytes": raw_bytes - MAX_INPUT_BYTES,
            },
        )


def max_pairs_for(candidate_count: int, claims_per_candidate: int) -> int:
    """Cross-candidate pair count for a rectangular workload.

    Used by the intake path to reject a request whose pair space would exceed
    :data:`MAX_CROSS_CANDIDATE_PAIRS` before any normalization work is done.
    """
    if candidate_count < 2:
        return 0
    pairs_of_candidates = candidate_count * (candidate_count - 1) // 2
    return pairs_of_candidates * claims_per_candidate * claims_per_candidate
