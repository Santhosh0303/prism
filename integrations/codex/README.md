# PRISM for Codex

## Install

1. Install the package so that `prism-mcp` is on PATH. There is no PyPI release yet, and
   `prism` on PyPI is an unrelated project, so install from an exact commit — never from a
   floating name or branch:

   ```
   uv tool install "git+https://github.com/Santhosh0303/prism@<full-commit-sha>"
   ```

   Pin the full SHA of the commit you reviewed — a branch name resolves to whatever was
   pushed last. The distribution is named `prism-preflight`; the commands it installs are
   `prism` and `prism-mcp`. When a tagged, signed release exists, pin that tag instead.

2. Verify before wiring it up:

   ```
   prism health --deep
   ```

3. Merge `config.toml.example` into your Codex configuration.

4. Copy `skills/prism/` into your Codex skills directory, and append
   `AGENTS.fragment.md` to your project `AGENTS.md`.

## Parity with the Claude Code integration

Both integrations drive the same four MCP tools in the same order with the same
budgets. `tests/compatibility/test_skill_parity.py` normalises both skill files
and asserts they agree on tool names, ordering, schema version, and safety rules.
Wording may differ; semantics may not.

## What it can do

Four read-only tools. No network, no credentials, no shell, no file writes, no
user-project scanning.

## Do not

Do not replace the command with a runtime bootstrap. Keep the configuration
project-local, secret-free, and free of network resolution.
