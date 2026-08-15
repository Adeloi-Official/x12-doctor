# SPDX-License-Identifier: Apache-2.0
"""Command line interface, built on argparse so there are no dependencies."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, catalog
from .diagnose import diagnose
from .fa997 import explain_997
from .partner import DEFAULT_PARTNER, PartnerProfile, ProfileError
from .render import (
    render_ack,
    render_catalog,
    render_code,
    render_report,
    render_scan,
)
from .report import Report
from .scan import scan

__all__ = ["build_parser", "main"]

#: Exit codes, documented in the README so CI configs can rely on them.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_UNUSABLE = 2

_SEVERITIES = ("error", "warning", "info")

_EPILOG = """\
exit codes:
  0  no findings at or above the --fail-on threshold
  1  findings at or above the threshold
  2  a file could not be read, or is not X12 at all

examples:
  x12doctor check rejected_po.edi
  cat po.edi | x12doctor check - --format json
  x12doctor explain ack.997 --original po.edi
  x12doctor scan ./outbound --missing-997
  x12doctor codes B01
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="x12doctor",
        description=(
            "Diagnose rejected and malformed ANSI X12 documents, in plain "
            "English."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"x12-doctor {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # -- check -----------------------------------------------------------
    check = subparsers.add_parser(
        "check",
        help="diagnose one or more documents",
        description="Diagnose X12 documents and report what is wrong with them.",
    )
    check.add_argument(
        "paths",
        nargs="+",
        metavar="FILE",
        help="documents to diagnose; '-' reads standard input",
    )
    _add_common(check)
    check.set_defaults(handler=_cmd_check)

    # -- explain ---------------------------------------------------------
    explain = subparsers.add_parser(
        "explain",
        help="translate a 997 acknowledgment into plain English",
        description=(
            "Decode a 997 functional acknowledgment. With --original, the "
            "positions it reports are resolved against the document it "
            "acknowledges, so the output names the values that were rejected."
        ),
    )
    explain.add_argument(
        "path", metavar="FILE", help="the 997 to explain; '-' reads standard input"
    )
    explain.add_argument(
        "--original",
        metavar="FILE",
        help="the document the 997 acknowledges, for position resolution",
    )
    _add_common(explain)
    explain.set_defaults(handler=_cmd_explain)

    # -- scan ------------------------------------------------------------
    scan_parser = subparsers.add_parser(
        "scan",
        help="diagnose a directory, including cross-file problems",
        description=(
            "Scan a directory of X12 traffic. Alongside the per-file checks "
            "this finds duplicate control numbers, unacknowledged groups and "
            "flows whose documents are stamped out of order."
        ),
    )
    scan_parser.add_argument(
        "path", metavar="DIR", help="directory (or single file) to scan"
    )
    scan_parser.add_argument(
        "--missing-997",
        dest="missing_997",
        action="store_true",
        default=None,
        help=(
            "always report groups with no matching 997; by default this runs "
            "only when the scanned set contains acknowledgments"
        ),
    )
    scan_parser.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="do not descend into subdirectories",
    )
    scan_parser.add_argument(
        "--all",
        dest="only_problems",
        action="store_false",
        help="list every file, not only those with findings",
    )
    _add_common(scan_parser)
    scan_parser.set_defaults(handler=_cmd_scan)

    # -- codes -----------------------------------------------------------
    codes = subparsers.add_parser(
        "codes",
        help="show the diagnostic catalog",
        description=(
            "Show every diagnosis x12-doctor can make, or explain one in full."
        ),
    )
    codes.add_argument(
        "code",
        nargs="?",
        metavar="CODE",
        help="a diagnostic code such as B01; omit for the whole catalog",
    )
    codes.add_argument(
        "--format", choices=("human", "json"), default="human", help="output format"
    )
    codes.add_argument(
        "--no-color",
        dest="colour",
        action="store_false",
        default=None,
        help="never emit ANSI colour",
    )
    codes.set_defaults(handler=_cmd_codes)

    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="output format (default: human)",
    )
    parser.add_argument(
        "--partner",
        metavar="FILE",
        help="partner profile (.toml or .json) to check the document against",
    )
    parser.add_argument(
        "--fail-on",
        choices=_SEVERITIES,
        default="error",
        help="lowest severity that makes the command exit non-zero (default: error)",
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help="show only what is wrong, without the why and the fix",
    )
    parser.add_argument(
        "--no-color",
        dest="colour",
        action="store_false",
        default=None,
        help="never emit ANSI colour",
    )


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def _read(path: str) -> tuple[bytes, str]:
    """Read a path or standard input, returning the bytes and a display name."""
    if path == "-":
        return sys.stdin.buffer.read(), "(stdin)"
    return Path(path).read_bytes(), path


