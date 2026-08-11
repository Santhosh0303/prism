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

## The trust anchor

**`artifacts/manifest.json` is the anchor.** It is committed, so the identity of every file
PRISM will accept is reviewable in a diff rather than asserted at runtime. Everything else
in this section exists to keep that anchor meaningful:

- **verification** proves the bytes on disk are unchanged since the manifest was written;
- **acquisition** is the separate step that ties the anchor to upstream — it is the only
  thing that turns "unchanged since generation" into "these are the pinned artifacts".

Neither step can rewrite the anchor. Adopting a new pinned revision is a deliberate act
that lands as a reviewable diff (see *Adopting a new revision* below).

## Obtaining the artifacts

```bash
uv run python scripts/acquire_models.py --dry-run   # prints every URL, fetches nothing
uv run python scripts/acquire_models.py
uv run python scripts/verify_models.py
```

`acquire_models.py` reads the committed manifest, cross-checks both repositories and both
revisions against [`../docs/model-card.md`](../docs/model-card.md), and fetches each file
from `/resolve/<revision>/` — the full 40-character upstream commit. A branch name, a tag,
`main`, `latest`, or a short hash is refused with `MODEL_INTEGRITY_FAILURE` before any
request is made, because a reference that can move is not an identity. Each response is
read under a hard ceiling of the pinned size and hashed as it arrives; anything whose size
or SHA-256 differs is discarded and never written into the model root.

Obtaining the files by hand works too — place them under `artifacts/e1/` and `artifacts/e2/`
at the relative paths the manifest lists, at those exact revisions. The verification step
is identical either way.

```bash
uv run python scripts/verify_models.py
```

That command hashes what is on disk and compares it against the committed manifest. It
fetches nothing. A mismatch, a symlink, a path that escapes the model root, or an ONNX
external-data reference fails closed with `MODEL_INTEGRITY_FAILURE`, and measurement stays
unavailable until it is resolved.

## Adopting a new revision

`--generate` rewrites the manifest from the bytes on disk. It is for adopting a new pinned
revision, not for making a failing verification pass. Regenerating to silence a mismatch
destroys the only control that detects a swapped artifact — so it refuses to overwrite an
existing manifest unless you say so explicitly:

```bash
uv run python scripts/verify_models.py --generate --overwrite-manifest
```

Update the revisions in `scripts/verify_models.py` and `../docs/model-card.md` first, then
review the manifest diff. If the diff is not the one you expected, the artifacts are not
the ones you think you have.

## Relocating the bundle

Set `PRISM_MODEL_ROOT` to an absolute path to keep the weights outside the working tree —
a read-only directory is the intended production arrangement. Everything beneath the root
is canonicalised and containment-checked before ONNX Runtime opens a single file.
