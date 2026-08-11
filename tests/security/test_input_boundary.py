"""The input trust boundary at the adapters.

Two properties, both of which were absent before this suite existed:

* the byte gate is *wired*. ``validate_input_size`` was fully tested and called nowhere,
  so every adapter accepted whatever arrived. The predicate itself is covered by
  ``test_limits.py``; these tests assert only that each door applies it, and applies it
  before the payload has been consumed;
* a contract violation on an external input is ``INVALID_INPUT``. Both adapters used to
  build a request object inside a generic guard, so a malformed field surfaced as
  ``INTERNAL_ERROR`` — an operator was told to file a bug for their own typo.

The character caps in the contracts do not bound bytes. A task of 100,000 euro signs is
well inside a 262,144-*character* cap and is 300,000 bytes, which is the case the byte
gate exists for and the case these tests pin.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from prism.cli import EXIT_INVALID_INPUT, EXIT_OK, main
from prism.constants import MCP_TOOL_NAMES
from prism.errors import ErrorCode
from prism.limits import MAX_INPUT_BYTES
from prism.mcp_server import build_server

# 4,000 words is the contract's word ceiling, so this is a large legal task rather than a
# rejected one: it proves the gate bounds size without shrinking what PRISM accepts.
LARGE_LEGAL_TASK = " ".join(["review"] * 4_000)


class _CountingBuffer:
    """A binary stream that remembers how much of itself was actually consumed."""

    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._stream.read(size)
        self.bytes_read += len(chunk)
        return chunk


class _CountingStdin:
    def __init__(self, payload: bytes) -> None:
        self.buffer = _CountingBuffer(payload)

    def read(self, size: int = -1) -> str:
        return self.buffer.read(size).decode("utf-8")


def run_cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await build_server().call_tool(name, arguments)
    structured = getattr(result, "structured_content", None)
    assert structured is not None, f"{name} returned no structured output"
    return dict(structured)


# --------------------------------------------------------------------------------------
# the read itself is bounded
# --------------------------------------------------------------------------------------


def test_oversized_stdin_is_refused_without_draining_the_stream(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Standard input is unbounded by nature, so measuring after the read is not a bound.

    The stream offered here is four times the limit. At most one byte past the limit may
    be taken from it.
    """
    stdin = _CountingStdin(b"a" * (MAX_INPUT_BYTES * 4))
    monkeypatch.setattr(sys, "stdin", stdin)

    code, payload = run_cli(["preflight", "--task-file", "-"], capsys)

    assert code == EXIT_INVALID_INPUT
    assert payload["code"] == ErrorCode.LIMIT_EXCEEDED
    assert stdin.buffer.bytes_read <= MAX_INPUT_BYTES + 1


def test_an_oversized_file_is_refused_from_its_size_not_its_contents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    oversized = tmp_path / "task.txt"
    oversized.write_bytes(b"a" * (MAX_INPUT_BYTES + 1))

    code, payload = run_cli(["preflight", "--task-file", str(oversized)], capsys)

    assert code == EXIT_INVALID_INPUT
    assert payload["code"] == ErrorCode.LIMIT_EXCEEDED
    assert payload["diagnostics"]["input_bytes"] == MAX_INPUT_BYTES + 1


def test_a_large_but_legal_task_file_still_flows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate must reject oversized input, not narrow the accepted input."""
    task_file = tmp_path / "task.txt"
    task_file.write_text(LARGE_LEGAL_TASK, encoding="utf-8")

    code, payload = run_cli(["preflight", "--task-file", str(task_file)], capsys)

    assert code == EXIT_OK
    assert payload["status"] == "OK"


def test_a_multibyte_task_is_bounded_in_bytes_not_characters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """100,000 euro signs: inside every character cap, 300,000 bytes on the wire."""
    task = "€" * 100_000
    assert len(task) < MAX_INPUT_BYTES
    assert len(task.encode("utf-8")) > MAX_INPUT_BYTES

    code, payload = run_cli(["preflight", "--task", task], capsys)

    assert code == EXIT_INVALID_INPUT
    assert payload["code"] == ErrorCode.LIMIT_EXCEEDED


def test_undecodable_input_is_bad_input_not_an_internal_fault(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    task_file = tmp_path / "task.bin"
    task_file.write_bytes(b"\xff\xfe not utf-8 at all")

    code, payload = run_cli(["preflight", "--task-file", str(task_file)], capsys)

    assert code == EXIT_INVALID_INPUT
    assert payload["code"] == ErrorCode.INVALID_INPUT


# --------------------------------------------------------------------------------------
# a contract violation is the caller's error, and says so
# --------------------------------------------------------------------------------------


def test_cli_reports_a_contract_violation_as_invalid_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """H-10: this path built PreflightRequest inside the generic guard, so a value the
    contract refuses was reported as an unexpected internal error."""
    code, payload = run_cli(
        ["preflight", "--task", "Review the release.", "--max-perspectives", "9"], capsys
    )

    assert code == EXIT_INVALID_INPUT
    assert payload["code"] == ErrorCode.INVALID_INPUT
    assert payload["diagnostics"]["fields"] == "max_perspectives"


@pytest.mark.anyio
async def test_mcp_reports_an_unknown_mode_as_invalid_input() -> None:
    """``PrismMode(mode)`` raised ValueError inside the guard: INTERNAL_ERROR for a typo."""
    payload = await call_tool(
        MCP_TOOL_NAMES[0], {"task": "Review the release.", "mode": "sideways"}
    )

    assert payload["code"] == ErrorCode.INVALID_INPUT
    assert payload["diagnostics"]["fields"] == "mode"


@pytest.mark.anyio
async def test_mcp_measures_the_payload_before_the_contract_parse() -> None:
    payload = await call_tool(MCP_TOOL_NAMES[0], {"task": "€" * 100_000})

    assert payload["code"] == ErrorCode.LIMIT_EXCEEDED


@pytest.mark.anyio
async def test_mcp_measure_refuses_an_oversized_request() -> None:
    request = {"candidates": [{"filler": "a" * (MAX_INPUT_BYTES + 1)}]}
    payload = await call_tool(MCP_TOOL_NAMES[1], {"request": request})

    assert payload["code"] == ErrorCode.LIMIT_EXCEEDED
