"""Frozen behavioural constants.

These are values the rest of the system reads but never negotiates. Anything a caller
may influence belongs in a request contract, not here. In particular the relevance floor
lives in ``prism.measure.pair`` as a compiled-in constant and is deliberately absent from
this module so that it cannot be reached through configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------

PACKAGE_NAME: Final[str] = "prism"

#: MCP tool names in their fixed registration order. The catalog is static and
#: deterministic so clients can cache discovery. These names are also asserted against
#: the host skill files, so a rename here cannot silently desynchronise an integration.
MCP_TOOL_NAMES: Final[tuple[str, ...]] = (
    "prism.preflight",
    "prism.measure",
    "prism.synthesis_contract",
    "prism.health",
)

# --------------------------------------------------------------------------------------
# Packaged data locations
# --------------------------------------------------------------------------------------

#: Repository root, resolved from this file. Used only to locate packaged read-only data.
_PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parent

#: The perspective registry is packaged read-only data, never user-project content.
REGISTRY_FILENAME: Final[str] = "registry.yaml"
REGISTRY_SCHEMA_FILENAME: Final[str] = "registry.schema.json"

#: Where the perspectives in a report came from. A closed vocabulary, not a path: the
#: distinction between packaged data and an operator's vendored lens set is contract
#: surface, while the operator's filesystem layout is not. Defined here so the loader and
#: the public contracts cannot drift to two different spellings of it.
RegistryOrigin = Literal["packaged", "override"]
PACKAGED_ORIGIN: Final[RegistryOrigin] = "packaged"
OVERRIDE_ORIGIN: Final[RegistryOrigin] = "override"

#: Dedicated read-only model root. Production deployment points this at a directory the
#: host sandbox mounts read-only. This is an application-level boundary, not an OS
#: sandbox.
MODEL_ROOT_ENV_VAR: Final[str] = "PRISM_MODEL_ROOT"
MODEL_MANIFEST_FILENAME: Final[str] = "manifest.json"

#: Kill switch. Disables all inference while leaving deterministic preflight available
#: It can never relax hash verification, path containment, source
#: grouping, duplicate suppression, or limits.
DISABLE_MEASURE_ENV_VAR: Final[str] = "PRISM_DISABLE_MEASURE"

# --------------------------------------------------------------------------------------
# Inference execution
# --------------------------------------------------------------------------------------

#: The only permitted ONNX Runtime execution provider. Asserted after session creation,
#: not merely requested: requesting a provider that is unavailable silently falls back.
REQUIRED_EXECUTION_PROVIDER: Final[str] = "CPUExecutionProvider"

# --------------------------------------------------------------------------------------
# Calibration state
# --------------------------------------------------------------------------------------

#: Emitted by every measurement report. Until a locked, human-labelled corpus has been
#: scored, the contradiction threshold is provisional: it may demonstrate that the
#: machinery runs, but it may NOT populate authoritative contradiction or agreement
#: fields. Those are suppressed and the provisional numbers appear only under
#: ``experimental_*`` (see prism.measure.calibration).
CALIBRATION_UNCALIBRATED: Final[str] = "UNCALIBRATED_PENDING_HUMAN_VALIDATION"
CALIBRATION_HUMAN_VALIDATED: Final[str] = "HUMAN_VALIDATED"

# --------------------------------------------------------------------------------------
# Canonical serialisation
# --------------------------------------------------------------------------------------

#: Separators used for every canonical digest. Deterministic across restart, locale,
#: time zone and hash seed.
CANONICAL_JSON_SEPARATORS: Final[tuple[str, str]] = (",", ":")

#: Decimal places used when serialising a rate. Fixed so that the same arithmetic yields
#: the same bytes on every platform.
RATE_DECIMAL_PLACES: Final[int] = 6
