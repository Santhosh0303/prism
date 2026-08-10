# Architecture decisions

One file per decision. Each records what was decided, what it costs, and what would have to
change for the decision to be revisited. A decision without a stated cost is a preference.

| ADR | Decision | Status |
|---|---|---|
| [001](001-local-stdio-first.md) | Local stdio transport only in v1 | Accepted |
| [002](002-no-llm-in-core.md) | No LLM or provider API in the core | Accepted |
| [003](003-host-generated-claim-packets.md) | The host generates claim packets | Accepted |
| [004](004-deterministic-preflight-routing.md) | Deterministic keyword routing, no model | Accepted |
| [005](005-two-cpu-encoders.md) | Two CPU encoders, relevance before contradiction | Accepted |
| [006](006-measurement-separated-from-synthesis.md) | Measurement and synthesis are separate | Accepted |
| [007](007-thin-integration-wrappers.md) | Integrations carry no logic | Accepted |
| [008](008-no-persistent-state.md) | No persistent state | Accepted |
| [009](009-suppress-uncalibrated-fields.md) | Suppress authoritative fields while uncalibrated | Accepted |
