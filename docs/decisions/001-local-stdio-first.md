# ADR-001: Local stdio transport only in v1

**Status:** Accepted

## Context

MCP supports stdio and Streamable HTTP. HTTP would let one PRISM instance serve several
hosts, and would make a hosted deployment possible later.

## Decision

v1 ships a local stdio server only. Streamable HTTP is excluded.

## Consequences

An entire class of attack disappears rather than being defended: no open port, no listener,
no authentication to get wrong, no transport encryption to configure, no rate limiting, no
session fixation. The threat model shrinks to inputs and artifacts.

The cost is real. No shared instance, no remote deployment, and one process per host. For a
component whose whole job is a few seconds of local CPU, that trade is worth taking.

## Revisit when

Someone needs a shared instance across machines and is prepared to own authentication,
transport security, and the operational surface that comes with them.
