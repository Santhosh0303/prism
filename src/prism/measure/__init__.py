"""Offline contradiction measurement.

Two CPU encoders, in strict order. E1 decides whether two claims are about the same
subject; E2 decides whether same-subject claims disagree. Embedding similarity never
becomes an agreement score (ADR-005), and the relevance stage is not an optimisation —
without it the NLI model confidently labels unrelated sentences as contradictory.
"""

from __future__ import annotations

from .agreement import agreement_type
from .calibration import calibration_status, contradiction_threshold, is_calibrated
from .contradiction import LedgerEntry, PairLedger, build_ledger, detect_internal_conflicts
from .models import ModelManifest, ModelSessions, measurement_disabled, verify_model_bundle
from .pair import RELEVANCE_FLOOR, ClaimPair, PairSet, enumerate_pairs
from .project import build_measure_report
from .retention import retain_distinct_claims
from .scope import ScopeVerdict, classify_scope
from .segment import (
    ClaimUnit,
    NormalizedCandidate,
    find_duplicate_candidates,
    normalize_claims,
    segment_plain_text,
)

__all__ = [
    "RELEVANCE_FLOOR",
    "ClaimPair",
    "ClaimUnit",
    "LedgerEntry",
    "ModelManifest",
    "ModelSessions",
    "NormalizedCandidate",
    "PairLedger",
    "PairSet",
    "ScopeVerdict",
    "agreement_type",
    "build_ledger",
    "build_measure_report",
    "calibration_status",
    "classify_scope",
    "contradiction_threshold",
    "detect_internal_conflicts",
    "enumerate_pairs",
    "find_duplicate_candidates",
    "is_calibrated",
    "measurement_disabled",
    "normalize_claims",
    "retain_distinct_claims",
    "segment_plain_text",
    "verify_model_bundle",
]
