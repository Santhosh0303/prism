"""Gate G15 — every evaluation seed is traceable and was locked before scoring.

The threat is not malice, it is drift: a pair gets reworded because it "obviously" should
have been labelled the other way, a few extra examples appear after a disappointing run,
and the reported F1 quietly stops describing anything. Hashing the corpus before the first
encoder run and failing on any movement is what separates a measurement from a preference.

Checks performed:

* the lock parses and declares a known schema version;
* every seed named in the lock exists and hashes to the recorded digest;
* every seed carries an author, two independent labellers, a provenance note, and a split;
* no seed file exists on disk that the lock does not name — the case that catches data
  added after the seal;
* `status` is honest about whether a corpus exists at all.

An empty corpus is reported as `SKIP`, never `PASS`. There is nothing to verify, and a
green result would be read as "calibration is fine".

    uv run python scripts/check_seed_lock.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Final

from _gate import REPO_ROOT, GateResult, failed, passed, report, skipped

GATE: Final[str] = "G15 seed provenance"

SEED_ROOT = REPO_ROOT / "tests" / "seeds"
LOCK_PATH = SEED_ROOT / "seeds.lock.json"

SUPPORTED_SCHEMA: Final[frozenset[str]] = frozenset({"1.0"})
VALID_SPLITS: Final[frozenset[str]] = frozenset({"calibration", "test"})
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "path",
    "sha256",
    "authored_by",
    "labelled_by",
    "provenance",
    "split",
)

#: Files in the seed directory that are documentation or the lock itself, not corpus data.
_NON_SEED_NAMES: Final[frozenset[str]] = frozenset({"README.md", "seeds.lock.json"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _declared_seed_files(seeds: list[dict[str, Any]]) -> set[Path]:
    return {(SEED_ROOT / str(seed.get("path", ""))).resolve() for seed in seeds}


def _on_disk_seed_files() -> set[Path]:
    return {
        path.resolve()
        for path in SEED_ROOT.rglob("*")
        if path.is_file() and path.name not in _NON_SEED_NAMES
    }


def _check_seed_entry(seed: dict[str, Any], index: int, findings: list[str]) -> None:
    label = str(seed.get("path", f"entry {index}"))

    for field in REQUIRED_FIELDS:
        if not seed.get(field):
            findings.append(f"{label}: missing required field '{field}'")

    labellers = seed.get("labelled_by")
    if isinstance(labellers, list) and len(labellers) < 2:
        findings.append(f"{label}: needs two independent labellers, found {len(labellers)}")
    if isinstance(labellers, list) and len(set(map(str, labellers))) != len(labellers):
        findings.append(f"{label}: the same labeller is recorded twice")

    split = seed.get("split")
    if split is not None and split not in VALID_SPLITS:
        findings.append(f"{label}: unknown split '{split}'")

    path_value = seed.get("path")
    if not isinstance(path_value, str) or not path_value:
        return
    seed_path = SEED_ROOT / path_value
    if not seed_path.is_file():
        findings.append(f"{label}: declared in the lock but absent from disk")
        return

    actual = _sha256(seed_path)
    expected = str(seed.get("sha256", ""))
    if actual != expected:
        findings.append(f"{label}: hash mismatch — the corpus changed after it was locked")


def run() -> GateResult:
    if not LOCK_PATH.is_file():
        return failed(GATE, [f"missing seed lock: {LOCK_PATH.relative_to(REPO_ROOT).as_posix()}"])

    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return failed(GATE, [f"seed lock is not valid JSON: {error.msg}"])

    schema_version = str(lock.get("schema_version", ""))
    if schema_version not in SUPPORTED_SCHEMA:
        return failed(GATE, [f"unsupported seed lock schema version '{schema_version}'"])

    seeds = lock.get("seeds", [])
    if not isinstance(seeds, list):
        return failed(GATE, ["'seeds' must be a list"])

    status = str(lock.get("status", ""))
    findings: list[str] = []

    orphans = _on_disk_seed_files() - _declared_seed_files(seeds)
    for orphan in sorted(orphans):
        findings.append(
            f"{orphan.relative_to(SEED_ROOT).as_posix()}: present on disk but not in the lock"
        )

    for index, seed in enumerate(seeds):
        if not isinstance(seed, dict):
            findings.append(f"entry {index}: must be an object")
            continue
        _check_seed_entry(seed, index, findings)

    splits = [str(seed.get("split")) for seed in seeds if isinstance(seed, dict)]
    detail: dict[str, Any] = {
        "status": status,
        "seeds": len(seeds),
        "calibration_seeds": splits.count("calibration"),
        "test_seeds": splits.count("test"),
    }

    if findings:
        return failed(GATE, findings, **detail)

    if not seeds or status == "NO_CORPUS":
        return skipped(
            GATE,
            "no evaluation corpus exists, so no accuracy figure can be published; "
            "see tests/seeds/README.md for the protocol",
            **detail,
        )

    if status != "LOCKED":
        return failed(GATE, [f"seeds are present but status is '{status}', not LOCKED"], **detail)

    if not lock.get("locked_at"):
        return failed(GATE, ["a locked corpus must record locked_at"], **detail)
    if not lock.get("model_manifest_digest"):
        return failed(
            GATE,
            ["a locked corpus must record the model manifest digest in force when it was sealed"],
            **detail,
        )

    return passed(GATE, **detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    args = parser.parse_args()
    return report(run(), as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
