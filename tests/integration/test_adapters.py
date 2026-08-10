"""Adapter equivalence and error actionability — plan Tasks 11, 12, and 18.

Gate G22 asks for canonical digest parity across adapters. That is the property that makes
"one core, many adapters" a fact rather than an intention: if the Python API, the CLI, and
the MCP server can produce different bytes for the same input, then a user's result depends
on which door they came through.

Task 18 Step 11 asks that every public error code be actionable from the payload alone.
An operator holding only the result must be able to tell retry from reconfigure from
restore from escalate, without a traceback.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from prism.canonical import canonical_digest, canonical_json
from prism.cli import EXIT_INVALID_INPUT, EXIT_OK, main
from prism.contracts import PreflightRequest, PrismMode, PrismStatus
from prism.errors import ErrorCode, PrismError
from prism.mcp_server import build_server
from prism.service import PrismService

TASK = (
    "Assess the system design: component boundaries, coupling between services, "
    "and the scalability tradeoff of the proposed architecture."
)


@pytest.fixture(scope="module")
def service() -> PrismService:
    return PrismService.from_default_bundle()


def run_cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main(argv)
    return code, capsys.readouterr().out.strip()


async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool through the real server and return its structured payload.

    Structured output is the contract: a host reads `structured_content`, not the
    human-readable content block.
    """
    result = await build_server().call_tool(name, arguments)
    # call_tool may also return an elicitation request; these tools never elicit.
    structured = getattr(result, "structured_content", None)
    assert structured is not None, f"{name} returned no structured output"
    return dict(structured)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_cli_version_reports_every_identifier(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["version"], capsys)
    payload = json.loads(out)
    assert code == EXIT_OK
    assert payload["schema_version"] == "1.0"
    assert payload["mcp_protocol_version"] == "2026-07-28"
    assert payload["audit_baseline_id"] == "PRISM-AUDIT-2026-08-06-C"


def test_cli_preflight_emits_one_json_object(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["preflight", "--task", TASK, "--mode", "standard"], capsys)
    assert code == EXIT_OK
    payload = json.loads(out)  # a single parse: one complete write, not a stream
    assert len(payload["perspectives"]) == 4


def test_cli_health_reports_calibration_state(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["health"], capsys)
    assert code == EXIT_OK
    assert json.loads(out)["calibration_status"] == "UNCALIBRATED_PENDING_HUMAN_VALIDATION"


def test_cli_rejects_a_missing_input_file(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["measure", "--input", "definitely-not-here.json"], capsys)
    assert code == EXIT_INVALID_INPUT
    assert json.loads(out)["code"] == "INVALID_INPUT"


def test_cli_rejects_a_directory_as_input(
    capsys: pytest.CaptureFixture[str], tmp_path: Any
) -> None:
    code, _ = run_cli(["measure", "--input", str(tmp_path)], capsys)
    assert code == EXIT_INVALID_INPUT


def test_cli_has_no_output_path_option() -> None:
    """v1 writes to stdout only, so no PRISM-controlled path can overwrite a user file."""
    from prism.cli import build_parser

    help_text = build_parser().format_help()
    for forbidden in ("--output", "--out-file", "--write"):
        assert forbidden not in help_text


def test_cli_exit_codes_are_distinct_and_stable() -> None:
    from prism.cli import (
        EXIT_INSUFFICIENT,
        EXIT_INTERNAL,
        EXIT_MODEL_UNAVAILABLE,
    )

    assert (
        EXIT_OK,
        EXIT_INVALID_INPUT,
        EXIT_INSUFFICIENT,
        EXIT_MODEL_UNAVAILABLE,
        EXIT_INTERNAL,
    ) == (0, 2, 3, 4, 5)


# --------------------------------------------------------------------------------------
# MCP
# --------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mcp_exposes_exactly_four_read_only_tools() -> None:
    from prism.constants import MCP_TOOL_NAMES

    tools = await build_server().list_tools()
    assert [tool.name for tool in tools] == list(MCP_TOOL_NAMES)
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.open_world_hint is False


