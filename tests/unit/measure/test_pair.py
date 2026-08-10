"""Pair enumeration and the frozen relevance floor — plan Task 6, Steps 4 and 5.

Step 5 asks for an architecture assertion that inspects the module source to prove no
speed-floor dependency. That check is here and it reads the file, not a comment: a
docstring promising independence is not evidence of independence.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from prism.errors import ErrorCode, PrismError
from prism.limits import MAX_CROSS_CANDIDATE_PAIRS, MAX_INTERNAL_PAIRS
from prism.measure import pair as pair_module
from prism.measure.pair import RELEVANCE_FLOOR, apply_relevance, enumerate_pairs, pair_id_for

from .conftest import FILLER, make_packet, normalized

# --------------------------------------------------------------------------------------
# the speed floor must be unreachable from this module
# --------------------------------------------------------------------------------------


def test_pair_module_source_never_mentions_a_speed_floor() -> None:
    source = Path(inspect.getfile(pair_module)).read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    # The docstring explains why the floor is absent, so only non-comment code is checked
    # for an actual reference.
    assert "SPEED_FLOOR" not in executable.replace("speed-floor configuration", "").replace(
        "speed floor", ""
    )
    assert not hasattr(pair_module, "SPEED_FLOOR")


def test_enumerate_pairs_signature_takes_only_candidates() -> None:
    parameters = list(inspect.signature(enumerate_pairs).parameters)
    assert parameters == ["candidates"]


def test_relevance_floor_is_a_frozen_module_constant() -> None:
    assert isinstance(RELEVANCE_FLOOR, float)
    assert 0.0 < RELEVANCE_FLOOR < 1.0


def test_relevance_floor_is_not_reachable_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No environment variable may move the floor. A caller who could raise it could
    manufacture agreement by making fewer pairs comparable."""
    monkeypatch.setenv("PRISM_RELEVANCE_FLOOR", "0.99")
    monkeypatch.setenv("RELEVANCE_FLOOR", "0.99")
    import importlib

    importlib.reload(pair_module)
    assert pair_module.RELEVANCE_FLOOR == RELEVANCE_FLOOR


# --------------------------------------------------------------------------------------
# enumeration
# --------------------------------------------------------------------------------------


def test_cross_candidate_pairs_are_the_cartesian_product() -> None:
    candidates = normalized(
        [
            make_packet("a", [f"alpha {FILLER}", f"beta {FILLER}"]),
            make_packet("b", [f"gamma {FILLER}", f"delta {FILLER}"]),
        ]
    )
    pair_set = enumerate_pairs(candidates)
    assert pair_set.pairs_total == 4
    assert len({pair.pair_id for pair in pair_set.cross_pairs}) == 4


def test_no_pair_is_built_within_one_candidate_on_the_cross_axis() -> None:
    candidates = normalized(
        [
            make_packet("a", [f"alpha {FILLER}", f"beta {FILLER}"]),
            make_packet("b", [f"gamma {FILLER}"]),
        ]
    )
    for pair in enumerate_pairs(candidates).cross_pairs:
        assert pair.a.candidate_id != pair.b.candidate_id


def test_pair_id_is_independent_of_argument_order() -> None:
    candidates = normalized(
        [make_packet("a", [f"alpha {FILLER}"]), make_packet("b", [f"beta {FILLER}"])]
    )
    unit_a = candidates[0].units[0]
    unit_b = candidates[1].units[0]
    assert pair_id_for(unit_a, unit_b) == pair_id_for(unit_b, unit_a)


def test_maximum_legal_workload_is_exactly_the_cap() -> None:
    """Five candidates, four claims each. The cap must admit the legal maximum, not sit
    below it, or a valid request would be unservable."""
    candidates = normalized(
        [
            make_packet(f"c{index}", [f"word{index}{claim} {FILLER}" for claim in range(4)])
            for index in range(5)
        ]
    )
    pair_set = enumerate_pairs(candidates)
    assert pair_set.pairs_total == MAX_CROSS_CANDIDATE_PAIRS == 160


def test_internal_pairs_are_bounded() -> None:
    candidates = normalized(
        [make_packet(f"c{i}", [f"w{i}{j} {FILLER}" for j in range(4)]) for i in range(5)]
    )
    assert len(enumerate_pairs(candidates).internal_pairs) <= MAX_INTERNAL_PAIRS


# --------------------------------------------------------------------------------------
# relevance application
# --------------------------------------------------------------------------------------


def test_pairs_at_the_floor_are_relevant_and_below_are_not() -> None:
    candidates = normalized(
        [make_packet("a", [f"alpha {FILLER}"]), make_packet("b", [f"beta {FILLER}"])]
    )
    pairs = enumerate_pairs(candidates).cross_pairs
    at_floor = apply_relevance(pairs, (RELEVANCE_FLOOR,))
    below = apply_relevance(pairs, (RELEVANCE_FLOOR - 0.001,))
    assert at_floor[0].is_relevant
    assert not below[0].is_relevant


def test_a_pair_without_a_relevance_score_is_not_relevant() -> None:
    """An unscored pair must never be treated as comparable by default."""
    candidates = normalized(
        [make_packet("a", [f"alpha {FILLER}"]), make_packet("b", [f"beta {FILLER}"])]
    )
    assert not enumerate_pairs(candidates).cross_pairs[0].is_relevant


def test_misaligned_score_count_fails_loudly() -> None:
    candidates = normalized(
        [make_packet("a", [f"alpha {FILLER}"]), make_packet("b", [f"beta {FILLER}"])]
    )
    pairs = enumerate_pairs(candidates).cross_pairs
    with pytest.raises(PrismError) as excinfo:
        apply_relevance(pairs, (0.5, 0.6))
    assert excinfo.value.code is ErrorCode.INTERNAL_ERROR
