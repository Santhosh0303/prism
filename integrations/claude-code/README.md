# PRISM for Claude Code

## Install

1. Install the package so that `prism-mcp` is on PATH:

   ```
   uv tool install prism
   ```

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
