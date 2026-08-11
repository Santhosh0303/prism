"""Golden preflight contracts.

The required property is identical perspective order and contract bytes across 100
repeated runs. Byte identity, not structural similarity, because the drift these tests
exist to catch is cosmetic-looking: a reordered set, a reworded rule, a changed budget.

The pinned digests are the golden values. If one changes, that is a semantic change to
what PRISM asks hosts to do, and it requires a reviewed registry version bump — not a
refreshed constant.
"""

from __future__ import annotations

import pytest

from prism.canonical import canonical_digest, canonical_json
from prism.contracts import PreflightRequest, PrismMode, PrismStatus
from prism.preflight.contract import build_preflight_contract
from prism.preflight.registry import PerspectiveRegistry

ARCHITECTURE_TASK = (
    "Assess the system design: component boundaries, coupling between services, "
    "and the scalability tradeoff of the proposed architecture."
)
SECURITY_TASK = (
    "Security review of the upload endpoint: threat model the attack surface, "
    "check authentication and the injection vulnerability we suspect."
)

TASKS: dict[str, str] = {"architecture": ARCHITECTURE_TASK, "security": SECURITY_TASK}

#: The registry the digests below were reviewed against. Pinned separately so that
#: "someone edited the registry" fails as its own named case rather than as six
#: simultaneous digest mismatches with no stated cause.
GOLDEN_REGISTRY_VERSION = "1.0.0"
GOLDEN_REGISTRY_HASH = "sha256:6aab424d376b0f2c60376b96110681c804fa11f22c85fba52b6906eff220aaf1"

#: Reviewed once, 2026-08-12, against registry 1.0.0. Confirmed identical under
#: ``PYTHONHASHSEED`` 0, 1, 42 and 12345 and across separate processes.
#:
#: If one of these fails, the contract PRISM hands hosts has changed. The fix is a
#: reviewed registry version bump, not a refreshed constant — regenerating the value to
#: make the test green removes the only thing standing between a silent semantic change
#: and a release.
GOLDEN_CONTRACT_DIGESTS: dict[tuple[str, PrismMode], str] = {
    ("architecture", PrismMode.LITE): (
        "sha256:be33d4356910e8b83a6a8102d25eba62aa575458171e4cf882f5a05632bee13b"
    ),
    ("architecture", PrismMode.STANDARD): (
        "sha256:6c0b12599d611396e9bca04b3fab32a5bae5aaae627232ed95d53d728e1a1fc6"
    ),
    ("architecture", PrismMode.CRITICAL): (
        "sha256:52d159ec4553c581820e9e082e08df3e18465ec113adfe86c3cc8cb3531fab95"
    ),
    ("security", PrismMode.LITE): (
        "sha256:6c5c7cf3d46489b4a24450588f9a5949363a7ac5a73a1fa44e87b34b5f128abc"
    ),
    ("security", PrismMode.STANDARD): (
        "sha256:5facb3f084d00758a32a8d98e1826cbc20228f51b356fe8de0883b4b5e4e6715"
    ),
    ("security", PrismMode.CRITICAL): (
        "sha256:72c1ef5b3f67caf592d9b0de6a54d913eee96833cc6cb3c6a017464a5815ede9"
    ),
}


@pytest.fixture(scope="module")
def registry() -> PerspectiveRegistry:
    return PerspectiveRegistry.load()


def build(registry: PerspectiveRegistry, task: str, mode: PrismMode) -> str:
    return canonical_digest(
        build_preflight_contract(PreflightRequest(task=task, mode=mode), registry)
    )


# --------------------------------------------------------------------------------------
# pinned golden digests
# --------------------------------------------------------------------------------------


def test_the_reviewed_registry_is_the_one_on_disk(registry: PerspectiveRegistry) -> None:
    """The digests below describe registry 1.0.0 and nothing else."""
    assert (registry.version, registry.content_hash) == (
        GOLDEN_REGISTRY_VERSION,
        GOLDEN_REGISTRY_HASH,
    ), "registry changed — the golden digests need a reviewed version bump, not a refresh"