@pytest.mark.anyio
async def test_mcp_tool_descriptions_carry_no_user_content() -> None:
    """Tool descriptions are static: a description built from user input would be a
    tool-poisoning vector."""
    tools = await build_server().list_tools()
    for tool in tools:
        assert tool.description
        assert "{" not in tool.description


@pytest.mark.anyio
async def test_mcp_preflight_returns_a_structured_report() -> None:
    payload = await call_tool("prism.preflight", {"task": TASK, "mode": "standard"})
    assert payload["status"] == PrismStatus.OK.value
    assert len(payload["perspectives"]) == 4


@pytest.mark.anyio
async def test_mcp_returns_a_typed_error_value_not_an_exception() -> None:
    payload = await call_tool("prism.measure", {"request": {"question": "x"}})
    assert payload["status"] == "ERROR"
    assert payload["code"] == "INVALID_INPUT"
    assert payload["retryable"] is False
    assert payload["component"] == "intake"
    assert payload["safe_action"]
    assert payload["request_id"]


# --------------------------------------------------------------------------------------
# G22 — canonical digest parity across adapters
# --------------------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["lite", "standard", "critical"])
async def test_python_cli_and_mcp_produce_identical_preflight_bytes(
    service: PrismService, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    python_digest = canonical_digest(
        service.preflight(PreflightRequest(task=TASK, mode=PrismMode(mode)))
    )

    _, cli_out = run_cli(["preflight", "--task", TASK, "--mode", mode], capsys)
    cli_digest = canonical_digest(json.loads(cli_out))

    mcp_digest = canonical_digest(await call_tool("prism.preflight", {"task": TASK, "mode": mode}))

    assert python_digest == cli_digest == mcp_digest


def test_repeated_python_calls_are_byte_identical(service: PrismService) -> None:
    digests = {canonical_digest(service.preflight(PreflightRequest(task=TASK))) for _ in range(20)}
    assert len(digests) == 1


def test_canonical_digest_excludes_adapter_timestamps() -> None:
    """Invariant A24: a timestamp an adapter attached must not change the digest."""
    base = {"status": "OK", "value": 1}
    assert canonical_digest(base) == canonical_digest({**base, "timestamp": "2026-08-10T00:00:00Z"})
    assert canonical_digest(base) == canonical_digest({**base, "duration_ms": 12.5})
    assert canonical_digest(base) != canonical_digest({**base, "value": 2})


def test_canonical_json_is_ascii_and_sorted() -> None:
    """Sorted keys and ASCII escaping keep the byte stream independent of platform
    encoding, which matters on Windows."""
    rendered = canonical_json({"b": 1, "a": "café"})
    assert rendered == '{"a":"caf\\u00e9","b":1}'


# --------------------------------------------------------------------------------------
# error actionability — plan Task 18 Step 11
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_error_is_actionable_from_the_payload_alone(code: ErrorCode) -> None:
    payload = PrismError(code=code, message="A failure occurred.").to_report(
        request_id="00000000-0000-4000-8000-000000000000"
    )
    assert payload.code is code
    assert payload.component
    assert isinstance(payload.retryable, bool)
    assert len(payload.safe_action) > 30, "an action must tell the operator what to do"
    assert payload.request_id


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (ErrorCode.BUSY, True),
        (ErrorCode.TIMEOUT, True),
        (ErrorCode.INVALID_INPUT, False),
        (ErrorCode.LIMIT_EXCEEDED, False),
        (ErrorCode.MODEL_INTEGRITY_FAILURE, False),
        (ErrorCode.VERSION_MISMATCH, False),
    ],
)
def test_retryability_is_classified_correctly(code: ErrorCode, retryable: bool) -> None:
    """An operator who retries an unretryable failure wastes time; one who does not retry
    a capacity failure gives up on a working system."""
    assert ErrorCode.recovery(code).retryable is retryable


def test_error_payload_contains_no_traceback_or_content() -> None:
    error = PrismError(
        code=ErrorCode.INTERNAL_ERROR,
        message="An unexpected internal error occurred.",
        diagnostics={"error_type": "ValueError"},
    )
    rendered = canonical_json(error.to_report(request_id="req-1"))
    for forbidden in ("Traceback", 'File "', "line ", "self."):
        assert forbidden not in rendered


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
