"""Closed-finding mutation suite.

A gate that has only ever been observed passing is not evidence. Each control here is given
the exact input it exists to reject, and must reject it. This is the difference between "the
check ran and was quiet" and "the check works".

The failure this prevents is specific and common: a regex that stops matching after a
refactor, a validator whose condition inverts, a comparison that silently starts reading a
missing key as zero. All of those keep reporting PASS forever.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Final

import pytest

from prism.canonical import canonical_digest
from prism.contracts import (
    MeasureReport,
    PrismStatus,
    ProvenanceStatus,
    SourceDiversity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
BENCHMARKS = REPO_ROOT / "benchmarks"
for directory in (SCRIPTS, BENCHMARKS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import check_links  # noqa: E402
import check_regression_baseline  # noqa: E402
import check_reproducible_build  # noqa: E402
import check_seed_lock  # noqa: E402
import compare_benchmarks  # noqa: E402
import run  # noqa: E402

# --------------------------------------------------------------------------------------
# documentation relations
# --------------------------------------------------------------------------------------


def test_the_link_checker_catches_a_broken_relative_link() -> None:
    text = "See [the runbook](docs/does-not-exist.md) for details.\n"
    assert check_links._link_targets(text) == ["docs/does-not-exist.md"]
    assert not (REPO_ROOT / "docs/does-not-exist.md").exists()


def test_the_link_checker_ignores_external_and_anchor_targets() -> None:
    """External URLs are not this gate's business: a link checker that reaches the network
    turns an offline build into a flaky one."""
    assert not check_links._is_local("https://example.invalid/page")
    assert not check_links._is_local("mailto:security@example.invalid")
    assert not check_links._is_local("#a-heading")
    assert check_links._is_local("../SECURITY.md")


def test_the_width_check_catches_overlong_prose() -> None:
    text = "x" * (check_links.MAX_LINE_LENGTH + 1) + "\n"
    assert check_links._overlong_lines(text) == [1]


def test_the_width_check_exempts_tables_fences_and_frontmatter() -> None:
    """Each of these would break if wrapped, which is why they are exempt — and why the
    exemption itself needs a test, or it becomes a hole big enough to hide prose in."""
    long = "x" * (check_links.MAX_LINE_LENGTH + 1)

    assert check_links._overlong_lines(f"| {long} |\n") == []
    assert check_links._overlong_lines(f"```\n{long}\n```\n") == []
    assert check_links._overlong_lines(f"---\ndescription: {long}\n---\n") == []

    # ...but prose after the frontmatter closes is still checked.
    assert check_links._overlong_lines(f"---\nname: x\n---\n\n{long}\n") == [5]


def test_the_unpublished_reference_check_catches_a_citation() -> None:
    """The private design documents are gitignored and purged from history. A shipped file
    citing one points a reader at something that does not exist."""
    assert check_links._UNPUBLISHED.search("see PRISM_ARCHITECTURE.md section 6")
    assert check_links._UNPUBLISHED.search("PRISM_IMPLEMENTATION_PLAN Task 19")
    assert not check_links._UNPUBLISHED.search("see docs/architecture.md")


# --------------------------------------------------------------------------------------
# seed provenance
# --------------------------------------------------------------------------------------


def _write_lock(root: Path, payload: dict[str, object]) -> None:
    (root / "seeds.lock.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def seed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(check_seed_lock, "SEED_ROOT", tmp_path)
    monkeypatch.setattr(check_seed_lock, "LOCK_PATH", tmp_path / "seeds.lock.json")
    return tmp_path


def test_a_seed_added_after_the_lock_is_caught(seed_root: Path) -> None:
    """The exact shape of quiet corpus growth: a file appears, the lock does not mention it."""
    _write_lock(seed_root, {"schema_version": "1.0", "status": "LOCKED", "seeds": []})
    (seed_root / "extra.jsonl").write_text('{"a": 1}\n', encoding="utf-8")

    result = check_seed_lock.run()
    assert result.verdict == "FAIL"
    assert any("not in the lock" in finding for finding in result.findings)


def test_an_edited_seed_is_caught(seed_root: Path) -> None:
    """A hash that no longer matches means the corpus moved after it was sealed."""
    seed = seed_root / "pairs.jsonl"
    seed.write_text('{"a": 1}\n', encoding="utf-8")
    _write_lock(
        seed_root,
        {
            "schema_version": "1.0",
            "status": "LOCKED",
            "locked_at": "2026-01-01T00:00:00Z",
            "model_manifest_digest": "sha256:abc",
            "seeds": [
                {
                    "path": "pairs.jsonl",
                    "sha256": "0" * 64,
                    "authored_by": "a human",
                    "labelled_by": ["first", "second"],
                    "provenance": "real outputs",
                    "split": "calibration",
                }
            ],
        },
    )

    result = check_seed_lock.run()
    assert result.verdict == "FAIL"
    assert any("hash mismatch" in finding for finding in result.findings)


def test_a_single_labeller_is_caught(seed_root: Path) -> None:
    """One labeller cannot disagree with themselves, so kappa is undefined and the labels
    are one person's opinion rather than a measurement."""
    seed = seed_root / "pairs.jsonl"
    seed.write_text('{"a": 1}\n', encoding="utf-8")
    digest = check_seed_lock._sha256(seed)
    _write_lock(
        seed_root,
        {
            "schema_version": "1.0",
            "status": "LOCKED",
            "locked_at": "2026-01-01T00:00:00Z",
            "model_manifest_digest": "sha256:abc",
            "seeds": [
                {
                    "path": "pairs.jsonl",
                    "sha256": digest,
                    "authored_by": "a human",
                    "labelled_by": ["only one"],
                    "provenance": "real outputs",
                    "split": "calibration",
                }
            ],
        },
    )

    result = check_seed_lock.run()
    assert result.verdict == "FAIL"
    assert any("two independent labellers" in finding for finding in result.findings)


