"""The versioned perspective registry.

The registry is canonical data, not code. Code embeds no perspective prose except
fallback error messages. It is content-hashed so that a semantic
change to a lens is visible in every report that used it, and it is validated at load so
that a malformed registry fails a shallow health check rather than producing a quietly
degraded perspective set.

The registry file lives inside the package rather than at the repository root. A
root-level path is not carried by an installed wheel, and shipping a second copy would
create exactly the accidental redundancy that a single canonical definition exists to
prevent. There is one copy, and it is the one that gets installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from ..constants import CANONICAL_JSON_SEPARATORS, REGISTRY_FILENAME
from ..errors import ErrorCode, PrismError
from ..limits import MAX_CLAIMS_PER_CANDIDATE, MAX_REGISTRY_PERSPECTIVES

#: Override for tests and for operators who vendor their own lens set. The file is still
#: fully validated; an override cannot relax any rule below.
REGISTRY_PATH_ENV_VAR: Final[str] = "PRISM_REGISTRY_PATH"

_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d+\.\d+\.\d+$")

#: Risk tags a perspective may declare. A closed vocabulary keeps selection deterministic.
VALID_RISK_TAGS: Final[frozenset[str]] = frozenset({"critical", "adversarial", "constructive"})

#: These lenses must remain simultaneously selectable. Critical mode is required to
#: include security and red_team together, so an exclusion between any pair of them would
#: make a mandatory selection unsatisfiable.
CRITICAL_LENSES: Final[frozenset[str]] = frozenset({"security", "red_team", "performance"})


@dataclass(frozen=True, slots=True)
class PerspectiveDefinition:
    """One lens. Immutable once loaded."""

    id: str
    purpose: str
    questions: tuple[str, ...]
    mutually_exclusive_with: frozenset[str]
    claim_budget: int
    risk_tags: frozenset[str]

    @property
    def is_adversarial(self) -> bool:
        return "adversarial" in self.risk_tags

    @property
    def is_constructive(self) -> bool:
        return "constructive" in self.risk_tags


class PerspectiveRegistry:
    """A validated, content-hashed set of perspective definitions."""

    def __init__(
        self,
        version: str,
        perspectives: tuple[PerspectiveDefinition, ...],
        content_hash: str,
    ) -> None:
        self.version = version
        self._perspectives = perspectives
        self._by_id = {perspective.id: perspective for perspective in perspectives}
        self.content_hash = content_hash

    # -- access ------------------------------------------------------------------------

    @property
    def ids(self) -> tuple[str, ...]:
        """Registry order, which is deterministic and part of the contract."""
        return tuple(perspective.id for perspective in self._perspectives)

    def __len__(self) -> int:
        return len(self._perspectives)

    def __contains__(self, perspective_id: object) -> bool:
        return perspective_id in self._by_id

    def get(self, perspective_id: str) -> PerspectiveDefinition:
        try:
            return self._by_id[perspective_id]
        except KeyError:
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="A selection rule references a perspective that does not exist.",
                diagnostics={"missing_perspective": perspective_id},
            ) from None

    def all(self) -> tuple[PerspectiveDefinition, ...]:
        return self._perspectives

    # -- loading -----------------------------------------------------------------------

    @classmethod
    def default_path(cls) -> Path:
        override = os.environ.get(REGISTRY_PATH_ENV_VAR)
        if override:
            return Path(override)
        return Path(__file__).resolve().parent.parent / "perspectives" / REGISTRY_FILENAME

    @classmethod
    def load(cls, path: Path | None = None) -> PerspectiveRegistry:
        """Load, validate, and hash the registry.

        Raises:
            PrismError: ``CONFIG_INTEGRITY_FAILURE`` for any structural problem. The
                registry fails closed; there is no partial or repaired load.
        """
        source = path if path is not None else cls.default_path()
        if not source.is_file():
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The packaged perspective registry is missing.",
                diagnostics={"registry_filename": source.name},
            )
        raw_text = source.read_text(encoding="utf-8")
        document = cls._parse(raw_text)
        version = cls._validate_version(document)
        perspectives = cls._validate_perspectives(document)
        content_hash = cls._canonical_hash(version, perspectives)
        return cls(version=version, perspectives=perspectives, content_hash=content_hash)

    # -- validation --------------------------------------------------------------------

    @staticmethod
    def _parse(raw_text: str) -> dict[str, Any]:
        if "\t" in raw_text:
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The registry contains tab characters, which make YAML indentation "
                "ambiguous.",
            )
        try:
            # safe_load only: no arbitrary object construction from a data file.
            document = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The registry is not valid YAML.",
                diagnostics={"parser_error_type": type(exc).__name__},
            ) from None
        if not isinstance(document, dict):
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The registry root must be a mapping.",
            )
        if "*" in raw_text and "&" in raw_text:
            # Aliases would let one definition silently mutate another.
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The registry appears to use YAML anchors or aliases, which are "
                "not permitted.",
            )
        return document

    @staticmethod
    def _validate_version(document: dict[str, Any]) -> str:
        version = document.get("version")
        if not isinstance(version, str) or not _SEMVER_PATTERN.match(version):
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The registry version must be a semantic version string.",
                diagnostics={"declared_version": str(version)},
            )
        return version

    @classmethod
    def _validate_perspectives(cls, document: dict[str, Any]) -> tuple[PerspectiveDefinition, ...]:
        entries = document.get("perspectives")
        if not isinstance(entries, list) or not entries:
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The registry must declare a non-empty perspectives list.",
            )
        if len(entries) > MAX_REGISTRY_PERSPECTIVES:
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The registry declares more perspectives than the supported maximum.",
                diagnostics={"count": len(entries), "limit": MAX_REGISTRY_PERSPECTIVES},
            )

        definitions: list[PerspectiveDefinition] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            definition = cls._validate_entry(entry, index)
            if definition.id in seen:
                raise PrismError(
                    code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                    message="The registry declares a duplicate perspective id.",
                    diagnostics={"perspective_id": definition.id},
                )
            seen.add(definition.id)
            definitions.append(definition)

        cls._validate_exclusions(definitions, seen)
        cls._validate_balance(definitions)
        return tuple(definitions)

    @staticmethod
    def _validate_entry(entry: Any, index: int) -> PerspectiveDefinition:
        if not isinstance(entry, dict):
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="Every registry entry must be a mapping.",
                diagnostics={"entry_index": index},
            )

        def fail(reason: str) -> PrismError:
            return PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message=reason,
                diagnostics={"entry_index": index, "perspective_id": str(entry.get("id"))},
            )

        perspective_id = entry.get("id")
        if not isinstance(perspective_id, str) or not _ID_PATTERN.match(perspective_id):
            raise fail("A perspective id must be lowercase alphanumeric with underscores.")

        purpose = entry.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            raise fail("A perspective must declare a non-empty purpose.")

        questions = entry.get("questions")
        if not isinstance(questions, list) or not (1 <= len(questions) <= 8):
            raise fail("A perspective must declare between one and eight questions.")
        if not all(isinstance(question, str) and question.strip() for question in questions):
            raise fail("Every question must be a non-empty string.")

        claim_budget = entry.get("claim_budget")
        if not isinstance(claim_budget, int) or isinstance(claim_budget, bool):
            raise fail("claim_budget must be an integer.")
        if not 1 <= claim_budget <= MAX_CLAIMS_PER_CANDIDATE:
            raise fail(f"claim_budget must be between 1 and {MAX_CLAIMS_PER_CANDIDATE}.")

        exclusions = entry.get("mutually_exclusive_with", [])
        if not isinstance(exclusions, list) or not all(
            isinstance(other, str) for other in exclusions
        ):
            raise fail("mutually_exclusive_with must be a list of perspective ids.")
        if perspective_id in exclusions:
            raise fail("A perspective cannot exclude itself.")

        risk_tags = entry.get("risk_tags", [])
        if not isinstance(risk_tags, list) or not all(isinstance(tag, str) for tag in risk_tags):
            raise fail("risk_tags must be a list of strings.")
        unknown = set(risk_tags) - VALID_RISK_TAGS
        if unknown:
            raise fail(f"Unknown risk tags: {sorted(unknown)}.")

        return PerspectiveDefinition(
            id=perspective_id,
            purpose=purpose.strip(),
            questions=tuple(question.strip() for question in questions),
            mutually_exclusive_with=frozenset(exclusions),
            claim_budget=claim_budget,
            risk_tags=frozenset(risk_tags),
        )

    @staticmethod
    def _validate_exclusions(definitions: list[PerspectiveDefinition], known_ids: set[str]) -> None:
        by_id = {definition.id: definition for definition in definitions}
        for definition in definitions:
            for other_id in sorted(definition.mutually_exclusive_with):
                if other_id not in known_ids:
                    raise PrismError(
                        code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                        message="A perspective excludes an id that is not in the registry.",
                        diagnostics={"perspective_id": definition.id, "missing_id": other_id},
                    )
                if definition.id not in by_id[other_id].mutually_exclusive_with:
                    raise PrismError(
                        code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                        message="Mutual exclusion must be symmetric.",
                        diagnostics={"perspective_id": definition.id, "other_id": other_id},
                    )
                if definition.id in CRITICAL_LENSES and other_id in CRITICAL_LENSES:
                    raise PrismError(
                        code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                        message="Two critical lenses cannot exclude each other; critical mode "
                        "would become unsatisfiable.",
                        diagnostics={"perspective_id": definition.id, "other_id": other_id},
                    )

    @staticmethod
    def _validate_balance(definitions: list[PerspectiveDefinition]) -> None:
        """A registry with no adversarial lens cannot falsify anything, and one with no
        constructive lens cannot propose anything. Selection asserts both are available."""
        if not any(definition.is_adversarial for definition in definitions):
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The registry declares no adversarial perspective.",
            )
        if not any(definition.is_constructive for definition in definitions):
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="The registry declares no constructive perspective.",
            )

    # -- hashing -----------------------------------------------------------------------

    @staticmethod
    def _canonical_hash(version: str, perspectives: tuple[PerspectiveDefinition, ...]) -> str:
        """Hash the semantic content, not the file bytes.

        Reformatting the YAML must not change the hash; changing a question must. Sets are
        sorted so that the digest is stable across process restarts and hash seeds.
        """
        canonical = {
            "version": version,
            "perspectives": [
                {
                    "id": perspective.id,
                    "purpose": perspective.purpose,
                    "questions": list(perspective.questions),
                    "mutually_exclusive_with": sorted(perspective.mutually_exclusive_with),
                    "claim_budget": perspective.claim_budget,
                    "risk_tags": sorted(perspective.risk_tags),
                }
                for perspective in perspectives
            ],
        }
        encoded = json.dumps(
            canonical,
            separators=CANONICAL_JSON_SEPARATORS,
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
