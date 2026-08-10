"""Scope classification.

Two claims can look contradictory while describing different worlds: "this scales fine"
and "this will not scale" are compatible if the first means a prototype and the second
means enterprise load. Excluding such pairs makes the contradiction rate honest.

The danger runs the other way too, and it is worse. A heuristic that over-detects scope
difference can quietly excuse a real contradiction. So the classifier is tri-state and
conservative:

* ``SCOPE_DIVERGENT`` — both claims carry explicit, differing markers on the *same*
  dimension. Only this state is excluded from the denominator.
* ``UNCERTAIN`` — one side carries a marker, or the signals conflict. **Stays in the
  denominator** and is reported.
* ``SAME_SCOPE`` — no differing markers.

Negation is checked before a marker is believed: "not in production" must not register as
a production-scope claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ..contracts import ScopeResult
from .pair import ClaimPair

#: Markers grouped by dimension. Two claims diverge only when they carry different values
#: on the same dimension; markers from different dimensions say nothing about each other.
SCOPE_MARKERS: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "lifecycle": {
        "prototype": ("prototype", "proof of concept", "poc", "spike", "draft", "mvp"),
        "beta": ("beta", "preview", "pilot", "staging"),
        "production": ("production", "live", "ga", "generally available"),
        "enterprise": ("enterprise", "enterprise grade", "large organisation", "fortune 500"),
    },
    "environment": {
        "local": ("local", "localhost", "on my machine", "developer machine", "laptop"),
        "cloud": ("cloud", "aws", "azure", "gcp", "hosted", "saas"),
        "offline": ("offline", "air gapped", "air-gapped", "disconnected"),
        "distributed": ("distributed", "multi node", "cluster", "federated"),
    },
    "scale": {
        "single": ("one user", "single user", "single tenant", "one request"),
        "team": ("team", "small team", "workgroup", "department"),
        "high": ("high throughput", "high load", "at scale", "millions of", "peak load"),
    },
    "time": {
        "current": ("currently", "today", "at present", "right now", "as of now"),
        "future": ("will", "future", "eventually", "roadmap", "planned", "later"),
        "past": ("previously", "historically", "used to", "in the past", "formerly"),
    },
    "platform": {
        "windows": ("windows", "win32", "powershell"),
        "linux": ("linux", "ubuntu", "debian", "posix"),
        "macos": ("macos", "mac os", "darwin", "osx"),
    },
    "certainty": {
        "possible": ("possible", "might", "could", "may", "potentially"),
        "required": ("must", "required", "mandatory", "shall"),
        "proven": ("proven", "measured", "demonstrated", "verified"),
    },
}

#: Only these dimensions may produce SCOPE_DIVERGENT.
#:
#: Divergence requires that the core proposition can otherwise coexist under the two
#: scopes, and that is what this set encodes. A lifecycle, environment, scale, or
#: platform difference genuinely describes two different worlds in which both claims
#: can hold.
#:
#: `time` and `certainty` do not. "The service is ready today" and "the service will fail"
#: differ in tense and modality, not in scope: they are a direct disagreement about the
#: same system. Treating that as scope divergence removed a real contradiction from the
#: denominator during integration testing — exactly the false-positive exclusion this
#: module exists to prevent. These dimensions can now reach UNCERTAIN at most, and
#: UNCERTAIN pairs stay in the denominator.
DIVERGENCE_CAPABLE_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {"lifecycle", "environment", "scale", "platform"}
)

#: A marker preceded by one of these within the negation window is not believed.
_NEGATIONS: Final[frozenset[str]] = frozenset(
    {"not", "no", "never", "without", "isn't", "aren't", "won't", "cannot", "can't", "nor"}
)

#: How many tokens before a marker are inspected for a negation.
_NEGATION_WINDOW: Final[int] = 3


@dataclass(frozen=True, slots=True)
class ScopeVerdict:
    result: ScopeResult
    dimension: str | None = None
    marker_a: str | None = None
    marker_b: str | None = None


def _token_positions(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text)


def _is_negated(tokens: list[str], index: int) -> bool:
    start = max(0, index - _NEGATION_WINDOW)
    return any(token in _NEGATIONS for token in tokens[start:index])


def detect_markers(matching_view: str) -> dict[str, set[str]]:
    """Return the believed scope values per dimension for one claim.

    A phrase marker matches on the raw view; a single-token marker matches on the token
    list so that "live" does not fire inside "delivery".
    """
    tokens = _token_positions(matching_view)
    found: dict[str, set[str]] = {}

    for dimension, values in SCOPE_MARKERS.items():
        for value, phrases in values.items():
            for phrase in phrases:
                if " " in phrase:
                    position = matching_view.find(phrase)
                    if position == -1:
                        continue
                    preceding = _token_positions(matching_view[:position])
                    if any(token in _NEGATIONS for token in preceding[-_NEGATION_WINDOW:]):
                        continue
                else:
                    indices = [i for i, token in enumerate(tokens) if token == phrase]
                    if not indices:
                        continue
                    if all(_is_negated(tokens, index) for index in indices):
                        continue
                found.setdefault(dimension, set()).add(value)
                break
    return found


def classify_scope(pair: ClaimPair) -> ScopeVerdict:
    """Classify one pair. Only an explicit, same-dimension disagreement is divergent."""
    markers_a = detect_markers(pair.a.matching_view)
    markers_b = detect_markers(pair.b.matching_view)

    if not markers_a and not markers_b:
        return ScopeVerdict(result=ScopeResult.SAME_SCOPE)

    # Dimensions are inspected in a fixed order so the reported dimension is deterministic.
    weak_disagreement = False
    for dimension in SCOPE_MARKERS:
        values_a = markers_a.get(dimension, set())
        values_b = markers_b.get(dimension, set())
        if not values_a or not values_b:
            continue
        if values_a == values_b:
            continue
        if values_a & values_b:
            # Overlapping values: the claims share at least one scope, so a difference in
            # the others is not evidence that they are talking past each other.
            continue
        if dimension not in DIVERGENCE_CAPABLE_DIMENSIONS:
            # A tense or modality difference is not a scope difference. Recorded, but it
            # cannot remove the pair from the denominator.
            weak_disagreement = True
            continue
        return ScopeVerdict(
            result=ScopeResult.SCOPE_DIVERGENT,
            dimension=dimension,
            marker_a=sorted(values_a)[0],
            marker_b=sorted(values_b)[0],
        )

    if weak_disagreement:
        return ScopeVerdict(result=ScopeResult.UNCERTAIN)

    shared_dimensions = set(markers_a) & set(markers_b)
    if shared_dimensions:
        return ScopeVerdict(result=ScopeResult.SAME_SCOPE)

    # One side is scoped and the other is not. That is exactly the case a keyword rule
    # gets wrong, so it stays in the denominator rather than being excused.
    return ScopeVerdict(
        result=ScopeResult.UNCERTAIN,
        dimension=sorted(set(markers_a) | set(markers_b))[0] if (markers_a or markers_b) else None,
    )
