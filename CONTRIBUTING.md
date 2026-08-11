# Contributing

PRISM measures disagreement between candidate answers and refuses to guess when it cannot
measure. That posture is the product, so the review bar here is about evidence rather than
style: a change is judged by what it proves, not by how it reads.

Read [`SECURITY.md`](SECURITY.md) before reporting anything that looks exploitable. Do not
open a public issue for it.

## Setup

```bash
uv sync --frozen
uv run python scripts/verify_models.py   # only if you have the model bundle
uv run prism health
```

Python is pinned to 3.12 (`requires-python = ">=3.12,<3.13"`). The model bundle is roughly
403 MB and is not committed; preflight, synthesis, and health work without it, and the
`models`-marked tests are the only ones that need it.

## Before you open a pull request

```bash
uv run python -m pytest --verbosity=0
uv run python -m ruff format src/ tests/
uv run python scripts/release_gate.py --text
```

The gate must report `verdict: PASS  blocking: 0`. Exactly one `SKIP` is expected — G15
seed provenance, because no evaluation corpus exists. `--strict` fails on this build by
design; that is not a regression, and making it pass by lowering a threshold is not a fix.

Run `ruff format` **before** the gate. G13 checks formatting and will fail an otherwise
correct change on its first pass otherwise.

## What review will ask for

- **A test that failed before your change.** New behaviour that no test would have caught
  is not covered, whatever the diff size. Say in the PR which test fails without the fix.
- **A stated scope for a semantic change.** Anything that moves canonical report bytes —
  a new field, a changed digest input, a different denominator — is a semantic change.
  Review the moved fixtures one by one and say in the commit message why the semantics
  changed. A rubber-stamped golden diff hides the next one.
- **Measured numbers with their conditions.** "Faster" is not a claim; a p95 with the
  hardware and the workload is. A missed target is recorded as missed, never re-described.
- **Honest limitations.** Heuristics are documented as heuristics. If a control catches
  the realistic case but not the general one, the docs say so — see the external-data scan
  in [`docs/threat-model.md`](docs/threat-model.md) for the tone.

## What review will refuse

- A weakened test: a deleted assertion, a new `skip` or `xfail`, or a loosened comparison
  used to make a step pass.
- A seventh runtime dependency without the analysis
  `tests/unit/test_dependency_budget.py` requires: the missing capability, the security
  and size impact, the alternatives, and a removal plan.
- Network access on any core path. PRISM is offline by construction and G8 enforces it.
- Raw user content in a log, a diagnostic, or an error message. Diagnostics name fields,
  never values.
- A `Co-Authored-By` trailer. Commits carry one author.

## Commits and pull requests

Conventional-commit subjects (`fix:`, `feat:`, `docs:`, `chore:`), imperative mood, and a
body that explains the defect rather than restating the diff. Squash merges are not used;
pull requests are rebased, so keep the history readable.

`main` is protected. Required checks are `Tests and static gates`, `Reproducible build`,
and `Analyse Python`, and they must be green before merge.

## Documentation

Markdown lines are capped at 100 characters — G14 enforces it, exempting fenced code,
table rows, and YAML frontmatter. Relative links must resolve, and shipped files must not
cite the unpublished design documents.
