"""The runtime dependency budget is a control, not an aspiration.

Every direct runtime dependency is native-code-adjacent or protocol-critical, and each one
widens the zero-day surface of a component that is supposed to be small enough to audit.
Six is the agreed ceiling. Raising it is allowed, but only deliberately: this test fails on
the addition so the justification lands in the same change.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

#: Ceiling agreed in pyproject.toml. A change here requires a documented missing
#: capability, security review, size/startup impact, licence review, alternative
#: analysis, and a removal plan.
RUNTIME_DEPENDENCY_BUDGET: Final[int] = 6

#: Packages that may never enter the runtime graph, restated here as a value check to
#: complement the import-linter contracts, which only see modules that are imported.
FORBIDDEN_RUNTIME_PACKAGES: Final[frozenset[str]] = frozenset(
    {
        "anthropic",
        "autogen",
        "cohere",
        "crewai",
        "fastapi",
        "flask",
        "httpx",
        "langchain",
        "langgraph",
        "mistralai",
        "openai",
        "requests",
        "sqlalchemy",
        "tensorflow",
        "torch",
        "transformers",
        "uvicorn",
    }
)

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _runtime_dependencies() -> list[str]:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    dependencies = data["project"]["dependencies"]
    assert isinstance(dependencies, list)
    return [str(entry) for entry in dependencies]


def _distribution_name(requirement: str) -> str:
    """Return the bare package name from a PEP 508 requirement string."""
    for separator in ("[", "<", ">", "=", "!", "~", ";", " "):
        requirement = requirement.split(separator, 1)[0]
    return requirement.strip().lower().replace("_", "-")


def test_runtime_dependency_count_is_within_budget() -> None:
    dependencies = _runtime_dependencies()
    assert len(dependencies) <= RUNTIME_DEPENDENCY_BUDGET, (
        f"runtime dependency budget is {RUNTIME_DEPENDENCY_BUDGET}, "
        f"found {len(dependencies)}: {dependencies}"
    )


def test_no_forbidden_runtime_package() -> None:
    names = {_distribution_name(entry) for entry in _runtime_dependencies()}
    intersection = names & FORBIDDEN_RUNTIME_PACKAGES
    assert not intersection, f"forbidden runtime dependency declared: {sorted(intersection)}"


def test_every_runtime_dependency_is_version_bounded() -> None:
    """An unbounded dependency silently accepts a future major version."""
    unbounded = [entry for entry in _runtime_dependencies() if "<" not in entry]
    assert not unbounded, f"runtime dependency without an upper bound: {unbounded}"


def test_web_and_database_stacks_are_absent() -> None:
    """G0: no website, database, auth, or remote server in v1."""
    names = {_distribution_name(entry) for entry in _runtime_dependencies()}
    assert not any(name.startswith(("django", "psycopg", "asyncpg", "pymongo")) for name in names)
