"""Hard limits, checked before any inference.

Every value here is pinned rather than tunable. They are a denial-of-service control and
a correctness control: they bound the pair space so that a legal maximum workload has a
knowable cost.

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
#: contradiction denominator.
MAX_INTERNAL_PAIRS: Final[int] = 30

# --------------------------------------------------------------------------------------
# identifiers
# --------------------------------------------------------------------------------------

MAX_IDENTIFIER_CHARS: Final[int] = 128
MAX_SOURCE_LABEL_CHARS: Final[int] = 256
MAX_REGISTRY_PERSPECTIVES: Final[int] = 64

#: A perspective registry is small canonical data. The cap bounds the read a declared
#: override performs, so an operator-supplied path cannot pull an arbitrary large file in.
MAX_REGISTRY_BYTES: Final[int] = 256 * 1024

# --------------------------------------------------------------------------------------
# output projection: aggregates stay exact, inline detail is capped
# --------------------------------------------------------------------------------------

#: Inline records per detailed category in the default public report. Exact aggregate
#: counts are always returned; only presentation is capped.
MAX_INLINE_PAIR_DETAILS: Final[int] = 20
MAX_DEFAULT_REPORT_BYTES: Final[int] = 12 * 1024

# --------------------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------------------

#: The measurement deadline: how long a caller waits before receiving a typed ``TIMEOUT``
#: instead of a report. Raised from 10.0 on 2026-08-12, because at 10 s the contract could
#: not keep its promise at the capacity it advertises. The legal maximum request — five
#: candidates, four claims each, all on one subject, so all 160 cross-candidate pairs reach
#: the NLI model — *straddled* the old deadline on the reference machine: one idle run's
#: worst measurement took 9,564 ms and passed, the next took 10,246 ms and failed. Same
#: machine, same input, different verdict.
#:
#: 15.0 is the worst measured idle maximum (10,246 ms) plus 46%. It is a bound on how long
#: a caller waits, not a guarantee of completion: the same workload measured 14,340 ms p95
#: on a busy machine, so a loaded host still returns ``TIMEOUT``, which is the deadline
#: working rather than failing.
#:
#: What did *not* move: ``MAX_CROSS_CANDIDATE_PAIRS`` is still 160, and the 8,000 ms p95 and
#: 10,000 ms p99 budgets in ``scripts/compare_benchmarks.py`` are unchanged and are still
#: missed by that worst-case workload. A missed budget is recorded as missed.
DEFAULT_TIMEOUT_SECONDS: Final[float] = 15.0

#: Admission is zero-queue by design. Excess work is rejected with a
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
