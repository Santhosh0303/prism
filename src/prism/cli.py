"""The command-line adapter.

It transforms files and standard input into public contracts and serialises reports. It
performs no analysis of its own (architecture section 5), so the CLI and the MCP server
cannot drift apart in behaviour: both call the same ``PrismService``.

Safety properties, all of which are tested:

* it never invokes a shell, interprets Markdown, loads a URL, or writes a file;
* it accepts standard input and regular files only — devices, FIFOs, sockets, and symlinks
  are rejected, so a path cannot make PRISM read something unbounded or privileged;
* it emits **one** final stdout write, after the report is complete and validated. A
  crash mid-run cannot leave a partially serialised report that parses (invariant A17);
* on a broken pipe it releases resources and exits with a stable code, printing no
  traceback and no content.

There is no output-path option in v1. Shell redirection stays under the user's control,
which means no PRISM-controlled path can ever overwrite a user's file.
"""

from __future__ import annotations

import argparse
import contextlib
import stat
import sys
from pathlib import Path
from typing import Any

from .canonical import canonical_json
from .contracts import (
    HealthReport,
    MeasureReport,
    MeasureRequest,
    PreflightReport,
    PreflightRequest,
    PrismMode,
    PrismStatus,
    SynthesisContract,
    parse_payload,
)
from .errors import ErrorCode, PrismError
from .service import PrismService
from .telemetry import new_request_id
from .version import PACKAGE_VERSION, version_info

# Stable exit codes (plan Task 11 Step 3).
EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_INSUFFICIENT = 3
EXIT_MODEL_UNAVAILABLE = 4
EXIT_INTERNAL = 5

_EXIT_FOR_CODE = {
    ErrorCode.INVALID_INPUT: EXIT_INVALID_INPUT,
    ErrorCode.LIMIT_EXCEEDED: EXIT_INVALID_INPUT,
    ErrorCode.VERSION_MISMATCH: EXIT_INVALID_INPUT,
    ErrorCode.MODEL_UNAVAILABLE: EXIT_MODEL_UNAVAILABLE,
    ErrorCode.MODEL_INTEGRITY_FAILURE: EXIT_MODEL_UNAVAILABLE,
    ErrorCode.MEASURE_DISABLED: EXIT_MODEL_UNAVAILABLE,
    ErrorCode.CONFIG_INTEGRITY_FAILURE: EXIT_INTERNAL,
    ErrorCode.BUSY: EXIT_INTERNAL,
    ErrorCode.TIMEOUT: EXIT_INTERNAL,
    ErrorCode.OUTPUT_BUDGET_EXCEEDED: EXIT_INTERNAL,
    ErrorCode.INTERNAL_ERROR: EXIT_INTERNAL,
}


def _read_input(source: str) -> str:
    """Read from stdin or a regular file. Nothing else is acceptable."""
    if source == "-":
        return sys.stdin.read()

    path = Path(source)
    if path.is_symlink():
        raise PrismError(
            code=ErrorCode.INVALID_INPUT,
            message="Input is a symbolic link. Pass the real file instead.",
            diagnostics={"input_kind": "symlink"},
        )
    if not path.exists():
        raise PrismError(
            code=ErrorCode.INVALID_INPUT,
            message="The input file does not exist.",
            diagnostics={"input_kind": "missing"},
        )
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode):
        # A FIFO or device could block forever or stream without bound.
        raise PrismError(
            code=ErrorCode.INVALID_INPUT,
            message="Input is not a regular file. Devices, FIFOs, and sockets are refused.",
            diagnostics={"input_kind": "irregular"},
        )
    return path.read_text(encoding="utf-8")


def _render(payload: Any, output_format: str) -> str:
    if output_format == "json":
        return canonical_json(payload)
    return _markdown(payload)


