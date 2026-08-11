"""Two measurements may run at once, so the encoders they share must be read-only.

Truncation and padding were re-enabled on the shared tokenizers inside every encode call.
Two concurrent measurements meant one thread mutating encoder configuration while another
was already encoding against it — a data race whose visible symptom would be a silently
mis-padded batch, not a crash.

These are `models`-marked: they need the real bundle and are deselected in CI, which is
correct, and they must actually be run locally.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from prism.measure.models import MAX_SEQUENCE_LENGTH, ModelSessions, measurement_disabled

#: Deliberately ragged: one short, one mid, one past the truncation ceiling. If padding or
#: truncation state is disturbed mid-flight, ragged input is where it shows.
TEXTS: list[str] = [
    "The service is available.",
    "The deployment finished at noon and every regional replica reported healthy.",
    " ".join(["token"] * (MAX_SEQUENCE_LENGTH * 2)),
]

PAIRS: list[tuple[str, str]] = [
    ("The service is available.", "The service is not available."),
    ("The deployment finished at noon.", "The deployment finished at noon."),
    (" ".join(["token"] * (MAX_SEQUENCE_LENGTH * 2)), "The release was cancelled."),
]


@pytest.mark.stress
@pytest.mark.models
def test_shared_tokenizers_are_configured_once_not_per_call() -> None:
    """Configuration belongs to construction; encoding must not touch it."""
    if measurement_disabled():
        pytest.skip("kill switch active")
    sessions = ModelSessions.get()

    for tokenizer in (sessions._relevance_tokenizer, sessions._nli_tokenizer):
        assert tokenizer.truncation is not None
        assert tokenizer.truncation["max_length"] == MAX_SEQUENCE_LENGTH
        assert tokenizer.padding is not None

    sessions.embed(TEXTS)
    sessions.contradiction_probabilities(PAIRS)

    for tokenizer in (sessions._relevance_tokenizer, sessions._nli_tokenizer):
        assert tokenizer.truncation is not None, "encoding cleared the truncation config"
        assert tokenizer.truncation["max_length"] == MAX_SEQUENCE_LENGTH
        assert tokenizer.padding is not None, "encoding cleared the padding config"


@pytest.mark.stress
@pytest.mark.models
def test_concurrent_encoding_agrees_with_serial_encoding() -> None:
    """Eight threads, ragged batches, same answers as one thread alone."""
    if measurement_disabled():
        pytest.skip("kill switch active")
    sessions = ModelSessions.get()

    serial_embeddings = sessions.embed(TEXTS)
    serial_probabilities = sessions.contradiction_probabilities(PAIRS)

    workers = 8
    barrier = threading.Barrier(workers)

    def race(index: int) -> tuple[np.ndarray, np.ndarray]:
        barrier.wait()
        # Alternate the batch shape per thread so the threads are not all encoding an
        # identically shaped batch, which is the case a shared padding config survives.
        texts = TEXTS if index % 2 == 0 else list(reversed(TEXTS))
        embeddings = sessions.embed(texts)
        if index % 2:
            embeddings = embeddings[::-1]
        return embeddings, sessions.contradiction_probabilities(PAIRS)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(race, range(workers)))

    # Tolerance, not equality: ONNX Runtime is free to reassociate reductions across
    # threads. A disturbed padding or truncation state moves results far further than
    # this, because it changes which tokens are attended to at all.
    for embeddings, probabilities in results:
        np.testing.assert_allclose(embeddings, serial_embeddings, rtol=0, atol=1e-5)
        np.testing.assert_allclose(probabilities, serial_probabilities, rtol=0, atol=1e-5)
