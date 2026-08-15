# SPDX-License-Identifier: Apache-2.0
"""Human-readable rendering for the terminal.

The output is meant to be read by someone who has just been told an order did
not arrive. It leads with what is wrong and where, then why documents end up
that way, then what to change. Colour is decoration only — every line reads the
same with it stripped, which is what makes the output safe to paste into a
ticket.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from collections.abc import Iterable
from typing import IO

from .catalog import CATALOG, CATEGORIES, Diagnostic
from .fa997 import Acknowledgment
from .report import Finding, Report
from .scan import ScanResult

__all__ = [
    "render_ack",
    "render_catalog",
    "render_code",
    "render_report",
    "render_scan",
    "use_colour",
]

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_BLUE = "\x1b[34m"
_GREEN = "\x1b[32m"

_SEVERITY_LABEL = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}
_SEVERITY_COLOUR = {"error": _RED, "warning": _YELLOW, "info": _BLUE}

#: Indent for the detail lines under a finding header.
_DETAIL_INDENT = " " * 9


def use_colour(stream: IO[str] | None = None) -> bool:
    """Whether to emit ANSI codes.

    Honours the NO_COLOR convention and FORCE_COLOR, and otherwise emits colour
    only to a terminal so that redirected output stays clean.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    target = stream if stream is not None else sys.stdout
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):
        return False


class _Painter:
    """Applies colour, or does not, depending on one decision made up front."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return "".join(codes) + text + _RESET


def _width() -> int:
    try:
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    except (OSError, ValueError):
        columns = 80
    return max(60, min(columns, 100))


def _wrap(text: str, indent: str, width: int) -> list[str]:
    return textwrap.wrap(
        text,
        width=width,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [indent.rstrip()]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def render_report(
    report: Report,
    *,
    colour: bool | None = None,
    brief: bool = False,
    show_ok: bool = True,
) -> str:
    """Render one document's diagnosis."""
    paint = _Painter(use_colour() if colour is None else colour)
    width = _width()
    lines: list[str] = []

    lines.append(_header(report, paint))
    lines.append("")

    if not report.findings:
        if show_ok:
            lines.append(
                "  " + paint("No problems found.", _GREEN)
            )
            lines.append("")
        lines.append(_summary(report, paint))
        return "\n".join(lines)

    show_lines = _show_lines(report.findings)
    for finding in report.findings:
        lines.extend(
            _render_finding(
                finding, paint, width, brief=brief, show_lines=show_lines
            )
        )
        lines.append("")

    lines.append(_summary(report, paint))
    return "\n".join(lines)


def _header(report: Report, paint: _Painter) -> str:
    from .diagnose import describe_doc_type

    name = report.source or "(stdin)"
    parts = [paint(name, _BOLD)]

    if report.doc_types and len(set(report.doc_types)) > 1:
        described = ", ".join(
            describe_doc_type(t) for t in dict.fromkeys(report.doc_types)
        )
        parts.append(f"— {described}")
    else:
        parts.append(f"— {describe_doc_type(report.doc_type)}")

    if report.version:
        parts.append(paint(f"({report.version})", _DIM))

    return " ".join(parts)


def _show_lines(findings: Iterable[Finding]) -> bool:
    """Whether line numbers are worth printing.

    X12 is commonly written as one unbroken line, and in that file every
    finding is on line 1. Printing it then adds a column that never varies and
    never helps.
    """
    return any(f.line is not None and f.line > 1 for f in findings)


def _render_finding(
    finding: Finding,
    paint: _Painter,
    width: int,
    *,
    brief: bool,
    show_lines: bool = True,
) -> list[str]:
    label = _SEVERITY_LABEL.get(finding.severity, finding.severity.upper())
    colour = _SEVERITY_COLOUR.get(finding.severity, "")

    location = finding.location
    if show_lines and finding.line is not None and finding.segment_index is not None:
        location += f", line {finding.line}"

    lines = [
        f"  {paint(label, colour, _BOLD)}  {paint(finding.code, _BOLD)}  "
        f"{paint(location, _DIM)}"
    ]
    lines.extend(_wrap(finding.message, _DETAIL_INDENT, width))

    if not brief:
        if finding.why:
            lines.extend(
                _wrap(f"Why this happens: {finding.why}", _DETAIL_INDENT, width)
            )
        if finding.hint:
            lines.extend(_wrap(f"Fix: {finding.hint}", _DETAIL_INDENT, width))

    return lines


