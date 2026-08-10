# Architecture

What PRISM is made of, why the pieces are separated the way they are, and which properties
the separation is there to protect.

## Position

PRISM is a component, not an application. It sits in a host's orchestration layer, between
the task arriving and the final answer being written:

```text
task ──▶ preflight ──▶ [ host model generates claim packets ] ──▶ measure ──▶ synthesis
             │                                                       │            │
      perspective contract                                 contradiction &   rules for the
      (3–5 lenses, budgets)                                scope report      final answer
```

The host generates. PRISM never does. That single boundary is what makes PRISM
provider-agnostic, keeps it free of API keys, and means it cannot invent a fact of its own
to report.

## Invariants

The rules the rest of the design exists to satisfy. Each is enforced somewhere mechanical —
an import contract, a test, or a gate script — not by convention.

| # | Invariant | Enforced by |
|---|---|---|
| A1 | PRISM orchestrates; it does not generate | no provider SDK in the import graph |
| A2 | One core, many adapters | `import-linter` layering contract |
| A3 | Core operations are pure or bounded | limits module, admission control |
| A4 | Inputs are data, never instructions | injection corpus in `tests/security/` |
| A5 | No hidden state | every version identifier appears in the report |
| A6 | Provenance claims stay conservative | `source_group_id` required; labels display-only |
| A7 | Measurement is optional and non-authoritative | preflight-only mode; calibration gate |
| A8 | Security is structural, not procedural | no network/shell/eval path exists to misuse |
| A9 | A partial report is not a report | reports validated whole before exposure |
| A10 | Diagnostics are content-free | telemetry drops content-bearing keys |

## Containers

| Container | Responsibility | Depends on |
|---|---|---|
| `prism` package | the whole core | Pydantic, NumPy, ONNX Runtime, Tokenizers |
| `prism` CLI | shell and CI surface | the service facade |
| `prism-mcp` server | local stdio MCP surface | the service facade, MCP SDK |
| Claude Code skill | workflow instructions only | nothing — it is text |
| Codex skill | workflow instructions only | nothing — it is text |

The two integrations contain no logic. They tell a model which tools to call in which order.
`tests/compatibility/test_skill_parity.py` fails if they drift apart, or if either starts
carrying a threshold or an algorithm that belongs in the core.

## Components

**Intake and limits.** Byte, word, candidate, claim, and pair ceilings are checked before
anything else runs. Every limit is a constant; exceeding one is a typed `LIMIT_EXCEEDED`,
never a truncation.

**Task classifier.** A deterministic keyword table. No model, no embedding, no learned
weights — so routing is reproducible, inspectable, and free.

**Perspective registry and selector.** Thirteen lenses in a content-hashed YAML file.
Selection is deterministic given task profile and mode, produces 3/4/5 lenses for
lite/standard/critical, and guarantees at least one adversarial and one constructive lens.
`critical` always includes `security` and `red_team`.

**Segmentation and pair enumeration.** Claim packets are normalised, exact duplicates are
removed and reported, then cross-candidate claim pairs are enumerated. Same-candidate pairs
are diagnostics, never denominator members.

**The two encoders.** E1 decides whether two claims are about the same subject; E2 decides
whether same-subject claims disagree. The order is load-bearing and is documented with
measured evidence in [`model-card.md`](model-card.md): E2 alone assigns 0.85 contradiction
probability to two entirely unrelated sentences.

**Scope classifier.** Tri-state and deliberately conservative. Only lifecycle, environment,
scale, and platform differences can remove a pair from the denominator. Uncertain pairs stay
in. Tense and modality are explicitly not grounds for exclusion.

**Calibration gate.** While the threshold is uncalibrated, a model validator suppresses the
authoritative contradiction count, rate, and agreement label. No code path can bypass it.

**Bounded report projector.** Detailed pair records are capped per category; aggregate
counts stay exact and the omitted count and pair-ledger digest are published, so bounding
the output can never quietly change the arithmetic.

## Data flow

**Flow A, full workflow.** preflight → host generation → measure → synthesis contract.
**Flow B, preflight-only.** Works with no model bundle at all.
**Flow C, measure-only.** For a host that already has its own perspective step.

## Trust boundaries

| Boundary | Threat | Response |
|---|---|---|
| host → PRISM | injection, oversized or adversarial input | input is data; typed failures; hard limits |
| PRISM → model bundle | tampered or swapped artifact | SHA-256 + size + containment before load |
| MCP client → server | tool poisoning, unexpected capability | static catalogue, read-only annotations |
| release → user | supply-chain compromise | lock, SBOM, hashes; signing not yet implemented |
| PRISM → host synthesis | overclaiming | contract forbids unsupported resolution |

## Decisions

Recorded in [`decisions/`](decisions/). The load-bearing ones: local stdio only in v1, no
LLM in the core, host-generated claim packets, deterministic preflight routing, two CPU
encoders, measurement separated from synthesis, thin integration wrappers, and no persistent
state.

## What is deliberately absent

No website, no database, no user accounts, no remote server, no provider API, no persistent
state, and no telemetry exporter. Each is a capability that would have to be secured,
operated, and explained, in exchange for something an AI-native component does not need.
