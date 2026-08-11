"""Session identity: one process serves one model bundle, and says so.

The cache exists for memory, not for convenience: a second copy of E2 is another 328 MB.
But a cache that answers every request with whatever it built first will hand a caller the
wrong bundle under the right name, and the resulting report carries a
``model_manifest_hash`` for artifacts that were never loaded. These tests pin the identity
rule rather than the caching behaviour.

No ONNX Runtime here. Session and tokenizer construction are stubbed so the identity logic
is exercised on its own; the real bundle is covered by the ``models``-marked tests.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from prism.constants import (
    DISABLE_MEASURE_ENV_VAR,
    MODEL_MANIFEST_FILENAME,
    MODEL_ROOT_ENV_VAR,
)
from prism.errors import ErrorCode, PrismError
from prism.measure import models as models_module
from prism.measure.models import ModelSessions

PAYLOAD: bytes = b"fake-onnx"


def write_bundle(root: Path, licence: str = "Apache-2.0") -> None:
    """Write a hash-consistent fake bundle, manifest included."""
    graph_digest = hashlib.sha256(PAYLOAD).hexdigest()
    tokenizer_digest = hashlib.sha256(b"{}").hexdigest()
    models = []
    for role, directory, contradiction_index in (
        ("relevance", "e1", None),
        ("nli", "e2", 0),
    ):
        (root / directory / "onnx").mkdir(parents=True, exist_ok=True)
        (root / directory / "onnx" / "model.onnx").write_bytes(PAYLOAD)
        (root / directory / "tokenizer.json").write_bytes(b"{}")
        models.append(
            {
                "role": role,
                "name": f"test/{directory}",
                "upstream_revision": directory[0] * 40,
                "licence": licence,
                "onnx_path": f"{directory}/onnx/model.onnx",
                "tokenizer_path": f"{directory}/tokenizer.json",
                "files": [
                    {
                        "path": f"{directory}/onnx/model.onnx",
                        "sha256": graph_digest,
                        "bytes": len(PAYLOAD),
                    },
                    {
                        "path": f"{directory}/tokenizer.json",
                        "sha256": tokenizer_digest,
                        "bytes": 2,
                    },
                ],
                "contradiction_index": contradiction_index,
                "id2label": None,
            }
        )
    (root / MODEL_MANIFEST_FILENAME).write_text(json.dumps({"models": models}), encoding="utf-8")


@pytest.fixture(autouse=True)
def stubbed_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No ONNX, no kill switch, no inherited root, no leaked singleton."""
    monkeypatch.delenv(DISABLE_MEASURE_ENV_VAR, raising=False)
    monkeypatch.delenv(MODEL_ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(models_module, "_create_session", lambda path: object())
    monkeypatch.setattr(models_module, "_load_tokenizer", lambda path: object())
    ModelSessions.reset()
    yield
    ModelSessions.reset()


def test_the_same_root_returns_the_same_pair(tmp_path: Path) -> None:
    """The one-pair-per-process guarantee still holds."""
    write_bundle(tmp_path)
    assert ModelSessions.get(tmp_path) is ModelSessions.get(tmp_path)


def test_a_second_root_raises_instead_of_inheriting_the_first_bundle(tmp_path: Path) -> None:
    """The audit case: two services, two roots, one process."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_bundle(first)
    write_bundle(second, licence="MIT")

    sessions = ModelSessions.get(first)

    with pytest.raises(PrismError) as excinfo:
        ModelSessions.get(second)
    assert excinfo.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE
    assert excinfo.value.diagnostics["reason"] == "model_root_mismatch"
    # And the caller that asked for the first root is still served.
    assert ModelSessions.get(first) is sessions


def test_a_manifest_swapped_beneath_a_live_process_raises(tmp_path: Path) -> None:
    """Same root, different trust anchor: the cache must not paper over it."""
    write_bundle(tmp_path)
    ModelSessions.get(tmp_path)

    write_bundle(tmp_path, licence="MIT")

    with pytest.raises(PrismError) as excinfo:
        ModelSessions.get(tmp_path)
    assert excinfo.value.code is ErrorCode.MODEL_INTEGRITY_FAILURE
    assert excinfo.value.diagnostics["reason"] == "manifest_digest_mismatch"


def test_another_spelling_of_one_root_is_not_a_second_root(tmp_path: Path) -> None:
    """Identity is the canonical path, so ``root/e1/..`` is not a mismatch."""
    write_bundle(tmp_path)
    sessions = ModelSessions.get(tmp_path)
    assert ModelSessions.get(tmp_path / "e1" / "..") is sessions


def test_the_recorded_identity_is_the_canonical_root_and_manifest_digest(
    tmp_path: Path,
) -> None:
    write_bundle(tmp_path)
    sessions = ModelSessions.get(tmp_path)
    assert sessions.root == tmp_path.resolve()
    assert sessions.manifest_digest == sessions.manifest.digest
