"""What the package says about itself has to be true.

Three claims used to be false at once: the documented install command fetched an unrelated
project from PyPI, the `Typing :: Typed` classifier shipped without a marker file, and the
licence pointed at a manifest path that does not exist. None of them is caught by a test
suite that only exercises behaviour, and all three are what a new user meets first.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
PYPROJECT: Final[Path] = REPO_ROOT / "pyproject.toml"

#: The distribution this project owns. `prism` on PyPI belongs to an unrelated
#: Bayesian/MCMC project, so naming it in an install example ships users to someone
#: else's code.
DISTRIBUTION_NAME: Final[str] = "prism-preflight"

#: An install command naming a bare distribution. A git URL or a local path is fine; a
#: name is only fine when it is ours.
_INSTALL = re.compile(
    r"(?:uv (?:tool install|add)|uvx|pip install)\s+(?P<target>[\"']?[A-Za-z0-9._+-]+)"
)

#: Shipped prose. The unpublished design documents and this repository's private working
#: files are not in the tree and are not scanned.
_DOCUMENTATION: Final[tuple[str, ...]] = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "docs/*.md",
    "docs/**/*.md",
    "integrations/**/*.md",
    "models/*.md",
)


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _project_table() -> dict[str, object]:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    return project


def _documentation_files() -> list[Path]:
    files: list[Path] = []
    for pattern in _DOCUMENTATION:
        files.extend(sorted(REPO_ROOT.glob(pattern)))
    return sorted(set(files))


def test_the_distribution_is_one_this_project_owns() -> None:
    assert _project_table()["name"] == DISTRIBUTION_NAME


def test_the_console_scripts_keep_their_names() -> None:
    """The distribution was renamed; the commands users type were not."""
    scripts = _pyproject()["project"]
    assert isinstance(scripts, dict)
    entry_points = scripts["scripts"]
    assert entry_points == {"prism": "prism.cli:main", "prism-mcp": "prism.mcp_server:main"}


def test_no_install_example_names_a_distribution_we_do_not_own() -> None:
    offenders: list[str] = []
    for path in _documentation_files():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _INSTALL.search(line)
            if match is None:
                continue
            target = match.group("target").strip("\"'")
            if target.startswith(("git+", ".", "/", "-")) or target == DISTRIBUTION_NAME:
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")
    assert not offenders, "install example names a distribution this project does not own:\n" + (
        "\n".join(offenders)
    )


def test_the_typed_classifier_ships_with_a_marker() -> None:
    """A classifier is a promise to type checkers; without py.typed it is not kept."""
    classifiers = _project_table()["classifiers"]
    assert isinstance(classifiers, list)
    if "Typing :: Typed" in classifiers:
        assert (REPO_ROOT / "src" / "prism" / "py.typed").is_file()


def test_the_licence_points_at_the_manifest_that_exists() -> None:
    licence = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "models/artifacts/manifest.json" in licence
    assert "\nmodels/manifest.json" not in licence
    assert (REPO_ROOT / "models" / "artifacts" / "manifest.json").is_file()


def test_recovery_actions_do_not_point_at_a_release_that_does_not_exist() -> None:
    """An operator following a recovery action must find the thing it names."""
    from prism.errors import ErrorCode

    for code in ErrorCode:
        action = ErrorCode.recovery(code).safe_action
        assert "signed release" not in action.casefold(), (
            f"{code.value} tells the operator to restore from a signed release; none has "
            "been published"
        )
