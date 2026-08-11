"""Verified offline encoder sessions.

Trust boundary 2. Everything in this module exists because a model file is code that runs
with the ambient rights of this process, and a hash check is the only thing standing
between "the artifact we pinned" and "an artifact someone replaced".

The verification order is deliberate: **verify, then load**. Every listed file is resolved
to a canonical path, confirmed to sit beneath the model root, confirmed to be a regular
file rather than a link, size-checked, and hashed — before ONNX Runtime is allowed to open
anything. A mismatch raises ``MODEL_INTEGRITY_FAILURE`` and measurement fails closed;
preflight keeps working.

Honest limitation, stated rather than buried: the external-data check is a bounded byte
scan for the ONNX external-data markers plus a sibling-file check. It is not a full
protobuf parse, because parsing would require an `onnx` dependency that the six-package
runtime budget does not have room for. It catches the realistic case — a graph that points
at weights outside the verified set — and it is recorded as a known limitation in the
model card rather than presented as proof.
"""

from __future__ import annotations

import hashlib
import mmap
import os
import threading
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
import onnxruntime as ort
from pydantic import BaseModel, ConfigDict, Field
from tokenizers import Tokenizer

from ..constants import (
    DISABLE_MEASURE_ENV_VAR,
    MODEL_MANIFEST_FILENAME,
    MODEL_ROOT_ENV_VAR,
    REQUIRED_EXECUTION_PROVIDER,
)
from ..errors import ErrorCode, PrismError

#: Byte markers that appear in an ONNX graph that references weights held outside the
#: file. Any hit fails verification in v1.
_EXTERNAL_DATA_MARKERS: Final[tuple[bytes, ...]] = (b"external_data", b"_ext_data")

#: Companion-file suffixes an external-data graph would create alongside the model.
_EXTERNAL_DATA_SUFFIXES: Final[tuple[str, ...]] = (".onnx_data", ".onnx.data", ".data", ".bin")

#: Maximum tokens per sequence. Both encoders are 512-position models; claims are capped
#: at 80 words, so this is headroom rather than a limit that bites.
MAX_SEQUENCE_LENGTH: Final[int] = 256

#: Conservative thread settings. Fixed rather than adaptive so that latency is
#: reproducible across runs on the same hardware.
INTRA_OP_THREADS: Final[int] = 2
INTER_OP_THREADS: Final[int] = 1


class ManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    #: Relative to the model root. Absolute paths and traversal are rejected at load.
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    role: Literal["relevance", "nli"]
    name: str
    #: The immutable upstream revision, resolved BEFORE the artifacts were accepted.
    #: "whatever the repository returned at that instant" is not an identity.
    upstream_revision: str = Field(min_length=40, max_length=64)
    licence: str
    onnx_path: str
    tokenizer_path: str
    files: tuple[ManifestFile, ...] = Field(min_length=1)
    #: For the NLI model: which output index carries the contradiction probability. A
    #: wrong value here would invert every measurement, so it is pinned, not assumed.
    contradiction_index: int | None = Field(default=None, ge=0)
    id2label: dict[str, str] | None = None


class MeasuredProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    reference_hardware: str = Field(min_length=1)
    combined_peak_rss_bytes: int = Field(gt=0)
    cold_start_ms: float = Field(gt=0)
    warm_p95_ms: float = Field(gt=0)


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"] = "1.0"
    execution_provider: Literal["CPUExecutionProvider"] = "CPUExecutionProvider"
    models: tuple[ManifestModel, ...] = Field(min_length=2, max_length=2)
    measured: MeasuredProfile | None = None

    def by_role(self, role: str) -> ManifestModel:
        for model in self.models:
            if model.role == role:
                return model
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="The model manifest does not declare a required role.",
            diagnostics={"missing_role": role},
        )

    @property
    def digest(self) -> str:
        from ..canonical import canonical_digest

        return canonical_digest(self)


# --------------------------------------------------------------------------------------
# location
# --------------------------------------------------------------------------------------


def model_root() -> Path:
    """Resolve the dedicated read-only model root."""
    override = os.environ.get(MODEL_ROOT_ENV_VAR)
    if override:
        return Path(override).resolve()
    return (Path(__file__).resolve().parents[3] / "models" / "artifacts").resolve()


