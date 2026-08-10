# Operations

How to install PRISM, watch it, and get out of trouble. Written for the person who has to
fix it at an inconvenient hour, so every step is a command that exists in this repository.

## Install

```bash
uv sync
uv run python scripts/verify_models.py
uv run prism health --deep
```

`prism health --deep` verifies artifact hashes, asserts the CPU execution provider, and
runs one synthetic inference. It prints no task text, no candidate text, no environment
values, and no home paths.

Production MCP configuration must invoke the installed, version-pinned `prism-mcp`
executable. Runtime package resolution — `uvx`, `npx`, `@latest`, a git URL — is prohibited
and `scripts/check_architecture.py` fails the build if an example configuration reintroduces
it.

## Required host controls

PRISM's own code performs no privileged operation, but Python and the native inference
runtime run with the ambient rights of the process. The application is least-capability;
it is not a sandbox. Production deployment needs all four of:

1. a host sandbox or container with no outbound route;
2. a minimal environment allowlist — no secrets in the child process environment;
3. a read-only model directory, ideally outside the working tree via `PRISM_MODEL_ROOT`;
4. a dedicated artifact directory for anything the operator wants to keep.

## Kill switch

`PRISM_DISABLE_MEASURE=1` disables all inference while leaving preflight, synthesis, health,
and the whole CLI fully available.

Use it when a model or native-runtime advisory has no fix yet. It is the first move in
almost every incident below, because it removes the ONNX Runtime code path without removing
the tool from the host that depends on it.

```bash
PRISM_DISABLE_MEASURE=1 prism health --deep
```

Health still reports `OK` with `measurement_disabled_by_kill_switch: true`. Degraded is a
state PRISM reports, not one it hides.

## Rollback procedure

Six steps, in order. Each is verifiable; none requires trusting that a previous step worked.

1. **Disable the MCP configuration.** Remove or comment out the `prism` server entry in the
   host configuration and restart the host. This stops new work immediately; the server is
   stateless, so nothing is lost.
2. **Pin the previous release.** Install the last known-good version explicitly. Never a
   mutable tag and never a range — an incident is not the moment to let a resolver choose.
3. **Verify the artifact you rolled back to.** Check the hash and, once release signing
   exists, the signature and its transparency-log entry. A rollback to an unverified
   artifact is a second incident.
4. **Clear and re-verify the model bundle.** Remove the artifact directory, restore the
   pinned revisions from `docs/model-card.md`, and run:

   ```bash
   uv run python scripts/verify_models.py
   ```

   Do not run `--generate` here. That rewrites the manifest from whatever is on disk, which
   would make a tampered bundle verify cleanly — it destroys the exact control you are
   trying to use.
5. **Re-run deep health.** `uv run prism health --deep`. Measurement is only re-enabled once
   this passes.
6. **Report the compromised versions.** Record which versions were affected, the window they
   were installed, and the artifact digests, in the security advisory and in
   `CHANGELOG.md`. See [`../SECURITY.md`](../SECURITY.md) for the disclosure route.

## Incident playbooks

### A dependency or model advisory lands

Set the kill switch. Check whether the affected component is in the runtime closure:

```bash
uv run python scripts/build_sbom.py --output dist/sbom.cdx.json
uv run python -m pip_audit --strict
```

If it is in the runtime set, patch or pin and rebuild. If it is dev-only, record it and fix
on the normal cycle. Known-exploited vulnerabilities block release regardless of how the
advisory is scored.

### Model artifact verification fails

Measurement is already unavailable — verification fails closed, before ONNX Runtime opens
anything. Preflight is unaffected. Treat a hash mismatch as tampering until proven
otherwise: re-download from the pinned revision into a clean directory and verify again. If
it still mismatches, stop and escalate; do not regenerate the manifest.

### Measurement hangs or the process will not settle

There is no queue by design: two active measurements maximum, immediate typed `BUSY` beyond
that. A hang is therefore a stuck worker, not a backlog. Restart the server process; state
is not persisted, so nothing is recovered or lost. If it recurs, capture the workload and
run it under the benchmark to see whether it is size-related:

```bash
uv run python benchmarks/run.py --profile smoke
```

### A host upgrade changes the workflow

Skills and MCP configuration are versioned against the server. A client outside the declared
range receives a typed `VERSION_MISMATCH` rather than a degraded result. Check the declared
range in the skill frontmatter against `prism version`.

## Release checks

```bash
uv run python scripts/release_gate.py --strict
```

Every blocking check, one verdict. A tool that is not installed reports `SKIP`, and under
`--strict` a skip blocks — a check that did not run has not passed.

**This build does not pass `--strict`, by design.** The evaluation corpus does not exist and
the regression baseline is unsigned. Both are stated as `SKIP` rather than worked around.

## What is not implemented

Stated so nobody plans around a control that is not here:

- no release signing, provenance attestation, or transparency-log verification;
- no signed regression baseline — the committed one is a recorded measurement;
- no endurance soak evidence beyond the short in-suite probe;
- no compatibility matrix against pinned prior host releases.
