"""The perspective registry override is opt-in, contained, and visible.

`PRISM_REGISTRY_PATH` used to name any path on the machine and be read whole, with no
containment, no link rejection, no size bound, and nothing in the report to say the
perspectives had not come out of the wheel. The vendored-lens-set use case the docstring
promises is kept; the ambient-authority part of it is not.

The opt-in *is* the containment root: an operator who vendors a lens set names the
directory it lives in. A stray `PRISM_REGISTRY_PATH` inherited from some other process
therefore cannot redirect preflight on its own, and it is refused loudly rather than
ignored silently — a registry that is not the one the operator asked for is exactly the
condition that must not pass quietly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prism.constants import OVERRIDE_ORIGIN, PACKAGED_ORIGIN
from prism.contracts import PreflightRequest
from prism.errors import ErrorCode, PrismError
from prism.limits import MAX_REGISTRY_BYTES
from prism.preflight.contract import build_preflight_contract
from prism.preflight.registry import (
    REGISTRY_PATH_ENV_VAR,
    REGISTRY_ROOT_ENV_VAR,
    PerspectiveRegistry,
)

TASK = (
    "Assess the system design: component boundaries, coupling between services, "
    "and the scalability tradeoff of the proposed architecture."
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this file may inherit an override from the surrounding process."""
    monkeypatch.delenv(REGISTRY_PATH_ENV_VAR, raising=False)
    monkeypatch.delenv(REGISTRY_ROOT_ENV_VAR, raising=False)


def vendor(root: Path) -> Path:
    """A valid vendored lens set: the packaged registry, copied into an operator's root."""
    root.mkdir(parents=True, exist_ok=True)
    target = root / "registry.yaml"
    target.write_bytes(PerspectiveRegistry.packaged_path().read_bytes())
    return target


def declare(monkeypatch: pytest.MonkeyPatch, path: Path, root: Path) -> None:
    monkeypatch.setenv(REGISTRY_PATH_ENV_VAR, str(path))
    monkeypatch.setenv(REGISTRY_ROOT_ENV_VAR, str(root))


# --------------------------------------------------------------------------------------
# the override is admitted when it is declared properly
# --------------------------------------------------------------------------------------


def test_a_contained_override_loads_and_says_it_is_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "lenses"
    declare(monkeypatch, vendor(root), root)

    registry = PerspectiveRegistry.load()

    assert registry.origin == OVERRIDE_ORIGIN
    assert len(registry) > 0


def test_the_override_is_visible_in_the_preflight_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A consumer holding only the report must be able to tell that the perspectives did
    not come from the wheel, without inspecting the host's environment."""
    root = tmp_path / "lenses"
    declare(monkeypatch, vendor(root), root)

    report = build_preflight_contract(PreflightRequest(task=TASK), PerspectiveRegistry.load())

    assert report.registry_origin == "override"


def test_the_packaged_registry_reports_itself_as_packaged() -> None:
    registry = PerspectiveRegistry.load()
    report = build_preflight_contract(PreflightRequest(task=TASK), registry)

    assert registry.origin == PACKAGED_ORIGIN
    assert report.registry_origin == "packaged"


# --------------------------------------------------------------------------------------
# and refused when it is not
# --------------------------------------------------------------------------------------


def test_a_path_without_a_declared_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REGISTRY_PATH_ENV_VAR, str(vendor(tmp_path / "lenses")))

    with pytest.raises(PrismError) as caught:
        PerspectiveRegistry.load()

    assert caught.value.code is ErrorCode.CONFIG_INTEGRITY_FAILURE
    assert caught.value.diagnostics["missing_variable"] == REGISTRY_ROOT_ENV_VAR


def test_an_override_outside_the_declared_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "lenses"
    root.mkdir()
    outside = vendor(tmp_path / "elsewhere")
    declare(monkeypatch, outside, root)

    with pytest.raises(PrismError) as caught:
        PerspectiveRegistry.load()

    assert caught.value.diagnostics["control"] == "containment"


def test_traversal_out_of_the_declared_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Containment is checked on the resolved path, so `..` does not buy anything."""
    root = tmp_path / "lenses"
    root.mkdir()
    vendor(tmp_path / "elsewhere")
    declare(monkeypatch, root / ".." / "elsewhere" / "registry.yaml", root)

    with pytest.raises(PrismError) as caught:
        PerspectiveRegistry.load()

    assert caught.value.diagnostics["control"] == "containment"


def test_an_oversized_override_is_refused_from_its_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "lenses"
    target = vendor(root)
    # Valid YAML — a comment — so that only the size check can be what refuses it.
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n" + "# padding\n" * (MAX_REGISTRY_BYTES // 10 + 1))
    declare(monkeypatch, target, root)

    with pytest.raises(PrismError) as caught:
        PerspectiveRegistry.load()

    assert caught.value.code is ErrorCode.CONFIG_INTEGRITY_FAILURE
    assert caught.value.diagnostics["limit_bytes"] == MAX_REGISTRY_BYTES


def test_a_missing_override_is_refused_rather_than_falling_back_to_the_packaged_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silently serving packaged lenses when the operator asked for their own would make
    the report's registry hash true and its provenance a lie."""
    root = tmp_path / "lenses"
    root.mkdir()
    declare(monkeypatch, root / "absent.yaml", root)

    with pytest.raises(PrismError) as caught:
        PerspectiveRegistry.load()

    assert caught.value.diagnostics["control"] == "override_exists"


def test_a_root_that_is_not_a_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    declare(monkeypatch, vendor(tmp_path / "lenses"), tmp_path / "not-a-directory")

    with pytest.raises(PrismError) as caught:
        PerspectiveRegistry.load()

    assert caught.value.diagnostics["control"] == "root_exists"


def test_an_override_reached_through_a_link_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link inside the root would let the file swap without the declared root changing."""
    root = tmp_path / "lenses"
    real = vendor(root)
    link = root / "linked.yaml"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError) as exc:  # unprivileged Windows, mainly
        pytest.skip(f"this platform will not create a symlink here: {type(exc).__name__}")
    declare(monkeypatch, link, root)

    with pytest.raises(PrismError) as caught:
        PerspectiveRegistry.load()

    assert caught.value.diagnostics["control"] == "link_rejection"


def test_a_refusal_names_the_control_and_never_the_operators_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """errors.py forbids filesystem paths in diagnostics. A refusal is not an exemption."""
    root = tmp_path / "lenses"
    root.mkdir()
    declare(monkeypatch, vendor(tmp_path / "elsewhere"), root)

    with pytest.raises(PrismError) as caught:
        PerspectiveRegistry.load()

    rendered = " ".join(str(value) for value in caught.value.diagnostics.values())
    assert str(tmp_path) not in rendered
    assert str(tmp_path) not in caught.value.message