def _markdown(report: Any) -> str:
    """Compact human-readable rendering. Never interprets candidate text as markup."""
    lines: list[str] = []
    if isinstance(report, PreflightReport):
        lines.append(f"# PRISM preflight: {report.task_profile} ({report.mode.value})")
        lines.append(f"classification confidence: {report.classification_confidence.value}")
        lines.append(f"registry {report.registry_version} {report.registry_hash[:19]}...")
        lines.append("")
        for instruction in report.perspectives:
            lines.append(f"## {instruction.id}  (up to {instruction.claim_budget} claims)")
            lines.append(instruction.purpose)
            lines.extend(f"- {question}" for question in instruction.questions)
            lines.append("")
        lines.append(f"Source rule: {report.execution_contract.source_rule}")
    elif isinstance(report, MeasureReport):
        lines.append(f"# PRISM measurement: {report.status.value}")
        lines.append(f"calibration: {report.calibration_status}")
        lines.append("")
        lines.append(f"- pairs total: {report.pairs_total}")
        lines.append(f"- relevant pairs: {report.relevant_pairs}")
        lines.append(
            f"- scope divergent: {report.scope_divergent_count} "
            f"(uncertain, retained: {report.scope_uncertain_count})"
        )
        lines.append(f"- contradiction denominator: {report.contradiction_denominator}")
        lines.append(f"- NLI coverage: {report.nli_coverage}")
        lines.append(f"- contradiction count: {report.contradiction_count}")
        lines.append(f"- contradiction rate: {report.contradiction_rate}")
        lines.append(f"- agreement: {report.agreement_type.value}")
        if report.experimental_contradiction_count is not None:
            lines.append("")
            lines.append(
                f"Provisional (uncalibrated, not a finding): "
                f"{report.experimental_contradiction_count} pairs above threshold "
                f"{report.experimental_threshold}"
            )
        lines.append("")
        lines.append(
            f"source diversity: {report.source_diversity.value} "
            f"({report.sources_distinct} group(s))"
        )
        lines.append(f"ledger digest: {report.pair_ledger_digest}")
    elif isinstance(report, HealthReport):
        lines.append(f"# PRISM health: {report.status.value}")
        lines.append(f"package {report.package_version}, schema {report.schema_version}")
        lines.append(f"calibration: {report.calibration_status}")
        lines.append(f"measurement available: {report.measurement_available}")
        if report.measurement_disabled_by_kill_switch:
            lines.append("measurement DISABLED by kill switch; preflight still available")
        lines.append("")
        for component in report.components:
            mark = "ok" if component.healthy else "FAILED"
            lines.append(f"- [{mark}] {component.name}: {component.detail}")
    elif isinstance(report, SynthesisContract):
        lines.append(f"# PRISM synthesis contract: {report.status.value}")
        lines.append(f"measurement available: {report.measurement_available}")
        for title, items in (
            ("Limitations", report.limitations),
            ("Must disclose", report.required_disclosures),
            ("Unresolved conflicts", report.unresolved_conflicts),
            ("Scope differences", report.scope_differences),
            ("Prohibited shortcuts", report.prohibited_shortcuts),
            ("Answer structure", report.final_answer_structure),
        ):
            if items:
                lines.append("")
                lines.append(f"## {title}")
                lines.extend(f"- {item}" for item in items)
        if report.retained_claim_ids:
            lines.append("")
            lines.append("## Retain these claims")
            lines.extend(f"- {claim_id}" for claim_id in report.retained_claim_ids)
    else:
        lines.append(canonical_json(report))
    return "\n".join(lines)


def _emit(payload: str) -> None:
    """The single final write. Nothing is streamed before the result is complete."""
    # Windows consoles default to cp1252. A non-ASCII character in a claim would be
    # mangled, or raise mid-write and break the single-complete-write property.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def _fail(error: PrismError, output_format: str) -> int:
    report = error.to_report(request_id=new_request_id())
    if output_format == "json":
        _emit(canonical_json(report))
    else:
        print(f"error: [{report.code}] {report.message}", file=sys.stderr)
        print(f"component: {report.component}", file=sys.stderr)
        print(f"retryable: {report.retryable}", file=sys.stderr)
        print(f"next step: {report.safe_action}", file=sys.stderr)
        print(f"request id: {report.request_id}", file=sys.stderr)
    return _EXIT_FOR_CODE.get(error.code, EXIT_INTERNAL)


