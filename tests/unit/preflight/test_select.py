"""Perspective selection.

Selection must be finite, deterministic, non-duplicating, and balanced. Four worked
examples are asserted directly so that a routing table edit cannot drift away from the
declared behaviour unnoticed.
"""

from __future__ import annotations

import pytest

from prism.contracts import PrismMode
from prism.preflight.profiles import MODE_PERSPECTIVE_COUNT, TaskProfile
from prism.preflight.registry import PerspectiveRegistry
from prism.preflight.select import select_perspectives, target_count


@pytest.fixture(scope="module")
def registry() -> PerspectiveRegistry:
    return PerspectiveRegistry.load()


# --------------------------------------------------------------------------------------
# the worked routing examples
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "mode", "expected"),
    [
        (
            TaskProfile.SOFTWARE_CHANGE,
            PrismMode.STANDARD,
            ("systems", "security", "maintainability", "red_team"),
        ),
        (
            TaskProfile.ARCHITECTURE,
            PrismMode.CRITICAL,
            ("systems", "security", "performance", "reliability", "red_team"),
        ),
        (
            TaskProfile.RESEARCH,
            PrismMode.STANDARD,
            ("evidence", "methodology", "governance", "red_team"),
        ),
        (
            TaskProfile.BUSINESS_DECISION,
            PrismMode.STANDARD,
            ("business", "cost", "operations", "red_team"),
        ),
    ],
)
def test_documented_routing_examples(
    registry: PerspectiveRegistry,
    profile: TaskProfile,
    mode: PrismMode,
    expected: tuple[str, ...],
) -> None:
    assert select_perspectives(registry, profile, mode) == expected


# --------------------------------------------------------------------------------------
# cardinality
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(PrismMode))
@pytest.mark.parametrize("profile", list(TaskProfile))
def test_selection_size_matches_mode(
    registry: PerspectiveRegistry, profile: TaskProfile, mode: PrismMode
) -> None:
    selected = select_perspectives(registry, profile, mode)
    assert len(selected) == MODE_PERSPECTIVE_COUNT[mode]
    assert len(set(selected)) == len(selected), "selection must not contain duplicates"


@pytest.mark.parametrize("mode", list(PrismMode))
def test_never_exceeds_the_hard_maximum_of_five(
    registry: PerspectiveRegistry, mode: PrismMode
) -> None:
    for profile in TaskProfile:
        assert len(select_perspectives(registry, profile, mode)) <= 5


def test_caller_may_lower_but_not_raise_the_count() -> None:
    assert target_count(PrismMode.CRITICAL, 3) == 3
    assert target_count(PrismMode.LITE, 5) == 3, "the mode allowance is a ceiling"
    assert target_count(PrismMode.STANDARD, None) == 4


# --------------------------------------------------------------------------------------
# mandatory lenses and balance
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", list(TaskProfile))
def test_critical_mode_always_includes_security_and_red_team(
    registry: PerspectiveRegistry, profile: TaskProfile
) -> None:
    selected = select_perspectives(registry, profile, PrismMode.CRITICAL)
    assert "security" in selected, profile
    assert "red_team" in selected, profile


@pytest.mark.parametrize("mode", list(PrismMode))
@pytest.mark.parametrize("profile", list(TaskProfile))
def test_every_selection_is_balanced(
    registry: PerspectiveRegistry, profile: TaskProfile, mode: PrismMode
) -> None:
    """A set that can only propose cannot falsify; one that can only attack cannot build."""
    selected = select_perspectives(registry, profile, mode)
    definitions = [registry.get(pid) for pid in selected]
    assert any(d.is_adversarial for d in definitions), (profile, mode, selected)
    assert any(d.is_constructive for d in definitions), (profile, mode, selected)


@pytest.mark.parametrize("profile", list(TaskProfile))
def test_every_selected_perspective_exists_and_is_budgeted(
    registry: PerspectiveRegistry, profile: TaskProfile
) -> None:
    for pid in select_perspectives(registry, profile, PrismMode.CRITICAL):
        definition = registry.get(pid)
        assert 1 <= definition.claim_budget <= 4
        assert definition.questions


# --------------------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------------------


def test_selection_is_stable_across_repeated_calls(registry: PerspectiveRegistry) -> None:
    results = {
        select_perspectives(registry, TaskProfile.ARCHITECTURE, PrismMode.CRITICAL)
        for _ in range(100)
    }
    assert len(results) == 1


def test_no_mutually_exclusive_pair_is_ever_selected_together(
    registry: PerspectiveRegistry,
) -> None:
    for profile in TaskProfile:
        for mode in PrismMode:
            selected = set(select_perspectives(registry, profile, mode))
            for pid in selected:
                overlap = registry.get(pid).mutually_exclusive_with & selected
                assert not overlap, f"{pid} selected alongside {overlap}"
