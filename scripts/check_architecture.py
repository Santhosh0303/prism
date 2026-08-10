"""Architectural fitness functions that import-linter cannot express.

import-linter reasons about the import graph, which covers most of the boundaries PRISM
cares about. The rules here are the remainder — properties about literals, tool names, and
configuration text that no import graph can see:

* the relevance floor stays a compiled constant and never becomes a knob;
* pair enumeration carries no speed-floor configuration, so the experimental optimisation
  cannot silently shrink the production denominator;
* the MCP tool catalogue is static and matches what both host skills tell a model to call;
* production MCP configuration resolves no package at start-up;
* no dynamic-execution primitive exists anywhere in the core.

Run:

    uv run python scripts/check_architecture.py
"""

from __future__ import annotations

import argparse
import io
import re
import tokenize
from pathlib import Path
from typing import Final

from _gate import REPO_ROOT, GateResult, add_src_to_path, failed, passed, report

add_src_to_path()

from prism.constants import MCP_TOOL_NAMES  # noqa: E402

GATE: Final[str] = "architecture fitness functions"

SRC = REPO_ROOT / "src" / "prism"
PAIR_MODULE = SRC / "measure" / "pair.py"
FLOOR_MODULE = "src/prism/measure/pair.py"

SKILLS: Final[tuple[Path, ...]] = (
    REPO_ROOT / "integrations" / "claude-code" / "skills" / "prism" / "SKILL.md",
    REPO_ROOT / "integrations" / "codex" / "skills" / "prism" / "SKILL.md",
)

CONFIG_EXAMPLES: Final[tuple[Path, ...]] = (
    REPO_ROOT / "integrations" / "claude-code" / ".mcp.json.example",
    REPO_ROOT / "integrations" / "codex" / "config.toml.example",
)

#: Runtime package resolution in a server command line. Each of these fetches or resolves
#: something at start-up, which is the supply-chain shape the design bans outright.
_BOOTSTRAP = re.compile(r"\b(uvx|npx|pipx\s+run|--from\s+git\+|@latest|:latest)\b")

#: Dynamic execution. Ruff's `S` rules cover several of these; restating them here keeps
#: the architectural intent in one place and survives a lint-config change.
_DYNAMIC_EXECUTION: Final[frozenset[str]] = frozenset({"eval", "exec", "compile", "__import__"})

#: A knob for the relevance floor would let a caller tune their way to a friendlier
#: result, which is precisely what a frozen constant prevents.
_FLOOR_KNOB = re.compile(r"(relevance[_-]?floor|RELEVANCE_FLOOR)")

_SPEED_FLOOR = re.compile(r"speed[_-]?floor|SPEED_FLOOR")

#: The three tools a host model is told to call. `prism.health` is deliberately absent: it
#: is an operator diagnostic, and putting it in a workflow skill would invite a model to
#: run health checks instead of answering.
WORKFLOW_TOOLS: Final[tuple[str, ...]] = MCP_TOOL_NAMES[:3]


def _source_files() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


def _code_tokens(path: Path) -> list[tokenize.TokenInfo]:
    """Executable tokens only.

    Grepping source text cannot tell `re.compile` from `compile`, or a docstring that
    documents a prohibition from code that violates it. Both mistakes produce a gate that
    fails on correct code, which is worse than no gate — it trains people to ignore it.
    """
    text = path.read_text(encoding="utf-8")
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    return [
        token
        for token in tokens
        if token.type not in {tokenize.STRING, tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE}
    ]


