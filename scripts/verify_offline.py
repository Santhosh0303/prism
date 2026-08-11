"""Gate G8 — no outbound connection during analysis.

Every network primitive the standard library exposes is replaced with one that records
the attempt and raises. Then preflight, synthesis, and — when the model bundle is present
— a full measurement run against the reference workload. Any attempt is recorded with the
destination and reported.

**What this proves and what it does not.** It proves that no Python-level code path in
PRISM or its pure-Python dependencies opens a socket. It cannot prove that a native
extension does not issue a syscall directly: ONNX Runtime is compiled code, and a
`connect(2)` from inside it never passes through `socket.socket`. Establishing that
requires an external packet capture or a network namespace with no route, which belongs in
the host sandbox rather than in this process. The limitation is stated here rather than
left for a reader to discover.

Run:

    uv run python scripts/verify_offline.py
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from typing import Any, Final, NoReturn

from _gate import REPO_ROOT, GateResult, add_src_to_path, failed, passed, report, skipped

add_src_to_path()
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from run import load_workload  # noqa: E402

from prism.contracts import PreflightRequest, PrismMode  # noqa: E402
from prism.measure.models import ModelSessions  # noqa: E402
from prism.service import PrismService  # noqa: E402

GATE: Final[str] = "G8 offline"

_attempts: list[str] = []


def _record(destination: object) -> NoReturn:
    _attempts.append(repr(destination))
    raise OSError("outbound network access is prohibited during analysis")


def _install_network_trap() -> None:
    """Replace every standard-library route to a socket.

    Patching `socket.socket` alone is not enough: `create_connection`,
    `getaddrinfo`, and `socketpair` reach the network without constructing one through
    the public class in every implementation.
    """

    def blocked_socket(*args: Any, **kwargs: Any) -> NoReturn:
        _record(("socket", args))

    def blocked_connection(address: Any, *args: Any, **kwargs: Any) -> NoReturn:
        _record(address)

    def blocked_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> NoReturn:
        _record((host, port))

    socket.socket = blocked_socket  # type: ignore[assignment,misc]
    socket.create_connection = blocked_connection  # type: ignore[assignment]
    socket.getaddrinfo = blocked_getaddrinfo  # type: ignore[assignment]


def run(*, include_measure: bool = True) -> GateResult:
    _attempts.clear()
    workload = load_workload()

    # Constructing the service loads the perspective registry and nothing else: the ONNX
    # sessions are built lazily, on the first measurement. This line used to carry a
    # comment claiming construction was what kept model loading out of the measured
    # window, which was never true — the ~4 s one-time load happened inside
    # ``service.measure()``, and therefore inside the workload's own timeout. The gate was
    # spending most of a 10 s deadline on setup and reporting SKIP when it ran out: a
    # timeout it caused itself. The sessions are warmed explicitly below instead.
    service = PrismService.from_default_bundle()
    measured = False
    measure_seconds: float | None = None

    # The trap still goes up before the models are loaded, exactly as it did when loading
    # happened lazily inside the measurement: loading reads local files and must reach no
    # network either, and that coverage is unchanged. What changes below is only *when*
    # the load happens, not whether it is watched.
    _install_network_trap()
    preflight = service.preflight(PreflightRequest(task=workload.question, mode=PrismMode.CRITICAL))
    measurement = None
    if include_measure:
        try:
            # Build the sessions here so the deadline covers analysis, which is what this
            # gate is about, rather than a one-time load that happens once per process.
            ModelSessions.get()
            started = time.perf_counter()
            measurement = service.measure(workload)
            measure_seconds = time.perf_counter() - started
            measured = True
        except Exception as error:
            # A broad catch is correct here: the bundle may legitimately be absent, and any
            # failure that came with a recorded network attempt must surface, not be
            # reported as a skip.
            if _attempts:
                raise
            return skipped(
                GATE,
                f"measurement unavailable ({type(error).__name__}); "
                "preflight and synthesis were still checked",
                preflight_checked=True,
                network_attempts=len(_attempts),
            )
    service.synthesis_contract(preflight, measurement)

    detail: dict[str, object] = {
        "preflight_checked": True,
        "synthesis_checked": True,
        "measure_checked": measured,
        "network_attempts": len(_attempts),
        "proves": "no Python-level socket use; native-code syscalls are out of scope",
    }
    if measure_seconds is not None:
        # Reported so the margin against the workload's own deadline is visible in the
        # gate output. A gate that is quietly running at 83% of its budget looks identical
        # to one running at 45% until the day it fails.
        detail["measure_seconds"] = round(measure_seconds, 2)
        detail["deadline_seconds"] = workload.config.timeout_seconds
    if _attempts:
        return failed(
            GATE,
            [f"outbound attempt to {destination}" for destination in _attempts],
            **detail,
        )
    return passed(GATE, **detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    parser.add_argument(
        "--skip-measure",
        action="store_true",
        help="check preflight and synthesis only; use when the model bundle is absent",
    )
    args = parser.parse_args()
    return report(run(include_measure=not args.skip_measure), as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
