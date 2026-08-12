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
- The adversarial workload at `benchmarks/workloads/adversarial.json`: all five candidates on
  one subject and one scope, so all 160 cross-candidate pairs survive E1 and every one is
  scored in both directions. `benchmarks/run.py` asserts that and fails the run if E1 removes
  a pair, gates the declared measurement deadline on it, and reports the pair work it
  actually performed.

### Fixed

- Peak RSS was a single `process.memory_info().rss` reading taken after the last measurement
  returned, which is not a peak — the maximum occurs inside a measurement. It is now a
  maximum sampled across the process tree on a background thread.
- README and `docs/performance.md` described the reference workload as "the maximum legal
  one … every same-scope pair scored in both directions". It submits the maximum input shape
  and scores 60 of its 160 pairs; the published 3.5-second p95 describes that, not the
  maximum NLI work. Both documents now carry the measured worst case: p95 9,421–9,985 ms
  across two idle runs, 18% to 25% over the 8,000 ms hard limit, with p99 straddling the
  10,000 ms limit and the declared 10-second deadline — one run passed it, the next missed
  it by 2.5%. A third run under ambient load reached p95 14,340 ms and a typed `TIMEOUT`;
  it is reported separately rather than folded into the range. No threshold was changed.

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

- **The measurement deadline is 15 s, raised from 10 s.** `DEFAULT_TIMEOUT_SECONDS` in
  `src/prism/limits.py`, and the declared `timeout_seconds` in both benchmark workloads. A
  caller that supplied no timeout previously waited up to 10 s for a report or a typed
  `TIMEOUT`, and now waits up to 15. The reason is measured: the legal maximum request —
  five candidates, four claims each, all on one subject, so all 160 cross-candidate pairs
  reach the NLI model — straddled the old deadline on one idle machine, one run passing at
  9,564 ms and the next failing at 10,246 ms. A contract that advertises a capacity has to
  be able to serve it. 15 s is the worst measured idle maximum plus 46%; it bounds the wait
  and does not promise completion under load, where a host still receives `TIMEOUT`.
  `MAX_CROSS_CANDIDATE_PAIRS` stays 160, and the 8,000 ms p95 and 10,000 ms p99 budgets are
  unchanged and still missed by that worst-case workload.
- Measurement p95 is republished from three consecutive release runs (3,154–3,468 ms). The
  previously published 4,876 ms was measured on a loaded machine.

## 0.1.0

Initial functionally complete build: preflight, measurement, synthesis contract, CLI, local
stdio MCP server, and Claude Code and Codex skills over one tested core.

The contradiction threshold is uncalibrated. Authoritative contradiction fields are
suppressed, and no precision, recall, F1, or MCC is published, because none has been
measured.
