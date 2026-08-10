# PRISM

**A reasoning preflight and contradiction-measurement component for AI harnesses.**

PRISM sits in the orchestration layer, before an AI system gives its final answer. It
selects a finite set of useful perspectives, asks the host model to express each one as
compact claim packets, measures explicit contradictions and scope differences between
them offline, and returns a synthesis contract. The host writes the answer.

PRISM does not call an LLM, store tasks, authenticate users, browse the web, execute code,
or decide which claim is true.

> **PRISM measures conflict. It does not establish truth.** Agreement is not correctness.
> A contradiction rate of zero means no measured disagreement among comparable claims —
> nothing more.

## Status

**Functionally complete, not release ready.** Version 0.1.0.

The contradiction threshold is **uncalibrated**: no human-labelled corpus has been scored,
so `calibration_status` reads `UNCALIBRATED_PENDING_HUMAN_VALIDATION`, the authoritative
`contradiction_count`, `contradiction_rate`, and `agreement_type` fields are suppressed,
and provisional numbers appear only under `experimental_*`. **No semantic accuracy claim
is made.** See [`docs/verification-ledger.md`](docs/verification-ledger.md) for exactly
which gates have run and which have not.

## What it is

Five delivery shapes over one tested core:

| Shape | Entry point |
|---|---|
| Python package | `from prism.service import PrismService` |
| CLI | `prism` |
| Local stdio MCP server | `prism-mcp` — four read-only tools |
| Claude Code skill | `integrations/claude-code/` |
| Codex skill and `AGENTS.md` fragment | `integrations/codex/` |

## Install

```bash
uv sync
uv run python scripts/verify_models.py   # verify the model bundle before first use
uv run prism health --deep
```

The model bundle is two ONNX encoders, pinned by immutable upstream revision and SHA-256,
totalling roughly 403 MB. They are not committed; `scripts/verify_models.py --generate`
records what is on disk and every subsequent run re-verifies it.

## Use

```bash
# 1. Which perspectives does this task need?
prism preflight --task "Review the release plan for the payment service" --mode critical

# 2. Produce one compact claim packet per perspective, in a single pass, then:
prism measure --input candidates.json --format markdown

# 3. Rules for the final answer:
prism synthesize --preflight preflight.json --measurement measure.json

prism health --deep
```

```python
from prism.contracts import MeasureRequest, PreflightRequest
from prism.service import PrismService

service = PrismService.from_default_bundle()
preflight = service.preflight(PreflightRequest(task="Review this architecture.", mode="standard"))
measurement = service.measure(MeasureRequest(question="Review this architecture.", candidates=packets))
contract = service.synthesis_contract(preflight, measurement)
```

## How it works

1. **Preflight** classifies the task with a deterministic keyword table — no model, no
   embedding — and selects 3, 4, or 5 perspectives from a content-hashed registry of 13.
   `critical` always includes `security` and `red_team`.
2. **The host** answers every perspective in one pass as claim packets: 8 to 80 words per
   claim, within the returned budget. All packets from one pass share one
   `source_group_id`.
3. **Measurement** enumerates cross-candidate claim pairs, scores relevance with E1, keeps
   pairs above a frozen floor, classifies scope, then scores contradiction with E2 in both
   directions and takes the maximum.
4. **Synthesis** returns rules: what to disclose, what to preserve, what not to do.

### Why two encoders, in that order

E1 decides whether two claims are about the same subject; E2 decides whether same-subject
claims disagree. The order is load-bearing, not an optimisation. Measured on the real
encoders in this repository:

| Claim pair | E1 similarity | E2 P(contradiction) |
|---|---:|---:|
| "is ready for production" vs "is **not** ready for production" | 0.542 | 0.9946 |
| "latency under 1s" vs "latency exceeds 10s" | 0.555 | 0.9944 |
| "the cat sat on the mat" vs "the registry has 13 perspectives" | **0.055** | **0.8492** |

The NLI model confidently calls two entirely unrelated sentences a contradiction. The
relevance floor is what stops that from becoming a reported finding.

## Boundaries

During normal analysis PRISM performs no outbound network access, no shell or subprocess
execution, no filesystem mutation, no credential handling, and no user-project scanning.
It reads packaged registry data, hash-verified model artifacts beneath a dedicated root,
and the input you give it.

**This is not an OS sandbox.** PRISM's own code performs no privileged operation, but
Python and the native inference runtime execute with the ambient rights of the host
process. Production deployment requires a host sandbox, a minimal environment allowlist, a
read-only model directory, and no secrets in the child process environment. See
[`SECURITY.md`](SECURITY.md).

`PRISM_DISABLE_MEASURE=1` disables all inference while leaving preflight fully available.

## Limitations

- **Uncalibrated.** No validated precision, recall, or F1. See the status note above.
- **Scope classification is heuristic.** Only lifecycle, environment, scale, and platform
  differences can exclude a pair; tense and modality cannot, because excluding on those
  removed a genuine contradiction during testing. Uncertain pairs stay in the denominator.
- **Known NLI weaknesses:** numeric conflicts, long technical claims, domain jargon,
  subtle temporal qualifiers.
- **Provenance is declared, never verified.** Several lenses from one host call are one
  source, and PRISM says so. It cannot confirm that separately declared sources are real.
- **Zero-day exposure is managed, not eliminated.** Hash verification proves an artifact is
  the one pinned; it cannot prove a correctly signed dependency is free of unknown flaws.

## Development

```bash
uv run pytest                       # 403 tests
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests               # strict
uv run lint-imports                 # architecture boundaries
uv run bandit -r src && uv run vulture src tests --min-confidence 90 && uv run deptry .
uv run python benchmarks/run.py --profile release
```

## Documents

The architecture, system design, and implementation plan are the authority for this code
and are hash-anchored in the verification ledger. Any conflict between them is a blocking
documentation-integrity failure.

- [`docs/architecture.md`](docs/architecture.md) — boundaries, invariants, threat model
- [`docs/system-design-tech-stack.md`](docs/system-design-tech-stack.md) — schemas,
  constants, algorithms
- [`docs/implementation-plan.md`](docs/implementation-plan.md) — order, tests, release gates
- [`docs/verification-ledger.md`](docs/verification-ledger.md) — what has and has not run
- [`docs/model-card.md`](docs/model-card.md) — the encoders and their limits

## Licence

MIT. The model artifacts are Apache-2.0 and are covered by their own upstream terms.
