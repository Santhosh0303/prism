"""Synthesis contract generation. Rules for the host's final answer, never the answer."""

from __future__ import annotations

from .contract import (
    FINAL_ANSWER_STRUCTURE,
    PROHIBITED_SHORTCUTS,
    build_synthesis_contract,
)

__all__ = ["FINAL_ANSWER_STRUCTURE", "PROHIBITED_SHORTCUTS", "build_synthesis_contract"]
