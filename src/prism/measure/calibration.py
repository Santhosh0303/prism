"""Contradiction threshold and calibration state.

The threshold that separates "contradiction" from "not contradiction" is the single
number that most directly determines what PRISM reports. Design section 6.9 says it may
be fitted **only** on locked, human-labelled calibration pairs, and implementation plan
Task 16 forbids any agent from writing, paraphrasing, expanding, or labelling those seeds.

No such corpus exists yet. So this module does the only honest thing available:

* it uses a *provisional* threshold, chosen as the natural decision boundary of a
  three-way softmax rather than fitted to anything;
* it reports ``UNCALIBRATED_PENDING_HUMAN_VALIDATION`` in every measurement;
* and the report contract refuses to publish authoritative contradiction counts or an
  agreement label while that status holds. The provisional numbers appear only under
  ``experimental_*``.

The provisional threshold is therefore allowed to demonstrate that the machinery runs.
It is not allowed to look like evidence about the world.

**Do not tune this constant.** Tuning it without a locked corpus would be fitting to
whatever examples happened to be at hand, which is precisely the failure the seed-lock
rule exists to prevent.
"""

from __future__ import annotations

from typing import Final

from ..constants import CALIBRATION_HUMAN_VALIDATED, CALIBRATION_UNCALIBRATED

#: Natural argmax boundary for a three-class softmax. Not fitted, not tuned, not
#: validated against any labelled set.
PROVISIONAL_CONTRADICTION_THRESHOLD: Final[float] = 0.5

#: Production requires every same-scope denominator pair to be scored. Anything less and
#: the agreement label is suppressed, because unscored pairs would otherwise be
#: indistinguishable from pairs that were checked and found compatible.
REQUIRED_NLI_COVERAGE: Final[float] = 1.0

#: Set to True only when a locked, human-labelled corpus has been scored once, with the
#: second-labeller agreement and pair-level precision/recall/F1/MCC published. Flipping
#: this without that evidence is the specific dishonesty this module is designed to make
#: difficult.
_HUMAN_VALIDATED: Final[bool] = False


def calibration_status() -> str:
    """The calibration state carried by every measurement report."""
    return CALIBRATION_HUMAN_VALIDATED if _HUMAN_VALIDATED else CALIBRATION_UNCALIBRATED


def is_calibrated() -> bool:
    return _HUMAN_VALIDATED


def contradiction_threshold() -> float:
    return PROVISIONAL_CONTRADICTION_THRESHOLD
