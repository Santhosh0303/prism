# ADR-006: Measurement and synthesis are separate

**Status:** Accepted

## Context

PRISM could return one combined result: what it measured plus what to do about it.

## Decision

`measure` returns findings. `synthesis_contract` returns rules. They are separate calls with
separate schemas, and synthesis works with no measurement at all.

## Consequences

A host can measure without accepting PRISM's view of what to write, or take the contract
without running inference at all. It also keeps the calibration boundary clean: while the
threshold is uncalibrated, the contract can instruct the host to treat provisional numbers
as a prompt to look rather than as a finding — a distinction that would be impossible to
express inside a single merged result.

The cost is an extra call in the workflow.

## Revisit when

Never; merging them would couple a measurement to a judgement about it.
