"""Shallow health reports what it actually knows.

`measurement_available` used to be set from the kill switch alone, so a clone carrying no
model bundle at all answered `True` — the one question an operator asks health to settle.
Shallow health still verifies nothing; it now checks that there is something to verify.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prism.constants import DISABLE_MEASURE_ENV_VAR, MODEL_MANIFEST_FILENAME
from prism.contracts import PrismStatus
from prism.service import PrismService


@pytest.fixture(autouse=True)
def kill_switch_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DISABLE_MEASURE_ENV_VAR, raising=False)


def test_shallow_health_on_a_bundleless_tree_does_not_claim_availability(
    tmp_path: Path,
) -> None:
    report = PrismService.from_default_bundle(model_root=tmp_path).health(deep=False)

    assert report.measurement_available is False
    assert report.measurement_disabled_by_kill_switch is False
    # Missing artifacts are not a fault: preflight is the guaranteed surface.
    assert report.status is PrismStatus.OK
    detail = next(c.detail for c in report.components if c.name == "measurement")
    assert "no model manifest" in detail


def test_shallow_health_with_a_manifest_present_reports_available_but_unverified(
    tmp_path: Path,
) -> None:
    (tmp_path / MODEL_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

    report = PrismService.from_default_bundle(model_root=tmp_path).health(deep=False)

    assert report.measurement_available is True
    # Shallow mode hashes nothing, and the report has to say so rather than imply proof.
    detail = next(c.detail for c in report.components if c.name == "measurement")
    assert "unverified" in detail
    assert report.model_manifest_hash is None


def test_the_kill_switch_still_wins_over_a_present_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / MODEL_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    monkeypatch.setenv(DISABLE_MEASURE_ENV_VAR, "1")

    report = PrismService.from_default_bundle(model_root=tmp_path).health(deep=False)

    assert report.measurement_available is False
    assert report.measurement_disabled_by_kill_switch is True
    assert report.status is PrismStatus.OK