def build_parser() -> argparse.ArgumentParser:
    """argparse only. A CLI framework would be a seventh runtime dependency for nothing."""
    parser = argparse.ArgumentParser(
        prog="prism",
        description="Reasoning preflight and contradiction measurement. Local, offline, "
        "and provider-neutral.",
    )
    parser.add_argument("--version", action="version", version=f"prism {PACKAGE_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_format(target: argparse.ArgumentParser) -> None:
        target.add_argument("--format", choices=("json", "markdown"), default="json")

    preflight = subparsers.add_parser("preflight", help="select perspectives for a task")
    source = preflight.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", help="the task text")
    source.add_argument("--task-file", help="a regular file, or - for standard input")
    preflight.add_argument("--mode", choices=[m.value for m in PrismMode], default="standard")
    preflight.add_argument("--max-perspectives", type=int, default=None)
    add_format(preflight)

    measure = subparsers.add_parser("measure", help="measure contradictions in claim packets")
    measure.add_argument("--input", default="-", help="JSON MeasureRequest, or - for stdin")
    add_format(measure)

    synthesize = subparsers.add_parser("synthesize", help="build the synthesis contract")
    synthesize.add_argument("--preflight", default=None)
    synthesize.add_argument("--measurement", default=None)
    add_format(synthesize)

    health = subparsers.add_parser("health", help="check local health")
    health.add_argument("--deep", action="store_true", help="verify artifacts and run a probe")
    add_format(health)

    subparsers.add_parser("version", help="print version identifiers")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output_format = getattr(args, "format", "json")

    try:
        if args.command == "version":
            _emit(canonical_json(version_info()))
            return EXIT_OK

        service = PrismService.from_default_bundle()

        if args.command == "preflight":
            task = args.task if args.task is not None else _read_input(args.task_file)
            report = service.preflight(
                PreflightRequest(
                    task=task,
                    mode=PrismMode(args.mode),
                    max_perspectives=args.max_perspectives,
                )
            )
            _emit(_render(report, output_format))
            return EXIT_OK

        if args.command == "measure":
            request = parse_payload(MeasureRequest, _read_input(args.input))
            measurement_report = service.measure(request)
            _emit(_render(measurement_report, output_format))
            return (
                EXIT_INSUFFICIENT
                if measurement_report.status is PrismStatus.INSUFFICIENT
                else EXIT_OK
            )

        if args.command == "synthesize":
            preflight_report = (
                parse_payload(PreflightReport, _read_input(args.preflight))
                if args.preflight
                else None
            )
            measurement = (
                parse_payload(MeasureReport, _read_input(args.measurement))
                if args.measurement
                else None
            )
            contract = service.synthesis_contract(preflight_report, measurement)
            _emit(_render(contract, output_format))
            return EXIT_OK

        if args.command == "health":
            health_report = service.health(deep=args.deep)
            _emit(_render(health_report, output_format))
            return EXIT_OK if health_report.status is PrismStatus.OK else EXIT_MODEL_UNAVAILABLE

    except PrismError as error:
        return _fail(error, output_format)
    except BrokenPipeError:
        # The reader went away. Say nothing, leak nothing, exit predictably.
        with contextlib.suppress(Exception):
            sys.stdout.close()
        return EXIT_INTERNAL
    except KeyboardInterrupt:
        return EXIT_INTERNAL
    except Exception as exc:
        return _fail(
            PrismError(
                code=ErrorCode.INTERNAL_ERROR,
                message="An unexpected internal error occurred.",
                diagnostics={"error_type": type(exc).__name__},
            ),
            output_format,
        )

    return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
