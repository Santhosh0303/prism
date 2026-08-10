# ADR-002: No LLM or provider API in the core

**Status:** Accepted

## Context

PRISM could classify tasks, generate perspectives, and judge contradictions with an LLM.
That would be less code and would probably classify better.

## Decision

The core makes zero LLM or provider API calls and requires no API key. The only models it
runs are two local CPU encoders it verifies itself.

## Consequences

PRISM is provider-agnostic, free to run, works air-gapped, and cannot leak a task to a third
party. It also cannot be broken by a provider's outage, pricing change, or content policy.

Determinism follows: the same input produces byte-identical output, which is what makes the
golden contract tests and the canonical digest matrix possible at all.

The cost is that classification is a keyword table and will be cruder than a model would be.

## Revisit when

Never for the core. A host is free to do anything it likes on its own side of the boundary.
