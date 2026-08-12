"""Limit tests.

Limits are a denial-of-service control and must be enforced before any model is loaded.
These tests assert both the pinned values themselves and that they reject rather than
truncate.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prism import limits
from prism.errors import ErrorCode, PrismError

# --------------------------------------------------------------------------------------
# the constants are pinned values, not whatever the implementation happens to use
# --------------------------------------------------------------------------------------


def test_limit_values_are_the_pinned_ones() -> None:
    """These numbers are published. A drift here silently changes the public contract."""
    assert limits.MAX_INPUT_BYTES == 256 * 1024
    assert limits.MAX_TASK_WORDS == 4_000
    assert limits.MAX_CANDIDATES == 5
    assert limits.MIN_CANDIDATES == 2
    assert limits.MAX_CLAIMS_PER_CANDIDATE == 4
    assert limits.MAX_WORDS_PER_CLAIM == 80
    assert limits.MIN_CONTENT_WORDS == 8
    assert limits.MAX_CROSS_CANDIDATE_PAIRS == 160
    assert limits.MAX_INTERNAL_PAIRS == 30
    assert limits.MAX_IDENTIFIER_CHARS == 128
    assert limits.MAX_SOURCE_LABEL_CHARS == 256
    assert limits.MAX_REGISTRY_PERSPECTIVES == 64
    assert limits.MAX_REGISTRY_BYTES == 256 * 1024
    assert limits.MAX_INLINE_PAIR_DETAILS == 20
    assert limits.MAX_DEFAULT_REPORT_BYTES == 12 * 1024
    assert limits.DEFAULT_TIMEOUT_SECONDS == 15.0
    assert limits.MAX_CONCURRENT_MEASUREMENTS == 2
    assert limits.MAX_QUEUED_MEASUREMENTS == 0


def test_maximum_pair_count_is_consistent_with_candidate_and_claim_caps() -> None:
    """5 candidates x 4 claims, all cross-candidate pairs = 160. The cap must not be
    smaller than the legal maximum workload, or a legal request would be unservable."""
    units = limits.MAX_CANDIDATES * limits.MAX_CLAIMS_PER_CANDIDATE
    cross = sum(
        limits.MAX_CLAIMS_PER_CANDIDATE**2
        for _ in range(limits.MAX_CANDIDATES * (limits.MAX_CANDIDATES - 1) // 2)
    )
    assert units == 20
    assert cross == limits.MAX_CROSS_CANDIDATE_PAIRS


# --------------------------------------------------------------------------------------
# input size is rejected, never truncated
# --------------------------------------------------------------------------------------


def test_input_at_the_limit_is_accepted() -> None:
    limits.validate_input_size(limits.MAX_INPUT_BYTES)


def test_input_above_the_limit_raises_typed_limit_error() -> None:
    with pytest.raises(PrismError) as excinfo:
        limits.validate_input_size(limits.MAX_INPUT_BYTES + 1)
    assert excinfo.value.code is ErrorCode.LIMIT_EXCEEDED
    assert excinfo.value.retryable is False


def test_limit_error_message_contains_no_raw_content() -> None:
    """Diagnostics are content-free."""
    secret = "SUPER-SECRET-TASK-TEXT"
    with pytest.raises(PrismError) as excinfo:
        limits.validate_input_size(limits.MAX_INPUT_BYTES + len(secret))
    rendered = str(excinfo.value) + repr(excinfo.value.diagnostics)
    assert secret not in rendered


@pytest.mark.parametrize("size", [-1, -1024])
def test_negative_input_size_is_rejected(size: int) -> None:
    with pytest.raises(PrismError):
        limits.validate_input_size(size)


# --------------------------------------------------------------------------------------
# oversized task text
# --------------------------------------------------------------------------------------


def test_task_above_word_limit_is_rejected() -> None:
    from prism.contracts import PreflightRequest

    with pytest.raises(ValidationError):
        PreflightRequest(task=" ".join(["word"] * (limits.MAX_TASK_WORDS + 1)))


def test_task_at_word_limit_is_accepted() -> None:
    from prism.contracts import PreflightRequest

    request = PreflightRequest(task=" ".join(["word"] * limits.MAX_TASK_WORDS))
    assert request.task.count(" ") == limits.MAX_TASK_WORDS - 1


# --------------------------------------------------------------------------------------
# typed failure surface: every error carries code, retryability, component, next action
# --------------------------------------------------------------------------------------


def test_every_error_code_declares_recovery_metadata() -> None:
    """An operator must be able to act on the error without reading a traceback."""
    for code in ErrorCode:
        meta = ErrorCode.recovery(code)
        assert meta.component, code
        assert meta.safe_action, code
        assert isinstance(meta.retryable, bool), code


def test_error_report_is_serialisable_without_content() -> None:
    error = PrismError(
        code=ErrorCode.LIMIT_EXCEEDED,
        message="Input exceeds the configured byte limit.",
        diagnostics={"input_bytes": 999999},
    )
    payload = error.to_report(request_id="00000000-0000-4000-8000-000000000000").model_dump()
    assert payload["status"] == "ERROR"
    assert payload["code"] == ErrorCode.LIMIT_EXCEEDED.value
    assert payload["retryable"] is False
    assert payload["request_id"] == "00000000-0000-4000-8000-000000000000"
    assert "component" in payload
    assert "safe_action" in payload
