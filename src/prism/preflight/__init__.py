"""Deterministic preflight: classification, selection, and the host execution contract.

This subpackage must never import an inference runtime. Preflight has to work when the
model bundle is absent, corrupt, or disabled (invariant A9, fitness function 1), and an
import-linter contract fails the build if that boundary is crossed.
"""

from __future__ import annotations

from .classify import Classification, classify_task
from .contract import build_preflight_contract
from .profiles import TaskProfile
from .registry import PerspectiveDefinition, PerspectiveRegistry
from .select import select_perspectives, target_count

__all__ = [
    "Classification",
    "PerspectiveDefinition",
    "PerspectiveRegistry",
    "TaskProfile",
    "build_preflight_contract",
    "classify_task",
    "select_perspectives",
    "target_count",
]