def test_an_empty_corpus_is_skipped_never_passed(seed_root: Path) -> None:
    """The live case. A green result here would be read as 'calibration is fine'."""
    _write_lock(seed_root, {"schema_version": "1.0", "status": "NO_CORPUS", "seeds": []})

    result = check_seed_lock.run()
    assert result.verdict == "SKIP"
    assert result.blocking


# --------------------------------------------------------------------------------------
# baseline monotonicity
# --------------------------------------------------------------------------------------


def _baseline(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "baseline_id": "test-1",
        "supersedes": None,
        "signature_status": "UNSIGNED",
        "package_version": "0.1.0",
        "workload": "reference.json",
        "hardware": {"platform": "test"},
        "metrics": dict.fromkeys(check_regression_baseline.REQUIRED_METRICS, 1.0),
    }
    payload.update(overrides)
    return payload


def test_a_baseline_ahead_of_the_candidate_is_caught(tmp_path: Path) -> None:
    """A stale or mismatched file, which is how a rollback gets mistaken for a pass."""
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(_baseline(package_version="9.9.9")), encoding="utf-8")

    result = check_regression_baseline.run(path=path, package_version="0.1.0")
    assert result.verdict == "FAIL"
    assert any("ahead of the candidate" in finding for finding in result.findings)


def test_a_baseline_that_supersedes_itself_is_caught(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(_baseline(supersedes="test-1")), encoding="utf-8")

    result = check_regression_baseline.run(path=path)
    assert result.verdict == "FAIL"
    assert any("supersede itself" in finding for finding in result.findings)


def test_a_missing_metric_is_caught(tmp_path: Path) -> None:
    """A baseline missing a metric cannot gate the thing it claims to gate."""
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(_baseline(metrics={"preflight_p95_ms": 1.0})), encoding="utf-8")

    result = check_regression_baseline.run(path=path)
    assert result.verdict == "FAIL"
    assert any("measurement_p95_ms" in finding for finding in result.findings)


def test_the_unsigned_baseline_blocks_under_require_signature() -> None:
    """The committed baseline is a recorded measurement, not attested evidence, and the
    release gate has to say so rather than implying a guarantee."""
    result = check_regression_baseline.run(require_signature=True)
    assert result.verdict == "FAIL"
    assert any("UNSIGNED" in finding for finding in result.findings)


# --------------------------------------------------------------------------------------
# performance regression
# --------------------------------------------------------------------------------------


