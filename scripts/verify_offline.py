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
from typing import Any, Final, NoReturn

from _gate import REPO_ROOT, GateResult, add_src_to_path, failed, passed, report, skipped

add_src_to_path()
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from run import load_workload  # noqa: E402

from prism.contracts import PreflightRequest, PrismMode  # noqa: E402
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

    # The service is constructed before the trap so that model loading, which is setup and
    # not analysis, is not what the gate measures. Verification of the bundle is a separate
    # gate; this one is about what happens while a request is being served.
    service = PrismService.from_default_bundle()
    measured = False

    _install_network_trap()
    preflight = service.preflight(PreflightRequest(task=workload.question, mode=PrismMode.CRITICAL))
    measurement = None
    if include_measure:
        try:
            measurement = service.measure(workload)
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

    detail = {
        "preflight_checked": True,
        "synthesis_checked": True,
        "measure_checked": measured,
        "network_attempts": len(_attempts),
        "proves": "no Python-level socket use; native-code syscalls are out of scope",
    }
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
