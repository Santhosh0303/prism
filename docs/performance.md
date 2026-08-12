# Performance

Every number here was measured on the machine named below, with the workload named below.
A latency figure without both is not evidence.

## Reference workload

The maximum legal *shape*: 5 candidates × 4 claims, 160 cross-candidate pairs, no speed
floor, NLI coverage of 1.0 over the pairs that reach the model. Its claims are about four
different subjects, so E1 drops 100 of the 160 pairs as not comparable and **60 are scored**
— in both directions, and every one of them. That is a correct measurement and a good
comparable workload; it is not the maximum amount of inference the contract permits. It is
held as data at
[`../benchmarks/workloads/reference.json`](../benchmarks/workloads/reference.json) so that
the benchmark, the offline-network check, and any later comparison all measure the same
input.

```bash
uv run python benchmarks/run.py --profile release --output benchmarks/out/run.json
uv run python scripts/compare_benchmarks.py
```

The release profile takes 100 preflight calls and 30 measurements after warm-up. The smoke
profile is smaller and is not release evidence.

## Adversarial workload

[`../benchmarks/workloads/adversarial.json`](../benchmarks/workloads/adversarial.json) puts
all five candidates on one subject and one scope. Every one of the 160 cross-candidate pairs
clears the relevance floor (lowest observed E1 similarity 0.72 against a floor of 0.42), none
is scope divergent, and all 160 are scored in both directions — 320 NLI calls, the maximum
work the contract permits. The runner asserts that: if E1 removes a single pair, the run
fails rather than publishing a p95 that describes the maximum input shape instead of the
maximum NLI work.

```bash
uv run python benchmarks/run.py --profile release \
  --workload benchmarks/workloads/adversarial.json \
  --output benchmarks/out/adversarial.json
```

