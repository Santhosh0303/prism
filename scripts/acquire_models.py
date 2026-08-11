"""Fetch the pinned model artifacts and tie them to the committed manifest.

`models/artifacts/manifest.json` is the trust anchor: it is committed, reviewable, and it
is what every later check compares against. On its own it proves only "unchanged since
generation". This script is the step that ties the anchor to upstream — it fetches each
file from the exact immutable revision recorded in the manifest and cross-checked against
`docs/model-card.md`, and refuses to keep anything whose bytes do not hash to the value
already in the manifest.

Three refusals, all fail-closed:

* a revision that can move — a branch, `main`, `latest`, a tag, a short hash — is not an
  identity and is rejected before a single request is made;
* a revision or repository the model card does not name is rejected, so the documented
  provenance and the fetched bytes cannot drift apart;
* a downloaded file whose size or SHA-256 differs from the manifest is deleted, never
  written into place.

Nothing here can write the manifest. Adopting a new revision is a separate, deliberate act
(`scripts/verify_models.py --generate --overwrite-manifest`) that a reviewer sees as a diff
to a committed file.

Run from the repository root:

    uv run python scripts/acquire_models.py --dry-run
    uv run python scripts/acquire_models.py
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from prism.errors import ErrorCode, PrismError  # noqa: E402
from prism.measure.models import (  # noqa: E402
    ModelManifest,
    resolve_model_root,
    verify_model_bundle,
)

ARTIFACT_ROOT = REPO_ROOT / "models" / "artifacts"
MANIFEST_PATH = ARTIFACT_ROOT / "manifest.json"
MODEL_CARD_PATH = REPO_ROOT / "docs" / "model-card.md"

#: Upstream resolve endpoint. `/resolve/<revision>/` serves the file as it existed at that
#: commit; the revision is substituted from the manifest, never from a caller.
RESOLVE_URL: Final[str] = "https://huggingface.co/{repo}/resolve/{revision}/{path}"

#: Every request must address this origin over HTTPS. Checked at the point of opening, so
#: a future edit to the template cannot quietly widen where artifacts may come from.
_PERMITTED_PREFIX: Final[str] = "https://huggingface.co/"

#: The only accepted spelling of a revision: a full 40-character lowercase hex commit.
#: Everything else can point somewhere new tomorrow.
IMMUTABLE_REVISION: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")

#: Any 40-hex token in the model card counts as a documented revision.
DOCUMENTED_REVISION: Final[re.Pattern[str]] = re.compile(r"\b[0-9a-f]{40}\b")

_BLOCK: Final[int] = 1024 * 1024
_USER_AGENT: Final[str] = "prism-acquire-models"

#: An opener takes a request and returns a file-like response. Injected so the download
#: path can be exercised without a network.
Opener = Callable[[urllib.request.Request], Any]


@dataclass(frozen=True, slots=True)
class Fetch:
    """One file to obtain, and the identity it must have when it lands."""

    role: str
    repo: str
    revision: str
    remote_path: str
    manifest_path: str
    destination: Path
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return RESOLVE_URL.format(repo=self.repo, revision=self.revision, path=self.remote_path)


def require_immutable_revision(revision: str, *, repo: str) -> str:
    """Return the revision, or refuse anything that can move."""
    if not IMMUTABLE_REVISION.fullmatch(revision):
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="A model revision that can move is not an identity. Pin the full "
            "40-character upstream commit; branch names, tags, 'main', 'latest', and "
            "short hashes are refused.",
            diagnostics={"repository": repo, "revision": revision},
        )
    return revision


def documented_revisions(card_text: str) -> frozenset[str]:
    return frozenset(DOCUMENTED_REVISION.findall(card_text))


def assert_documented(manifest: ModelManifest, card_text: str) -> None:
    """Refuse to fetch a revision or repository the model card does not name.

    The manifest is the anchor and the card is the human-readable record of it. If they
    disagree, the documented provenance is wrong, and fetching against the manifest alone
    would quietly make the disagreement permanent.
    """
    named = documented_revisions(card_text)
    for model in manifest.models:
        if model.upstream_revision not in named:
            raise PrismError(
                code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                message="The manifest pins a revision that docs/model-card.md does not "
                "name. Reconcile the two before fetching anything.",
                diagnostics={"role": model.role, "revision": model.upstream_revision},
            )
        if model.name not in card_text:
            raise PrismError(
                code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                message="The manifest pins a repository that docs/model-card.md does not "
                "name. Reconcile the two before fetching anything.",
                diagnostics={"role": model.role, "repository": model.name},
            )


def destination_root(path: Path) -> Path:
    """Canonical form of the target root.

    `verify_model_bundle` compares each resolved artifact path against the root it was
    given, so the root has to arrive canonical. A relative path — or, on Windows, an 8.3
    short name — resolves to a different spelling than the one passed in, and every
    artifact then looks like it escapes the root it was just written into.
    """
    return resolve_model_root(path)


def _contained(root: Path, relative: str) -> Path:
    """Resolve a manifest-relative path and confirm it stays beneath the root."""
    resolved_root = destination_root(root)
    candidate = (resolved_root / relative).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="A manifest path escapes the model root.",
            diagnostics={"path": relative},
        )
    return candidate


def plan(manifest: ModelManifest, destination_root: Path) -> tuple[Fetch, ...]:
    """Turn the committed manifest into the exact list of files to obtain."""
    fetches: list[Fetch] = []
    for model in manifest.models:
        revision = require_immutable_revision(model.upstream_revision, repo=model.name)
        directory = model.onnx_path.split("/", 1)[0]
        prefix = f"{directory}/"
        for entry in model.files:
            if not entry.path.startswith(prefix):
                raise PrismError(
                    code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                    message="A manifest file does not sit under its model's directory, so "
                    "its upstream path cannot be derived.",
                    diagnostics={"role": model.role, "path": entry.path},
                )
            fetches.append(
                Fetch(
                    role=model.role,
                    repo=model.name,
                    revision=revision,
                    remote_path=entry.path[len(prefix) :],
                    manifest_path=entry.path,
                    destination=_contained(destination_root, entry.path),
                    sha256=entry.sha256,
                    size=entry.bytes,
                )
            )
    return tuple(fetches)


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def already_present(fetch: Fetch) -> bool:
    """True only if the file on disk is already the exact artifact the manifest names."""
    if not fetch.destination.is_file() or fetch.destination.stat().st_size != fetch.size:
        return False
    return _sha256_of(fetch.destination) == fetch.sha256


def download(fetch: Fetch, opener: Opener) -> None:
    """Fetch one file, and move it into place only if it is byte-for-byte the pinned one.

    The response is read into a `.part` file with a hard ceiling of the manifest size plus
    one byte, so a hostile or wrong response cannot fill the disk before the hash is even
    checked.
    """
    url = fetch.url
    if not url.startswith(_PERMITTED_PREFIX):
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="An artifact URL does not address the pinned upstream over HTTPS.",
            diagnostics={"path": fetch.manifest_path, "repository": fetch.repo},
        )
    # scheme and host are fixed by the check above; nothing here is caller-supplied.
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310
    part = fetch.destination.with_name(fetch.destination.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    written = 0
    try:
        response = opener(request)
    except urllib.error.URLError as error:
        raise PrismError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            message="An artifact could not be fetched from upstream.",
            diagnostics={"path": fetch.manifest_path, "reason": type(error).__name__},
        ) from error

    try:
        status = getattr(response, "status", 200)
        if status != 200:
            raise PrismError(
                code=ErrorCode.MODEL_UNAVAILABLE,
                message="Upstream did not serve the artifact.",
                diagnostics={"path": fetch.manifest_path, "status": int(status)},
            )
        with part.open("wb") as handle:
            while written <= fetch.size:
                block = response.read(min(_BLOCK, fetch.size + 1 - written))
                if not block:
                    break
                written += len(block)
                digest.update(block)
                handle.write(block)
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()

    try:
        if written != fetch.size or digest.hexdigest() != fetch.sha256:
            raise PrismError(
                code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                message="A downloaded artifact does not match the committed manifest. It "
                "was discarded; nothing was written into the model root.",
                diagnostics={
                    "path": fetch.manifest_path,
                    "expected_bytes": fetch.size,
                    "actual_bytes": written,
                    "hash_matches": digest.hexdigest() == fetch.sha256,
                },
            )
    except PrismError:
        part.unlink(missing_ok=True)
        raise

    part.replace(fetch.destination)


def acquire(
    fetches: tuple[Fetch, ...],
    opener: Opener,
    *,
    log: Callable[[str], None] = print,
) -> int:
    """Obtain every planned file. Returns the number actually downloaded."""
    downloaded = 0
    for fetch in fetches:
        if already_present(fetch):
            log(f"  present {fetch.manifest_path}")
            continue
        log(f"  fetch   {fetch.manifest_path}  {fetch.size:,} bytes")
        download(fetch, opener)
        downloaded += 1
    return downloaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact URLs and identities that would be fetched, and fetch nothing",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=ARTIFACT_ROOT,
        help="where the bundle lands (default: models/artifacts; use the same path as "
        "PRISM_MODEL_ROOT when the weights live outside the working tree)",
    )
    args = parser.parse_args(argv)

    try:
        target = destination_root(args.destination)
        manifest = ModelManifest.model_validate_json(MANIFEST_PATH.read_text(encoding="utf-8"))
        assert_documented(manifest, MODEL_CARD_PATH.read_text(encoding="utf-8"))
        fetches = plan(manifest, target)

        print(f"anchor    {MANIFEST_PATH.relative_to(REPO_ROOT).as_posix()}")
        print(f"digest    {manifest.digest}")
        print(f"target    {target}")
        for model in manifest.models:
            print(f"  {model.role:10} {model.name} @ {model.upstream_revision}")

        if args.dry_run:
            for fetch in fetches:
                print(f"  would fetch {fetch.url}")
                print(f"    -> {fetch.manifest_path}  {fetch.size:,} bytes  sha256:{fetch.sha256}")
            total = sum(fetch.size for fetch in fetches)
            print(f"dry run: {len(fetches)} files, {total:,} bytes, nothing fetched")
            return 0

        downloaded = acquire(fetches, urllib.request.urlopen)
        verify_model_bundle(manifest, target)
    except PrismError as error:
        print(f"ACQUIRE FAILED [{error.code}] {error.message} {error.diagnostics}")
        return 1

    print(f"downloaded {downloaded} of {len(fetches)} files; bundle verifies against the manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
