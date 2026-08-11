"""Perspective selection.

Selection is finite, deterministic, and non-overlapping. It is not creative: there is no
persona generation, and no lens is invented at runtime.

The output is an ordered tuple. Order is part of the contract, because the golden tests
assert byte-identical preflight reports across runs, processes, and platforms.
"""

from __future__ import annotations

from collections.abc import Callable

from ..contracts import PrismMode
from ..errors import ErrorCode, PrismError
from .profiles import (
    GENERAL_FALLBACK_ORDER,
    MANDATORY_CRITICAL_LENSES,
    MODE_PERSPECTIVE_COUNT,
    MODE_PERSPECTIVE_FLOOR,
    PROFILE_PRIORITY,
    TaskProfile,
)
from .registry import PerspectiveRegistry


def target_count(mode: PrismMode, max_perspectives: int | None) -> int:
    """Resolve how many lenses to select.

    A caller may lower the count but never raise it above the mode's allowance: five is a
    hard ceiling and the mode already encodes the intended review depth.

    Critical is the exception, and deliberately so. Both host skills tell the user that
    critical means five lenses including ``security`` and ``red_team``; a caller passing
    ``max_perspectives=3`` used to get three, so the mode quietly stopped meaning what the
    documentation says it means — on the one mode chosen for security and irreversible
    actions. The floor wins over the caller's ceiling there. The override is recorded in
    the report's diagnostics rather than swallowed: see
    :func:`prism.preflight.contract.build_preflight_contract`.
    """
    allowed = MODE_PERSPECTIVE_COUNT[mode]
    if max_perspectives is None:
        return allowed
    return max(MODE_PERSPECTIVE_FLOOR.get(mode, 0), min(allowed, max_perspectives))


def select_perspectives(
    registry: PerspectiveRegistry,
    profile: TaskProfile,
    mode: PrismMode,
    max_perspectives: int | None = None,
) -> tuple[str, ...]:
    """Select 3 to 5 non-overlapping lenses for a profile and mode."""
    count = target_count(mode, max_perspectives)
    priority = _priority_order(registry, profile)
    rank = {perspective_id: index for index, perspective_id in enumerate(priority)}

    selected = _greedy_select(registry, priority, count)

    if mode is PrismMode.CRITICAL:
        selected = _force_mandatory(registry, selected, rank, count)

    selected = _ensure_balance(registry, selected, rank, count)

    if len(selected) != count:
        raise PrismError(
            code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
            message="The registry cannot supply the required number of compatible "
            "perspectives for this mode.",
            diagnostics={"required": count, "selected": len(selected), "profile": profile.value},
        )
    return tuple(sorted(selected, key=lambda pid: rank.get(pid, len(rank))))


def _priority_order(registry: PerspectiveRegistry, profile: TaskProfile) -> tuple[str, ...]:
    """Profile template, then the general fallback, then anything else in registry order.

    Duplicates are removed while preserving first appearance, so the profile's own
    preference always wins over the fallback.
    """
    ordered: list[str] = []
    for source in (
        PROFILE_PRIORITY.get(profile, ()),
        GENERAL_FALLBACK_ORDER,
        registry.ids,
    ):
        for perspective_id in source:
            if perspective_id in registry and perspective_id not in ordered:
                ordered.append(perspective_id)
    return tuple(ordered)


def _conflicts(registry: PerspectiveRegistry, candidate: str, chosen: list[str]) -> bool:
    """True when the candidate would duplicate the purpose of something already chosen."""
    definition = registry.get(candidate)
    for existing in chosen:
        if existing in definition.mutually_exclusive_with:
            return True
        if candidate in registry.get(existing).mutually_exclusive_with:
            return True
    return False


def _greedy_select(
    registry: PerspectiveRegistry, priority: tuple[str, ...], count: int
) -> list[str]:
    chosen: list[str] = []
    for perspective_id in priority:
        if len(chosen) == count:
            break
        if perspective_id in chosen:
            continue
        if _conflicts(registry, perspective_id, chosen):
            continue
        chosen.append(perspective_id)
    return chosen


def _force_mandatory(
    registry: PerspectiveRegistry,
    selected: list[str],
    rank: dict[str, int],
    count: int,
) -> list[str]:
    """Critical mode always includes security and red_team.

    If the profile template did not reach them, the lowest-priority non-mandatory lens is
    displaced rather than the set being grown past its mode allowance.
    """
    result = list(selected)
    for mandatory in MANDATORY_CRITICAL_LENSES:
        if mandatory in result:
            continue
        if mandatory not in registry:
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message="Critical mode requires a perspective the registry does not define.",
                diagnostics={"missing_perspective": mandatory},
            )
        displaceable = [pid for pid in result if pid not in MANDATORY_CRITICAL_LENSES]
        if len(result) >= count and displaceable:
            victim = max(displaceable, key=lambda pid: rank.get(pid, len(rank)))
            result.remove(victim)
        result.append(mandatory)
    return result


def _ensure_balance(
    registry: PerspectiveRegistry,
    selected: list[str],
    rank: dict[str, int],
    count: int,
) -> list[str]:
    """Guarantee at least one adversarial and one constructive lens.

    A set that can only propose cannot falsify, and a set that can only attack cannot
    build. Either failure would quietly halve the value of the review.
    """
    result = list(selected)
    requirements: tuple[tuple[Callable[[str], bool], str], ...] = (
        (lambda pid: registry.get(pid).is_adversarial, "adversarial"),
        (lambda pid: registry.get(pid).is_constructive, "constructive"),
    )
    for predicate, attribute in requirements:
        if any(predicate(pid) for pid in result):
            continue
        replacement = next(
            (
                pid
                for pid in sorted(registry.ids, key=lambda p: rank.get(p, len(rank)))
                if pid not in result and predicate(pid) and not _conflicts(registry, pid, result)
            ),
            None,
        )
        if replacement is None:
            raise PrismError(
                code=ErrorCode.CONFIG_INTEGRITY_FAILURE,
                message=f"The registry offers no compatible {attribute} perspective.",
                diagnostics={"attribute": attribute},
            )
        if len(result) >= count:
            protected = set(MANDATORY_CRITICAL_LENSES)
            displaceable = [
                pid for pid in result if pid not in protected and not predicate(pid)
            ] or [pid for pid in result if not predicate(pid)]
            victim = max(displaceable, key=lambda pid: rank.get(pid, len(rank)))
            result.remove(victim)
        result.append(replacement)
    return result
