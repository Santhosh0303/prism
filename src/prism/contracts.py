"""Public contracts.

This module is the only place a public field shape is defined. Adapters serialise these
models; they never invent a parallel schema, because a second definition of the same
shape is a second thing to keep in sync and the two will drift.

Three rules govern everything here:

* **Inputs are data.** Task and claim text is validated, never interpreted — text that
  arrives from a caller must not be able to steer the system that measures it.
* **Outputs are immutable and complete.** A partial report is not a report: a consumer
  cannot tell a missing field from a field that was measured and found empty, so every
  report is emitted whole or not at all.
* **Provenance is declared, not inferred.** ``source_group_id`` is the only thing that
  can raise source diversity; ``source_label`` is display-only. Guessing independence
  from labels would let two copies of one source look like corroboration.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, NamedTuple, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .constants import (
    CALIBRATION_HUMAN_VALIDATED,
    CALIBRATION_UNCALIBRATED,
    PACKAGED_ORIGIN,
    RegistryOrigin,
)
from .limits import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_CANDIDATES,
    MAX_CLAIMS_PER_CANDIDATE,
    MAX_IDENTIFIER_CHARS,
    MAX_SOURCE_LABEL_CHARS,
    MAX_TASK_WORDS,
    MAX_WORDS_PER_CLAIM,
    MIN_CANDIDATES,
    MIN_CONTENT_WORDS,
)
from .version import SCHEMA_VERSION

if SCHEMA_VERSION != "1.0":  # pragma: no cover - module-load invariant
    raise RuntimeError(
        "The Literal annotations in this module are pinned to schema 1.0 and no "
        f"longer match SCHEMA_VERSION={SCHEMA_VERSION!r}."
    )

# --------------------------------------------------------------------------------------
# enums
# --------------------------------------------------------------------------------------


class PrismMode(StrEnum):
    LITE = "lite"
    STANDARD = "standard"
    CRITICAL = "critical"


class PrismStatus(StrEnum):
    """Outcome of an operation. Deliberately orthogonal to :class:`SourceDiversity`."""

    OK = "OK"
    INSUFFICIENT = "INSUFFICIENT"
    ERROR = "ERROR"


class SourceDiversity(StrEnum):
    SINGLE_SOURCE = "SINGLE_SOURCE"
    MULTI_SOURCE = "MULTI_SOURCE"


class ProvenanceStatus(StrEnum):
    DECLARED_UNVERIFIED = "DECLARED_UNVERIFIED"
    EXTERNALLY_ATTESTED = "EXTERNALLY_ATTESTED"


class AgreementType(StrEnum):
    """Note the absence of INDEPENDENT and CONVERGENT: PRISM cannot establish either,
    and an architecture gate fails the build if those labels reappear."""

    MULTI_SOURCE_AGREEMENT = "MULTI_SOURCE_AGREEMENT"
    SINGLE_SOURCE_AGREEMENT = "SINGLE_SOURCE_AGREEMENT"
    CONTESTED = "CONTESTED"
    UNCLEAR = "UNCLEAR"


class EvidenceStatus(StrEnum):
    """How the host says it arrived at a claim. Declared, never verified by PRISM."""

    OBSERVED = "OBSERVED"
    CITED = "CITED"
    INFERRED = "INFERRED"
    ASSUMED = "ASSUMED"


class ScopeResult(StrEnum):
    """Tri-state by design. UNCERTAIN pairs stay in the denominator so a heuristic
    cannot hide a real contradiction."""

    SAME_SCOPE = "SAME_SCOPE"
    SCOPE_DIVERGENT = "SCOPE_DIVERGENT"
    UNCERTAIN = "UNCERTAIN"


class RetentionReason(StrEnum):
    """The four declared reasons a claim survives synthesis. Retention is not
    endorsement, and the field is deliberately not called 'correct minority'."""

    UNIQUE_EVIDENCE = "UNIQUE_EVIDENCE"
    DISTINCT_CAUSAL_MECHANISM = "DISTINCT_CAUSAL_MECHANISM"
    UNMENTIONED_STAKEHOLDER = "UNMENTIONED_STAKEHOLDER"
    NAMED_FAILURE_MODE = "NAMED_FAILURE_MODE"


class ClassificationConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class NormalizationWarningCode(StrEnum):
    """Content loss is always visible. Silent stripping is forbidden (FR-4)."""

    CODE_BLOCK_REMOVED = "CODE_BLOCK_REMOVED"
    TABLE_REMOVED = "TABLE_REMOVED"
    QUOTED_PROMPT_REMOVED = "QUOTED_PROMPT_REMOVED"
    BOILERPLATE_REMOVED = "BOILERPLATE_REMOVED"
    UNIT_BELOW_CONTENT_FLOOR = "UNIT_BELOW_CONTENT_FLOOR"
    UNIT_TRUNCATED = "UNIT_TRUNCATED"
    DUPLICATE_UNIT_REMOVED = "DUPLICATE_UNIT_REMOVED"
    DUPLICATE_CANDIDATE_REMOVED = "DUPLICATE_CANDIDATE_REMOVED"


class InternalConflictKind(StrEnum):
    NLI_CONTRADICTION = "NLI_CONTRADICTION"
    BOOLEAN_CONFLICT = "BOOLEAN_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    DATE_CONFLICT = "DATE_CONFLICT"
    NUMERIC_CONFLICT = "NUMERIC_CONFLICT"


# --------------------------------------------------------------------------------------
# shared text validation
# --------------------------------------------------------------------------------------

#: Identifiers are ASCII-conservative on purpose. They flow into digests, log lines, and
#: pair keys, so homoglyph and directionality ambiguity is excluded by construction
#: rather than normalised away.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

#: Explicitly named because these are the characters used to make two different strings
#: render identically (threat model boundary 1).
#:
#: Written as escapes, never as literals: a source file containing real bidirectional
#: controls is itself the Trojan Source pattern, and would make this file's own review
#: unreliable. Static analysis flags the literal form as a high-severity finding, correctly.
_BIDI_CONTROLS = frozenset(
    "\u202a"  # LEFT-TO-RIGHT EMBEDDING
    "\u202b"  # RIGHT-TO-LEFT EMBEDDING
    "\u202c"  # POP DIRECTIONAL FORMATTING
    "\u202d"  # LEFT-TO-RIGHT OVERRIDE
    "\u202e"  # RIGHT-TO-LEFT OVERRIDE
    "\u2066"  # LEFT-TO-RIGHT ISOLATE
    "\u2067"  # RIGHT-TO-LEFT ISOLATE
    "\u2068"  # FIRST STRONG ISOLATE
    "\u2069"  # POP DIRECTIONAL ISOLATE
    "\u200e"  # LEFT-TO-RIGHT MARK
    "\u200f"  # RIGHT-TO-LEFT MARK
    "\u061c"  # ARABIC LETTER MARK
    "\u200b"  # ZERO WIDTH SPACE
    "\u2060"  # WORD JOINER
    "\ufeff"  # ZERO WIDTH NO-BREAK SPACE / BOM
)


def _reject_dangerous_characters(value: str, *, allow_newlines: bool) -> str:
    """Reject control, format, surrogate, and private-use characters.

    Bidirectional overrides and zero-width characters are the specific concern: they let
    one string display as another, which would let a candidate disguise its content or an
    identifier collide visually with a different one.
    """
    for char in value:
        if char in _BIDI_CONTROLS:
            raise ValueError(f"bidirectional control character U+{ord(char):04X} is not accepted")
        if allow_newlines and char in "\n\t":
            continue
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            raise ValueError(f"character U+{ord(char):04X} (category {category}) is not accepted")
    return value


def _validate_identifier(value: str) -> str:
    _reject_dangerous_characters(value, allow_newlines=False)
    if not _IDENTIFIER_PATTERN.match(value):
        raise ValueError(
            "identifier must start alphanumeric and contain only letters, digits, "
            "dot, underscore, colon, or hyphen"
        )
    return value


Identifier = Annotated[str, Field(min_length=1, max_length=MAX_IDENTIFIER_CHARS)]
SourceLabel = Annotated[str, Field(min_length=1, max_length=MAX_SOURCE_LABEL_CHARS)]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class _Strict(BaseModel):
    """Strict, closed, immutable base for every public contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# --------------------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------------------


