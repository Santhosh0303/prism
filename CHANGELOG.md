# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow
semantic versioning: patch releases do not change public schemas, and a minor release
accepts the previous declared minor or returns a typed `VERSION_MISMATCH`.

## Unreleased

### Added

- Release gate scripts: `release_gate.py`, `verify_offline.py`, `build_sbom.py`,
  `check_seed_lock.py`, `check_links.py`, `check_architecture.py`,
  `check_regression_baseline.py`, `compare_benchmarks.py`, `check_reproducible_build.py`.
- CI, scheduled security, CodeQL, and release workflows, plus Dependabot configuration.
- Published documentation: architecture, system design and technology stack, threat model,
  performance, operations, and nine architecture decision records.
- `registry.schema.json`, so an operator vendoring a lens set can validate it before PRISM
  sees it, held against the loader by a drift test.
- Operational acceptance tests (G17), a closed-finding mutation suite, non-model performance
  budgets, and an endurance probe that is deselected by default.
- The reference workload as data at `benchmarks/workloads/reference.json`, a benchmark
  report schema, and a recorded regression baseline.
- The seed lock at `tests/seeds/seeds.lock.json`, which reports `NO_CORPUS` and blocks any
  accuracy claim.
- `models/artifacts/manifest.json` is now committed, so a fresh clone can verify a bundle it
  obtained independently.

### Fixed

- README reported 403 tests and 58 type-checked files; both predated the removal of two test
  modules and were wrong.
- README claimed `deptry` reported no issues; it exited non-zero on `benchmarks/run.py`.
  `benchmarks/` is now correctly scoped as developer tooling.
- `pyproject.toml` cited two test modules that no longer exist as the controls enforcing the
  dependency budget and the lint configuration. The dependency budget test is restored; the
  stale comment is gone.
- `AUDIT_BASELINE_DOC_VERSION` referenced an unpublished document revision and was read by
  nothing. Removed.
- The Codex configuration example pointed at `models/manifest.json`, which is not where the
  manifest lives.
- `compare_benchmarks.py` raised instead of reporting when the candidate file was outside
  the working tree.

### Changed

- Measurement p95 is republished from three consecutive release runs (3,154–3,468 ms). The
  previously published 4,876 ms was measured on a loaded machine.

## 0.1.0

Initial functionally complete build: preflight, measurement, synthesis contract, CLI, local
stdio MCP server, and Claude Code and Codex skills over one tested core.

The contradiction threshold is uncalibrated. Authoritative contradiction fields are
suppressed, and no precision, recall, F1, or MCC is published, because none has been
measured.
