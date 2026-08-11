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

Also not yet done: a full endurance soak — the in-suite probe is short and catches a leak,
not a plateau — signed release provenance and artifact signing, and a compatibility matrix
against pinned prior host releases. The regression baseline is recorded but `UNSIGNED`. See
[`docs/operations.md`](docs/operations.md) for the complete list of absent controls.

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
uv run python scripts/verify_models.py   # fetch-free: hashes what is on disk
uv run prism health --deep
```

The model bundle is two ONNX encoders pinned by immutable upstream revision and SHA-256,
totalling roughly 403 MB. The weights are not committed; `models/artifacts/manifest.json`
is, so a clone can verify a bundle it obtained independently. `scripts/verify_models.py`
hashes what is on disk and compares it against that manifest, and every subsequent run
re-verifies hashes, sizes, and path containment before any session is created. See
[`models/README.md`](models/README.md) for how to obtain the artifacts and
[`docs/model-card.md`](docs/model-card.md) for the exact revisions.

Preflight, synthesis, and health need none of it — PRISM is fully usable without the
bundle, and `PRISM_DISABLE_MEASURE=1` makes that mode explicit.

There is no PyPI release. The distribution is named `prism-preflight` — `prism` on PyPI is
an unrelated Bayesian/MCMC project — and until a tagged, signed release exists, the
documented way to install the MCP server elsewhere is an exact commit, not a name:

```bash
uv tool install "git+https://github.com/Santhosh0303/prism@<commit>"
```

### What the lock covers, and what it does not

`uv sync` and the reproducible-build gate are lock-bound: exact versions, exact hashes.
An installed wheel is not. `pyproject.toml` declares compatible ranges (`mcp>=2,<3`,
`onnxruntime>=1.20,<2`, and four more), so any install resolves them independently of
`uv.lock`, and the same command run a month later can produce different transitive
versions. The lock governs this repository's development and CI environments; it does not
travel inside the wheel. For a lock-bound deployment, clone at a pinned commit and run
`uv sync --frozen` rather than installing the distribution.

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

task = "Review this architecture."

service = PrismService.from_default_bundle()
preflight = service.preflight(PreflightRequest(task=task, mode="standard"))
measurement = service.measure(MeasureRequest(question=task, candidates=packets))
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

| Metric | Measured (worst of 3) | Target | Hard limit |
|---|---:|---:|---:|
| Preflight p95 | 0.150 ms | < 15 ms | < 50 ms |
| Preflight p99 | 0.228 ms | — | < 75 ms |
| Measurement p95 | 3,468 ms | < 3,500 ms | < 8,000 ms |
| Measurement p99 | 3,579 ms | < 9,000 ms | < 10,000 ms |
| Peak RSS | 750 MB | < 2.2 GB | < 3 GB |
| Default report size | 3,175 bytes | < 6 KB | < 12 KB |
| Cold start | 5,748 ms | reported, not gated | — |

Measurement p95 landed between 3,154 ms and 3,468 ms across the three runs — inside the
3,500 ms target, but by less than the run-to-run spread itself, so read "meets target" as
provisional on this hardware. A busier or slower machine will miss it. An earlier run
published 4,876 ms; it was taken under other load and is not comparable. Full per-run
figures are in [`docs/performance.md`](docs/performance.md). Preflight is effectively free.

## Verification

```
504 tests passing, 2 deselected (endurance)
ruff check + ruff format --check    clean
mypy --strict (src + tests)         no issues, 74 files
bandit                              0 issues
vulture / deptry                    0 findings / no issues
import-linter                       5 architecture contracts kept, 0 broken
pip-audit (exported lock)           no known vulnerabilities
reproducible build                  two clean builds, identical normalised digest
```

One command runs all of it:

```bash
uv run python scripts/release_gate.py --text
```

Seventeen gates pass and one reports `SKIP`: there is no evaluation corpus, so no accuracy
figure can be published. Under `--strict` a skip blocks and the unsigned baseline blocks
too, so **`--strict` fails on this build by design** — a check that did not run has not
passed.

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