def _summary(report: Report, paint: _Painter) -> str:
    counts = report.counts()
    total = sum(counts.values())
    if total == 0:
        return paint("0 findings", _GREEN)

    parts = [
        f"{count} {name}{'' if count == 1 else 's'}"
        for name, count in counts.items()
        if count
    ]
    colour = (
        _RED if counts["error"] else _YELLOW if counts["warning"] else _BLUE
    )
    return paint(
        f"{total} finding{'' if total == 1 else 's'} ({', '.join(parts)})", colour
    )


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------


def render_scan(
    result: ScanResult,
    *,
    colour: bool | None = None,
    brief: bool = False,
    only_problems: bool = True,
) -> str:
    """Render a directory scan: per-file findings, then the cross-file ones."""
    paint = _Painter(use_colour() if colour is None else colour)
    width = _width()
    lines: list[str] = []

    root = str(result.root) if result.root else "the given files"
    lines.append(
        paint(
            f"Scanned {len(result.entries)} file"
            f"{'' if len(result.entries) == 1 else 's'} in {root}",
            _BOLD,
        )
    )
    lines.append("")

    for entry in result.entries:
        if only_problems and not entry.report.findings:
            continue
        lines.append(_header(entry.report, paint))
        lines.append("")
        show_lines = _show_lines(entry.report.findings)
        for finding in entry.report.findings:
            lines.extend(
                _render_finding(
                    finding, paint, width, brief=brief, show_lines=show_lines
                )
            )
            lines.append("")

    if result.findings:
        lines.append(paint("Across files", _BOLD))
        lines.append("")
        cross_lines = _show_lines(result.findings)
        for finding in result.findings:
            lines.extend(
                _render_finding(
                    finding, paint, width, brief=brief, show_lines=cross_lines
                )
            )
            if finding.source:
                lines.append(f"{_DETAIL_INDENT}{paint(finding.source, _DIM)}")
            lines.append("")

    for path, reason in result.unreadable:
        lines.append(f"  {paint('SKIP ', _DIM)}  {path}: {reason}")
    if result.unreadable:
        lines.append("")

    counts = result.counts()
    total = sum(counts.values())
    if total == 0:
        lines.append(paint("No problems found.", _GREEN))
    else:
        parts = [
            f"{count} {name}{'' if count == 1 else 's'}"
            for name, count in counts.items()
            if count
        ]
        colour_code = (
            _RED if counts["error"] else _YELLOW if counts["warning"] else _BLUE
        )
        lines.append(
            paint(
                f"{total} finding{'' if total == 1 else 's'} "
                f"({', '.join(parts)}) across {len(result.entries)} file"
                f"{'' if len(result.entries) == 1 else 's'}",
                colour_code,
            )
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Acknowledgments
# ---------------------------------------------------------------------------

_STATUS_TEXT = {
    "accepted": ("Accepted", _GREEN),
    "accepted_with_errors": ("Accepted, but errors were noted", _YELLOW),
    "partially_accepted": ("Partially accepted", _YELLOW),
    "rejected": ("Rejected", _RED),
    "unknown": ("Status unknown", _DIM),
}


def render_ack(
    ack: Acknowledgment,
    *,
    colour: bool | None = None,
    brief: bool = False,
) -> str:
    """Render a decoded 997 as prose rather than as codes."""
    paint = _Painter(use_colour() if colour is None else colour)
    width = _width()
    lines: list[str] = []

    name = ack.source or "(stdin)"
    lines.append(f"{paint(name, _BOLD)} — 997 Functional Acknowledgment")
    lines.append("")

    text, status_colour = _STATUS_TEXT.get(ack.status, _STATUS_TEXT["unknown"])
    lines.append(f"  {paint(text, status_colour, _BOLD)}")
    lines.append("")

    for group in ack.groups:
        header = f"  Group {group.control or '?'}"
        if group.functional_id:
            header += f" ({group.functional_id})"
        if group.meaning:
            header += f" — {group.meaning}"
        lines.append(header)

        if group.received or group.accepted:
            lines.append(
                f"{_DETAIL_INDENT}{group.accepted or '?'} of "
                f"{group.received or '?'} transaction sets accepted."
            )

        for code, meaning in group.syntax_errors:
            lines.extend(
                _wrap(f"Group error {code}: {meaning}.", _DETAIL_INDENT, width)
            )

        for transaction in group.transactions:
            lines.append("")
            label = (
                f"{transaction.doc_type or 'transaction set'} "
                f"{transaction.control or '?'}"
            )
            verdict = transaction.meaning or "no status given"
            lines.append(f"    {paint(label, _BOLD)} — {verdict}")

            for code, meaning in transaction.syntax_errors:
                # Code 5 only says "one or more segments are in error", which
                # is exactly what the AK3/AK4 detail printed below spells out.
                if code == "5" and transaction.segment_errors:
                    continue
                lines.extend(
                    _wrap(f"Error {code}: {meaning}.", _DETAIL_INDENT, width)
                )

            for segment_error in transaction.segment_errors:
                lines.extend(
                    _wrap(segment_error.describe() + ".", _DETAIL_INDENT, width)
                )
                if segment_error.original is not None:
                    lines.extend(
                        _wrap(
                            f"The segment reads: {segment_error.original.raw}",
                            _DETAIL_INDENT + "  ",
                            width,
                        )
                    )
                for element_error in segment_error.elements:
                    lines.extend(
                        _wrap(
                            element_error.describe() + ".",
                            _DETAIL_INDENT + "  ",
                            width,
                        )
                    )

        lines.append("")

    if ack.paired:
        lines.append(
            paint(
                "  Positions above were resolved against the original document.",
                _DIM,
            )
        )
        lines.append("")

    if ack.findings and not brief:
        lines.append(paint("What to do", _BOLD))
        lines.append("")
        ack_lines = _show_lines(ack.findings)
        for finding in ack.findings:
            lines.extend(
                _render_finding(
                    finding, paint, width, brief=brief, show_lines=ack_lines
                )
            )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------


def render_catalog(*, colour: bool | None = None) -> str:
    """The whole diagnostic catalog as a table, grouped by category."""
    paint = _Painter(use_colour() if colour is None else colour)
    lines: list[str] = []

    for prefix, category in CATEGORIES.items():
        entries = [d for d in CATALOG.values() if d.category == prefix]
        if not entries:
            continue
        lines.append(paint(f"{prefix} — {category}", _BOLD))
        for entry in entries:
            severity = paint(
                _SEVERITY_LABEL.get(entry.severity, entry.severity).strip(),
                _SEVERITY_COLOUR.get(entry.severity, ""),
            )
            lines.append(f"  {paint(entry.code, _BOLD)}  {severity:<7}  {entry.title}")
        lines.append("")

    lines.append(paint("x12doctor codes <CODE> explains one in full.", _DIM))
    return "\n".join(lines)


def render_code(entry: Diagnostic, *, colour: bool | None = None) -> str:
    """One diagnostic in full, in the shape of a manual page."""
    paint = _Painter(use_colour() if colour is None else colour)
    width = _width()
    indent = "  "
    lines: list[str] = []

    severity = paint(
        entry.severity.upper(), _SEVERITY_COLOUR.get(entry.severity, ""), _BOLD
    )
    lines.append(f"{paint(entry.code, _BOLD)}  {entry.title}  [{severity}]")
    lines.append(
        paint(
            f"{indent}{CATEGORIES.get(entry.category, entry.category)}"
            f" · applies to {entry.scope}",
            _DIM,
        )
    )
    lines.append("")

    for title, body in (
        ("What it checks", entry.what),
        ("Why it happens in production", entry.why),
        ("How to fix it", entry.hint),
    ):
        lines.append(f"{indent}{paint(title, _BOLD)}")
        lines.extend(_wrap(body, indent + "  ", width))
        lines.append("")

    if entry.fixture:
        lines.append(f"{indent}{paint('Matching fixture', _BOLD)}")
        lines.extend(
            _wrap(
                f"faults.{entry.fixture}() in @adeloi/edi-fixtures produces a "
                "document that triggers this diagnosis.",
                indent + "  ",
                width,
            )
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
