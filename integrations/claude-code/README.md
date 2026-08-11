# PRISM for Claude Code

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

2. Verify the install before wiring it up:

   ```
   prism health --deep
   ```

   Deep health verifies model artifact hashes, asserts the CPU execution provider,
   and runs one synthetic inference. If the model bundle is absent it reports
   `MODEL_UNAVAILABLE` — preflight still works, measurement does not.

3. Copy `.mcp.json.example` to your project as `.mcp.json`, review it, and approve
   the server when Claude Code prompts you.

4. Copy `skills/prism/` into your project's `.claude/skills/` directory.

## Scope

Project or local scope is recommended over user scope: PRISM is useful on review
work, not on every conversation.

## What it can do

Four read-only tools. No network, no credentials, no shell, no file writes, no
user-project scanning. It reads packaged registry data, the verified model
bundle, and the input you pass it.

## Do not

Do not change the command to `uvx`, `npx`, `latest`, or a URL. Production
configuration invokes an installed, version-pinned executable; anything else
resolves code at server start.
