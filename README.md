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

---

## Status: functionally complete, not release ready

Version 0.1.0. Read this section before relying on any number PRISM produces.

**The contradiction threshold is uncalibrated.** No human-labelled corpus has been scored
against it, so every report carries `calibration_status =
UNCALIBRATED_PENDING_HUMAN_VALIDATION`, and while that holds:

- the authoritative `contradiction_count`, `contradiction_rate`, and `agreement_type`
  fields are **suppressed** — `None`, `None`, and `UNCLEAR` — by a model validator that no
  code path can bypass;
- provisional values appear only under `experimental_contradiction_count`,
  `experimental_contradiction_rate`, and `experimental_threshold`;
- the synthesis contract instructs the host to treat those as a prompt to look, never as a
  finding.

**No precision, recall, F1, or MCC is published for this build**, because none has been
measured. Any such number would be fabricated. Calibrating requires real pre-existing
outputs harvested with provenance, labelled independently by a second human, with the
manifest hash committed before any encoder run and the sealed test set scored exactly once.

Also not yet done: an endurance soak, an independent reproducible-build comparison, signed
release provenance, and a compatibility matrix against pinned prior host releases.

---

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
uv run python scripts/verify_models.py --generate   # fetch-free: measures what is on disk
uv run prism health --deep
```

The model bundle is two ONNX encoders pinned by immutable upstream revision and SHA-256,
totalling roughly 403 MB. They are not committed. `scripts/verify_models.py` records what
is on disk and every subsequent run re-verifies hashes, sizes, and path containment before
any session is created. See [`docs/model-card.md`](docs/model-card.md) for the exact
revisions and how to obtain them.

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
   `source_group_id`, because one model in one pass is one source however many labels it
   carries.
3. **Measurement** enumerates cross-candidate claim pairs, scores relevance with E1, keeps
   pairs above a frozen floor, classifies scope, then scores contradiction with E2 in both
   directions and takes the maximum.
4. **Synthesis** returns rules: what to disclose, what to preserve, what not to do.

### Why two encoders, in that order

E1 decides whether two claims are about the same subject; E2 decides whether same-subject
claims disagree. The order is load-bearing, not an optimisation. Measured on the exact
encoders this repository pins:

| Claim pair | E1 similarity | E2 P(contradiction) |
|---|---:|---:|
| "is ready for production" vs "is **not** ready for production" | 0.542 | 0.9946 |
| "latency under 1s" vs "latency exceeds 10s" | 0.555 | 0.9944 |
| "is ready for production" vs "can be deployed to production" | — | 0.0009 |
| "the cat sat on the mat" vs "the registry has 13 perspectives" | **0.055** | **0.8492** |

The last row is the point. The NLI model confidently calls two entirely unrelated sentences
a contradiction. The relevance floor is what stops that becoming a reported finding.

## Measured performance

Reference workload is the maximum legal one: 5 candidates × 4 claims, every same-scope pair
scored in both directions with no speed floor. 100 preflight calls and 30 measurements
after warm-up, on AMD64 (AMD, 16 logical cores), Windows 11, Python 3.12.10.

| Metric | Measured | Target | Hard limit |
|---|---:|---:|---:|
| Preflight p95 | 0.288 ms | < 15 ms | < 50 ms |
| Preflight p99 | 0.889 ms | — | < 75 ms |
| Measurement p95 | **4,876 ms** | < 3,500 ms | < 8,000 ms |
| Measurement p99 | 5,085 ms | < 9,000 ms | < 10,000 ms |
| Peak RSS | 755 MB | < 2.2 GB | < 3 GB |
| Default report size | 3,175 bytes | < 6 KB | < 12 KB |
| Cold start | 8,088 ms | reported, not gated | — |

**Measurement p95 misses its 3,500 ms target.** It is inside the hard limit, so it does not
block, but it is a miss and is reported as one. Expect roughly five seconds for a full
five-lens measurement on comparable hardware. Preflight is effectively free.

## Verification

```
403 tests passing
ruff check + ruff format --check    clean
mypy --strict (src + tests)         no issues, 58 files
bandit                              0 issues
vulture / deptry                    0 findings / no issues
import-linter                       5 architecture contracts kept, 0 broken
pip-audit (resolved lock)           no known vulnerabilities, 46 packages
```

Notable properties under test: canonical digest parity across the Python API, CLI, and MCP
server; determinism across nine environment permutations in fresh processes, including the
Turkish dotless-i locale; a 20-client burst holding at two active with zero queued and
sub-50 ms rejection; ten simultaneous cold callers producing exactly one encoder session
pair; and a maximum-conflict 160-pair report staying under 12 KB with exact counts intact.

## Boundaries

During normal analysis PRISM performs no outbound network access, no shell or subprocess
execution, no filesystem mutation, no credential handling, and no user-project scanning. It
reads packaged registry data, hash-verified model artifacts beneath a dedicated root, and
the input you give it.

**This is not an OS sandbox.** PRISM's own code performs no privileged operation, but
Python and the native inference runtime execute with the ambient rights of the host
process. Production deployment requires a host sandbox, a minimal environment allowlist, a
read-only model directory, and no secrets in the child process environment. See
[`SECURITY.md`](SECURITY.md).

`PRISM_DISABLE_MEASURE=1` disables all inference while leaving preflight fully available.

## Limitations

- **Uncalibrated.** No validated precision, recall, or F1. See Status above.
- **Scope classification is heuristic.** Only lifecycle, environment, scale, and platform
  differences can exclude a pair from the denominator. Tense and modality cannot: excluding
  on those removed a genuine contradiction during testing — "is ready today" versus "is not
  ready and will fail" is a disagreement, not two different worlds. Uncertain pairs stay in
  the denominator.
- **Known NLI weaknesses:** numeric conflicts, long technical claims, domain jargon, subtle
  temporal qualifiers.
- **Provenance is declared, never verified.** Several lenses from one host call are one
  source, and PRISM says so. It cannot confirm that separately declared sources are real.
- **Zero-day exposure is managed, not eliminated.** Hash verification proves an artifact is
  the one pinned; it cannot prove a correctly signed dependency is free of unknown flaws.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run lint-imports
uv run bandit -r src && uv run vulture src tests --min-confidence 90 && uv run deptry .
uv run python benchmarks/run.py --profile release
```

## Licence

MIT — see [`LICENSE`](LICENSE). The model artifacts are Apache-2.0 and are governed by
their own upstream terms.
