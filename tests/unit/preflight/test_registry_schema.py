"""The published registry schema must not drift from the loader that actually enforces it.

`registry.schema.json` exists so an operator vendoring a lens set through
`PRISM_REGISTRY_PATH` can validate it with ordinary tooling first. That is only useful if
the schema says the same thing the loader does. Two documents describing one format is
exactly the accidental redundancy PRISM refuses elsewhere, so it is permitted here only
with a test holding them together.

No JSON Schema engine is used. Pulling one in for a single data file would widen the
dependency surface to re-derive constraints that are already constants, so the schema is
cross-checked against those constants directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from prism.limits import MAX_CLAIMS_PER_CANDIDATE, MAX_REGISTRY_PERSPECTIVES
from prism.preflight.registry import (
    _ID_PATTERN,
    _SEMVER_PATTERN,
    VALID_RISK_TAGS,
    PerspectiveRegistry,
)

SCHEMA_PATH = Path(PerspectiveRegistry.default_path()).parent / "registry.schema.json"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    document = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


@pytest.fixture(scope="module")
def perspective_schema(schema: dict[str, Any]) -> dict[str, Any]:
    definition = schema["$defs"]["perspective"]
    assert isinstance(definition, dict)
    return definition


def test_schema_ships_beside_the_registry(schema: dict[str, Any]) -> None:
    """A schema left behind by packaging is worse than none: it would 404 for the operator
    it exists to serve."""
    assert SCHEMA_PATH.is_file()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_registry_bounds_match_the_code_limits(
    schema: dict[str, Any], perspective_schema: dict[str, Any]
) -> None:
    assert schema["properties"]["perspectives"]["maxItems"] == MAX_REGISTRY_PERSPECTIVES
    budget = perspective_schema["properties"]["claim_budget"]
    assert budget["maximum"] == MAX_CLAIMS_PER_CANDIDATE
    assert budget["minimum"] == 1


def test_risk_tag_vocabulary_matches_the_loader(perspective_schema: dict[str, Any]) -> None:
    declared = perspective_schema["properties"]["risk_tags"]["items"]["enum"]
    assert set(declared) == set(VALID_RISK_TAGS)


def test_patterns_match_the_loader(schema: dict[str, Any]) -> None:
    assert schema["properties"]["version"]["pattern"] == _SEMVER_PATTERN.pattern
    assert schema["$defs"]["perspectiveId"]["pattern"] == _ID_PATTERN.pattern


def test_required_fields_match_what_the_loader_demands(perspective_schema: dict[str, Any]) -> None:
    """`mutually_exclusive_with` is the one optional field; the loader defaults it to
    empty. Everything else is rejected when missing."""
    assert set(perspective_schema["required"]) == {
        "id",
        "purpose",
        "questions",
        "claim_budget",
        "risk_tags",
    }
    assert "mutually_exclusive_with" in perspective_schema["properties"]
    assert perspective_schema["additionalProperties"] is False


def test_shipped_registry_satisfies_the_published_schema(
    schema: dict[str, Any], perspective_schema: dict[str, Any]
) -> None:
    document = yaml.safe_load(PerspectiveRegistry.default_path().read_text(encoding="utf-8"))

    assert set(document) <= set(schema["properties"])
    assert _SEMVER_PATTERN.match(document["version"])

    entries = document["perspectives"]
    assert 1 <= len(entries) <= MAX_REGISTRY_PERSPECTIVES

    allowed = set(perspective_schema["properties"])
    required = set(perspective_schema["required"])
    questions_schema = perspective_schema["properties"]["questions"]

    for entry in entries:
        assert required <= set(entry), f"{entry.get('id')} is missing a required field"
        assert set(entry) <= allowed, f"{entry.get('id')} declares an unknown field"
        assert _ID_PATTERN.match(entry["id"])
        assert entry["purpose"].strip()
        assert (
            questions_schema["minItems"] <= len(entry["questions"]) <= questions_schema["maxItems"]
        )
        assert 1 <= entry["claim_budget"] <= MAX_CLAIMS_PER_CANDIDATE
        assert entry["risk_tags"], "risk_tags has minItems 1"
        assert len(set(entry["risk_tags"])) == len(entry["risk_tags"]), "uniqueItems"
        assert set(entry["risk_tags"]) <= set(VALID_RISK_TAGS)


def test_schema_and_loader_agree_that_the_registry_loads() -> None:
    """The schema can only ever be necessary, never sufficient: this is the real gate."""
    registry = PerspectiveRegistry.load()
    assert len(registry) >= 1
    assert registry.content_hash.startswith("sha256:")
