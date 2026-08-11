"""The model trust anchor, and the acquisition step that ties it to upstream.

`models/artifacts/manifest.json` is committed, so a reviewer can see the identity of every
file PRISM will accept. Two things have to be true for that to mean anything:

* the anchor cannot be minted from whatever bytes are on disk — `--generate` must refuse
  to overwrite it without a deliberate override;
* the step that fetches the artifacts must be pinned to an immutable revision, must match
  the documented provenance, and must discard anything whose hash differs.

Each control below is given the exact input it exists to reject. A control that has only
ever been observed passing is not evidence.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from prism.errors import ErrorCode, PrismError
from prism.measure.models import ModelManifest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import acquire_models  # noqa: E402
import verify_models  # noqa: E402

MANIFEST_TEXT = (REPO_ROOT / "models" / "artifacts" / "manifest.json").read_text(encoding="utf-8")
MODEL_CARD_TEXT = (REPO_ROOT / "docs" / "model-card.md").read_text(encoding="utf-8")


def committed_manifest() -> ModelManifest:
    return ModelManifest.model_validate_json(MANIFEST_TEXT)


class FakeResponse:
    """The bytes a server chose to send, which is not the same as what was asked for."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self._offset = 0
        self.status = status
        self.closed = False

    def read(self, amount: int) -> bytes:
        block = self._body[self._offset : self._offset + amount]
        self._offset += len(block)
        return block

    def close(self) -> None:
        self.closed = True


def opener_for(body: bytes, status: int = 200) -> tuple[object, list[str]]:
    """Return an opener serving `body`, plus the list of URLs it was asked for."""
    seen: list[str] = []

    def opener(request: object) -> FakeResponse:
        seen.append(getattr(request, "full_url", ""))
        return FakeResponse(body, status)

    return opener, seen


# --------------------------------------------------------------------------------------
# a revision that can move is not an identity
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutable",
    [
        "main",
        "latest",
        "refs/heads/main",
        "v1.0.0",
        "1110a243fdf4",  # short hash: still ambiguous, still refused
        "1110A243FDF4706B3F48F1D95DB1A4F5529B4D41",  # uppercase is not the pinned spelling
        "1110a243fdf4706b3f48f1d95db1a4f5529b4d41 ",
        "",
    ],
)
def test_a_mutable_reference_is_refused_with_a_typed_error(mutable: str) -> None:
    with pytest.raises(PrismError) as raised:
        acquire_models.require_immutable_revision(mutable, repo="example/model")

    assert raised.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE
    assert raised.value.diagnostics["revision"] == mutable


def test_the_pinned_revisions_are_accepted() -> None:
    for model in committed_manifest().models:
        assert (
            acquire_models.require_immutable_revision(model.upstream_revision, repo=model.name)
            == model.upstream_revision
        )


# --------------------------------------------------------------------------------------
# the fetched bytes and the documented provenance cannot drift apart
# --------------------------------------------------------------------------------------


def test_the_committed_manifest_matches_the_model_card() -> None:
    """Both revisions and both repository names in the anchor are named in the card."""
    acquire_models.assert_documented(committed_manifest(), MODEL_CARD_TEXT)


def test_a_revision_the_model_card_does_not_name_is_refused() -> None:
    manifest = committed_manifest()
    card = MODEL_CARD_TEXT.replace(manifest.models[0].upstream_revision, "0" * 40)

    with pytest.raises(PrismError) as raised:
        acquire_models.assert_documented(manifest, card)

    assert raised.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE
    assert raised.value.diagnostics["revision"] == manifest.models[0].upstream_revision


def test_a_repository_the_model_card_does_not_name_is_refused() -> None:
    manifest = committed_manifest()
    card = MODEL_CARD_TEXT.replace(manifest.models[1].name, "someone-else/model")

    with pytest.raises(PrismError) as raised:
        acquire_models.assert_documented(manifest, card)

    assert raised.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE
    assert raised.value.diagnostics["repository"] == manifest.models[1].name


def test_recombining_two_documented_models_is_refused() -> None:
    """Both repositories and both revisions stay in the card; only the pairing changes.

    Checking membership of the repository and of the revision independently would pass
    this — which is the whole reason the card declares them as one record.
    """
    raw = json.loads(MANIFEST_TEXT)
    first, second = raw["models"][0], raw["models"][1]
    first["upstream_revision"], second["upstream_revision"] = (
        second["upstream_revision"],
        first["upstream_revision"],
    )
    swapped = ModelManifest.model_validate(_tuple_shaped(raw))

    for model in swapped.models:
        assert model.name in MODEL_CARD_TEXT
        assert model.upstream_revision in MODEL_CARD_TEXT

    with pytest.raises(PrismError) as raised:
        acquire_models.assert_documented(swapped, MODEL_CARD_TEXT)

    assert raised.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE
    assert raised.value.diagnostics["repository"] == swapped.models[0].name
    assert raised.value.diagnostics["revision"] == swapped.models[0].upstream_revision


def test_the_card_declares_exactly_the_pinned_pairs() -> None:
    manifest = committed_manifest()

    assert acquire_models.documented_pairs(MODEL_CARD_TEXT) == frozenset(
        (model.name, model.upstream_revision) for model in manifest.models
    )


