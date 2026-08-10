"""Distinct-claim retention.

A claim that nobody else engaged with is the one most likely to be dropped during
synthesis, and it is also the one most likely to be the risk nobody else noticed. This
module finds those claims so the synthesis contract can require the host to keep them.

The field is called ``retained_distinct_claims`` and not "correct minority" for a reason
that matters: PRISM cannot verify truth, so it cannot know whether a lone claim is right.
**Retention is not endorsement** (design section 6.13). It says only that this claim was
unopposed and carries one of four declared kinds of content worth preserving.

The four reasons are a closed set. A claim that matches none is not retained, because a
catch-all reason would make retention meaningless.
"""

from __future__ import annotations

import re
from typing import Final

from ..contracts import EvidenceStatus, RetainedClaim, RetentionReason
from .contradiction import PairLedger
from .segment import NormalizedCandidate

#: Vocabulary for a specific, named way something breaks. Deliberately concrete: "risk"
#: and "issue" are excluded because they name no mechanism.
_FAILURE_TERMS: Final[frozenset[str]] = frozenset(
    {
        "fails",
        "fail",
        "failure",
        "breaks",
        "break",
        "crash",
        "crashes",
        "outage",
        "corrupt",
        "corruption",
        "deadlock",
        "starvation",
        "leak",
        "leaks",
        "overflow",
        "race",
        "timeout",
        "timeouts",
        "exhaustion",
        "unavailable",
        "downtime",
        "regression",
        "rollback",
        "data loss",
        "silently",
    }
)

#: Parties who bear a consequence. A claim naming one of these has considered someone the
#: other lenses may not have.
_STAKEHOLDER_TERMS: Final[frozenset[str]] = frozenset(
    {
        "user",
        "users",
        "customer",
        "customers",
        "operator",
        "operators",
        "auditor",
        "auditors",
        "regulator",
        "regulators",
        "maintainer",
        "maintainers",
        "reviewer",
        "on-call",
        "oncall",
        "administrator",
        "administrators",
        "client",
        "clients",
        "patient",
        "patients",
        "student",
        "students",
        "employee",
        "employees",
    }
)

#: Causal connectives. A claim that states a mechanism is worth more than one that states
#: a conclusion.
_CAUSAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(because|causes?|caused by|leads? to|results? in|due to|so that|therefore|"
    r"which means|driven by|triggers?)\b"
)

_WORD_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z'-]*")


def _tokens(text: str) -> set[str]:
    return set(_WORD_PATTERN.findall(text))


def retain_distinct_claims(
    candidates: tuple[NormalizedCandidate, ...],
    ledger: PairLedger,
) -> tuple[RetainedClaim, ...]:
    """Retain unopposed claims that carry declared distinct content.

    A claim is a retention candidate when no other candidate produced a *relevant* pair
    with it: nobody addressed the same subject, so synthesis has nothing to merge it into
    and will tend to drop it.
    """
    engaged: set[tuple[str, str]] = set()
    for entry in ledger.entries:
        engaged.add((entry.candidate_a_id, entry.claim_a_id))
        engaged.add((entry.candidate_b_id, entry.claim_b_id))

    # Stakeholders named anywhere else; a stakeholder is only "unmentioned" if no other
    # candidate raised them.
    mentions: dict[str, set[str]] = {}
    for candidate in candidates:
        for unit in candidate.units:
            for token in _tokens(unit.matching_view) & _STAKEHOLDER_TERMS:
                mentions.setdefault(token, set()).add(candidate.candidate_id)

    retained: list[RetainedClaim] = []
    for candidate in candidates:
        for unit in candidate.units:
            if (candidate.candidate_id, unit.claim_id) in engaged:
                continue
            reason = _reason_for(
                unit.matching_view, unit.evidence_status, candidate.candidate_id, mentions
            )
            if reason is None:
                continue
            retained.append(
                RetainedClaim(
                    claim_id=unit.claim_id,
                    candidate_id=candidate.candidate_id,
                    reason=reason,
                    note=_NOTES[reason],
                )
            )
    return tuple(retained)


#: Fixed, non-endorsing wording. These strings go into the synthesis contract, so they
#: must not read as agreement with the claim.
_NOTES: Final[dict[RetentionReason, str]] = {
    RetentionReason.NAMED_FAILURE_MODE: (
        "Names a specific failure mode no other candidate addressed. Unverified."
    ),
    RetentionReason.UNMENTIONED_STAKEHOLDER: (
        "Raises a party no other candidate mentioned. Unverified."
    ),
    RetentionReason.UNIQUE_EVIDENCE: (
        "Declares observed or cited evidence no other candidate engaged with. "
        "Evidence status is declared by the host and unverified by PRISM."
    ),
    RetentionReason.DISTINCT_CAUSAL_MECHANISM: (
        "States a causal mechanism no other candidate addressed. Unverified."
    ),
}


def _reason_for(
    view: str,
    evidence_status: EvidenceStatus,
    candidate_id: str,
    mentions: dict[str, set[str]],
) -> RetentionReason | None:
    """First matching reason in a fixed order, so retention is deterministic."""
    tokens = _tokens(view)

    if tokens & _FAILURE_TERMS or any(term in view for term in ("data loss", "silently")):
        return RetentionReason.NAMED_FAILURE_MODE

    for stakeholder in sorted(tokens & _STAKEHOLDER_TERMS):
        if mentions.get(stakeholder, set()) == {candidate_id}:
            return RetentionReason.UNMENTIONED_STAKEHOLDER

    if evidence_status in {EvidenceStatus.OBSERVED, EvidenceStatus.CITED}:
        return RetentionReason.UNIQUE_EVIDENCE

    if _CAUSAL_PATTERN.search(view):
        return RetentionReason.DISTINCT_CAUSAL_MECHANISM

    return None