def test_an_absolute_breach_is_caught() -> None:
    """Being no worse than last time is not a defence when last time was over budget."""
    budget = next(b for b in compare_benchmarks.BUDGETS if b.key == "measurement_p95_ms")
    assert budget.hard_limit is not None
    assert budget.absolute_finding(budget.hard_limit + 1) is not None
    assert budget.absolute_finding(budget.hard_limit - 1) is None


def test_a_relative_drift_is_caught() -> None:
    """The degradation shape that absolute limits miss: a few percent at a time, each step
    defensible on its own."""
    budget = next(b for b in compare_benchmarks.BUDGETS if b.key == "measurement_p95_ms")
    baseline = 1_000.0
    over = baseline * (1 + budget.relative_budget + 0.01)
    under = baseline * (1 + budget.relative_budget - 0.01)

    assert budget.relative_finding(over, baseline) is not None
    assert budget.relative_finding(under, baseline) is None


def test_a_smoke_profile_is_refused_as_release_evidence(tmp_path: Path) -> None:
    """Too few samples for a p95 to mean anything."""
    candidate = tmp_path / "run.json"
    candidate.write_text(
        json.dumps(
            {
                "profile": "smoke",
                "workload": "reference.json",
                "hardware": {},
                "preflight": {"p95_ms": 1.0},
                "measurement": {"p95_ms": 1.0, "p99_ms": 1.0},
                "cpu_seconds_per_measurement": 1.0,
                "peak_rss_bytes": 1,
                "default_report_bytes": 1,
            }
        ),
        encoding="utf-8",
    )

    result = compare_benchmarks.run(candidate_path=candidate)
    assert result.verdict == "FAIL"
    assert any("release profile" in finding for finding in result.findings)


def test_a_workload_mismatch_is_caught(tmp_path: Path) -> None:
    """Two runs of different workloads are not a comparison."""
    candidate = tmp_path / "run.json"
    candidate.write_text(
        json.dumps(
            {
                "profile": "release",
                "workload": "something-else.json",
                "hardware": {},
                "preflight": {"p95_ms": 1.0},
                "measurement": {"p95_ms": 1.0, "p99_ms": 1.0},
                "cpu_seconds_per_measurement": 1.0,
                "peak_rss_bytes": 1,
                "default_report_bytes": 1,
            }
        ),
        encoding="utf-8",
    )

    result = compare_benchmarks.run(candidate_path=candidate)
    assert result.verdict == "FAIL"
    assert any("workload mismatch" in finding for finding in result.findings)


# --------------------------------------------------------------------------------------
# benchmark runner controls
# --------------------------------------------------------------------------------------


def benchmark_report(**overrides: object) -> MeasureReport:
    """A report that reached the full 160-pair space; tests vary one field at a time."""
    base: dict[str, object] = {
        "status": PrismStatus.OK,
        "source_diversity": SourceDiversity.MULTI_SOURCE,
        "provenance_status": ProvenanceStatus.DECLARED_UNVERIFIED,
        "pairs_total": 160,
        "relevant_pairs": 160,
        "scope_divergent_count": 0,
        "scope_uncertain_count": 0,
        "contradiction_denominator": 160,
        "pairs_scored_by_nli": 160,
        "pairs_inferred_not_contradictory": 0,
        "nli_coverage": 1.0,
        "sources_distinct": 5,
        "pair_ledger_digest": canonical_digest([]),
    }
    base.update(overrides)
    return MeasureReport(**base)  # type: ignore[arg-type]


def test_the_adversarial_assertion_accepts_the_full_pair_space() -> None:
    assert run.full_pair_space_findings(benchmark_report()) == []


def test_the_adversarial_assertion_rejects_the_reference_workloads_coverage() -> None:
    """The exact input the control exists to reject.

    The reference workload submits the same 5x4 shape and reaches 60 scored pairs, because
    its claims are about four subjects and E1 correctly drops the rest. A p95 measured over
    that is the maximum input shape, not the maximum NLI work — which is the whole reason
    this assertion exists.
    """
    findings = run.full_pair_space_findings(
        benchmark_report(
            relevant_pairs=60,
            contradiction_denominator=60,
            pairs_scored_by_nli=60,
        )
    )
    assert findings
    assert any("E1 kept 60 of 160 pairs" in finding for finding in findings)
    assert any("both directions" in finding for finding in findings)


