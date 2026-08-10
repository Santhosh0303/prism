# PRISM model card

Two CPU-only ONNX encoders, used for two narrow and separate jobs. Neither is asked to
judge truth.

## Artifacts

Both were resolved to an immutable upstream revision **before** any file was accepted;
`main` was never used as an identity. Every hash below was measured locally by
`scripts/verify_models.py --generate` from the bytes on disk, not transcribed from an API
response.

| | E1 — relevance | E2 — natural language inference |
|---|---|---|
| Repository | `sentence-transformers/all-MiniLM-L6-v2` | `cross-encoder/nli-MiniLM2-L6-H768` |
| Revision | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` | `b95119ce93d3e065de6214e38cd4a97b0f2f2c6d` |
| Licence | Apache-2.0 | Apache-2.0 |
| Variant | fp32 `onnx/model.onnx` | fp32 `onnx/model.onnx` |
| Size | 90,405,214 bytes | 328,649,957 bytes |
| Tokenizer | WordPiece (`vocab.txt`) | BPE (`vocab.json` + `merges.txt`) |
| Max sequence | 256 tokens (PRISM cap) | 256 tokens (PRISM cap) |

Total bundle: 13 files, 422,366,141 bytes. Manifest digest is emitted in every measured
report so the artifacts behind a result are always identifiable.

**fp32 was chosen deliberately over the available qint8/quint8 variants.** Quantisation
shifts NLI scores, and there is no calibrated threshold to absorb that drift. Revisit only
after a human-labelled corpus exists.

## Label orientation

E2's `config.json` declares:

```json
{"0": "contradiction", "1": "entailment", "2": "neutral"}
```

**Contradiction is output index 0.** This is read from the model's own config and pinned
in `models/artifacts/manifest.json` as `contradiction_index`; the loader refuses to build
sessions if it is absent. A wrong index here would invert every measurement silently,
which is why it is never assumed from a conventional ordering.

## Responsibilities

**E1 does one thing:** decide whether two claims are about the same subject. It produces
mean-pooled, L2-normalised embeddings and PRISM compares them by cosine similarity against
a frozen floor compiled into `measure/pair.py`. The floor is not exposed through the CLI,
MCP, environment, or configuration, so no caller can tune their way to a more agreeable
result.

E1 never produces agreement, contradiction, truth, or confidence.

**E2 does one thing:** score contradiction between two same-subject claims. Both
directions are scored and the maximum is taken, because NLI is not symmetric.

## Why the relevance stage is load-bearing

Measured on these exact artifacts:

| Claim pair | E1 similarity | E2 P(contradiction) |
|---|---:|---:|
| "is ready for production" vs "is **not** ready for production" | 0.542 | 0.9946 |
| "latency under 1s" vs "latency exceeds 10s" | 0.555 | 0.9944 |
| "is ready for production" vs "can be deployed to production" | — | 0.0009 |
| "the cat sat on the mat" vs "the registry has 13 perspectives" | **0.055** | **0.8492** |

The last row is the important one. E2 assigns 0.85 contradiction probability to two
sentences with nothing to do with each other. Without the relevance floor that would be
reported as a contradiction. The two-stage design is a correctness control, not a
performance trick.

## Calibration

**Status: `UNCALIBRATED_PENDING_HUMAN_VALIDATION`.**

The contradiction threshold is 0.5 — the natural argmax boundary of a three-class softmax.
It was **not fitted**, not tuned, and not validated against any labelled data.

Design section 6.9 permits fitting only on locked, human-labelled calibration pairs, and
implementation plan Task 16 forbids any agent from writing, paraphrasing, expanding, or
labelling those seeds. No such corpus exists, so none was invented.

While this status holds:

- `contradiction_count`, `contradiction_rate`, and `agreement_type` are suppressed —
  `None`, `None`, and `UNCLEAR` — by a model validator that no code path can bypass;
- provisional values appear only under `experimental_contradiction_count`,
  `experimental_contradiction_rate`, and `experimental_threshold`;
- the synthesis contract instructs the host to present them as a prompt to look, never as
  a finding.

**There is no published precision, recall, F1, or MCC for this build.** Any such number
would be fabricated.

To calibrate: harvest real pre-existing outputs with full provenance, have a second human
label them independently, report raw agreement and Cohen's kappa, commit the manifest hash
before any encoder run, then score the sealed test set exactly once.

## Known limitations

Inherited from the encoders and from PRISM's own heuristics:

- **Numeric conflicts** are weakly detected by NLI. PRISM adds conservative same-unit
  numeric checks as separate diagnostics, and only within a candidate.
- **Long technical claims** and **domain jargon** degrade NLI quality. The 80-word cap
  helps; it does not solve it.
- **Subtle temporal qualifiers** are unreliable. This is why tense differences are
  explicitly *not* allowed to exclude a pair from the denominator.
- **Scope classification is keyword-based.** Only lifecycle, environment, scale, and
  platform differences can exclude a pair; everything else reaches `UNCERTAIN` at most and
  stays in the denominator.
- **Paraphrase is not agreement.** Two claims saying the same thing score low
  contradiction; that is consistency, not corroboration.

## Integrity

Every artifact is canonicalised beneath the model root, confirmed to be a regular file,
size-checked, and SHA-256 verified **before** ONNX Runtime opens anything. Symlinks, path
traversal, and external-data references fail closed with `MODEL_INTEGRITY_FAILURE`. The
CPU execution provider is asserted after session creation, because requesting it is not
the same as getting it.

The external-data check is a bounded byte scan for the ONNX external-data markers plus a
companion-file check. It is **not** a full protobuf parse — that would require an `onnx`
dependency the six-package runtime budget has no room for. It catches the realistic case;
it is recorded here as a limitation rather than presented as proof.

## Provenance

Model artifacts are third-party. PRISM verifies that the files match what was pinned. It
cannot verify how they were trained, on what data, or whether a correctly signed artifact
contains an unknown flaw. Zero-day exposure is minimised and made recoverable — narrow
dependencies, no open port, no secrets, an independent measurement kill switch — but it is
never claimed to be eliminated.