def _load_partner(path: str | None) -> PartnerProfile:
    if not path:
        return DEFAULT_PARTNER
    return PartnerProfile.load(path)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_check(args: argparse.Namespace, out: object) -> int:
    partner = _load_partner(args.partner)
    reports: list[Report] = []
    exit_code = EXIT_OK

    for path in args.paths:
        try:
            raw, name = _read(path)
        except OSError as exc:
            print(f"x12doctor: cannot read {path}: {exc.strerror}", file=sys.stderr)
            exit_code = EXIT_UNUSABLE
            continue

        report = diagnose(raw, partner=partner, source=name)
        reports.append(report)

    if args.format == "json":
        payload = (
            reports[0].to_dict()
            if len(reports) == 1
            else {
                "schema_version": reports[0].to_dict()["schema_version"]
                if reports
                else "1.0",
                "reports": [r.to_dict() for r in reports],
            }
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        rendered = [
            render_report(report, colour=args.colour, brief=args.brief)
            for report in reports
        ]
        print("\n\n".join(rendered))

    for report in reports:
        if report.has("A00"):
            exit_code = EXIT_UNUSABLE
        elif report.exceeds(args.fail_on) and exit_code != EXIT_UNUSABLE:
            exit_code = EXIT_FINDINGS

    return exit_code


def _cmd_explain(args: argparse.Namespace, out: object) -> int:
    partner = _load_partner(args.partner)

    try:
        raw, name = _read(args.path)
    except OSError as exc:
        print(
            f"x12doctor: cannot read {args.path}: {exc.strerror}", file=sys.stderr
        )
        return EXIT_UNUSABLE

    original: bytes | None = None
    if args.original:
        try:
            original, _ = _read(args.original)
        except OSError as exc:
            print(
                f"x12doctor: cannot read {args.original}: {exc.strerror}",
                file=sys.stderr,
            )
            return EXIT_UNUSABLE

    ack = explain_997(raw, original, partner=partner, source=name)

    if args.format == "json":
        print(json.dumps(ack.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_ack(ack, colour=args.colour, brief=args.brief), end="")

    if any(f.code == "A00" for f in ack.findings):
        return EXIT_UNUSABLE
    if any(
        f.severity == "error" or (args.fail_on != "error" and f.severity == "warning")
        for f in ack.findings
    ):
        return EXIT_FINDINGS
    return EXIT_OK


def _cmd_scan(args: argparse.Namespace, out: object) -> int:
    partner = _load_partner(args.partner)
    root = Path(args.path)

    if not root.exists():
        print(f"x12doctor: {args.path}: no such file or directory", file=sys.stderr)
        return EXIT_UNUSABLE

    result = scan(
        root,
        partner=partner,
        recursive=args.recursive,
        check_acknowledgments=args.missing_997,
    )

    if args.format == "json":
        print(result.to_json())
    else:
        print(
            render_scan(
                result,
                colour=args.colour,
                brief=args.brief,
                only_problems=args.only_problems,
            )
        )

    if not result.entries and not result.unreadable:
        print(
            f"x12doctor: no X12 documents found under {args.path}", file=sys.stderr
        )
        return EXIT_UNUSABLE
    if result.unreadable:
        return EXIT_UNUSABLE
    return EXIT_FINDINGS if result.exceeds(args.fail_on) else EXIT_OK


def _cmd_codes(args: argparse.Namespace, out: object) -> int:
    if args.code:
        try:
            entry = catalog.get(args.code)
        except KeyError:
            print(
                f"x12doctor: unknown diagnostic code {args.code!r}. "
                "Run 'x12doctor codes' for the catalog.",
                file=sys.stderr,
            )
            return EXIT_UNUSABLE

        if args.format == "json":
            print(json.dumps(entry.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(render_code(entry, colour=args.colour), end="")
        return EXIT_OK

    if args.format == "json":
        print(
            json.dumps(
                {
                    "categories": catalog.CATEGORIES,
                    "diagnostics": [
                        entry.to_dict() for entry in catalog.CATALOG.values()
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(render_catalog(colour=args.colour))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    try:
        result: int = args.handler(args, sys.stdout)
        return result
    except ProfileError as exc:
        print(f"x12doctor: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE
    except BrokenPipeError:
        # Downstream closed the pipe, as `| head` does. Not an error.
        return EXIT_OK
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
