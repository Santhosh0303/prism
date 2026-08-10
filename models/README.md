# Model bundle

This directory holds the two CPU ONNX encoders PRISM uses for measurement. The weights are
**not** in the repository: they total roughly 403 MB and are third-party artifacts under
their own upstream licence. What *is* committed is `artifacts/manifest.json` — the pinned
identity of every file PRISM will accept.

Preflight, synthesis, health, and the whole CLI work without this bundle. Only `measure`
needs it.

## Layout

```text
models/
├── README.md                  committed
├── artifacts/
│   ├── manifest.json          committed — hashes, sizes, revisions, label orientation
│   ├── e1/                    not committed — relevance encoder
│   └── e2/                    not committed — NLI encoder
```

## Obtaining the artifacts

Fetch each repository at the exact revision recorded in
[`../docs/model-card.md`](../docs/model-card.md) — the revision, never a branch name — and
place the files under `artifacts/e1/` and `artifacts/e2/` using the relative paths listed
in the manifest. Then verify:

```bash
uv run python scripts/verify_models.py
```

That command hashes what is on disk and compares it against the committed manifest. It
fetches nothing. A mismatch, a symlink, a path that escapes the model root, or an ONNX
external-data reference fails closed with `MODEL_INTEGRITY_FAILURE`, and measurement stays
unavailable until it is resolved.

`--generate` rewrites the manifest from the bytes on disk. It is for adopting a new pinned
revision, not for making a failing verification pass. Regenerating to silence a mismatch
destroys the only control that detects a swapped artifact.

## Relocating the bundle

Set `PRISM_MODEL_ROOT` to an absolute path to keep the weights outside the working tree —
a read-only directory is the intended production arrangement. Everything beneath the root
is canonicalised and containment-checked before ONNX Runtime opens a single file.
