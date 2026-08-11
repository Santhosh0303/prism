"""Shared fixtures for measurement tests.

Most of these tests must not touch ONNX Runtime. Pure arithmetic deserves exact
assertions: synthetic-vector arithmetic is exact, and no encoder is invoked in pure-math
tests. The fake encoder below makes relevance and
contradiction *inputs* to the arithmetic rather than something to be discovered, so a
denominator bug cannot hide behind model noise.
"""

from __future__ import annotations

import numpy as np
import pytest

from prism.contracts import (
    CandidatePacket,
    Claim,
    EvidenceStatus,
    ProvenanceStatus,
)
from prism.measure.segment import NormalizedCandidate, normalize_claims


class FakeEncoders:
    """Deterministic stand-in for the E1/E2 pair.

    Relevance is driven by a shared keyword, and contradiction by the literal token
    "NOT-" appearing in exactly one side of a pair. Both are crude on purpose: the point
    is that the arithmetic downstream is exercised with known inputs.
    """

    def __init__(self, dimensions: int = 8) -> None:
        self.dimensions = dimensions
        self.embed_calls = 0
        self.nli_calls = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        self.embed_calls += 1
        vectors = []
        for text in texts:
            vector = np.zeros(self.dimensions, dtype=np.float32)
            # Bucket by the first token so claims sharing a subject land on the same axis.
            subject = text.split()[0].strip(".,").casefold().removeprefix("not-")
            vector[hash(subject) % self.dimensions] = 1.0
            vectors.append(vector)
        return np.array(vectors, dtype=np.float32)

    def contradiction_probabilities(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        self.nli_calls += 1
        scores = []
        for premise, hypothesis in pairs:
            negated = ("NOT-" in premise) != ("NOT-" in hypothesis)
            scores.append(0.99 if negated else 0.01)
        return np.array(scores, dtype=np.float32)


@pytest.fixture
def encoders() -> FakeEncoders:
    return FakeEncoders()


def make_claim(claim_id: str, text: str, confidence: int | None = 70) -> Claim:
    return Claim(
        claim_id=claim_id,
        text=text,
        confidence=confidence,
        evidence_status=EvidenceStatus.INFERRED,
    )


def make_packet(
    candidate_id: str,
    texts: list[str],
    source_group_id: str = "host-pass-001",
    provenance: ProvenanceStatus = ProvenanceStatus.DECLARED_UNVERIFIED,
) -> CandidatePacket:
    return CandidatePacket(
        candidate_id=candidate_id,
        source_group_id=source_group_id,
        source_label=f"label-{candidate_id}",
        provenance_status=provenance,
        perspective=candidate_id,
        claims=tuple(
            make_claim(f"{candidate_id}-{index + 1}", text) for index, text in enumerate(texts)
        ),
    )


def normalized(packets: list[CandidatePacket]) -> tuple[NormalizedCandidate, ...]:
    return tuple(normalize_claims(packet)[0] for packet in packets)


#: Eight content words, which is exactly the floor, so tests can vary one thing at a time.
FILLER = "alpha bravo charlie delta echo foxtrot golf hotel"
