# ADR-005: Two CPU encoders, relevance before contradiction

**Status:** Accepted

## Context

A single NLI model could score contradiction directly across all claim pairs.

## Decision

Two stages. E1 decides whether two claims are about the same subject; E2 decides whether
same-subject claims disagree. Only pairs above a frozen relevance floor reach E2.

## Consequences

This is a correctness control, not a performance optimisation, and the evidence is direct:
on the exact pinned artifacts, E2 assigns **0.85** contradiction probability to "the cat sat
on the mat" versus "the registry has 13 perspectives" — two sentences with nothing to do
with each other. Without the relevance stage that becomes a reported finding.

The cost is a second model in the bundle, roughly 90 MB, and a constant —
`RELEVANCE_FLOOR = 0.42` — that the whole result depends on and that has not been validated
against labelled data. It is the most load-bearing unvalidated number in the system.

## Revisit when

A labelled corpus exists and the floor can be fitted rather than chosen.
