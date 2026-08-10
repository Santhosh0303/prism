# Security Policy

## Security position

PRISM is a local, read-only analytic component. Its primary security control is a narrow
capability set rather than a policy layer wrapped around powerful tools
(architecture invariant A10).

During normal analysis PRISM performs:

- no outbound network access;
- no shell or subprocess execution;
- no filesystem mutation;
- no credential, token, or secret handling;
- no user-project scanning.

It reads only three things: packaged registry data, hash-verified model artifacts beneath
a dedicated model root, and input explicitly supplied by the caller.

## What PRISM does not claim

PRISM does not claim that zero-day vulnerabilities are eliminated (invariant A12).
Hash verification proves an artifact is the one that was pinned; it cannot prove that a
correctly signed native dependency is free of unknown flaws.

**PRISM application code performs no privileged operation, but Python and the native
inference runtime still execute with the ambient rights of the host process.** Production
deployment therefore requires all of the following from the operator:

1. an OS-level sandbox around the server process;
2. a minimal environment-variable allowlist;
3. a dedicated read-only directory for model artifacts;
4. no secrets present in the child process environment.

Application least capability is not an OS sandbox (invariant A16).

## Kill switch

Setting `PRISM_DISABLE_MEASURE=1` disables all model inference while leaving deterministic
preflight and synthesis-contract generation fully available. Use it when a native runtime
or model advisory has no fix yet.

`prism health --deep` reports the disabled state explicitly.

No environment switch can bypass hash verification, canonical path containment, source
grouping, duplicate suppression, or input limits.

## Reporting a vulnerability

Report privately to the repository owner. Do not open a public issue for an unpatched
vulnerability. Include: affected version, `prism health --deep` output, reproduction
steps, and impact. Please allow a reasonable period for a fix before public disclosure.

Reports containing raw task or candidate text are discouraged; PRISM never logs that
content and neither should a report.

## Supply chain

Every release is intended to carry an SBOM, SHA-256 sums, SLSA provenance, and a Sigstore
signature. **Version 0.1.0 carries none of these yet.** Signed provenance requires a
hosted CI identity that does not exist for this build, and it is recorded as outstanding
in the README rather than presented as done.

Dependencies are pinned in `uv.lock` and were audited with `pip-audit` against the
resolved lock: no known vulnerabilities across 46 packages at the time of writing. Model
artifacts are pinned by immutable upstream revision and SHA-256 and re-verified before
every session; see `docs/model-card.md`.
