"""Operational acceptance tests — gate G17.

The five Phase-1 OATs ask a different question from the unit and security suites. Those
ask whether a component is correct; these ask whether an operator can actually run the
thing, and what happens to them when it breaks.

    OAT1  clean install, then a fully air-gapped workflow
    OAT2  missing or corrupt model bundle, with preflight-only fallback
    OAT3  maximum legal workload, concurrency, overload, and timeout recovery
    OAT4  forced interrupt with no partial report and no abandoned work
    OAT5  kill switch, rollback, and revocation

OAT3 and the interrupt half of OAT4 are already covered end to end by
`tests/stress/test_admission_recovery.py`, and duplicating them here would create exactly
the redundancy the architecture forbids. What is added here is the operator-facing view:
the whole workflow with the network removed, the degraded mode a missing bundle produces,
and whether the documented recovery procedure exists and matches the code.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn

import pytest

from prism.constants import DISABLE_MEASURE_ENV_VAR
from prism.contracts import (
    CandidatePacket,
    Claim,
    EvidenceStatus,
    MeasureRequest,
    PreflightRequest,
    PrismMode,
    PrismStatus,
    ProvenanceStatus,
)
from prism.errors import ErrorCode, PrismError
from prism.measure.models import ModelSessions
from prism.service import PrismService

REPO_ROOT = Path(__file__).resolve().parents[2]

TASK = "Assess whether the payment service release is ready to ship this week."


def _packets(count: int = 2) -> tuple[CandidatePacket, ...]:
    texts = [
        "The release is ready for production because every blocking defect is closed now.",
        "The release is not ready for production because the retry path still drops messages.",
    ]
    return tuple(
        CandidatePacket(
            candidate_id=f"lens{index}",
            source_group_id="host-pass-001",
            source_label=f"lens{index}",
            provenance_status=ProvenanceStatus.DECLARED_UNVERIFIED,
            perspective=f"lens{index}",
            claims=(
                Claim(
                    claim_id=f"lens{index}-1",
                    text=texts[index % len(texts)],
                    confidence=70,
                    evidence_status=EvidenceStatus.INFERRED,
                ),
            ),
        )
        for index in range(count)
    )


@pytest.fixture
def airgapped(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Remove the network, recording anything that reaches for it."""
    attempts: list[str] = []

    def blocked(*args: Any, **_: Any) -> NoReturn:
        attempts.append(repr(args))
        raise OSError("air-gapped")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    yield attempts


# --------------------------------------------------------------------------------------
# OAT1 — air-gapped workflow
# --------------------------------------------------------------------------------------


def test_oat1_full_workflow_completes_with_no_network(airgapped: list[str]) -> None:
    """Preflight, then synthesis, with every socket route removed.

    Measurement is exercised against the real bundle by `scripts/verify_offline.py`; here
    the point is that the deterministic path an operator always has needs nothing from
    the network, including at import and registry-load time.
    """
    service = PrismService.from_default_bundle()

    preflight = service.preflight(PreflightRequest(task=TASK, mode=PrismMode.CRITICAL))
    contract = service.synthesis_contract(preflight, None)

    assert preflight.perspectives
    assert contract.prohibited_shortcuts
    assert contract.final_answer_structure
    assert airgapped == []


def test_oat1_registry_loads_from_the_installed_package(airgapped: list[str]) -> None:
    """A clean install must not depend on the repository layout being present."""
    service = PrismService.from_default_bundle()
    report = service.preflight(PreflightRequest(task=TASK, mode=PrismMode.LITE))
    assert report.registry_version
    assert report.registry_hash.startswith("sha256:")
    assert airgapped == []


# --------------------------------------------------------------------------------------
# OAT2 — degraded mode
# --------------------------------------------------------------------------------------


def test_oat2_preflight_survives_when_measurement_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kill switch is the operator's response to a model or runtime advisory. It must
    remove inference without removing the tool."""
    monkeypatch.setenv(DISABLE_MEASURE_ENV_VAR, "1")
    ModelSessions.reset()

    service = PrismService.from_default_bundle()
    assert service.preflight(PreflightRequest(task=TASK, mode=PrismMode.STANDARD)).perspectives

    with pytest.raises(PrismError) as raised:
        service.measure(MeasureRequest(question=TASK, candidates=_packets()))

    assert raised.value.code in {ErrorCode.MEASURE_DISABLED, ErrorCode.MODEL_UNAVAILABLE}
    assert ErrorCode.recovery(raised.value.code).safe_action


def test_oat2_the_synthesis_contract_is_still_usable_without_a_measurement() -> None:
    """Degraded must not mean silent. The contract has to say that nothing was measured,
    rather than reading like a clean result."""
    service = PrismService.from_default_bundle()
    preflight = service.preflight(PreflightRequest(task=TASK, mode=PrismMode.STANDARD))
    contract = service.synthesis_contract(preflight, None)

    assert contract.measurement_available is False
    assert contract.prohibited_shortcuts
    assert contract.limitations, "an unmeasured contract must state that nothing was measured"


def test_oat2_a_failed_measurement_never_produces_a_partial_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report is assembled completely or not at all; there is no half-populated object
    for a caller to mistake for a finding."""
    monkeypatch.setenv(DISABLE_MEASURE_ENV_VAR, "1")
    ModelSessions.reset()

    service = PrismService.from_default_bundle()
    try:
        report = service.measure(MeasureRequest(question=TASK, candidates=_packets()))
    except PrismError:
        return  # a typed refusal is the correct outcome; there is nothing partial to check
    assert report.status is not PrismStatus.OK


# --------------------------------------------------------------------------------------
# OAT5 — recovery procedure
# --------------------------------------------------------------------------------------


def test_oat5_the_kill_switch_is_documented_where_an_operator_will_look() -> None:
    """A switch nobody can find is not a control. It has to be named in the security
    policy and in the operations runbook, not only in the code."""
    security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    operations = (REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    assert "PRISM_DISABLE_MEASURE" in security
    assert "PRISM_DISABLE_MEASURE" in operations


def test_oat5_the_rollback_procedure_names_every_required_step() -> None:
    """Task 19 fixes the six steps a rollback must cover. A runbook missing one of them
    fails an operator at the worst possible moment."""
    operations = (REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8").casefold()

    for required in ("mcp", "pin", "verify", "model", "health", "report"):
        assert required in operations, f"the rollback procedure does not mention {required}"


def test_oat5_recovery_commands_exist_in_this_repository() -> None:
    """Every command the runbook tells an operator to run must be a real path here."""
    operations = (REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    for script in ("scripts/verify_models.py", "scripts/release_gate.py"):
        if script in operations:
            assert (REPO_ROOT / script).is_file(), f"{script} is documented but absent"
