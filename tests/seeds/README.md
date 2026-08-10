# Evaluation seeds

This directory holds the human-authored, human-labelled pairs used to calibrate and
evaluate contradiction detection. **It is currently empty**, which is why PRISM publishes
no precision, recall, F1, or MCC, and why every report carries
`calibration_status = UNCALIBRATED_PENDING_HUMAN_VALIDATION`.

`seeds.lock.json` is the manifest. `scripts/check_seed_lock.py` enforces it.

## Why a lock exists

A threshold fitted on data that was edited after the fact is not measured, it is chosen.
The lock makes that impossible to do quietly: every seed file is hashed before any encoder
runs, and the gate fails if a hash moves, if a file appears that the lock does not name, or
if a seed is missing the provenance that makes it real.

## Rules

1. **No generated seeds.** No model may author, paraphrase, expand, translate, or label a
   seed. A corpus written by the class of system under test measures nothing.
2. **Real provenance.** Every seed records where the text actually came from — a real
   pre-existing output, with enough detail that someone else could find it again.
3. **Two independent labellers.** A second human labels without seeing the first set of
   labels. Report raw agreement and Cohen's kappa; do not quietly reconcile.
4. **Lock before scoring.** Commit the lock, including the model manifest digest in force,
   *before* the first encoder run.
5. **Score the sealed set once.** A test set scored repeatedly while a threshold is tuned
   is a training set wearing a disguise.

## Manifest shape

```json
{
  "schema_version": "1.0",
  "status": "LOCKED",
  "locked_at": "2026-01-01T00:00:00Z",
  "model_manifest_digest": "sha256:...",
  "seeds": [
    {
      "path": "pairs/release-readiness.jsonl",
      "sha256": "...",
      "count": 120,
      "authored_by": "name or role of the human author",
      "labelled_by": ["first labeller", "second labeller"],
      "provenance": "where these outputs came from, specifically",
      "split": "calibration"
    }
  ]
}
```

`split` is `calibration` or `test`. The test split is scored once, at the end.

## Status values

| Status | Meaning | Gate |
|---|---|---|
| `NO_CORPUS` | No seeds. Calibration is impossible. | `SKIP` — blocks a release claim |
| `LOCKED` | Seeds hashed and sealed; scoring may proceed | `PASS` when every hash matches |
