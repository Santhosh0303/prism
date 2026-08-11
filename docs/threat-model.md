# Threat model

What PRISM is defending against, what each defence actually proves, and where the defence
stops. Controls that are not implemented are listed as not implemented.

## Assets

1. **The integrity of a measurement.** A wrong contradiction result is worse than none: it
   is acted on.
2. **The host's decision process.** PRISM output feeds a model that writes an answer.
   Poisoned output steers that answer.
3. **The user's environment.** PRISM runs locally with the invoking user's rights.
4. **The release channel.** A compromised artifact reaches every installation.

## Trust boundaries

### 1. Host and user → PRISM

*Everything crossing this boundary is data.* Task text and candidate claims are never
interpreted as instructions, never placed in a shell, path, SQL statement, template engine,
or evaluator, and never used to select a code path by name.

- **Prompt injection.** Direct and indirect probes live in `tests/security/`. An injected
  instruction inside a claim survives as *content* — it is measured like any other text —
  and never as a directive.
- **Unicode obfuscation.** Identifiers are constrained to an explicit pattern, so homoglyph
  and bidirectional ambiguity is excluded by construction rather than normalised away.
- **Resource exhaustion.** Byte, word, candidate, claim, and pair ceilings are checked
  before inference. The maximum legal workload is a tested case, not a hope.
- **Source spoofing.** `source_group_id` is required and bounded; human-readable labels are
  display-only and cannot increase measured source diversity. One host generation pass is
  one source however many lenses it carries.

### 2. PRISM → local model bundle

Every artifact is canonicalised beneath a dedicated root, confirmed to be a regular file,
size-checked, and SHA-256 verified **before** ONNX Runtime opens anything. Symlinks, hard
links, path traversal, and ONNX external-data references fail closed with
`MODEL_INTEGRITY_FAILURE`. A hard link is rejected by link count (`st_nlink > 1`): a second
name for the same inode is a name outside the verified root through which the bytes can be
rewritten after they were hashed.

One process serves one model bundle. Encoder sessions are cached process-wide for memory
reasons, and that cache is keyed on the canonical model root plus the digest of the manifest
the bundle was verified against. A request naming a different root, or the same root whose
manifest has since changed, raises rather than being served the first bundle under the
second bundle's name.

*What this proves:* the bytes are the bytes that were pinned. *What it does not:* that those
bytes are safe. A correctly hashed artifact with an unknown flaw verifies perfectly. The
external-data check is a bounded byte scan plus a companion-file check, not a full protobuf
parse — that would need an `onnx` dependency, and the runtime budget is six packages.

### 3. MCP client → local server

Local stdio only. No open port, no listener, no remote transport in v1 — the entire class of
network-facing attacks is removed rather than defended. The tool catalogue is static and
deterministic, and all four tools carry read-only annotations. There is no write tool, no
destructive tool, and no tool that accepts a path to write to, a URL, a shell command, or a
credential.

*Tool poisoning* is bounded by that catalogue being fixed at build time: a malicious
description cannot appear at runtime because descriptions are not fetched at runtime.

### 4. Release and supply chain

Exact resolution in `uv.lock`, a CycloneDX SBOM from the lock, dependency advisory scanning,
and a six-package runtime budget that is enforced by a test rather than a policy document.

**Not implemented:** artifact signing, SLSA provenance, transparency-log verification, and
trusted publishing. Until they exist, an installer's assurance comes from the lock and the
hashes, and nothing here should be read as attestation.

### 5. PRISM output → host synthesis

The synthesis contract forbids resolving a conflict PRISM could not resolve, and requires
preserving unresolved contradictions, scope-divergent findings, and retained minority
claims. While the threshold is uncalibrated, the contract instructs the host to treat
provisional numbers as a prompt to look, never as a finding.

## Zero-day exposure

Not eliminated, and not claimed to be. The strategy is blast radius and recovery time:

- narrow dependency surface — six runtime packages, two of them native;
- no open port, no network route, no secrets in the process;
- an independent kill switch that removes the inference path without removing the tool;
- hash-pinned artifacts that make a swap detectable;
- a documented rollback in [`operations.md`](operations.md).

A native runtime advisory is handled by disabling measurement first and patching second.

## What could still go wrong

Stated plainly, because a threat model that only lists solved problems is marketing.

- **A native syscall.** `scripts/verify_offline.py` proves no Python-level code opens a
  socket. It cannot prove ONNX Runtime does not. Establishing that needs a network namespace
  with no route — a host control, not an in-process one.
- **An uncalibrated threshold.** The largest correctness risk in the system is not an
  attacker; it is that nobody has validated the 0.5 boundary against labelled data. The
  calibration gate suppresses the affected fields for exactly this reason.
- **A frozen constant chosen without a corpus.** `RELEVANCE_FLOOR = 0.42` is what stops E2's
  false positives from becoming reported findings. It was chosen by inspection.
- **Ambient process rights.** Least-capability application code inside a normally privileged
  process is not isolation. The host sandbox is a requirement, not a recommendation.
- **Declared provenance.** PRISM records what a caller declares about sources. It cannot
  verify that two declared sources are genuinely independent.

## Reporting

See [`../SECURITY.md`](../SECURITY.md).