The run exits non-zero when its worst warm measurement misses the workload's declared
deadline. On current hardware it lands on both sides of that line. See
[Worst case](#worst-case-measured) below.

## Hardware

| | |
|---|---|
| Platform | Windows 11 (10.0.26200) |
| Processor | AMD64 Family 25 Model 80 (AMD, 16 logical cores) |
| Python | 3.12.10 |
| Execution provider | CPU |

## Measured — reference workload, three consecutive release runs

One machine, otherwise idle, three runs back to back. The spread is published because a
single run would imply a precision this workload does not have. These figures describe 60
scored pairs; the worst case is in the next section.

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

## Worst case, measured

Two release runs of the adversarial workload, back to back on the same idle machine, 160 of
160 pairs scored in both directions in each. Two runs, not three, so this is a ceiling
observation rather than a baseline and it is not directly comparable with the table above.
A third run, taken earlier while the machine was busy, is reported separately below.

| Metric | Idle run A | Idle run B | Reference (worst of 3) | Target | Hard limit |
|---|---:|---:|---:|---:|---:|
| Pairs scored by NLI | 160 of 160 | 160 of 160 | 60 of 160 | — | — |
| Preflight p95 | 0.144 ms | 0.110 ms | 0.150 ms | < 15 ms | < 50 ms |
| Measurement p50 | 8,395 ms | 8,529 ms | 3,232 ms | — | — |
| Measurement p95 | **9,421 ms** | **9,985 ms** | 3,468 ms | < 3,500 ms | **< 8,000 ms** |
| Measurement p99 | 9,564 ms | **10,246 ms** | 3,579 ms | < 9,000 ms | **< 10,000 ms** |
| Measurement MAD | 301 ms | 176 ms | — | — | — |
| CPU per measurement | 18.79 s | 19.31 s | 6.59 s | — | — |
| Peak RSS | 953 MB | 953 MB | 750 MB | < 2.2 GB | < 3 GB |
| Default report size | 3,205 B | 3,205 B | 3,175 B | < 6 KB | < 12 KB |
| Cold start | 11,549 ms | 10,800 ms | 5,748 ms | reported | not gated |
| Deadline verdict | PASS | **FAIL** | PASS | — | 10 s |

**The p95 hard limit is missed in both runs — 17.8% and 24.8% over 8,000 ms — and the
workload sits directly on the 10-second measurement deadline.** The two runs agree to within
1.6% on p50, 2.8% on CPU-seconds and 20 KB on peak RSS, and disagree on the verdict: run A's
worst measurement took 9,564 ms and passed, run B's took 10,246 ms and failed, which is also
2.5% over the 10,000 ms p99 hard limit. Whether the legal-maximum workload completes is
decided by the run, not by the input. That is a worse property than a clean failure would be,
because it is invisible until it is not.

The latency series are taken under a longer instrumentation timeout, recorded in each run's
`deadline.series_measured_under_seconds`: a ceiling cannot be recorded without observing past
it. The verdict is judged against the workload's declared 10-second timeout, and
`benchmarks/run.py` exits non-zero when it is missed — run A exited 0, run B exited 1.

### The same workload under load

An earlier run of the identical workload, taken while the machine was busy, measured p50
13,591 ms, p95 14,340 ms, p99 14,592 ms and 30.96 CPU-seconds, and `service.measure()`
returned a typed `TIMEOUT` rather than a report — verified directly against the shipped
10-second deadline, not inferred. It is excluded from the range above and reported here
because of what it shows: ambient load moves this workload by roughly 50%, against a margin
of 4%. The give-away is in the preflight column. Preflight is pure Python and loads no model,
so its p95 tripling from 0.110–0.144 ms to 0.306 ms measured the machine, not the code. Any
worst-case figure published without that check is describing whatever else was running.

No threshold was moved to accommodate any of this. The 8,000 ms budget, the 10-second
deadline, the 160-pair maximum, and the fixed 2-thread inference settings are all unchanged.
What the numbers say is that a five-lens measurement whose claims all address one subject
costs roughly 8.5 seconds and 19 CPU-seconds on an idle machine, against a contract that
promises an answer or a refusal within ten. The published 3.5-second figure was never wrong;
it describes 60 pairs of inference, and the legal maximum is 160.

Memory is the one budget with real headroom: 953 MB against a 2.2 GB target, so the
2.7× increase in NLI work costs 27% more resident memory. Peak RSS is now a maximum sampled
across the process tree every 50 ms on a background thread, not a single reading taken when
the run ends: the maximum sits inside a measurement, and the final reading walks past it. On
this run the sampled maximum was 13 MB above the final reading; on the reference workload the
two agree to within 0.1%, which is why the earlier 750 MB figures need no restatement.

## Measured — endurance soak

`tests/endurance/test_soak.py::test_measurement_reaches_a_resource_plateau`, run explicitly
on the machine above:

```bash
uv run pytest tests/endurance -m "endurance and models"
```

500 measurements of the reference workload, real inference throughout, 25.2 minutes; the
first 10 are discarded as warm-up and the remaining 490 are scored in five equal windows.
The run writes `benchmarks/out/soak.json` and the test then reads that file back and asserts
against what is on disk — a soak that was started, or whose record was never opened, is not
evidence.

| Window | Measurements | Mean RSS | Handles | Threads | Permits free at rest | Encoder sessions | Preflight p95 | Measurement p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 (warm baseline) | 98 | 730.6 MB | 346 | 38.4 | 2 of 2 | 1 | 0.426 ms | 3,780 ms |
| 2 | 98 | 731.7 MB | 344 | 37.7 | 2 of 2 | 1 | 0.327 ms | 3,209 ms |
| 3 | 98 | 732.6 MB | 344 | 37.2 | 2 of 2 | 1 | 0.308 ms | 3,047 ms |
| 4 | 98 | 733.6 MB | 344 | 37.2 | 2 of 2 | 1 | 0.308 ms | 3,071 ms |
| 5 (final 20%) | 98 | 734.6 MB | 344 | 37.0 | 2 of 2 | 1 | 0.310 ms | 2,995 ms |

**Verdict `PASS`, no findings.** The final window is 1.0055× the warm baseline on RSS,
0.993× on handles and 0.964× on threads, against a 5% allowance. Peak RSS over the whole run
was 735.1 MB from 20,308 samples. Every permit was back at rest in every window, no worker
was ever abandoned, the measurement pool never held more than one thread, and one pair of
encoder sessions served all 500 measurements.

**RSS did rise in every window, and that is recorded rather than rounded off.** The rise is
1.04 MB per window, 0.55% end to end — roughly 10 KB per measurement. It sits below the 1%
floor the slope check applies, so it is not reported as a leak, and 25 minutes cannot
separate a drift that small from allocator behaviour. What can be said is bounded: no leak
large enough to matter over half an hour, and nothing here rules out a slow one over days.
Extrapolating 10 KB per measurement is arithmetic, not a measurement, and is not published
as a figure.

**The ambient control.** Preflight loads no model, so its p95 cannot move because of
anything the soak does; a moving preflight p95 means the machine moved. It is recorded per
window, and a window more than 1.5× the baseline is discarded rather than believed. No
window was discarded here: p95 fell from 0.426 ms to about 0.31 ms and stayed there, which
is the process settling, not drifting. The absolute figures sit above the 0.110–0.144 ms
idle numbers in the table above because the soak itself keeps the CPU busy for 25 minutes —
the control watches drift across windows, not absolute idleness. Had the baseline or the
final window been contaminated, the verdict would be `INCONCLUSIVE`; a resource slope
measured on a loaded machine is a reading of the machine, which this project has already
published once and does not intend to repeat.

## Measured — cross-machine build reproducibility

`scripts/check_reproducible_build.py` builds twice and compares normalised wheel content.
Two builds on one machine share a filesystem, a clock and a `uv`, so they are held constant
on exactly the inputs that would differ elsewhere. The CI `reproducible-build` job therefore
also writes its own record and uploads it, and the same script compares a local build
against it:

```bash
gh run download <run-id> --name build-digest
uv run python scripts/check_reproducible_build.py --compare-with build-digest.json
```

The comparison is keyed on the git tree rather than the commit, and refuses to proceed when
the two records name different trees. A `pull_request` run builds the merge of the branch
into its base: that commit exists on no branch and matches nothing a maintainer can check
out, while its tree is the branch tip's whenever the base has not moved. Keying on the
commit would have refused a runner build of exactly this source — it did, on the first
attempt, which is why the check is on the tree.

Measured, for tree `ade6f985e610cfbd09e67fc3d99d1c9b7fd99519`:

| | Local | CI runner |
|---|---|---|
| Platform | Windows 11 (10.0.26200) | Linux 6.17 (azure), glibc 2.39 |
| Python | 3.12.10 | 3.12.3 |
| `prism_preflight-0.1.0-py3-none-any.whl` | `sha256:a93b47bb…8790` | `sha256:a93b47bb…8790` |

`verdict: PASS`, no findings: two operating systems, two Python patch releases, one wheel.
That is the claim two builds in one temporary directory cannot support.

What it does not cover: the wheel holds `src/prism` and its metadata, so this says nothing
about the model bundle, the lock resolution on a third platform, or any artifact that is not
built here. It is one comparison against one runner, not a reproducibility guarantee.

## Baseline

The [regression baseline](../benchmarks/baselines/regression-baseline.json)
records the median run **of the reference workload**. It is **`UNSIGNED`** — there is no
release signing pipeline yet, so it is a recorded measurement, not attested evidence, and
`scripts/check_regression_baseline.py --require-signature` fails on it deliberately. It has
not been re-pinned against the adversarial workload: three consecutive release runs on an
idle machine are what a baseline requires, and a workload that fails its own deadline is not
something to pin a regression budget to before that failure has been ruled on.

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

- No soak longer than half an hour. The 25-minute run above shows a plateau over 490
  measurements; it cannot speak to a process left up for days, and the 0.55% rise it did
  record is below what a run that length can attribute.
- No soak under concurrency. Measurements in the soak are sequential, so the second permit
  and the second pool thread are never exercised over a sustained run.
- No throughput-under-concurrency figure. Admission is capped at two active measurements
  with zero queue, which is tested for correctness but not profiled for sustained rate.
- No second hardware platform, and no Linux or macOS numbers.
