"""Gate G14 — documentation relations stay valid.

Three failures this catches, all of which have happened to real repositories:

1. A relative link that pointed somewhere before a file moved. Published documentation
   that 404s is a defect, not a cosmetic issue: the install path in README depends on it.
2. A reference to material that is deliberately unpublished. PRISM's design documents are
   gitignored and purged from history; a shipped file citing one sends a reader to
   something that does not exist and advertises the omission.
3. Prose that runs past the agreed width. No Markdown linter is configured, so nothing
   else notices. Table rows and fenced code are exempt — a table row cannot be wrapped,
   and rewrapping code changes its meaning.

Run:

    uv run python scripts/check_links.py
"""

from __future__ import annotations

import argparse
import re
from typing import Final

from _gate import REPO_ROOT, GateResult, failed, passed, report, tracked_files

GATE: Final[str] = "G14 documentation relations"

MAX_LINE_LENGTH: Final[int] = 100

#: Inline and reference-style links, excluding images and bare autolinks.
_LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

#: Material that is intentionally absent from the published repository. Citing it from a
#: shipped file is a broken relation even though no filesystem path is involved.
_UNPUBLISHED = re.compile(r"PRISM_(?:ARCHITECTURE|IMPLEMENTATION_PLAN|SYSTEM_DESIGN_TECH_STACK)")

_SKIP_SCHEMES = ("http://", "https://", "mailto:", "#")


def _link_targets(text: str) -> list[str]:
    return [match.group(1) for match in _LINK.finditer(text)]


def _is_local(target: str) -> bool:
    return not target.startswith(_SKIP_SCHEMES)


def _overlong_lines(text: str) -> list[int]:
    """Line numbers exceeding the width budget.

    Three exemptions, each because wrapping would break something:

    * fenced code — rewrapping changes what the example does;
    * table rows — Markdown has no continuation syntax for a cell;
    * YAML frontmatter — a skill ``description`` is parsed as one scalar, and a host
      reads it to decide whether to invoke the skill at all.
    """
    overlong: list[int] = []
    lines = text.splitlines()
    in_fence = False
    in_frontmatter = bool(lines) and lines[0].rstrip() == "---"

    for number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if in_frontmatter:
            if number > 1 and stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or stripped.startswith("|"):
            continue
        if len(line) > MAX_LINE_LENGTH:
            overlong.append(number)
    return overlong


def run() -> GateResult:
    findings: list[str] = []
    checked_links = 0
    documents = tracked_files(suffix=".md")

    for document in documents:
        relative = document.relative_to(REPO_ROOT).as_posix()
        text = document.read_text(encoding="utf-8")

        for target in _link_targets(text):
            if not _is_local(target):
                continue
            checked_links += 1
            resolved = (document.parent / target.split("#", 1)[0]).resolve()
            if not resolved.exists():
                findings.append(f"{relative}: broken link -> {target}")

        for match in _UNPUBLISHED.finditer(text):
            findings.append(f"{relative}: references unpublished document {match.group(0)}")

        for number in _overlong_lines(text):
            findings.append(f"{relative}:{number}: line exceeds {MAX_LINE_LENGTH} characters")

    detail = {"documents": len(documents), "local_links_checked": checked_links}
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
