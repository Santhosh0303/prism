# Performance

Every number here was measured on the machine named below, with the workload named below.
A latency figure without both is not evidence.

## Reference workload

The maximum legal one: 5 candidates × 4 claims, every same-scope pair scored in both
directions, no speed floor, production coverage of 1.0. It is held as data at
[`../benchmarks/workloads/reference.json`](../benchmarks/workloads/reference.json) so that
the benchmark, the offline-network check, and any later comparison all measure the same
input.

```bash
uv run python benchmarks/run.py --profile release --output benchmarks/out/run.json
uv run python scripts/compare_benchmarks.py
```

The release profile takes 100 preflight calls and 30 measurements after warm-up. The smoke
profile is smaller and is not release evidence.

## Hardware

| | |
|---|---|
| Platform | Windows 11 (10.0.26200) |
| Processor | AMD64 Family 25 Model 80 (AMD, 16 logical cores) |
| Python | 3.12.10 |
| Execution provider | CPU |

## Measured — three consecutive release runs

One machine, otherwise idle, three runs back to back. The spread is published because a
single run would imply a precision this workload does not have.

| Metric | Run 1 | Run 2 | Run 3 | Target | Hard limit |
|---|---:|---:|---:|---:|---:|
| Preflight p95 | 0.113 ms | 0.141 ms | 0.150 ms | < 15 ms | < 50 ms |
| Preflight p99 | 0.165 ms | 0.174 ms | 0.228 ms | — | < 75 ms |
| Measurement p50 | 3,063 ms | 3,212 ms | 3,232 ms | — | — |
| Measurement p95 | 3,154 ms | 3,391 ms | 3,468 ms | < 3,500 ms | < 8,000 ms |
| Measurement p99 | 3,207 ms | 3,392 ms | 3,579 ms | < 9,000 ms | < 10,000 ms |
| CPU per measurement | 6.17 s | 6.48 s | 6.59 s | — | — |
| Peak RSS | 750 MB | 750 MB | 750 MB | < 2.2 GB | < 3 GB |
| Default report size | 3,175 B | 3,175 B | 3,175 B | < 6 KB | < 12 KB |
| Cold start | 5,184 ms | 5,308 ms | 5,748 ms | reported | not gated |

Measurement p95 lands between 3,154 ms and 3,468 ms across the three runs — inside the
3,500 ms target, but by a margin narrower than the run-to-run spread itself. Treat "meets
target" as provisional on this hardware: a slightly slower machine or a busier one will miss
it. Expect roughly 3.5 seconds for a full five-lens measurement. Preflight is effectively
free at a fraction of a millisecond.

**An earlier run on this same machine recorded a measurement p95 of 4,876 ms.** It was taken
while the machine was under other load and is not comparable; it is mentioned because it was
previously published, and because it shows how much ambient load moves this number.

## Baseline

The [regression baseline](../benchmarks/baselines/regression-baseline.json)
records the median run. It is **`UNSIGNED`** — there is no release signing pipeline yet, so
it is a recorded measurement, not attested evidence, and
`scripts/check_regression_baseline.py --require-signature` fails on it deliberately.

`compare_benchmarks.py` refuses to evaluate relative regression across different hardware
rather than reporting it with a caveat. A p95 from another machine is not a faster or slower
version of this one; it is a different measurement.

## Regression budgets

| Metric | Budget vs baseline |
|---|---:|
| Preflight p95 | 10% |
| Measurement p95 / p99 | 15% |
| CPU seconds per measurement | 15% |
| Peak RSS | 10% |
| Default report bytes | 10% |

Latency, CPU, memory, and output size are compared in one pass, so a speed gain bought with
memory or a larger report cannot be presented as a straight win.

## Where the time goes

Measurement is dominated by E2. The reference workload produces 160 directional NLI scorings
(every cross-candidate claim pair, both directions), against a single pooled embedding pass
for E1. Preflight does no inference at all — a keyword table and a registry lookup — which is
why it is three orders of magnitude faster and why preflight-only mode is a genuinely
different operating point rather than a degraded one.

The production profile scores every same-scope pair and reports `nli_coverage = 1.0`. A
speed floor that skipped pairs would be faster and would silently convert unscored pairs into
apparent agreement, so it is prohibited in `pair.py` and the prohibition is checked by
`scripts/check_architecture.py`.

## Not measured

- No endurance soak. The in-suite probe is short and is not evidence of a plateau over
  hours.
- No throughput-under-concurrency figure. Admission is capped at two active measurements
  with zero queue, which is tested for correctness but not profiled for sustained rate.
- No second hardware platform, and no Linux or macOS numbers.
- No cross-machine reproducibility comparison.
