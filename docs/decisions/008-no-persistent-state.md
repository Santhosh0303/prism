# ADR-008: No persistent state

**Status:** Accepted

## Context

Caching measurements would help a repeated workload. Storing tasks would enable history and
trend reporting.

## Decision

Nothing is persisted. No database, no cache, no task store, no history. The process holds
model sessions and nothing else.

## Consequences

There is no user data at rest to protect, no retention policy to write, no cache-poisoning
path, and no cache-invalidation bug. A restart is a complete recovery: the rollback
procedure can say "restart the process" and mean it, because nothing survives to be
corrupted.

The cost is that every measurement pays full price, and cold start is around five seconds.

## Revisit when

A caching layer can be shown to be worth the retention policy, the invalidation logic, and
the privacy surface it brings with it.