# --------------------------------------------------------------------------------------
# the plan is derived from the anchor, not from a caller
# --------------------------------------------------------------------------------------


def test_every_planned_url_carries_the_pinned_revision(tmp_path: Path) -> None:
    manifest = committed_manifest()
    fetches = acquire_models.plan(manifest, tmp_path)

    assert len(fetches) == sum(len(model.files) for model in manifest.models)
    for fetch in fetches:
        assert fetch.url.startswith(
            f"https://huggingface.co/{fetch.repo}/resolve/{fetch.revision}/"
        )
        assert acquire_models.IMMUTABLE_REVISION.fullmatch(fetch.revision)
        # the manifest directory (e1/, e2/) is PRISM's layout, not upstream's
        assert not fetch.remote_path.startswith(("e1/", "e2/"))
        assert fetch.destination.is_relative_to(tmp_path)


def test_the_destination_root_is_canonicalised_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination that is merely a different spelling of the same directory — a
    relative path, or a Windows 8.3 short name — must resolve to the canonical form.
    `verify_model_bundle` compares each resolved artifact against the root it was handed,
    so an uncanonicalised root makes every file it just wrote look like an escape. This
    was observed: a full 13-file download landed correctly and then failed verification
    with MODEL_INTEGRITY_FAILURE on the first file."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bundle").mkdir()

    canonical = acquire_models.destination_root(Path("bundle"))

    assert canonical.is_absolute()
    assert canonical == (tmp_path / "bundle").resolve()

    manifest = committed_manifest()
    for fetch in acquire_models.plan(manifest, Path("bundle")):
        assert fetch.destination.is_relative_to(canonical)


def test_a_manifest_path_outside_its_model_directory_is_refused(tmp_path: Path) -> None:
    """The upstream path is derived by stripping the model directory. A file that does not
    sit under it has no derivable upstream path, and guessing one would fetch the wrong
    bytes from the right revision."""
    raw = json.loads(MANIFEST_TEXT)
    raw["models"][0]["files"][0]["path"] = "elsewhere/config.json"

    with pytest.raises(PrismError) as raised:
        acquire_models.plan(ModelManifest.model_validate(_tuple_shaped(raw)), tmp_path)

    assert raised.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE


def _tuple_shaped(raw: dict[str, object]) -> dict[str, object]:
    """Strict mode does not coerce list to tuple; rebuild the exact shape."""
    models = raw["models"]
    assert isinstance(models, list)
    for model in models:
        assert isinstance(model, dict)
        model["files"] = tuple(model["files"])
    raw["models"] = tuple(models)
    return raw


# --------------------------------------------------------------------------------------
# what lands on disk is what the manifest names, or nothing lands at all
# --------------------------------------------------------------------------------------


def one_fetch(tmp_path: Path, body: bytes) -> acquire_models.Fetch:
    """A fetch whose expected identity is the hash of `body`."""
    return acquire_models.Fetch(
        role="relevance",
        repo="example/model",
        revision="1" * 40,
        remote_path="config.json",
        manifest_path="e1/config.json",
        destination=tmp_path / "e1" / "config.json",
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body),
    )


def test_a_matching_download_is_moved_into_place(tmp_path: Path) -> None:
    body = b'{"hidden_size": 384}'
    fetch = one_fetch(tmp_path, body)
    opener, seen = opener_for(body)

    acquire_models.download(fetch, opener)  # type: ignore[arg-type]

    assert fetch.destination.read_bytes() == body
    assert seen == [fetch.url]
    assert not list(tmp_path.rglob("*.part"))
    assert acquire_models.already_present(fetch)


def test_the_predictable_part_name_is_never_written_through(tmp_path: Path) -> None:
    """A fixed `<artifact>.part` is a name an attacker who can write the destination
    directory first can occupy. The scratch file is created exclusively under an
    unguessable name instead, so anything already sitting at the obvious one is untouched."""
    body = b'{"hidden_size": 384}'
    fetch = one_fetch(tmp_path, body)
    fetch.destination.parent.mkdir(parents=True)
    decoy = fetch.destination.with_name(fetch.destination.name + ".part")
    decoy.write_bytes(b"not mine to overwrite")
    opener, _ = opener_for(body)

    acquire_models.download(fetch, opener)  # type: ignore[arg-type]

    assert fetch.destination.read_bytes() == body
    assert decoy.read_bytes() == b"not mine to overwrite"


def test_a_pre_created_part_symlink_is_not_followed(tmp_path: Path) -> None:
    """The threat the exclusive creation exists for: opening a fixed name for writing
    follows a symlink and overwrites its target outside the model root."""
    body = b'{"hidden_size": 384}'
    fetch = one_fetch(tmp_path, body)
    fetch.destination.parent.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"untouched")
    link = fetch.destination.with_name(fetch.destination.name + ".part")
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:  # unprivileged Windows, mainly
        pytest.skip(f"this platform will not create a symlink here: {type(exc).__name__}")
    opener, _ = opener_for(body)

    acquire_models.download(fetch, opener)  # type: ignore[arg-type]

    assert outside.read_bytes() == b"untouched"
    assert fetch.destination.read_bytes() == body