def resolve_model_root(root: Path | None = None) -> Path:
    """Canonical form of a caller-supplied root, or the default root.

    Session identity is keyed on this value, so two spellings of one directory must not
    look like two roots.
    """
    return root.resolve() if root is not None else model_root()


def manifest_present(root: Path | None = None) -> bool:
    """Cheap presence check: is there a manifest to verify at all?

    Presence is not verification. Shallow health uses this to avoid claiming measurement
    is available in a clone that carries no bundle; deep health is what actually hashes.
    """
    return (resolve_model_root(root) / MODEL_MANIFEST_FILENAME).is_file()


def measurement_disabled() -> bool:
    """True when the kill switch is set."""
    return os.environ.get(DISABLE_MEASURE_ENV_VAR, "") not in {"", "0", "false", "False"}


def load_manifest(root: Path | None = None) -> ModelManifest:
    base = root if root is not None else model_root()
    path = base / MODEL_MANIFEST_FILENAME
    if not path.is_file():
        raise PrismError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            message="No model manifest was found beneath the model root. Measurement is "
            "unavailable; preflight remains fully operational.",
            diagnostics={"manifest_filename": MODEL_MANIFEST_FILENAME},
        )
    try:
        return ModelManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except PrismError:
        raise
    except Exception as exc:
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="The model manifest is malformed.",
            diagnostics={"error_type": type(exc).__name__},
        ) from None


# --------------------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------------------


def _resolve_contained(root: Path, relative: str) -> Path:
    """Resolve a manifest path and prove it stays beneath the root.

    Rejects absolute paths, traversal, and links. ``Path.resolve`` follows symlinks, so
    the containment check is performed on the resolved path and the link check on the
    unresolved one — a link that resolves inside the root is still a link, and v1 does
    not accept them.

    Hard links are rejected too, by link count. A hard link is a second name for the
    same inode, so an artifact with ``st_nlink > 1`` can be rewritten through a name
    outside the verified root between this check and the ONNX session opening it. The
    threat model names unexpected hard links as a rejected case "where detectable";
    ``st_nlink`` is reported on Windows and POSIX alike, so here it is detectable.
    """
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="A manifest path is absolute or contains a traversal segment.",
            diagnostics={"path": relative},
        )
    candidate = root / relative
    if candidate.is_symlink():
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="A model artifact is a symbolic link. Links are not accepted.",
            diagnostics={"path": relative},
        )
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="A model artifact resolves outside the model root.",
            diagnostics={"path": relative},
        )
    if not resolved.is_file():
        raise PrismError(
            code=ErrorCode.MODEL_UNAVAILABLE,
            message="A model artifact listed in the manifest is missing.",
            diagnostics={"path": relative},
        )
    link_count = resolved.stat().st_nlink
    if link_count > 1:
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="A model artifact has more than one hard link. Unexpected hard links "
            "are not accepted.",
            diagnostics={"path": relative, "link_count": link_count},
        )
    return resolved


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_external_data(path: Path) -> None:
    """Bounded scan for external-data references and companion files."""
    for sibling in path.parent.iterdir():
        if sibling == path:
            continue
        if sibling.name.startswith(path.stem) and sibling.suffix in _EXTERNAL_DATA_SUFFIXES:
            raise PrismError(
                code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                message="An external-data companion file sits beside a model graph.",
                diagnostics={"companion": sibling.name},
            )
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
        for marker in _EXTERNAL_DATA_MARKERS:
            if view.find(marker) != -1:
                raise PrismError(
                    code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                    message="The model graph references external data, which v1 does not accept.",
                    diagnostics={"marker": marker.decode("ascii")},
                )


def verify_model_bundle(manifest: ModelManifest, root: Path | None = None) -> dict[str, Path]:
    """Verify every artifact and return the resolved paths.

    Nothing is opened by ONNX Runtime until this returns. Raises
    ``MODEL_INTEGRITY_FAILURE`` on any mismatch — there is no repair path and no
    "continue anyway" flag.
    """
    base = root if root is not None else model_root()
    resolved: dict[str, Path] = {}

    for model in manifest.models:
        for entry in model.files:
            path = _resolve_contained(base, entry.path)
            actual_size = path.stat().st_size
            if actual_size != entry.bytes:
                raise PrismError(
                    code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                    message="A model artifact has an unexpected size.",
                    diagnostics={
                        "path": entry.path,
                        "expected_bytes": entry.bytes,
                        "actual_bytes": actual_size,
                    },
                )
            actual_hash = _sha256_of(path)
            if actual_hash != entry.sha256:
                raise PrismError(
                    code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                    message="A model artifact does not match its pinned SHA-256.",
                    diagnostics={"path": entry.path},
                )
            resolved[f"{model.role}:{entry.path}"] = path

        graph = _resolve_contained(base, model.onnx_path)
        _reject_external_data(graph)
        resolved[f"{model.role}:graph"] = graph
        resolved[f"{model.role}:tokenizer"] = _resolve_contained(base, model.tokenizer_path)

    return resolved


