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
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_links  # noqa: E402
import check_regression_baseline  # noqa: E402
import check_seed_lock  # noqa: E402
import compare_benchmarks  # noqa: E402

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