def test_a_download_that_does_not_match_the_manifest_is_discarded(tmp_path: Path) -> None:
    """Same length, different bytes: the size check alone would pass this."""
    body = b'{"hidden_size": 384}'
    fetch = one_fetch(tmp_path, body)
    opener, _ = opener_for(b'{"hidden_size": 999}')

    with pytest.raises(PrismError) as raised:
        acquire_models.download(fetch, opener)  # type: ignore[arg-type]

    assert raised.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE
    assert raised.value.diagnostics["hash_matches"] is False
    assert not fetch.destination.exists()
    assert not list(tmp_path.rglob("*.part"))


def test_a_response_longer_than_the_manifest_is_refused_and_bounded(tmp_path: Path) -> None:
    """The read is capped at the pinned size plus one byte, so an endless response cannot
    fill the disk before its hash is ever checked."""
    body = b"x" * 64
    fetch = one_fetch(tmp_path, body)
    opener, _ = opener_for(body + b"y" * 4096)

    with pytest.raises(PrismError) as raised:
        acquire_models.download(fetch, opener)  # type: ignore[arg-type]

    assert raised.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE
    assert raised.value.diagnostics["actual_bytes"] == fetch.size + 1
    assert not fetch.destination.exists()


def test_a_truncated_response_is_refused(tmp_path: Path) -> None:
    body = b"x" * 64
    fetch = one_fetch(tmp_path, body)
    opener, _ = opener_for(body[:32])

    with pytest.raises(PrismError) as raised:
        acquire_models.download(fetch, opener)  # type: ignore[arg-type]

    assert raised.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE
    assert not fetch.destination.exists()


def test_a_non_200_response_is_a_typed_unavailability(tmp_path: Path) -> None:
    body = b"<html>404</html>"
    fetch = one_fetch(tmp_path, body)
    opener, _ = opener_for(body, status=404)

    with pytest.raises(PrismError) as raised:
        acquire_models.download(fetch, opener)  # type: ignore[arg-type]

    assert raised.value.code is ErrorCode.MODEL_UNAVAILABLE
    assert not fetch.destination.exists()


def test_an_artifact_already_present_is_not_fetched_again(tmp_path: Path) -> None:
    body = b'{"hidden_size": 384}'
    fetch = one_fetch(tmp_path, body)
    fetch.destination.parent.mkdir(parents=True)
    fetch.destination.write_bytes(body)
    opener, seen = opener_for(b"")

    assert acquire_models.acquire((fetch,), opener, log=lambda _: None) == 0  # type: ignore[arg-type]
    assert seen == []


def test_a_tampered_local_file_is_not_treated_as_present(tmp_path: Path) -> None:
    body = b'{"hidden_size": 384}'
    fetch = one_fetch(tmp_path, body)
    fetch.destination.parent.mkdir(parents=True)
    fetch.destination.write_bytes(b'{"hidden_size": 999}')

    assert not acquire_models.already_present(fetch)


# --------------------------------------------------------------------------------------
# the anchor cannot be minted over
# --------------------------------------------------------------------------------------


def test_generate_refuses_to_overwrite_an_existing_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(verify_models, "MANIFEST_PATH", manifest)
        patch.setattr(verify_models, "ARTIFACT_ROOT", tmp_path)
        with pytest.raises(SystemExit) as raised:
            verify_models.generate(allow_overwrite=False)

    assert "refusing to overwrite" in str(raised.value)
    assert "--overwrite-manifest" in str(raised.value)
    assert manifest.read_text(encoding="utf-8") == MANIFEST_TEXT


def test_the_override_is_what_opens_the_gate(tmp_path: Path) -> None:
    """With the override the anchor guard is passed — and generation then stops on the
    missing artifacts rather than on the guard, which is how we know the flag is the only
    thing standing there."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(MANIFEST_TEXT, encoding="utf-8")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(verify_models, "MANIFEST_PATH", manifest)
        patch.setattr(verify_models, "ARTIFACT_ROOT", tmp_path)
        with pytest.raises(SystemExit) as raised:
            verify_models.generate(allow_overwrite=True)

    assert "missing artifact directory" in str(raised.value)
    assert manifest.read_text(encoding="utf-8") == MANIFEST_TEXT


def test_the_override_alone_is_rejected() -> None:
    """`--overwrite-manifest` without `--generate` would read as "I have permission to
    rewrite the anchor" while doing a read-only verify. Refuse the ambiguity."""
    with pytest.raises(SystemExit) as raised:
        verify_models.main(["--overwrite-manifest"])

    assert raised.value.code == 2


def test_the_committed_manifest_is_the_documented_anchor() -> None:
    """The claim in models/README.md is load-bearing: if the acquisition script stops
    being the tie to upstream, the sentence that says it is must fail too."""
    readme = (REPO_ROOT / "models" / "README.md").read_text(encoding="utf-8")

    assert "scripts/acquire_models.py" in readme
    assert "--overwrite-manifest" in readme
    assert (REPO_ROOT / "scripts" / "acquire_models.py").is_file()