def test_a_pair_scored_in_one_direction_only_fails_the_assertion() -> None:
    """NLI is not symmetric, so a one-directional pass is a different measurement."""
    findings = run.full_pair_space_findings(benchmark_report(pairs_scored_by_nli=159))
    assert any("159 of 160 pairs in both directions" in finding for finding in findings)


def test_a_scope_divergent_pair_fails_the_assertion() -> None:
    findings = run.full_pair_space_findings(
        benchmark_report(scope_divergent_count=4, contradiction_denominator=156)
    )
    assert any("scope divergent" in finding for finding in findings)


def test_the_rss_sampler_records_a_maximum_a_final_reading_would_miss() -> None:
    """A peak that lives inside the run is the number this sampler exists to catch."""
    with run.PeakRssSampler(interval_seconds=0.01) as sampler:
        ballast = bytearray(256 * 1024 * 1024)
        ballast[::4096] = b"\x01" * len(ballast[::4096])  # touch it, so it is resident
        time.sleep(0.3)
        while_held = sampler.tree_rss_bytes()
        del ballast

    assert sampler.samples > 1, "the sampler thread never ran"
    assert sampler.peak_bytes >= while_held, (
        f"peak {sampler.peak_bytes} missed the {while_held} bytes resident during the run"
    )


def test_a_reading_taken_beside_the_peak_can_never_exceed_it() -> None:
    """A peak smaller than a number published next to it is not a peak.

    The runner takes one reading directly, to report what the end-of-run reading it
    replaced would have said. That reading has to go through the maximum: taken outside it,
    it came back 4 KB above the recorded peak on a real run, and the pair contradicted
    itself in the published record.
    """
    with run.PeakRssSampler(interval_seconds=0.01) as sampler:
        ballast = bytearray(64 * 1024 * 1024)
        final_reading = sampler.sample()
        del ballast

    assert sampler.peak_bytes >= final_reading


# --------------------------------------------------------------------------------------
# endurance soak verdict controls
# --------------------------------------------------------------------------------------
#
# The soak itself takes half an hour and needs the model bundle, so it cannot run here.
# What runs here is the part that decides what the soak means: a verdict function whose
# failure mode is reporting PASS forever. The private names are imported deliberately —
# duplicating the logic to test it would leave the copy passing and the original free to
# rot.

from prism.limits import MAX_CONCURRENT_MEASUREMENTS  # noqa: E402
from tests.endurance import test_soak  # noqa: E402


def soak_samples(
    rss_by_index: list[int],
    preflight_ms: list[float] | None = None,
    **overrides: object,
) -> list[dict[str, object]]:
    """Post-warm-up samples in the shape the soak records them."""
    latencies = preflight_ms or [0.12] * len(rss_by_index)
    samples: list[dict[str, object]] = []
    for index, (rss, preflight) in enumerate(zip(rss_by_index, latencies, strict=True)):
        sample: dict[str, object] = {
            "measurement": index,
            "measure_ms": 3_400.0,
            "preflight_ms": preflight,
            "rss_bytes": rss,
            "handles": 900,
            "threads": 12,
            "measure_workers": MAX_CONCURRENT_MEASUREMENTS,
            "available_permits": MAX_CONCURRENT_MEASUREMENTS,
            "abandoned_workers": 0,
            "encoder_session_id": 4_242,
        }
        sample.update(overrides)
        samples.append(sample)
    return samples


def soak_verdict(samples: list[dict[str, object]]) -> tuple[str, list[str]]:
    verdict, findings, _ = test_soak._evaluate(test_soak._windows(samples))
    return verdict, findings


def test_a_flat_soak_passes() -> None:
    """The control has to accept the thing it exists to accept, or it is just a red light."""
    plateau = [760_000_000, 760_400_000, 760_100_000, 760_300_000, 760_200_000] * 4
    verdict, findings = soak_verdict(soak_samples(plateau))
    assert (verdict, findings) == ("PASS", [])


def test_a_leaking_soak_is_caught() -> None:
    """Five megabytes a measurement: over the window allowance and rising in every window."""
    leak = [760_000_000 + index * 5_000_000 for index in range(20)]
    verdict, findings = soak_verdict(soak_samples(leak))
    assert verdict == "FAIL"
    assert any("rss in the final 20% window" in finding.lower() for finding in findings)
    assert any("rose in every retained window" in finding for finding in findings)


