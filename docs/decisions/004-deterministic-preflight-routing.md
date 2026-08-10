# ADR-004: Deterministic keyword routing, no model

**Status:** Accepted

## Context

Task classification chooses which lenses a task gets. An embedding model would generalise
better than a keyword table.

## Decision

Classification is a deterministic keyword table. Selection is a deterministic function of
task profile and mode.

## Consequences

Routing is reproducible, inspectable, and costs nothing. A user can read the table and
predict the result, and a golden test can assert byte-identical contracts across a hundred
runs and across fresh processes.

It also means preflight needs no model bundle, which is what makes preflight-only mode a
genuine operating point rather than a degraded fallback.

The cost is that unusual phrasings fall through to the general profile. A general profile
that returns four sensible lenses is an acceptable failure; a mysterious one would not be.

## Revisit when

Routing quality is measured against a labelled task corpus and found wanting — which needs
the corpus to exist first.
