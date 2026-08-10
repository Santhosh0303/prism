"""The agreement truth table.

Four labels, and none of them means "true". ``MULTI_SOURCE_AGREEMENT`` means several
declared source groups did not contradict each other on the pairs that were comparable.
That is a statement about the claims, not about the world (invariant A10 and A13).

``INDEPENDENT`` and ``CONVERGENT`` are deliberately absent. PRISM cannot establish either,
and an architecture gate fails the build if those strings reappear.

The table is evaluated top to bottom and the first match wins. The suppressing conditions
come first on purpose: it must be impossible to reach an agreement label by accident when
coverage, calibration, or provenance is insufficient.
"""

from __future__ import annotations

from typing import Final

from ..contracts import AgreementType, PrismStatus, SourceDiversity
from .calibration import REQUIRED_NLI_COVERAGE, is_calibrated

#: If more than this share of the denominator is scope-UNCERTAIN, the scope heuristic is
#: carrying too much of the result to call it agreement. Uncertain pairs stay in the
#: denominator either way; this only suppresses the label.
MAX_UNCERTAIN_SCOPE_SHARE: Final[float] = 0.5


def agreement_type(
    *,
    status: PrismStatus,
    contradiction_denominator: int,
    contradiction_count: int | None,
    nli_coverage: float | None,
    scope_uncertain_count: int,
    source_diversity: SourceDiversity,
) -> AgreementType:
    """Apply the fixed truth table.

    Returns ``UNCLEAR`` whenever the evidence cannot support a stronger statement. That is
    the safe direction: a false ``UNCLEAR`` costs the reader a little confidence, while a
    false agreement costs them the disagreement they needed to see.
    """
    # An uncalibrated threshold cannot support any agreement claim, because the count it
    # produced has no validated meaning.
    if not is_calibrated():
        return AgreementType.UNCLEAR

    if status in {PrismStatus.ERROR, PrismStatus.INSUFFICIENT}:
        return AgreementType.UNCLEAR

    if contradiction_denominator == 0 or contradiction_count is None:
        return AgreementType.UNCLEAR

    if nli_coverage is None or nli_coverage < REQUIRED_NLI_COVERAGE:
        # Unscored pairs are not evidence of compatibility.
        return AgreementType.UNCLEAR

    if scope_uncertain_count / contradiction_denominator > MAX_UNCERTAIN_SCOPE_SHARE:
        return AgreementType.UNCLEAR

    if contradiction_count > 0:
        return AgreementType.CONTESTED

    if source_diversity is SourceDiversity.MULTI_SOURCE:
        return AgreementType.MULTI_SOURCE_AGREEMENT

    return AgreementType.SINGLE_SOURCE_AGREEMENT
