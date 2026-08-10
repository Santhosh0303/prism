"""Version and compatibility identifiers.

Every behaviourally relevant version is surfaced in reports (architecture invariant A6:
no hidden state) and in the compatibility declarations that host skills check against
(invariant A22: compatibility is explicit, not inferred).
"""

from __future__ import annotations

from typing import Final

#: Distribution version. Track A functional completeness; not a production release claim.
PACKAGE_VERSION: Final[str] = "0.1.0"

#: Public contract version carried by every request and report.
#: Patch releases do not change public schemas. Minor releases must accept the previous
#: supported minor or return VERSION_MISMATCH. Major changes require explicit migration.
SCHEMA_VERSION: Final[str] = "1.0"

#: Oldest public schema this build still accepts on input.
MIN_SUPPORTED_SCHEMA_VERSION: Final[str] = "1.0"

#: Largest schema major this build can serve. A client above this receives VERSION_MISMATCH.
MAX_SUPPORTED_SCHEMA_MAJOR: Final[int] = 1

#: Model Context Protocol specification revision this server targets.
MCP_PROTOCOL_VERSION: Final[str] = "2026-07-28"

#: Audit baseline the implementation was built against. The three source documents are
#: hash-anchored in docs/verification-ledger.md. A release may not regress to an older
#: audit baseline (invariant A19, gate G21).
AUDIT_BASELINE_ID: Final[str] = "PRISM-AUDIT-2026-08-06-C"
AUDIT_BASELINE_DOC_VERSION: Final[str] = "1.3"


def version_info() -> dict[str, str | int]:
    """Return the version identifiers embedded in reports and health output."""
    return {
        "package_version": PACKAGE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "min_supported_schema_version": MIN_SUPPORTED_SCHEMA_VERSION,
        "max_supported_schema_major": MAX_SUPPORTED_SCHEMA_MAJOR,
        "mcp_protocol_version": MCP_PROTOCOL_VERSION,
        "audit_baseline_id": AUDIT_BASELINE_ID,
    }
