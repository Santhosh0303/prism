# ADR-007: Integrations carry no logic

**Status:** Accepted

## Context

The Claude Code and Codex skills could each include helper logic, adjusted thresholds, or a
tailored workflow.

## Decision

Integrations contain workflow instructions and configuration only. No thresholds, no
algorithms, no core logic, in either one.

## Consequences

One tested core behaves identically through the Python API, the CLI, MCP, and both skills.
Adding a third host becomes writing a document rather than porting code, and a measurement
cannot change depending on which host asked for it.

`tests/compatibility/test_skill_parity.py` enforces it: both skills must drive the same
tools in the same order, publish identical budgets, and contain no threshold or algorithm.

The cost is that a host-specific optimisation has nowhere to live.

## Revisit when

Never. This is what makes "one core, many adapters" true rather than aspirational.