@pytest.mark.parametrize(("task_name", "mode"), list(GOLDEN_CONTRACT_DIGESTS))
def test_contract_digest_matches_the_pinned_golden(
    registry: PerspectiveRegistry, task_name: str, mode: PrismMode
) -> None:
    assert build(registry, TASKS[task_name], mode) == GOLDEN_CONTRACT_DIGESTS[(task_name, mode)]


# --------------------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(PrismMode))
def test_contract_bytes_are_identical_across_one_hundred_runs(
    registry: PerspectiveRegistry, mode: PrismMode
) -> None:
    digests = {build(registry, ARCHITECTURE_TASK, mode) for _ in range(100)}
    assert len(digests) == 1


def test_a_freshly_loaded_registry_produces_the_same_contract() -> None:
    """Determinism must survive a reload, not just a cached object."""
    first = build(PerspectiveRegistry.load(), SECURITY_TASK, PrismMode.CRITICAL)
    second = build(PerspectiveRegistry.load(), SECURITY_TASK, PrismMode.CRITICAL)
    assert first == second


def test_distinct_tasks_produce_distinct_contracts(registry: PerspectiveRegistry) -> None:
    assert build(registry, ARCHITECTURE_TASK, PrismMode.STANDARD) != build(
        registry, SECURITY_TASK, PrismMode.STANDARD
    )


# --------------------------------------------------------------------------------------
# contract shape
# --------------------------------------------------------------------------------------


def test_contract_reports_registry_identity(registry: PerspectiveRegistry) -> None:
    report = build_preflight_contract(PreflightRequest(task=ARCHITECTURE_TASK), registry)
    assert report.registry_version == registry.version
    assert report.registry_hash == registry.content_hash
    assert report.status is PrismStatus.OK


@pytest.mark.parametrize(
    ("mode", "expected_count"),
    [(PrismMode.LITE, 3), (PrismMode.STANDARD, 4), (PrismMode.CRITICAL, 5)],
)
def test_mode_controls_perspective_count(
    registry: PerspectiveRegistry, mode: PrismMode, expected_count: int
) -> None:
    report = build_preflight_contract(PreflightRequest(task=ARCHITECTURE_TASK, mode=mode), registry)
    assert len(report.perspectives) == expected_count


def test_every_perspective_carries_purpose_questions_and_budget(
    registry: PerspectiveRegistry,
) -> None:
    report = build_preflight_contract(
        PreflightRequest(task=ARCHITECTURE_TASK, mode=PrismMode.CRITICAL), registry
    )
    for instruction in report.perspectives:
        assert instruction.purpose.strip()
        assert instruction.questions
        assert 1 <= instruction.claim_budget <= 4


def test_contract_states_the_source_and_untrusted_input_rules(
    registry: PerspectiveRegistry,
) -> None:
    """The host is told in-band that one pass is one source, and that task text is data."""
    report = build_preflight_contract(PreflightRequest(task=ARCHITECTURE_TASK), registry)
    contract = report.execution_contract
    assert "source_group_id" in contract.source_rule
    assert "independent" in contract.source_rule.lower()
    assert "instructions" in contract.untrusted_input_rule.lower()
    assert contract.output == "claim_packets"


def test_contract_does_not_echo_the_task_text(registry: PerspectiveRegistry) -> None:
    """Echoing the task back would waste host tokens and re-present untrusted text as
    if PRISM had endorsed it."""
    marker = "Assess the system design"
    report = build_preflight_contract(PreflightRequest(task=ARCHITECTURE_TASK), registry)
    assert marker not in canonical_json(report)


# --------------------------------------------------------------------------------------
# token budget — the contract is host tokens the user pays for
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(PrismMode))
def test_instruction_size_stays_within_the_token_budget(
    registry: PerspectiveRegistry, mode: PrismMode
) -> None:
    """Target < 900 tokens, hard gate < 1,400. Estimated at 4 characters per token, which
    is deliberately pessimistic for English prose."""
    report = build_preflight_contract(PreflightRequest(task=ARCHITECTURE_TASK, mode=mode), registry)
    estimated_tokens = len(canonical_json(report)) / 4
    assert estimated_tokens < 1_400, f"{mode}: {estimated_tokens:.0f} estimated tokens"
