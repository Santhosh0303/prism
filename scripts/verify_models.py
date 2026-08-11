"""Generate and verify the model artifact manifest.

Two modes, one source of truth:

* ``--generate`` measures the artifacts on disk and writes ``models/artifacts/manifest.json``.
  Every hash, size, and revision is measured here, never transcribed from an upstream API
  response or a third party's report.
* default (verify) re-reads the manifest and re-verifies every artifact, which is what CI
  and ``prism health --deep`` rely on.

The committed manifest is the trust anchor, so ``--generate`` will not overwrite an
existing one without ``--overwrite-manifest``. Without that guard, trust metadata could be
minted from whatever bytes happened to be on disk — which is exactly how a swapped
artifact starts verifying cleanly. ``scripts/acquire_models.py`` is what ties the anchor to
upstream; this script never fetches anything.

Run from the repository root:

    uv run python scripts/verify_models.py
    uv run python scripts/verify_models.py --generate --overwrite-manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from prism.errors import PrismError  # noqa: E402
from prism.measure.models import ModelManifest, verify_model_bundle  # noqa: E402

ARTIFACT_ROOT = REPO_ROOT / "models" / "artifacts"
MANIFEST_PATH = ARTIFACT_ROOT / "manifest.json"

#: Resolved before the artifacts were accepted, and pinned here. Downloads were performed
#: against these revisions only; `main` was never used as an identity.
PINNED = {
    "relevance": {
        "name": "sentence-transformers/all-MiniLM-L6-v2",
        "upstream_revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "licence": "Apache-2.0",
        "directory": "e1",
    },
    "nli": {
        "name": "cross-encoder/nli-MiniLM2-L6-H768",
        "upstream_revision": "b95119ce93d3e065de6214e38cd4a97b0f2f2c6d",
        "licence": "Apache-2.0",
        "directory": "e2",
    },
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def label_map(config_path: Path) -> tuple[dict[str, str] | None, int | None]:
    """Read id2label and locate the contradiction index.

    A wrong index would invert every measurement, so it is derived from the model's own
    config and recorded, never assumed from a conventional ordering.
    """
    if not config_path.is_file():
        return None, None
    config = json.loads(config_path.read_text(encoding="utf-8"))
    id2label = config.get("id2label")
    if not isinstance(id2label, dict):
        return None, None
    normalised = {str(key): str(value) for key, value in id2label.items()}
    index = next(
        (int(key) for key, value in normalised.items() if value.lower() == "contradiction"),
        None,
    )
    return normalised, index


def generate(*, allow_overwrite: bool) -> ModelManifest:
    if MANIFEST_PATH.exists() and not allow_overwrite:
        raise SystemExit(
            f"refusing to overwrite the committed trust anchor: {MANIFEST_PATH}\n"
            "  --generate rewrites every hash from the bytes now on disk, which would make "
            "a swapped artifact verify cleanly.\n"
            "  To obtain the pinned artifacts instead, run: "
            "python scripts/acquire_models.py\n"
            "  To adopt a new pinned revision deliberately, rerun with "
            "--generate --overwrite-manifest and review the manifest diff."
        )

    models = []
    for role, pinned in PINNED.items():
        directory = ARTIFACT_ROOT / str(pinned["directory"])
        if not directory.is_dir():
            raise SystemExit(f"missing artifact directory: {directory}")
        files = []
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            files.append(
                {
                    "path": path.relative_to(ARTIFACT_ROOT).as_posix(),
                    "sha256": sha256_of(path),
                    "bytes": path.stat().st_size,
                }
            )
        id2label, contradiction_index = label_map(directory / "config.json")
        models.append(
            {
                "role": role,
                "name": pinned["name"],
                "upstream_revision": pinned["upstream_revision"],
                "licence": pinned["licence"],
                "onnx_path": f"{pinned['directory']}/onnx/model.onnx",
                "tokenizer_path": f"{pinned['directory']}/tokenizer.json",
                "files": tuple(files),
                "contradiction_index": contradiction_index if role == "nli" else None,
                "id2label": id2label if role == "nli" else None,
            }
        )

    # strict mode does not coerce list to tuple; build the exact shape.
    manifest = ModelManifest.model_validate({"models": tuple(models)})
    MANIFEST_PATH.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true", help="measure artifacts and write")
    parser.add_argument(
        "--overwrite-manifest",
        action="store_true",
        help="allow --generate to replace an existing manifest. Required to adopt a new "
        "pinned revision; never use it to make a failing verification pass.",
    )
    args = parser.parse_args(argv)

    if args.overwrite_manifest and not args.generate:
        parser.error("--overwrite-manifest has no effect without --generate")

    if args.generate:
        manifest = generate(allow_overwrite=args.overwrite_manifest)
        print(f"wrote {MANIFEST_PATH}")
    else:
        manifest = ModelManifest.model_validate_json(MANIFEST_PATH.read_text(encoding="utf-8"))

    try:
        verify_model_bundle(manifest, ARTIFACT_ROOT)
    except PrismError as error:
        print(f"VERIFY FAILED [{error.code}] {error.message} {error.diagnostics}")
        return 1

    total = sum(entry.bytes for model in manifest.models for entry in model.files)
    print(f"verified {sum(len(m.files) for m in manifest.models)} files, {total:,} bytes")
    for model in manifest.models:
        print(f"  {model.role:10} {model.name} @ {model.upstream_revision[:12]} ({model.licence})")
    nli = manifest.by_role("nli")
    print(f"  contradiction index: {nli.contradiction_index} from id2label={nli.id2label}")
    print(f"  manifest digest: {manifest.digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