def _check_relevance_floor(findings: list[str]) -> None:
    """The constant may be defined in pair.py and re-exported; it may not be settable."""
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if not _FLOOR_KNOB.search(line):
                continue
            if relative == FLOOR_MODULE or "measure/__init__.py" in relative:
                continue
            findings.append(f"{relative}:{number}: relevance floor referenced outside pair.py")

    text = PAIR_MODULE.read_text(encoding="utf-8")
    if not re.search(r"^RELEVANCE_FLOOR:\s*Final\[float\]\s*=", text, re.MULTILINE):
        findings.append(f"{FLOOR_MODULE}: RELEVANCE_FLOOR is no longer a module-level constant")
    if re.search(r"os\.environ.*(?i:floor)", text):
        findings.append(f"{FLOOR_MODULE}: the relevance floor is readable from the environment")


def _check_speed_floor(findings: list[str]) -> None:
    """pair.py documents the prohibition in its docstring; only code may not name it."""
    for token in _code_tokens(PAIR_MODULE):
        if token.type == tokenize.NAME and _SPEED_FLOOR.search(token.string):
            findings.append(
                f"{FLOOR_MODULE}:{token.start[0]}: pair enumeration references a speed floor"
            )


def _check_tool_catalogue(findings: list[str]) -> None:
    server_text = (SRC / "mcp_server.py").read_text(encoding="utf-8")
    for index, name in enumerate(MCP_TOOL_NAMES):
        if f"MCP_TOOL_NAMES[{index}]" not in server_text:
            findings.append(f"mcp_server.py does not register {name} from the constant")
    if re.search(r"name\s*=\s*[\"']prism\.", server_text):
        findings.append("mcp_server.py hard-codes a tool name instead of using MCP_TOOL_NAMES")

    known = set(MCP_TOOL_NAMES)
    for skill in SKILLS:
        relative = skill.relative_to(REPO_ROOT).as_posix()
        if not skill.is_file():
            findings.append(f"{relative}: missing")
            continue
        text = skill.read_text(encoding="utf-8")
        for name in WORKFLOW_TOOLS:
            if name not in text:
                findings.append(f"{relative}: does not reference {name}")
        for referenced in set(re.findall(r"prism\.[a-z_]+", text)):
            if referenced not in known:
                findings.append(f"{relative}: references unknown tool {referenced}")


def _check_no_runtime_bootstrap(findings: list[str]) -> None:
    for example in CONFIG_EXAMPLES:
        relative = example.relative_to(REPO_ROOT).as_posix()
        if not example.is_file():
            findings.append(f"{relative}: missing")
            continue
        for number, line in enumerate(example.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue  # the ban is explained in prose in the Codex example
            if _BOOTSTRAP.search(line):
                findings.append(f"{relative}:{number}: runtime package resolution in MCP config")


def _check_no_dynamic_execution(findings: list[str]) -> None:
    """A bare call to one of these. `re.compile` is an attribute access, not the builtin,
    so the preceding token settles it."""
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        tokens = _code_tokens(path)
        for index, token in enumerate(tokens):
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            preceding = tokens[index - 1] if index else None

            if (
                token.type == tokenize.NAME
                and token.string in _DYNAMIC_EXECUTION
                and following is not None
                and following.string == "("
                and (preceding is None or preceding.string != ".")
            ):
                findings.append(
                    f"{relative}:{token.start[0]}: dynamic execution primitive {token.string}()"
                )

            after = tokens[index + 2] if index + 2 < len(tokens) else None
            if (
                token.string == "os"
                and following is not None
                and following.string == "."
                and after is not None
                and after.string in {"system", "popen"}
            ):
                findings.append(f"{relative}:{token.start[0]}: os.{after.string} in core")


def run() -> GateResult:
    findings: list[str] = []
    _check_relevance_floor(findings)
    _check_speed_floor(findings)
    _check_tool_catalogue(findings)
    _check_no_runtime_bootstrap(findings)
    _check_no_dynamic_execution(findings)

    detail = {
        "source_files": len(_source_files()),
        "tools": len(MCP_TOOL_NAMES),
        "skills": len(SKILLS),
    }
    if findings:
        return failed(GATE, findings, **detail)
    return passed(GATE, **detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    args = parser.parse_args()
    return report(run(), as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
