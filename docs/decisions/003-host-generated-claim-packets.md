# ADR-003: The host generates claim packets

**Status:** Accepted

## Context

Something has to turn a task into perspective-specific claims. PRISM could call a model, or
it could ask the host to do it.

## Decision

The host generates. PRISM emits a contract describing what each perspective must produce —
purpose, required questions, forbidden overlap, claim budget — and measures what comes back.

## Consequences

This is what keeps ADR-002 possible, and it puts generation where the capable model already
is. It also means PRISM never has to defend a fact it produced, because it produces none.

The cost is that PRISM cannot verify how the packets were made. It records what the caller
declares. Several lenses from one model call are one source, and PRISM says so — but it
cannot confirm that separately declared sources are genuinely independent.

## Revisit when

Never without giving up provider independence.