class PreflightRequest(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    task: str = Field(min_length=1, max_length=256 * 1024)
    mode: PrismMode = PrismMode.STANDARD
    max_perspectives: int | None = Field(default=None, ge=3, le=5)

    @field_validator("task")
    @classmethod
    def _check_task(cls, value: str) -> str:
        _reject_dangerous_characters(value, allow_newlines=True)
        if not value.strip():
            raise ValueError("task must contain non-whitespace content")
        if len(value.split()) > MAX_TASK_WORDS:
            raise ValueError(f"task exceeds {MAX_TASK_WORDS} words")
        return value


class PerspectiveInstruction(_Strict):
    id: Identifier
    purpose: str = Field(min_length=1, max_length=512)
    questions: tuple[str, ...] = Field(min_length=1, max_length=8)
    claim_budget: int = Field(ge=1, le=MAX_CLAIMS_PER_CANDIDATE)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return _validate_identifier(value)


class ExecutionContract(_Strict):
    """What the host is being asked to produce. Deliberately small: claim packets, not
    essays: a five-lens review must not cost five essays."""

    output: Literal["claim_packets"] = "claim_packets"
    max_claims_per_perspective: int = Field(ge=1, le=MAX_CLAIMS_PER_CANDIDATE)
    max_words_per_claim: int = Field(default=MAX_WORDS_PER_CLAIM, ge=1)
    min_words_per_claim: int = Field(default=MIN_CONTENT_WORDS, ge=1)
    source_rule: str
    untrusted_input_rule: str
    packet_schema: str


class PreflightReport(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    status: PrismStatus
    task_profile: str
    classification_confidence: ClassificationConfidence
    mode: PrismMode
    registry_version: str
    registry_hash: Digest
    #: Packaged data, or an operator-declared lens set. A consumer should be able to see
    #: that the perspectives did not come from the wheel without inspecting the host's
    #: environment.
    registry_origin: RegistryOrigin = PACKAGED_ORIGIN
    perspectives: tuple[PerspectiveInstruction, ...] = Field(min_length=3, max_length=5)
    execution_contract: ExecutionContract
    diagnostics: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


# --------------------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------------------


class Claim(_Strict):
    claim_id: Identifier
    text: str
    confidence: int | None = Field(default=None, ge=0, le=100)
    evidence_status: EvidenceStatus = EvidenceStatus.INFERRED

    @field_validator("claim_id")
    @classmethod
    def _check_claim_id(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("confidence")
    @classmethod
    def _reject_bool_confidence(cls, value: int | None) -> int | None:
        # bool is a subclass of int; strict mode does not reject it on its own.
        if isinstance(value, bool):
            raise ValueError("confidence must be an integer, not a boolean")
        return value

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        _reject_dangerous_characters(value, allow_newlines=True)
        word_count = len(value.split())
        if word_count < MIN_CONTENT_WORDS:
            raise ValueError(
                f"claim has {word_count} words; at least {MIN_CONTENT_WORDS} are required "
                "for it to be an independently understandable proposition"
            )
        if word_count > MAX_WORDS_PER_CLAIM:
            raise ValueError(f"claim has {word_count} words; the limit is {MAX_WORDS_PER_CLAIM}")
        return value


class CandidatePacket(_Strict):
    candidate_id: Identifier
    #: Required. All packets produced in one host generation pass MUST share one group.
    #: This is the only field that can raise source diversity.
    source_group_id: Identifier
    #: Display only. Cannot influence source diversity, however distinctive it looks.
    source_label: SourceLabel | None = None
    provenance_status: ProvenanceStatus = ProvenanceStatus.DECLARED_UNVERIFIED
    perspective: Identifier
    claims: tuple[Claim, ...] = Field(min_length=1, max_length=MAX_CLAIMS_PER_CANDIDATE)

    @field_validator("candidate_id", "source_group_id", "perspective")
    @classmethod
    def _check_identifiers(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("source_label")
    @classmethod
    def _check_label(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_dangerous_characters(value, allow_newlines=False)
        return value

    @model_validator(mode="after")
    def _unique_claim_ids(self) -> Self:
        ids = [claim.claim_id for claim in self.claims]
        if len(set(ids)) != len(ids):
            raise ValueError("claim_id values must be unique within a candidate")
        return self


class MeasureConfig(_Strict):
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0, le=60)
    #: Raw directional NLI scores are diagnostic. They are bounded by the same inline
    #: detail cap as every other array.
    include_raw_nli_scores: bool = False


class MeasureRequest(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    question: str = Field(min_length=1, max_length=256 * 1024)
    candidates: tuple[CandidatePacket, ...] = Field(
        min_length=MIN_CANDIDATES, max_length=MAX_CANDIDATES
    )
    config: MeasureConfig = MeasureConfig()

    @field_validator("question")
    @classmethod
    def _check_question(cls, value: str) -> str:
        _reject_dangerous_characters(value, allow_newlines=True)
        if not value.strip():
            raise ValueError("question must contain non-whitespace content")
        if len(value.split()) > MAX_TASK_WORDS:
            raise ValueError(f"question exceeds {MAX_TASK_WORDS} words")
        return value

    @model_validator(mode="after")
    def _unique_candidate_ids(self) -> Self:
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("candidate_id values must be unique within a request")
        return self

    def distinct_source_count(self) -> int:
        """Count canonical source groups *as submitted*. Labels are not consulted.

        This describes the request, not the report. A report must use
        :func:`aggregate_effective_sources` over the candidates that actually scored,
        because duplicates and non-viable candidates never reach the measured set.
        """
        return len({candidate.source_group_id for candidate in self.candidates})

    def source_diversity(self) -> SourceDiversity:
        """Diversity *as submitted*. See :meth:`distinct_source_count` — not for reports."""
        if self.distinct_source_count() > 1:
            return SourceDiversity.MULTI_SOURCE
        return SourceDiversity.SINGLE_SOURCE


class EffectiveSources(NamedTuple):
    """What a report may claim about its sources, derived from what actually scored."""

    sources_distinct: int
    source_diversity: SourceDiversity
    provenance_status: ProvenanceStatus


#: Weakest first. The aggregate reports the first status present in the effective set.
_PROVENANCE_WEAKEST_FIRST = (
    ProvenanceStatus.DECLARED_UNVERIFIED,
    ProvenanceStatus.EXTERNALLY_ATTESTED,
)


def aggregate_effective_sources(candidates: Sequence[CandidatePacket]) -> EffectiveSources:
    """Aggregate the effective candidate set: duplicates and non-viable ones removed.

    Two copies of one source cannot raise diversity, because the duplicate is gone before
    this is called. ``EXTERNALLY_ATTESTED`` holds only when *every* effective candidate is
    attested; any weaker status present degrades the whole report, so one attested
    candidate cannot launder four unattested ones. An empty set is
    ``DECLARED_UNVERIFIED``: nothing scored, so nothing was attested.
    """
    distinct = len({candidate.source_group_id for candidate in candidates})
    present = {candidate.provenance_status for candidate in candidates}
    return EffectiveSources(
        sources_distinct=distinct,
        source_diversity=(
            SourceDiversity.MULTI_SOURCE if distinct > 1 else SourceDiversity.SINGLE_SOURCE
        ),
        provenance_status=next(
            (status for status in _PROVENANCE_WEAKEST_FIRST if status in present),
            ProvenanceStatus.DECLARED_UNVERIFIED,
        ),
    )


# --------------------------------------------------------------------------------------
# measurement detail records
# --------------------------------------------------------------------------------------


class ContradictionPair(_Strict):
    pair_id: str
    claim_a_id: Identifier
    claim_b_id: Identifier
    candidate_a_id: Identifier
    candidate_b_id: Identifier
    contradiction_score: float = Field(ge=0.0, le=1.0)
    score_a_to_b: float = Field(ge=0.0, le=1.0)
    score_b_to_a: float = Field(ge=0.0, le=1.0)


class ScopeDivergentPair(_Strict):
    pair_id: str
    claim_a_id: Identifier
    claim_b_id: Identifier
    dimension: str
    marker_a: str
    marker_b: str


class RetainedClaim(_Strict):
    claim_id: Identifier
    candidate_id: Identifier
    reason: RetentionReason
    #: Retention is not endorsement.
    note: str = Field(max_length=256)


class InternalConflict(_Strict):
    candidate_id: Identifier
    claim_a_id: Identifier
    claim_b_id: Identifier
    kind: InternalConflictKind
    detail: str = Field(max_length=256)


class NormalizationWarning(_Strict):
    candidate_id: Identifier
    code: NormalizationWarningCode
    count: int = Field(ge=1)
    #: Hash of the removed region, never the region itself.
    removed_digest: Digest | None = None


class DuplicateRecord(_Strict):
    kind: Literal["CANDIDATE", "CLAIM"]
    removed_id: Identifier
    duplicate_of_id: Identifier


class RawNliScore(_Strict):
    pair_id: str
    score_a_to_b: float = Field(ge=0.0, le=1.0)
    score_b_to_a: float = Field(ge=0.0, le=1.0)


# --------------------------------------------------------------------------------------
# measurement report
# --------------------------------------------------------------------------------------


class MeasureReport(_Strict):
    """The complete measurement result.

    Two gating rules are enforced here rather than in the engine, so that no code path,
    including a future one, can emit a report that violates them:

    1. A zero denominator yields ``null``, never ``0.0``. "No comparable pairs" and
       "no contradictions" are different findings.
    2. While ``calibration_status`` is uncalibrated, the authoritative contradiction and
       agreement fields stay empty. The provisional numbers are published under
       ``experimental_*`` so that an alpha result can never be mistaken later for
       calibrated semantic evidence.
    """

    schema_version: Literal["1.0"] = "1.0"
    status: PrismStatus

    #: Until a locked human-labelled corpus has been scored, this reads
    #: UNCALIBRATED_PENDING_HUMAN_VALIDATION and the authoritative fields are suppressed.
    calibration_status: str = CALIBRATION_UNCALIBRATED

    source_diversity: SourceDiversity
    provenance_status: ProvenanceStatus

    pairs_total: int = Field(ge=0)
    relevant_pairs: int = Field(ge=0)
    scope_divergent_count: int = Field(ge=0)
    scope_uncertain_count: int = Field(ge=0)
    contradiction_denominator: int = Field(ge=0)
    pairs_scored_by_nli: int = Field(ge=0)
    pairs_inferred_not_contradictory: int = Field(ge=0)
    nli_coverage: float | None = Field(default=None, ge=0.0, le=1.0)

    #: Authoritative. Populated only when calibration_status is HUMAN_VALIDATED.
    contradiction_count: int | None = Field(default=None, ge=0)
    contradiction_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    agreement_type: AgreementType = AgreementType.UNCLEAR

    #: Provisional. Populated whenever inference ran, calibrated or not. These prove the
    #: machinery works; they are not evidence about the world.
    experimental_contradiction_count: int | None = Field(default=None, ge=0)
    experimental_contradiction_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    experimental_threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    contradictions: tuple[ContradictionPair, ...] = ()
    contradictions_omitted_count: int = Field(default=0, ge=0)
    scope_divergent: tuple[ScopeDivergentPair, ...] = ()
    scope_divergent_omitted_count: int = Field(default=0, ge=0)
    retained_distinct_claims: tuple[RetainedClaim, ...] = ()
    internal_conflicts: tuple[InternalConflict, ...] = ()
    internal_conflicts_omitted_count: int = Field(default=0, ge=0)
    normalization_warnings: tuple[NormalizationWarning, ...] = ()
    normalization_warnings_omitted_count: int = Field(default=0, ge=0)
    duplicate_candidates: tuple[DuplicateRecord, ...] = ()
    raw_nli_scores: tuple[RawNliScore, ...] = ()
    raw_nli_scores_omitted_count: int = Field(default=0, ge=0)

    confidence_spread: int | None = Field(default=None, ge=0, le=100)
    #: Exploratory until separately validated.
    overconfidence_gap: float | None = None

    #: Distinct source groups among the candidates that actually scored, not among those
    #: submitted. Zero is legal and reachable: every candidate can be dropped as a
    #: duplicate or as non-viable, and a report that measured nothing must not claim a
    #: source it did not use.
    sources_distinct: int = Field(ge=0)
    pair_ledger_digest: Digest
    report_bytes: int | None = Field(default=None, ge=0)
    diagnostics: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_arithmetic(self) -> Self:
        if self.contradiction_denominator == 0:
            if self.contradiction_rate is not None:
                raise ValueError(
                    "a zero contradiction denominator must yield a null rate, never 0.0"
                )
            if self.nli_coverage is not None:
                raise ValueError("a zero contradiction denominator must yield null coverage")
        if (
            self.contradiction_count is not None
            and self.contradiction_count > self.contradiction_denominator
        ):
            raise ValueError("contradiction_count cannot exceed contradiction_denominator")
        if (
            self.experimental_contradiction_count is not None
            and self.experimental_contradiction_count > self.contradiction_denominator
        ):
            raise ValueError(
                "experimental_contradiction_count cannot exceed contradiction_denominator"
            )
        if self.relevant_pairs > self.pairs_total:
            raise ValueError("relevant_pairs cannot exceed pairs_total")
        if self.scope_divergent_count > self.relevant_pairs:
            raise ValueError("scope_divergent_count cannot exceed relevant_pairs")
        return self

    @model_validator(mode="after")
    def _check_calibration_gate(self) -> Self:
        if self.calibration_status == CALIBRATION_HUMAN_VALIDATED:
            return self
        if self.calibration_status != CALIBRATION_UNCALIBRATED:
            raise ValueError(f"unknown calibration_status: {self.calibration_status!r}")
        if self.contradiction_count is not None or self.contradiction_rate is not None:
            raise ValueError(
                "an uncalibrated threshold must not populate the authoritative "
                "contradiction fields; use experimental_contradiction_* instead"
            )
        if self.agreement_type is not AgreementType.UNCLEAR:
            raise ValueError(
                "an uncalibrated threshold must report agreement_type=UNCLEAR; "
                f"got {self.agreement_type}"
            )
        return self


# --------------------------------------------------------------------------------------
# synthesis
# --------------------------------------------------------------------------------------


class SynthesisContract(_Strict):
    """Instructions for the host's final answer. PRISM writes rules, not prose."""

    schema_version: Literal["1.0"] = "1.0"
    status: PrismStatus
    compatible_findings: tuple[str, ...] = ()
    unresolved_conflicts: tuple[str, ...] = ()
    scope_differences: tuple[str, ...] = ()
    retained_claim_ids: tuple[Identifier, ...] = ()
    required_disclosures: tuple[str, ...] = ()
    prohibited_shortcuts: tuple[str, ...]
    final_answer_structure: tuple[str, ...]
    measurement_available: bool
    limitations: tuple[str, ...] = ()


# --------------------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------------------


class ComponentHealth(_Strict):
    name: str
    healthy: bool
    detail: str = Field(max_length=256)


class HealthReport(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    status: PrismStatus
    deep: bool
    package_version: str
    registry_version: str
    registry_hash: Digest | None = None
    registry_origin: RegistryOrigin = PACKAGED_ORIGIN
    model_manifest_hash: Digest | None = None
    measurement_available: bool
    measurement_disabled_by_kill_switch: bool = False
    calibration_status: str = CALIBRATION_UNCALIBRATED
    components: tuple[ComponentHealth, ...] = ()
    diagnostics: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


def parse_payload[ModelT: BaseModel](model: type[ModelT], payload: object) -> ModelT:
    """Validate an untrusted payload into a public contract.

    Strict mode does not coerce a ``list`` into a ``tuple``, and every sequence field here
    is a tuple because reports are immutable. A payload that arrived as JSON therefore has
    to be validated in JSON mode, where an array maps to a tuple correctly.

    Both adapters route through this one function. When this was implemented per-adapter,
    the CLI and the MCP server failed identically on the same valid input, which is
    exactly the duplicated-integration-logic failure that one shared entry point exists
    to prevent.

    Raises:
        PrismError: ``INVALID_INPUT`` with the offending field paths — never the offending
            values, which would put user content into a diagnostic.
    """
    from .errors import ErrorCode, PrismError

    try:
        if isinstance(payload, (str, bytes, bytearray)):
            return model.model_validate_json(payload)
        return model.model_validate_json(json.dumps(payload, default=str, ensure_ascii=True))
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})[
            :10
        ]
        raise PrismError(
            code=ErrorCode.INVALID_INPUT,
            message=f"Input does not satisfy the {model.__name__} contract.",
            diagnostics={
                "model": model.__name__,
                "error_count": exc.error_count(),
                "fields": ", ".join(fields),
            },
        ) from None
    except (TypeError, ValueError) as exc:
        raise PrismError(
            code=ErrorCode.INVALID_INPUT,
            message="Input could not be read as JSON.",
            diagnostics={"model": model.__name__, "error_type": type(exc).__name__},
        ) from None


def public_models() -> tuple[type[BaseModel], ...]:
    """Every model whose JSON Schema is part of the published contract surface.

    Used by the schema-digest and previous-minor compatibility gates.
    """
    return (
        PreflightRequest,
        PreflightReport,
        PerspectiveInstruction,
        ExecutionContract,
        Claim,
        CandidatePacket,
        MeasureConfig,
        MeasureRequest,
        MeasureReport,
        SynthesisContract,
        HealthReport,
    )


def _schema_of(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()
