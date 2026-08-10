"""The service facade.

``PrismService`` is the only entry point adapters use. The CLI, the MCP server, and any
embedding host all call this same object, which is what makes "equivalent behaviour across
interfaces" a structural property rather than a promise (invariant A2).

Three behaviours live here because they must hold for every caller:

* **Zero-queue admission.** Two measurements may run; a third is refused immediately with
  a typed ``BUSY``. There is no queue, so a burst cannot become hidden background work or
  a stale result delivered long after it was wanted (invariant A20).
* **Complete-result atomicity.** A report is fully assembled and validated before it is
  returned. A timeout or crash cannot produce a partial report with a plausible-looking
  contradiction rate (invariant A17).
* **Timeout circuit breaking.** A Python-side timeout does not stop native inference. The
  worker may still be running, so new measurements are refused until it finishes rather
  than piling a second job on top of an unhealthy one.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from .canonical import canonical_digest
from .constants import CALIBRATION_UNCALIBRATED
from .contracts import (
    ComponentHealth,
    DuplicateRecord,
    HealthReport,
    MeasureReport,
    MeasureRequest,
    NormalizationWarning,
    PreflightReport,
    PreflightRequest,
    PrismStatus,
    SynthesisContract,
)
from .errors import ErrorCode, PrismError
from .limits import MAX_CONCURRENT_MEASUREMENTS
from .measure.agreement import agreement_type
from .measure.calibration import calibration_status
from .measure.contradiction import build_ledger, detect_internal_conflicts
from .measure.models import ModelSessions, measurement_disabled
from .measure.pair import enumerate_pairs
from .measure.project import build_measure_report
from .measure.retention import retain_distinct_claims
from .measure.segment import (
    NormalizedCandidate,
    find_duplicate_candidates,
    normalize_claims,
)
from .preflight.contract import build_preflight_contract
from .preflight.registry import PerspectiveRegistry
from .synthesis.contract import build_synthesis_contract
from .telemetry import emit, new_request_id, timed
from .version import PACKAGE_VERSION


class PrismService:
    """The public behaviour of PRISM, independent of any adapter."""

    def __init__(self, registry: PerspectiveRegistry, model_root: Path | None = None) -> None:
        self._registry = registry
        self._model_root = model_root
        # Non-blocking admission. A caller either gets a permit now or is told BUSY now.
        self._permits = threading.BoundedSemaphore(MAX_CONCURRENT_MEASUREMENTS)
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_MEASUREMENTS, thread_name_prefix="prism-measure"
        )
        #: Set when a measurement timed out. Native inference may still be occupying a
        #: worker, so further measurements are refused until it is observed to finish.
        self._abandoned: set[Future[MeasureReport]] = set()
        self._breaker_lock = threading.Lock()

    @classmethod
    def from_default_bundle(cls, model_root: Path | None = None) -> PrismService:
        return cls(registry=PerspectiveRegistry.load(), model_root=model_root)

    # -- preflight ---------------------------------------------------------------------

    def preflight(self, request: PreflightRequest) -> PreflightReport:
        """Deterministic and pure. Loads no model and touches no artifact."""
        request_id = new_request_id()
        with timed("preflight", request_id):
            report = build_preflight_contract(request, self._registry)
        emit(
            "preflight",
            request_id=request_id,
            profile=report.task_profile,
            mode=report.mode.value,
            perspectives=len(report.perspectives),
            registry_hash=report.registry_hash,
        )
        return report

    # -- measurement -------------------------------------------------------------------

    def measure(self, request: MeasureRequest) -> MeasureReport:
        """Measure contradictions, under admission control and a deadline."""
        request_id = new_request_id()
        self._check_circuit()

        if not self._permits.acquire(blocking=False):
            raise PrismError(
                code=ErrorCode.BUSY,
                message="All measurement capacity is in use and there is no queue by "
                "design. Retry when a measurement completes.",
                diagnostics={"request_id": request_id, "capacity": MAX_CONCURRENT_MEASUREMENTS},
            )

        future: Future[MeasureReport] = self._executor.submit(
            self._measure_inner, request, request_id
        )
        try:
            report = future.result(timeout=request.config.timeout_seconds)
        except FutureTimeout:
            # The permit is deliberately NOT released: the worker may still be inside
            # native inference, and releasing would admit a second job onto a busy CPU.
            with self._breaker_lock:
                self._abandoned.add(future)
            future.add_done_callback(self._release_abandoned)
            raise PrismError(
                code=ErrorCode.TIMEOUT,
                message="The measurement deadline elapsed. No partial result was produced.",
                diagnostics={
                    "request_id": request_id,
                    "timeout_seconds": request.config.timeout_seconds,
                },
            ) from None
        except PrismError:
            self._permits.release()
            raise
        except Exception as exc:
            self._permits.release()
            raise PrismError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Measurement failed unexpectedly.",
                diagnostics={"request_id": request_id, "error_type": type(exc).__name__},
            ) from None
        else:
            self._permits.release()
            return report

    def _release_abandoned(self, future: Future[MeasureReport]) -> None:
        """Recover the permit once an abandoned worker actually finishes."""
        with self._breaker_lock:
            self._abandoned.discard(future)
        self._permits.release()

    def _check_circuit(self) -> None:
        with self._breaker_lock:
            abandoned = len(self._abandoned)
        if abandoned >= MAX_CONCURRENT_MEASUREMENTS:
            raise PrismError(
                code=ErrorCode.TIMEOUT,
                message="Previous measurements timed out and their workers have not yet "
                "completed. Restart the process if this persists.",
                diagnostics={"abandoned_workers": abandoned},
            )

    def _measure_inner(self, request: MeasureRequest, request_id: str) -> MeasureReport:
        """The measurement pipeline. Runs entirely inside one worker."""
        normalized: list[NormalizedCandidate] = []
        warnings: list[NormalizationWarning] = []
        with timed("normalize", request_id):
            for packet in request.candidates:
                candidate, candidate_warnings = normalize_claims(packet)
                normalized.append(candidate)
                warnings.extend(candidate_warnings)

        duplicate_pairs = find_duplicate_candidates(tuple(normalized))
        duplicate_ids = {removed for removed, _ in duplicate_pairs}
        duplicates = tuple(
            DuplicateRecord(kind="CANDIDATE", removed_id=removed, duplicate_of_id=original)
            for removed, original in duplicate_pairs
        )
        # A duplicate cannot count as a second opinion, so it leaves the scored set.
        scored = tuple(c for c in normalized if c.candidate_id not in duplicate_ids)

        viable = tuple(c for c in scored if c.is_viable)
        if len(viable) < 2:
            return self._insufficient_report(request, duplicates, tuple(warnings), request_id)

        sessions = ModelSessions.get(self._model_root)

        with timed("pairs", request_id):
            pair_set = enumerate_pairs(viable)
        with timed("inference", request_id):
            ledger = build_ledger(pair_set, sessions)

        internal_conflicts = detect_internal_conflicts(viable)
        retained = retain_distinct_claims(viable, ledger)

        confidences = [
            unit.confidence
            for candidate in viable
            for unit in candidate.units
            if unit.confidence is not None
        ]
        spread = max(confidences) - min(confidences) if len(confidences) >= 2 else None

        status = (
            PrismStatus.INSUFFICIENT if ledger.contradiction_denominator == 0 else PrismStatus.OK
        )
        source_diversity = request.source_diversity()

        agreement = agreement_type(
            status=status,
            contradiction_denominator=ledger.contradiction_denominator,
            contradiction_count=ledger.contradiction_count,
            nli_coverage=ledger.nli_coverage,
            scope_uncertain_count=ledger.scope_uncertain_count,
            source_diversity=source_diversity,
        )

        report = build_measure_report(
            ledger=ledger,
            status=status,
            source_diversity=source_diversity,
            provenance_status=request.candidates[0].provenance_status,
            sources_distinct=request.distinct_source_count(),
            agreement=agreement,
            retained=retained,
            internal_conflicts=internal_conflicts,
            normalization_warnings=tuple(warnings),
            duplicates=duplicates,
            confidence_spread=spread,
            include_raw_nli_scores=request.config.include_raw_nli_scores,
            diagnostics={
                "request_id": request_id,
                "package_version": PACKAGE_VERSION,
                "registry_version": self._registry.version,
                "registry_hash": self._registry.content_hash,
                "model_manifest_hash": sessions.manifest.digest,
                "candidates_scored": len(viable),
            },
        )
        emit(
            "measure",
            request_id=request_id,
            pairs_total=report.pairs_total,
            relevant_pairs=report.relevant_pairs,
            denominator=report.contradiction_denominator,
            status=report.status.value,
            calibration_status=report.calibration_status,
        )
        return report

    def _insufficient_report(
        self,
        request: MeasureRequest,
        duplicates: tuple[DuplicateRecord, ...],
        warnings: tuple[NormalizationWarning, ...],
        request_id: str,
    ) -> MeasureReport:
        """Fewer than two viable candidates. No measurement, and no implied agreement."""
        return MeasureReport(
            status=PrismStatus.INSUFFICIENT,
            calibration_status=calibration_status(),
            source_diversity=request.source_diversity(),
            provenance_status=request.candidates[0].provenance_status,
            pairs_total=0,
            relevant_pairs=0,
            scope_divergent_count=0,
            scope_uncertain_count=0,
            contradiction_denominator=0,
            pairs_scored_by_nli=0,
            pairs_inferred_not_contradictory=0,
            nli_coverage=None,
            contradiction_count=None,
            contradiction_rate=None,
            duplicate_candidates=duplicates,
            normalization_warnings=warnings[:20],
            normalization_warnings_omitted_count=max(0, len(warnings) - 20),
            sources_distinct=request.distinct_source_count(),
            pair_ledger_digest=canonical_digest([]),
            diagnostics={"request_id": request_id, "reason": "fewer_than_two_viable_candidates"},
        )

    # -- synthesis ---------------------------------------------------------------------

    def synthesis_contract(
        self,
        preflight: PreflightReport | None,
        measurement: MeasureReport | None,
    ) -> SynthesisContract:
        """Pure and deterministic. Generates rules, never prose."""
        return build_synthesis_contract(preflight, measurement)

    # -- health ------------------------------------------------------------------------

    def health(self, deep: bool = False) -> HealthReport:
        """Shallow checks contracts and registry. Deep additionally verifies artifacts.

        Deep health never scans the user's project or environment; it reads only the
        packaged registry and the verified model bundle.
        """
        components: list[ComponentHealth] = [
            ComponentHealth(
                name="registry",
                healthy=True,
                detail=f"{len(self._registry)} perspectives, version {self._registry.version}",
            ),
            ComponentHealth(name="contracts", healthy=True, detail="schema 1.0 loaded"),
        ]
        disabled = measurement_disabled()
        manifest_hash: str | None = None
        measurement_available = False

        if disabled:
            components.append(
                ComponentHealth(
                    name="measurement",
                    healthy=True,
                    detail="disabled by kill switch; preflight remains available",
                )
            )
        elif deep:
            try:
                sessions = ModelSessions.get(self._model_root)
                probe = sessions.contradiction_probabilities(
                    [("The service is available.", "The service is not available.")]
                )
                manifest_hash = sessions.manifest.digest
                measurement_available = True
                components.append(
                    ComponentHealth(
                        name="encoders",
                        healthy=True,
                        detail=f"CPU sessions verified; synthetic probe {float(probe[0]):.3f}",
                    )
                )
            except PrismError as error:
                components.append(
                    ComponentHealth(
                        name="encoders", healthy=False, detail=f"{error.code}: unavailable"
                    )
                )
        else:
            measurement_available = not disabled

        status = (
            PrismStatus.OK
            if all(component.healthy for component in components)
            else PrismStatus.ERROR
        )
        return HealthReport(
            status=status,
            deep=deep,
            package_version=PACKAGE_VERSION,
            registry_version=self._registry.version,
            registry_hash=self._registry.content_hash,
            model_manifest_hash=manifest_hash,
            measurement_available=measurement_available,
            measurement_disabled_by_kill_switch=disabled,
            calibration_status=calibration_status(),
            components=tuple(components),
            diagnostics={"calibration": CALIBRATION_UNCALIBRATED},
        )
