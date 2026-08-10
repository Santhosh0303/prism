"""Registry loading and integrity.

The registry is the only place perspective prose lives. If it can be loaded in a broken
state, every downstream selection is quietly wrong, so every structural rule fails closed
with CONFIG_INTEGRITY_FAILURE rather than being repaired.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from prism.errors import ErrorCode, PrismError
from prism.preflight.registry import PerspectiveRegistry

EXPECTED_IDS = (
    "systems",
    "security",
    "performance",
    "reliability",
    "maintainability",
    "user",
    "business",
    "evidence",
    "methodology",
    "governance",
    "cost",
    "operations",
    "red_team",
)


def write_registry(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def minimal_document() -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "perspectives": [
            {
                "id": "systems",
                "purpose": "Check boundaries.",
                "questions": ["Which dependency can fail?"],
                "mutually_exclusive_with": [],
                "claim_budget": 3,
                "risk_tags": ["constructive"],
            },
            {
                "id": "red_team",
                "purpose": "Attempt to falsify.",
                "questions": ["Which assumption breaks?"],
                "mutually_exclusive_with": [],
                "claim_budget": 4,
                "risk_tags": ["adversarial"],
            },
        ],
    }


# --------------------------------------------------------------------------------------
# the shipped registry
# --------------------------------------------------------------------------------------


def test_packaged_registry_loads() -> None:
    registry = PerspectiveRegistry.load()
    assert registry.version == "1.0.0"
    assert registry.ids == EXPECTED_IDS


def test_packaged_registry_hash_is_stable_across_loads() -> None:
    first = PerspectiveRegistry.load().content_hash
    second = PerspectiveRegistry.load().content_hash
    assert first == second
    assert first.startswith("sha256:")


def test_every_perspective_declares_exactly_one_orientation() -> None:
    """Selection asserts a chosen set has both an adversarial and a constructive lens,
    so a lens that is neither could never satisfy either requirement."""
    for definition in PerspectiveRegistry.load().all():
        assert definition.is_adversarial != definition.is_constructive, definition.id


def test_security_and_red_team_are_not_mutually_exclusive() -> None:
    """Critical mode requires both. An exclusion would make it unsatisfiable."""
    registry = PerspectiveRegistry.load()
    assert "red_team" not in registry.get("security").mutually_exclusive_with
    assert "security" not in registry.get("red_team").mutually_exclusive_with


# --------------------------------------------------------------------------------------
# content hashing
# --------------------------------------------------------------------------------------


def test_hash_tracks_semantic_content_not_file_formatting(tmp_path: Path) -> None:
    """Reformatting the YAML must not move the hash; changing a question must.

    This is what lets a report's registry_hash mean "these lenses", rather than "this
    file happened to have these bytes".
    """
    document = minimal_document()

    plain = tmp_path / "plain.yaml"
    plain.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    baseline = PerspectiveRegistry.load(plain).content_hash

    # Same content, deliberately different serialisation: flow style, wide lines,
    # alphabetised keys.
    restyled = tmp_path / "restyled.yaml"
    restyled.write_text(
        yaml.safe_dump(document, sort_keys=True, default_flow_style=False, width=200, indent=6),
        encoding="utf-8",
    )
    assert PerspectiveRegistry.load(restyled).content_hash == baseline

    changed = yaml.safe_load(yaml.safe_dump(document))
    changed["perspectives"][0]["questions"] = ["A materially different question?"]
    altered = tmp_path / "altered.yaml"
    altered.write_text(yaml.safe_dump(changed, sort_keys=False), encoding="utf-8")
    assert PerspectiveRegistry.load(altered).content_hash != baseline


# --------------------------------------------------------------------------------------
# rejection cases
# --------------------------------------------------------------------------------------


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PrismError) as excinfo:
        PerspectiveRegistry.load(tmp_path / "absent.yaml")
    assert excinfo.value.code is ErrorCode.CONFIG_INTEGRITY_FAILURE


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    document = minimal_document()
    document["perspectives"].append(dict(document["perspectives"][0]))
    with pytest.raises(PrismError) as excinfo:
        PerspectiveRegistry.load(write_registry(tmp_path, document))
    assert excinfo.value.code is ErrorCode.CONFIG_INTEGRITY_FAILURE


def test_unknown_exclusion_reference_is_rejected(tmp_path: Path) -> None:
    document = minimal_document()
    document["perspectives"][0]["mutually_exclusive_with"] = ["does_not_exist"]
    with pytest.raises(PrismError):
        PerspectiveRegistry.load(write_registry(tmp_path, document))


def test_asymmetric_exclusion_is_rejected(tmp_path: Path) -> None:
    document = minimal_document()
    document["perspectives"][0]["mutually_exclusive_with"] = ["red_team"]
    with pytest.raises(PrismError) as excinfo:
        PerspectiveRegistry.load(write_registry(tmp_path, document))
    assert "symmetric" in excinfo.value.message.lower()


def test_exclusion_between_two_critical_lenses_is_rejected(tmp_path: Path) -> None:
    document = minimal_document()
    document["perspectives"].append(
        {
            "id": "security",
            "purpose": "Find exploitable weakness.",
            "questions": ["What crosses a trust boundary?"],
            "mutually_exclusive_with": ["red_team"],
            "claim_budget": 4,
            "risk_tags": ["adversarial"],
        }
    )
    document["perspectives"][1]["mutually_exclusive_with"] = ["security"]
    with pytest.raises(PrismError) as excinfo:
        PerspectiveRegistry.load(write_registry(tmp_path, document))
    assert "critical" in excinfo.value.message.lower()


@pytest.mark.parametrize("budget", [0, 5, 99, -1])
def test_claim_budget_outside_one_to_four_is_rejected(tmp_path: Path, budget: int) -> None:
    document = minimal_document()
    document["perspectives"][0]["claim_budget"] = budget
    with pytest.raises(PrismError):
        PerspectiveRegistry.load(write_registry(tmp_path, document))


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "latest", "", "1.0.0-rc1"])
def test_non_semantic_version_is_rejected(tmp_path: Path, version: str) -> None:
    document = minimal_document()
    document["version"] = version
    with pytest.raises(PrismError):
        PerspectiveRegistry.load(write_registry(tmp_path, document))


def test_tabs_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text('version: "1.0.0"\nperspectives:\n\t- id: x\n', encoding="utf-8")
    with pytest.raises(PrismError) as excinfo:
        PerspectiveRegistry.load(path)
    assert "tab" in excinfo.value.message.lower()


def test_registry_without_adversarial_lens_is_rejected(tmp_path: Path) -> None:
    document = minimal_document()
    document["perspectives"][1]["risk_tags"] = ["constructive"]
    with pytest.raises(PrismError) as excinfo:
        PerspectiveRegistry.load(write_registry(tmp_path, document))
    assert "adversarial" in excinfo.value.message.lower()


def test_unknown_risk_tag_is_rejected(tmp_path: Path) -> None:
    document = minimal_document()
    document["perspectives"][0]["risk_tags"] = ["spicy"]
    with pytest.raises(PrismError):
        PerspectiveRegistry.load(write_registry(tmp_path, document))


def test_self_exclusion_is_rejected(tmp_path: Path) -> None:
    document = minimal_document()
    document["perspectives"][0]["mutually_exclusive_with"] = ["systems"]
    with pytest.raises(PrismError):
        PerspectiveRegistry.load(write_registry(tmp_path, document))


def test_unknown_perspective_lookup_is_typed() -> None:
    registry = PerspectiveRegistry.load()
    with pytest.raises(PrismError) as excinfo:
        registry.get("no_such_lens")
    assert excinfo.value.code is ErrorCode.CONFIG_INTEGRITY_FAILURE