def test_a_drift_under_the_window_allowance_is_still_caught() -> None:
    """The leak shape the 5% window comparison misses on its own.

    A megabyte per measurement moves the final window only 2% above the baseline — inside
    the allowance, and still 20 MB an hour on a process that is supposed to have plateaued.
    The slope is what catches it, which is why both checks are here.
    """
    drift = [760_000_000 + index * 1_000_000 for index in range(20)]
    verdict, findings = soak_verdict(soak_samples(drift))
    assert verdict == "FAIL"
    assert not any("final 20% window" in finding for finding in findings)
    assert any("rose in every retained window" in finding for finding in findings)


def test_a_soak_on_a_loaded_machine_is_inconclusive_not_a_leak() -> None:
    """The failure this exists to prevent, and it has already happened once on this project.

    Preflight loads no model, so its p95 tripling measured the machine. A run whose RSS
    slope arrives together with a preflight slope has not observed a leak; it has observed
    a busy box, and reporting either verdict from it would be a fabrication.
    """
    rising = [760_000_000 + index * 1_000_000 for index in range(20)]
    ambient = [0.12] * 12 + [0.31] * 8
    verdict, findings = soak_verdict(soak_samples(rising, preflight_ms=ambient))
    assert verdict == "INCONCLUSIVE"
    assert any("ambient load" in finding for finding in findings)


def test_a_withheld_permit_is_caught() -> None:
    """Capacity lost to a permit that was never released, which no RSS reading would show."""
    plateau = [760_000_000] * 20
    verdict, findings = soak_verdict(
        soak_samples(plateau, available_permits=MAX_CONCURRENT_MEASUREMENTS - 1)
    )
    assert verdict == "FAIL"
    assert any("permit was not released" in finding for finding in findings)


def test_rebuilt_encoder_sessions_are_caught() -> None:
    """One process, one pair of sessions. A second pair is a duplicate model in memory."""
    plateau = [760_000_000] * 20
    samples = soak_samples(plateau)
    for index, sample in enumerate(samples):
        sample["encoder_session_id"] = 4_242 + index
    verdict, findings = soak_verdict(samples)
    assert verdict == "FAIL"
    assert any("encoder sessions were rebuilt" in finding for finding in findings)


# --------------------------------------------------------------------------------------
# cross-machine build controls
# --------------------------------------------------------------------------------------
#
# The build itself is exercised by the gate; what is exercised here is the comparison the
# gate cannot make on one machine. Building twice locally holds every environmental input
# constant, so the same-machine check passes by construction on exactly the differences a
# second machine would expose.

LOCAL_BUILD: Final[dict[str, object]] = {
    "source_revision": "f97a5e1",
    "platform": "Windows-11-10.0.26200-SP0",
    "python": "3.12.11",
    "artifacts": {"prism_preflight-0.1.0-py3-none-any.whl": "sha256:aaaa"},
}


def ci_build(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "source_revision": "f97a5e1",
        "platform": "Linux-6.11-x86_64",
        "python": "3.12.11",
        "artifacts": {"prism_preflight-0.1.0-py3-none-any.whl": "sha256:aaaa"},
    }
    record.update(overrides)
    return record


def test_two_machines_agreeing_produce_no_finding() -> None:
    assert check_reproducible_build.cross_machine_findings(LOCAL_BUILD, ci_build()) == []


def test_a_machine_dependent_artifact_is_caught() -> None:
    """The finding two builds in one temporary directory can never produce."""
    findings = check_reproducible_build.cross_machine_findings(
        LOCAL_BUILD,
        ci_build(artifacts={"prism_preflight-0.1.0-py3-none-any.whl": "sha256:bbbb"}),
    )
    assert any("depends on the machine that built it" in finding for finding in findings)


def test_comparing_two_different_commits_is_refused() -> None:
    """Same digest, different source, is not evidence of anything — and the mismatch case
    would blame the machine for a difference that is really a difference in the code."""
    findings = check_reproducible_build.cross_machine_findings(
        LOCAL_BUILD, ci_build(source_revision="0000000")
    )
    assert any("different source" in finding for finding in findings)