# --------------------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------------------


def _session_options() -> ort.SessionOptions:
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = INTRA_OP_THREADS
    options.inter_op_num_threads = INTER_OP_THREADS
    options.log_severity_level = 3
    return options


def _load_tokenizer(path: Path) -> Tokenizer:
    """Load a tokenizer and fix its encoding parameters once, at construction.

    Truncation and padding used to be re-enabled inside every encode call. Two
    measurements may run concurrently and they share these objects, so one thread was
    mutating encoder state another thread was already encoding against. Configuring here
    means the shared tokenizers are read-only for the life of the process.
    """
    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.enable_truncation(max_length=MAX_SEQUENCE_LENGTH)
    tokenizer.enable_padding()
    return tokenizer


def _create_session(path: Path) -> ort.InferenceSession:
    session = ort.InferenceSession(
        str(path),
        sess_options=_session_options(),
        providers=[REQUIRED_EXECUTION_PROVIDER],
    )
    actual = session.get_providers()
    if actual != [REQUIRED_EXECUTION_PROVIDER]:
        # Requesting CPU is not the same as getting it. An unexpected provider means the
        # runtime is not the one that was reviewed.
        raise PrismError(
            code=ErrorCode.MODEL_INTEGRITY_FAILURE,
            message="ONNX Runtime did not honour the CPU-only execution provider.",
            diagnostics={"providers": ",".join(actual)},
        )
    return session


