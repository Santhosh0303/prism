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
3. **Verify the artifact you rolled back to.** A rollback to an unverified artifact is a
   second incident. Releases from 0.1.0 onward carry provenance, so check it rather than
   trusting the version string:

   ```bash
   gh attestation verify prism_preflight-<version>-py3-none-any.whl --repo Santhosh0303/prism
   ```

   A non-zero exit means the bytes in front of you were not produced by this repository's
   release workflow. Treat that as the incident, not as a tooling problem.
4. **Clear and re-verify the model bundle.** Remove the artifact directory, restore the
   pinned revisions from `docs/model-card.md`, and run:

   ```bash
   uv run python scripts/acquire_models.py
   uv run python scripts/verify_models.py
   ```

   `acquire_models.py` fetches only the immutable revisions the committed manifest names
   and discards anything that does not hash to it; a bundle restored by hand is fine too,
   and the verification step is the same.

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

Stated so nobody plans around a control that is not here. Each one carries why it is absent
and the next thing that would actually close it — a pending item with no next action is a
wish, and drifts into looking like a plan.

| Control | State | Why it is not closed | Next executable action |
|---|---|---|---|
| Compatibility matrix against pinned prior host releases | `PENDING_EXTERNAL_VALIDATION` | The host versions to pin have not been chosen, and a matrix against "latest" re-measures a moving target and reports it as compatibility. | Pin explicit versions of the MCP hosts PRISM claims to support, add them to the `determinism` matrix in `ci.yml`, and record the measured pass per pinned version. |
| Signed regression baseline | `UNSIGNED`, recorded | No longer blocked, merely not done. The release identity it was waiting on now exists, but the committed baseline is still a measurement rather than attested evidence, and `check_regression_baseline.py --require-signature` fails on it on purpose. | Attest the baseline through the same identity that signs the distributions. |
| Signed upgrade and rollback drills (G17, G20) | `PENDING_EXTERNAL_VALIDATION` | Both need two signed releases to move between, and exactly one exists. Not a missing capability now — a missing second data point. | Repeat after 0.1.1 is released through the same pipeline. |
| Evaluation corpus, precision/recall/F1 | absent | No corpus of real pre-existing outputs with provenance and independent second-human labels exists. | Harvest and label a corpus; commit the manifest hash before any encoder run; score the sealed set once. |

Four items that used to sit in this list have been executed and moved out, none of them by
being described more generously.

The **endurance soak** is measured, and the **cross-machine build comparison** has run: a
Windows local build and a Linux CI-runner build of one tree produce the same normalised wheel
content. Both, including what each does not cover, are in [`performance.md`](performance.md).

**Artifact signing, SLSA provenance, the transparency log and trusted publishing** closed
together on 2026-08-13, when `prism-preflight` 0.1.0 was published by
[run 31649871113](https://github.com/Santhosh0303/prism/actions/runs/31649871113) from tag
`v0.1.0` at commit `1ca7e7b`. No API token exists or was used; PyPI minted a short-lived
credential from the workflow's OIDC identity. Both distributions carry a SLSA v1 provenance
attestation signed through the Public Good Sigstore instance and recorded in Rekor, and PEP
740 attestations naming publisher `GitHub / Santhosh0303/prism / release.yml / pypi`.

What makes that a verified claim rather than a reported one: the artifacts were downloaded
back **from the index** and checked on a separate machine with
`gh attestation verify <artifact> --repo Santhosh0303/prism`, which exited 0 for both — while
the same wheel checked against an unrelated repository exited 1 with a 404. The negative
control matters, because a verification command that cannot fail proves nothing.

Three release controls remain open and are in the table above. Signing exists now, so the
regression baseline is no longer blocked from being attested — merely not attested yet — and
the two signed-release drills need a second release to move between.
