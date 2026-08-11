"""Task profiles, their lens priorities, and the deterministic classification signals.

Everything here is a table. There is no model, no embedding, and no learned weight, which
is what makes preflight reproducible, inspectable, free, and fast.

Adding or reweighting a signal is a semantic registry change: it alters routing, so it
must be accompanied by a golden-test diff review.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from ..contracts import PrismMode


class TaskProfile(StrEnum):
    SOFTWARE_CHANGE = "software_change"
    ARCHITECTURE = "architecture"
    SECURITY_REVIEW = "security_review"
    RESEARCH = "research"
    BUSINESS_DECISION = "business_decision"
    DATA_ANALYSIS = "data_analysis"
    INCIDENT = "incident"
    GENERAL = "general"


#: Perspective count by mode. The hard maximum is five (plan global constraints).
MODE_PERSPECTIVE_COUNT: Final[dict[PrismMode, int]] = {
    PrismMode.LITE: 3,
    PrismMode.STANDARD: 4,
    PrismMode.CRITICAL: 5,
}

#: Minimum a mode must deliver, whatever ``max_perspectives`` asks for. Only critical has
#: one: it is the mode documented as five lenses including security and red_team, and it
#: is the mode chosen for security and irreversible actions, so a caller-supplied ceiling
#: must not be able to hollow it out. Lite and standard carry no floor — lowering their
#: count is a preference, not a safety claim.
MODE_PERSPECTIVE_FLOOR: Final[dict[PrismMode, int]] = {
    PrismMode.CRITICAL: 5,
}

#: Critical mode always includes these, regardless of profile.
MANDATORY_CRITICAL_LENSES: Final[tuple[str, ...]] = ("security", "red_team")

#: Used when classification is ambiguous, and to fill any profile template that cannot
#: supply enough distinct lenses after exclusions.
GENERAL_FALLBACK_ORDER: Final[tuple[str, ...]] = (
    "systems",
    "user",
    "evidence",
    "red_team",
    "security",
)

#: Ordered lens preference per profile. The first three serve lite, the first four serve
#: standard, and the first five serve critical, subject to exclusions and the mandatory
#: critical lenses. Each list is deliberately longer than five so that removing an
#: excluded lens does not force a fallback.
PROFILE_PRIORITY: Final[dict[TaskProfile, tuple[str, ...]]] = {
    TaskProfile.SOFTWARE_CHANGE: (
        "systems",
        "security",
        "maintainability",
        "red_team",
        "reliability",
        "performance",
        "user",
    ),
    TaskProfile.ARCHITECTURE: (
        "systems",
        "security",
        "performance",
        "reliability",
        "red_team",
        "maintainability",
        "cost",
    ),
    TaskProfile.SECURITY_REVIEW: (
        "security",
        "red_team",
        "systems",
        "governance",
        "reliability",
        "operations",
        "evidence",
    ),
    TaskProfile.RESEARCH: (
        "evidence",
        "methodology",
        "governance",
        "red_team",
        "systems",
        "user",
        "security",
    ),
    TaskProfile.BUSINESS_DECISION: (
        "business",
        "cost",
        "operations",
        "red_team",
        "user",
        "governance",
        "security",
    ),
    TaskProfile.DATA_ANALYSIS: (
        "evidence",
        "methodology",
        "systems",
        "red_team",
        "governance",
        "user",
        "security",
    ),
    TaskProfile.INCIDENT: (
        "reliability",
        "operations",
        "systems",
        "red_team",
        "security",
        "evidence",
        "user",
    ),
    TaskProfile.GENERAL: (*GENERAL_FALLBACK_ORDER, "business", "reliability"),
}

#: Weighted indicators. Weight 3 is close to decisive on its own, 2 is a strong hint, 1 is
#: corroborating. Phrases containing a space are matched against the normalised text;
#: single tokens are matched against the token set, so "cost" does not fire on "costume".
PROFILE_INDICATORS: Final[dict[TaskProfile, tuple[tuple[str, int], ...]]] = {
    TaskProfile.SOFTWARE_CHANGE: (
        ("refactor", 3),
        ("pull request", 3),
        ("merge request", 3),
        ("patch", 2),
        ("bug", 2),
        ("bugfix", 3),
        ("regression", 2),
        ("implement", 2),
        ("function", 1),
        ("module", 1),
        ("api", 1),
        ("code", 1),
        ("test", 1),
        ("commit", 2),
        ("diff", 2),
        ("library", 1),
        ("dependency", 1),
    ),
    TaskProfile.ARCHITECTURE: (
        ("architecture", 3),
        ("architectural", 3),
        ("design document", 3),
        ("system design", 3),
        ("component", 2),
        ("boundary", 2),
        ("scalability", 2),
        ("microservice", 2),
        ("monolith", 2),
        ("trade off", 2),
        ("tradeoff", 2),
        ("coupling", 2),
        ("topology", 2),
        ("schema", 1),
        ("interface", 1),
        ("migration", 1),
    ),
    TaskProfile.SECURITY_REVIEW: (
        ("security review", 3),
        ("threat model", 3),
        ("vulnerability", 3),
        ("exploit", 3),
        ("penetration test", 3),
        ("attack surface", 3),
        ("cve", 2),
        ("authentication", 2),
        ("authorization", 2),
        ("authorisation", 2),
        ("encryption", 2),
        ("secret", 2),
        ("credential", 2),
        ("injection", 2),
        ("privilege", 2),
        ("hardening", 2),
    ),
    TaskProfile.RESEARCH: (
        ("literature", 3),
        ("hypothesis", 3),
        ("research question", 3),
        ("peer review", 3),
        ("citation", 2),
        ("evidence", 2),
        ("study", 2),
        ("paper", 2),
        ("methodology", 2),
        ("replication", 2),
        ("survey", 1),
        ("theory", 1),
        ("investigate", 1),
    ),
    TaskProfile.BUSINESS_DECISION: (
        ("business case", 3),
        ("roi", 3),
        ("pricing", 3),
        ("procurement", 3),
        ("budget", 2),
        ("revenue", 2),
        ("stakeholder", 2),
        ("vendor", 2),
        ("contract", 2),
        ("headcount", 2),
        ("market", 2),
        ("customer", 1),
        ("cost", 1),
        ("strategy", 1),
    ),
    TaskProfile.DATA_ANALYSIS: (
        ("dataset", 3),
        ("statistical", 3),
        ("correlation", 3),
        ("regression analysis", 3),
        ("sample size", 3),
        ("confidence interval", 3),
        ("metric", 2),
        ("baseline", 1),
        ("distribution", 2),
        ("outlier", 2),
        ("query", 1),
        ("dashboard", 1),
        ("data", 1),
    ),
    TaskProfile.INCIDENT: (
        ("incident", 3),
        ("outage", 3),
        ("postmortem", 3),
        ("post mortem", 3),
        ("root cause", 3),
        ("on call", 2),
        ("oncall", 2),
        ("degradation", 2),
        ("rollback", 2),
        ("alert", 2),
        ("downtime", 3),
        ("mitigation", 2),
        ("severity", 2),
        ("production down", 3),
    ),
}

#: Deterministic tie-break. When two profiles score identically, the earlier entry wins.
#: Ordered so that the more specific and higher-consequence reading is preferred: an
#: incident or security concern should not be reclassified as a routine change.
TIE_BREAK_ORDER: Final[tuple[TaskProfile, ...]] = (
    TaskProfile.INCIDENT,
    TaskProfile.SECURITY_REVIEW,
    TaskProfile.ARCHITECTURE,
    TaskProfile.DATA_ANALYSIS,
    TaskProfile.RESEARCH,
    TaskProfile.BUSINESS_DECISION,
    TaskProfile.SOFTWARE_CHANGE,
    TaskProfile.GENERAL,
)

#: A profile must reach this score to be selected at all.
MIN_WINNING_SCORE: Final[int] = 3

#: The winner must beat the runner-up by this margin. Below it the task is genuinely
#: ambiguous and is routed to the general fallback with LOW confidence rather than
#: guessing.
MIN_WINNING_MARGIN: Final[int] = 2

#: Above this score, and with the required margin, classification is reported as HIGH.
HIGH_CONFIDENCE_SCORE: Final[int] = 6
