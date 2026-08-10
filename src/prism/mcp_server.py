"""The local stdio MCP adapter.

Four read-only tools, registered in a fixed order, with static descriptions and no
dynamic names — so a client can cache discovery and a tool description can never carry
user content.

The transport is stdio because the client launches the process: there is no listening
port, no OAuth, no token handling, and no remote exposure. Streamable HTTP is not merely
disabled here, it is absent from v1.

This module contains no analysis. It validates, delegates to ``PrismService``, and
converts typed failures into structured results. A returned error is a *value*, not an
exception trace: the caller gets a stable code, retryability, the affected component, a
safe next action, and a content-free request id.
"""

from __future__ import annotations

import sys
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .canonical import canonical_json
from .constants import MCP_TOOL_NAMES
from .contracts import (
    MeasureReport,
    MeasureRequest,
    PreflightReport,
    PreflightRequest,
    PrismMode,
    parse_payload,
)
from .errors import ErrorCode, PrismError
from .service import PrismService
from .telemetry import new_request_id
from .version import MCP_PROTOCOL_VERSION, PACKAGE_VERSION

#: Every tool is read-only, non-destructive, idempotent, and closed-world. These
#: annotations are what a host uses to decide how much trust a tool call needs.
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

_service: PrismService | None = None


def service() -> PrismService:
    """Lazily construct the service so that a registry fault surfaces as a typed tool
    error rather than a crash during server start."""
    global _service
    if _service is None:
        _service = PrismService.from_default_bundle()
    return _service


def _error_result(error: PrismError) -> dict[str, Any]:
    return error.to_report(request_id=new_request_id()).model_dump(mode="json")


def _guard(operation: str, call: Any) -> dict[str, Any]:
    """Run a tool body and convert any failure into a typed structured result."""
    try:
        return dict(call())
    except PrismError as error:
        return _error_result(error)
    except Exception as exc:
        return _error_result(
            PrismError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"The {operation} tool failed unexpectedly.",
                diagnostics={"error_type": type(exc).__name__},
            )
        )


def build_server() -> MCPServer:
    """Construct the server with its four tools in deterministic registration order."""
    server = MCPServer(
        name="prism",
        version=PACKAGE_VERSION,
        instructions=(
            "PRISM structures multi-perspective analysis and measures contradictions "
            "between claims. Call prism.preflight before analysing a complex task, "
            "produce one compact claim packet per returned perspective in a single pass, "
            "call prism.measure, then call prism.synthesis_contract before writing the "
            "final answer. PRISM measures conflict; it does not establish truth."
        ),
    )

    @server.tool(
        name=MCP_TOOL_NAMES[0],
        title="PRISM preflight",
        description=(
            "Select 3-5 useful perspectives for a task and return the claim-packet "
            "contract the host should follow. Deterministic and offline. Reads packaged "
            "registry data only: no user-project files, no network, no credentials."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def prism_preflight(
        task: str,
        mode: str = "standard",
        max_perspectives: int | None = None,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            request = PreflightRequest(
                task=task, mode=PrismMode(mode), max_perspectives=max_perspectives
            )
            return service().preflight(request).model_dump(mode="json")

        return _guard("preflight", run)

    @server.tool(
        name=MCP_TOOL_NAMES[1],
        title="PRISM measure",
        description=(
            "Measure contradictions, scope divergence, duplicates, and internal conflicts "
            "across 2-5 candidate claim packets using local CPU encoders. Bounded runtime. "
            "Reads only the verified local model bundle and the supplied input; mutates "
            "nothing and reaches no network. Reports conflict, never truth."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def prism_measure(request: dict[str, Any]) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            return service().measure(parse_payload(MeasureRequest, request)).model_dump(mode="json")

        return _guard("measure", run)

    @server.tool(
        name=MCP_TOOL_NAMES[2],
        title="PRISM synthesis contract",
        description=(
            "Return the rules the host must follow when writing the final answer: which "
            "conflicts to disclose, which distinct claims to preserve, and which "
            "shortcuts are prohibited. Deterministic; generates no prose."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def prism_synthesis_contract(
        preflight: dict[str, Any] | None = None,
        measurement: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            return (
                service()
                .synthesis_contract(
                    parse_payload(PreflightReport, preflight) if preflight else None,
                    parse_payload(MeasureReport, measurement) if measurement else None,
                )
                .model_dump(mode="json")
            )

        return _guard("synthesis_contract", run)

    @server.tool(
        name=MCP_TOOL_NAMES[3],
        title="PRISM health",
        description=(
            "Report local health. Shallow mode checks contracts and the perspective "
            "registry. Deep mode additionally verifies model artifact hashes, asserts the "
            "CPU execution provider, and runs one synthetic inference. Never scans the "
            "user's project or environment."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    def prism_health(deep: bool = False) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            return service().health(deep=deep).model_dump(mode="json")

        return _guard("health", run)

    return server


def main() -> int:
    """Entry point for the ``prism-mcp`` executable.

    Production MCP configuration invokes this installed, version-pinned executable
    directly. Runtime `uvx`, `npx`, mutable tags, and network bootstrap are prohibited
    (trust boundary 3).
    """
    server = build_server()
    try:
        # Synchronous by design: importing anyio directly would add a seventh runtime
        # dependency for something the SDK already wraps.
        server.run(transport="stdio")
    except (BrokenPipeError, KeyboardInterrupt):
        # The client went away. Exit quietly: no traceback, no content.
        return 0
    except PrismError as error:
        print(canonical_json(_error_result(error)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MCP_PROTOCOL_VERSION", "build_server", "main"]
