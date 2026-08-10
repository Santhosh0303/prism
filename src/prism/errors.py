"""Typed failures and the human-recovery contract.

Every public error carries a stable code, retryability, the affected component, a safe
next action, and a content-free request identifier. An
operator must be able to tell malformed input from version skew, saturation, a missing
model, an integrity failure, a timeout, and an internal fault *without reading a
traceback*.

Diagnostics are content-free. Nothing in this module may
carry task text, claim text, source labels, environment values, or filesystem paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .version import SCHEMA_VERSION

DiagnosticValue = str | int | float | bool | None


class ErrorCode(StrEnum):
    """Stable, machine-readable failure identities.

    These strings are part of the public contract. A minor release may add a code but
    may not rename or repurpose one.
    """

    INVALID_INPUT = "INVALID_INPUT"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    BUSY = "BUSY"
    TIMEOUT = "TIMEOUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_INTEGRITY_FAILURE = "MODEL_INTEGRITY_FAILURE"
    CONFIG_INTEGRITY_FAILURE = "CONFIG_INTEGRITY_FAILURE"
    MEASURE_DISABLED = "MEASURE_DISABLED"
    OUTPUT_BUDGET_EXCEEDED = "OUTPUT_BUDGET_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    @staticmethod
    def recovery(code: ErrorCode) -> Recovery:
        """Return the operator recovery contract for a code."""
        return _RECOVERY[code]


@dataclass(frozen=True, slots=True)
class Recovery:
    """What an operator should do about a failure."""

    component: str
    retryable: bool
    safe_action: str


#: The recovery table is exhaustive by construction: tests/security/test_limits.py and
#: tests/operational/test_error_actionability.py iterate every ErrorCode member.
_RECOVERY: Final[dict[ErrorCode, Recovery]] = {
    ErrorCode.INVALID_INPUT: Recovery(
        component="intake",
        retryable=False,
        safe_action="Correct the request to match the published schema, then resend. "
        "Retrying the same payload will fail identically.",
    ),
    ErrorCode.LIMIT_EXCEEDED: Recovery(
        component="intake",
        retryable=False,
        safe_action="Reduce the input below the published limits: at most 5 candidates, "
        "4 claims each, 80 words per claim, 256 KiB total. Do not split a "
        "single claim to evade the limit; drop or merge candidates instead.",
    ),
    ErrorCode.VERSION_MISMATCH: Recovery(
        component="contract",
        retryable=False,
        safe_action="Update the client, skill, or server so the schema versions overlap. "
        "The supported range is reported in the diagnostics of this error.",
    ),
    ErrorCode.BUSY: Recovery(
        component="admission",
        retryable=True,
        safe_action="Capacity is exhausted and there is no queue by design. Wait for an "
        "in-flight measurement to finish and resend the identical request.",
    ),
    ErrorCode.TIMEOUT: Recovery(
        component="measurement",
        retryable=True,
        safe_action="The deadline elapsed and no partial result was produced. Resend with "
        "fewer claims. If timeouts repeat, restart the server process: a native "
        "inference job may still be occupying a worker.",
    ),
    ErrorCode.MODEL_UNAVAILABLE: Recovery(
        component="model_bundle",
        retryable=False,
        safe_action="Install the verified model bundle and point PRISM_MODEL_ROOT at it, "
        "or continue in preflight-only mode, which does not require encoders.",
    ),
    ErrorCode.MODEL_INTEGRITY_FAILURE: Recovery(
        component="model_bundle",
        retryable=False,
        safe_action="An artifact does not match its pinned hash or escapes the model root. "
        "Do not attempt to bypass this. Restore the bundle from the signed "
        "release and rerun 'prism health --deep' before measuring again.",
    ),
    ErrorCode.CONFIG_INTEGRITY_FAILURE: Recovery(
        component="registry",
        retryable=False,
        safe_action="The packaged perspective registry is malformed or fails its schema. "
        "Reinstall the package; a partially applied upgrade is the usual cause.",
    ),
    ErrorCode.MEASURE_DISABLED: Recovery(
        component="kill_switch",
        retryable=False,
        safe_action="Measurement is disabled by PRISM_DISABLE_MEASURE. Preflight and "
        "synthesis remain available. Unset the variable once the advisory that "
        "prompted the shutdown is resolved.",
    ),
    ErrorCode.OUTPUT_BUDGET_EXCEEDED: Recovery(
        component="report_projection",
        retryable=False,
        safe_action="The result could not be reduced below the size budget without "
        "silently truncating it. Resend with fewer or shorter claims.",
    ),
    ErrorCode.INTERNAL_ERROR: Recovery(
        component="service",
        retryable=False,
        safe_action="Report the request_id from this error to the maintainer. It contains "
        "no task or claim content and is safe to share.",
    ),
}


class PrismErrorReport(BaseModel):
    """The public error envelope. This is what a caller actually receives."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION  # type: ignore[assignment]
    status: Literal["ERROR"] = "ERROR"
    code: ErrorCode
    message: str = Field(max_length=1024)
    retryable: bool
    component: str
    safe_action: str
    request_id: str
    diagnostics: dict[str, DiagnosticValue] = Field(default_factory=dict)


class PrismError(Exception):
    """Internal exception carrying enough structure to become a public report.

    Raise this rather than a bare exception anywhere a caller could observe the failure.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        diagnostics: dict[str, DiagnosticValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostics: dict[str, DiagnosticValue] = dict(diagnostics or {})

    @property
    def recovery(self) -> Recovery:
        return ErrorCode.recovery(self.code)

    @property
    def retryable(self) -> bool:
        return self.recovery.retryable

    def to_report(self, request_id: str) -> PrismErrorReport:
        """Project the exception into the public envelope."""
        recovery = self.recovery
        return PrismErrorReport(
            code=self.code,
            message=self.message,
            retryable=recovery.retryable,
            component=recovery.component,
            safe_action=recovery.safe_action,
            request_id=request_id,
            diagnostics=dict(self.diagnostics),
        )
