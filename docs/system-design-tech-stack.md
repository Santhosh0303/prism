# System design and technology stack

Selected technologies, the constants that govern behaviour, and why each choice was made
rather than an obvious alternative.

## Runtime stack

| Layer | Choice | Why not the alternative |
|---|---|---|
| Language | Python 3.12 | The harnesses PRISM plugs into are Python; a faster core would gain nothing against a 3-second inference cost |
| Contracts | Pydantic v2 | Strict mode, frozen models, and validators that no code path can bypass |
| Arrays | NumPy | Already required transitively by the runtime; pooling and cosine are a few lines |
| Inference | ONNX Runtime (CPU) | PyTorch would add gigabytes and a GPU story for two small encoders |
| Tokenisation | Hugging Face Tokenizers | Matches the pinned models exactly; a hand-rolled WordPiece would silently disagree |
| Protocol | Official MCP Python SDK v2 | A hand-written stdio server would drift from the spec |
| Registry format | YAML via PyYAML `safe_load` | Data, not code; anchors and aliases are rejected outright |

Six direct runtime dependencies, enforced by `tests/unit/test_dependency_budget.py`. Two are
native. That count is a control, not an aspiration: each one widens the zero-day surface of
a component small enough to audit.

**Explicitly excluded from the runtime:** any provider SDK, any web framework, any database
driver, any agent framework, `requests`/`httpx`, `torch`, `transformers`. `import-linter`
fails the build if one appears in the import graph.

## Development stack

`uv` for resolution and build, `pytest` with Hypothesis, Ruff for lint and format, mypy in
strict mode, coverage.py, Bandit, vulture, deptry, import-linter, pip-audit. All pinned in
the dev group; none ships.

## Governing constants

Behaviour that a reader would otherwise have to infer from code.

| Constant | Value | Consequence of changing it |
|---|---:|---|
| Perspectives per mode | 3 / 4 / 5 | Host token cost and coverage |
| Hard perspective maximum | 5 | Selection and report bounds |
| Claims per candidate | 4 | Pair count grows quadratically |
| Words per claim | 8–80 | Below the floor a claim is not measurable; above it, NLI degrades |
| Candidates per request | 2–5 | Denominator size |
| Registry maximum | 64 lenses | Registry load bound |
| `RELEVANCE_FLOOR` | 0.42 | **Semantic change.** Needs golden-diff review |
| Contradiction threshold | 0.5 | Uncalibrated; argmax boundary of a 3-class softmax |
| Concurrent measurements | 2, zero queue | Admission behaviour under burst |
| Default report cap | 20 records/category, < 12 KB | Detail, never arithmetic |

`RELEVANCE_FLOOR` is a compiled constant, never a runtime parameter. It is not exposed
through the CLI, MCP, environment, or configuration, so no caller can tune their way to a
friendlier result. `scripts/check_architecture.py` fails if it becomes reachable.

## The two-stage measurement

**E1 — relevance.** `sentence-transformers/all-MiniLM-L6-v2`. Mean-pooled, L2-normalised
embeddings compared by cosine similarity against the frozen floor. E1 never produces
agreement, contradiction, truth, or confidence.

**E2 — contradiction.** `cross-encoder/nli-MiniLM2-L6-H768`. Both directions scored, maximum
taken, because NLI is not symmetric. The contradiction output index is read from the model's
own config and pinned in the manifest; assuming a conventional ordering would invert every
measurement silently.

The order matters and the evidence is in [`model-card.md`](model-card.md): E2 assigns 0.85
contradiction probability to `"the cat sat on the mat"` versus `"the registry has 13
perspectives"`. The relevance stage is a correctness control, not an optimisation.

## Denominator design

Only cross-candidate pairs above the relevance floor and not scope-divergent count. Same
candidate pairs are internal-conflict diagnostics. Exact duplicates are removed and reported
before scoring, so duplication cannot inflate agreement. A zero denominator produces `null`,
never `0.0` — "nothing comparable was found" and "nothing disagreed" are different facts.

Production coverage is `1.0`: every same-scope pair is scored. Anything less and the
agreement label becomes `UNCLEAR`.

## Scope rules

Tri-state, conservative. `SAME`, `DIVERGENT`, `UNCERTAIN`. Only lifecycle, environment,
scale, and platform differences can reach `DIVERGENT` and leave the denominator. Tense and
modality cannot: excluding on those removed a genuine contradiction during testing — "is
ready today" versus "is not ready and will fail" is a disagreement, not two different worlds.
`UNCERTAIN` stays in the denominator, because the conservative error is to keep a pair.

## Interfaces

```python
preflight(request: PreflightRequest) -> PreflightReport
measure(request: MeasureRequest) -> MeasureReport
synthesis_contract(preflight, measurement) -> SynthesisContract
```

MCP: `prism.preflight`, `prism.measure`, `prism.synthesis_contract`, `prism.health` — all
read-only, static catalogue. CLI: `preflight`, `measure`, `synthesize`, `health`, `version`.

No public interface accepts an executable expression, a file-write destination, a shell
command, a URL, a credential, or a provider key.

## Versioning

`schema_version` changes only for contract-breaking changes; `perspective_registry_version`
changes on every semantic registry edit; `model_manifest_hash` appears in every measured
report. Patch releases do not change public schemas. A minor release accepts the previous
declared minor or returns a typed `VERSION_MISMATCH`.

## Observability

Content-free by construction. Diagnostics carry hashes, sizes, durations, statuses,
versions, and warning counts. Raw task and candidate logging is not implemented — including
in debug mode, because a debug switch that logs content is a data-leak path waiting for an
incident. There is no telemetry exporter.