class ModelSessions:
    """Lazily created, process-wide singleton pair of encoder sessions.

    Concurrent cold callers share one initialisation. Duplicate sessions are a
    release-blocking leak, not a performance detail: two copies of E2 would double a
    328 MB resident footprint and quietly breach the resident-memory budget. The
    stress suite asserts that ten simultaneous cold callers create exactly one pair.

    One pair per process is a memory decision, not a licence to ignore which bundle was
    asked for. The cached pair carries the canonical root it was built from and the
    digest of the manifest it was verified against; a request naming a different root, or
    the same root whose manifest has since changed, raises rather than being served the
    first bundle under the second bundle's name.
    """

    _instance: ModelSessions | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        manifest: ModelManifest,
        relevance_session: ort.InferenceSession,
        relevance_tokenizer: Tokenizer,
        nli_session: ort.InferenceSession,
        nli_tokenizer: Tokenizer,
        root: Path | None = None,
    ) -> None:
        self.root = resolve_model_root(root)
        self.manifest_digest = manifest.digest
        self.manifest = manifest
        self._relevance_session = relevance_session
        self._relevance_tokenizer = relevance_tokenizer
        self._nli_session = nli_session
        self._nli_tokenizer = nli_tokenizer
        self._contradiction_index = manifest.by_role("nli").contradiction_index
        if self._contradiction_index is None:
            raise PrismError(
                code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                message="The NLI model does not declare which output index carries the "
                "contradiction probability.",
            )

    # -- lifecycle ---------------------------------------------------------------------

    @classmethod
    def get(cls, root: Path | None = None) -> ModelSessions:
        if measurement_disabled():
            raise PrismError(
                code=ErrorCode.MEASURE_DISABLED,
                message="Measurement is disabled by the kill switch. Preflight and "
                "synthesis remain available.",
                diagnostics={"env_var": DISABLE_MEASURE_ENV_VAR},
            )
        requested = resolve_model_root(root)
        cached = cls._instance
        if cached is not None:
            cached._assert_identity(requested)
            return cached
        with cls._lock:
            # Re-checked inside the lock: ten simultaneous first callers must produce
            # exactly one E1 and one E2 session.
            if cls._instance is None:
                cls._instance = cls._build(requested)
            else:
                cls._instance._assert_identity(requested)
        return cls._instance

    def _assert_identity(self, root: Path) -> None:
        """Confirm the cached pair is the bundle this caller asked for.

        Serving a cached bundle for a root it did not come from would make the reported
        ``model_manifest_hash`` describe artifacts that were never loaded, which is worse
        than being unavailable. The manifest is re-read here — a few kilobytes against a
        measurement measured in seconds — so a manifest swapped beneath a live process is
        caught rather than papered over by the cache.
        """
        if root != self.root:
            raise PrismError(
                code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                message="Encoder sessions were already created from a different model "
                "root. One process serves one model bundle.",
                diagnostics={"reason": "model_root_mismatch"},
            )
        if load_manifest(root).digest != self.manifest_digest:
            raise PrismError(
                code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                message="The model manifest changed after the encoder sessions were "
                "created. Restart before measuring again.",
                diagnostics={"reason": "manifest_digest_mismatch"},
            )

    @classmethod
    def _build(cls, root: Path) -> ModelSessions:
        manifest = load_manifest(root)
        paths = verify_model_bundle(manifest, root)
        return cls(
            manifest=manifest,
            relevance_session=_create_session(paths["relevance:graph"]),
            relevance_tokenizer=_load_tokenizer(paths["relevance:tokenizer"]),
            nli_session=_create_session(paths["nli:graph"]),
            nli_tokenizer=_load_tokenizer(paths["nli:tokenizer"]),
            root=root,
        )

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton. Test and restart support only."""
        with cls._lock:
            cls._instance = None

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._instance is not None

    # -- inference ---------------------------------------------------------------------

    @staticmethod
    def _encode_batch(
        tokenizer: Tokenizer, session: ort.InferenceSession, texts: list[str]
    ) -> dict[str, np.ndarray]:
        # No tokenizer configuration here: these objects are shared by concurrent
        # measurements and are configured once, in _load_tokenizer.
        encodings = tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed: dict[str, np.ndarray] = {}
        expected = {i.name for i in session.get_inputs()}
        if "input_ids" in expected:
            feed["input_ids"] = input_ids
        if "attention_mask" in expected:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in expected:
            feed["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)
        return feed

    def embed(self, texts: list[str]) -> np.ndarray:
        """Mean-pooled, L2-normalised sentence embeddings from E1.

        E1's only job is same-subject relevance. It never produces agreement,
        contradiction, truth, or confidence.
        """
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        feed = self._encode_batch(self._relevance_tokenizer, self._relevance_session, texts)
        outputs = self._relevance_session.run(None, feed)
        hidden = np.asarray(outputs[0], dtype=np.float32)
        mask = feed["attention_mask"].astype(np.float32)[..., None]
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return pooled / np.clip(norms, 1e-9, None)

    def contradiction_probabilities(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        """Contradiction probability for each ordered (premise, hypothesis) pair.

        The index is read from the manifest rather than assumed, because label order
        differs between NLI checkpoints and guessing it would invert the result silently.
        """
        if not pairs:
            return np.zeros((0,), dtype=np.float32)
        encodings = self._nli_tokenizer.encode_batch(pairs)
        feed: dict[str, np.ndarray] = {
            "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
        }
        expected = {i.name for i in self._nli_session.get_inputs()}
        feed = {name: value for name, value in feed.items() if name in expected}
        if "token_type_ids" in expected:
            feed["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)
        logits = np.asarray(self._nli_session.run(None, feed)[0], dtype=np.float32)
        shifted = logits - logits.max(axis=1, keepdims=True)
        exponentials = np.exp(shifted)
        probabilities = exponentials / exponentials.sum(axis=1, keepdims=True)
        index = self._contradiction_index
        if index is None:  # pragma: no cover - established in __init__
            raise PrismError(
                code=ErrorCode.MODEL_INTEGRITY_FAILURE,
                message="The NLI contradiction index is unset.",
            )
        return probabilities[:, index]

    def describe(self) -> dict[str, Any]:
        """Content-free description for deep health output."""
        return {
            "model_manifest_hash": self.manifest.digest,
            "execution_provider": REQUIRED_EXECUTION_PROVIDER,
            "relevance_model": self.manifest.by_role("relevance").name,
            "relevance_revision": self.manifest.by_role("relevance").upstream_revision,
            "nli_model": self.manifest.by_role("nli").name,
            "nli_revision": self.manifest.by_role("nli").upstream_revision,
            "contradiction_index": self._contradiction_index,
        }
