"""PRISM — a reasoning preflight and contradiction-measurement component.

PRISM sits in the orchestration layer before an AI system gives its final answer. It
selects a finite set of useful perspectives, asks the host AI to express each perspective
as compact claim packets, measures explicit contradictions and scope differences, and
returns a synthesis contract. The host AI then writes the final answer.

PRISM does not call an LLM, store tasks, authenticate users, browse the web, execute
code, or decide which claim is true.
"""

from __future__ import annotations

from .constants import MCP_TOOL_NAMES
from .errors import ErrorCode, PrismError, PrismErrorReport
from .version import PACKAGE_VERSION, SCHEMA_VERSION, version_info

__all__ = [
    "MCP_TOOL_NAMES",
    "PACKAGE_VERSION",
    "SCHEMA_VERSION",
    "ErrorCode",
    "PrismError",
    "PrismErrorReport",
    "version_info",
]

__version__ = PACKAGE_VERSION
