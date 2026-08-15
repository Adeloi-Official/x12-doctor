# SPDX-License-Identifier: Apache-2.0
"""Findings, reports and their stable JSON serialisation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "SCHEMA_VERSION",
    "SEVERITY_ORDER",
    "Finding",
    "Report",
    "Severity",
    "severity_at_least",
]

Severity = Literal["error", "warning", "info"]

#: Version of the JSON output structure. Bumped only on breaking changes so
#: that CI pipelines can pin against it.
SCHEMA_VERSION = "1.0"

#: Most severe first. Used for sorting and for ``--fail-on`` thresholds.
SEVERITY_ORDER: dict[str, int] = {"error": 0, "warning": 1, "info": 2}


def severity_at_least(severity: str, threshold: str) -> bool:
    """True when ``severity`` is as severe as ``threshold`` or worse."""
    return SEVERITY_ORDER.get(severity, 99) <= SEVERITY_ORDER.get(threshold, 99)


@dataclass(frozen=True)
class Finding:
    """A single diagnosis with its location and plain-English explanation."""

    #: Catalog code, e.g. ``"A02"``.
    code: str
    severity: Severity
    #: What is wrong, with the concrete values of this document filled in.
    message: str
    #: Why documents end up like this in production.
    why: str
    #: What to do about it.
    hint: str
    #: Zero-based segment position in the document, or ``None`` for
    #: file-level findings.
    segment_index: int | None = None
    #: Segment identifier at that position, e.g. ``"SE"``.
    segment_id: str | None = None
    #: One-based element position within the segment, when applicable.
    element: int | None = None
    #: One-based component position within the element, when applicable.
    component: int | None = None
    #: One-based line number in the source file, when known.
    line: int | None = None
    #: Path of the file the finding belongs to. Set by directory scans.
    source: str | None = None
    #: Machine-readable extras, e.g. ``{"declared": 13, "actual": 14}``.
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def location(self) -> str:
        """Human-readable location, e.g. ``"segment 14 (SE), element 01"``."""
        if self.segment_index is None:
            return "file"
        where = f"segment {self.segment_index + 1}"
        if self.segment_id:
            where += f" ({self.segment_id})"
        if self.element is not None:
            where += f", element {self.element:02d}"
            if self.component is not None:
                where += f", component {self.component:02d}"
        return where

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "why": self.why,
            "hint": self.hint,
            "segment_index": self.segment_index,
            "segment_id": self.segment_id,
            "element": self.element,
            "component": self.component,
            "line": self.line,
        }
        if self.source is not None:
            payload["source"] = self.source
        if self.context:
            payload["context"] = self.context
        return payload

    def _sort_key(self) -> tuple[Any, ...]:
        return (
            self.source or "",
            self.segment_index if self.segment_index is not None else -1,
            SEVERITY_ORDER.get(self.severity, 99),
            self.code,
            self.element if self.element is not None else -1,
            self.message,
        )


@dataclass
class Report:
    """Everything x12-doctor concluded about one document."""

    findings: list[Finding] = field(default_factory=list)
    #: Primary transaction set, e.g. ``"850"``. ``"unknown"`` when undetectable.
    doc_type: str = "unknown"
    #: All transaction sets present, in document order.
    doc_types: list[str] = field(default_factory=list)
    #: X12 version from GS08/ISA12, e.g. ``"004010"``.
    version: str | None = None
    #: Path or label of the diagnosed document.
    source: str | None = None
    #: Delimiters as detected, for display only.
    delimiters: str | None = None
    #: Number of segments the tokenizer recovered.
    segment_count: int = 0

    def __iter__(self) -> Iterator[Finding]:
        return iter(self.findings)

    def __len__(self) -> int:
        return len(self.findings)

    @property
    def ok(self) -> bool:
        """True when nothing of severity ``error`` was found."""
        return not self.errors

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "info"]

    def counts(self) -> dict[str, int]:
        return {
            "error": len(self.errors),
            "warning": len(self.warnings),
            "info": len(self.infos),
        }

    def has(self, code: str) -> bool:
        """True when at least one finding carries ``code``."""
        return any(f.code == code for f in self.findings)

    def codes(self) -> list[str]:
        """Distinct codes in report order."""
        seen: dict[str, None] = {}
        for finding in self.findings:
            seen.setdefault(finding.code, None)
        return list(seen)

    def worst_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return min(
            (f.severity for f in self.findings),
            key=lambda s: SEVERITY_ORDER.get(s, 99),
        )

    def exceeds(self, threshold: str) -> bool:
        """True when any finding reaches the ``--fail-on`` threshold."""
        return any(severity_at_least(f.severity, threshold) for f in self.findings)

    def sort(self) -> None:
        """Order findings by position, then severity — stable and repeatable."""
        self.findings.sort(key=Finding._sort_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source": self.source,
            "doc_type": self.doc_type,
            "doc_types": self.doc_types,
            "version": self.version,
            "delimiters": self.delimiters,
            "segment_count": self.segment_count,
            "ok": self.ok,
            "counts": self.counts(),
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Deterministic JSON — the same document always yields the same bytes."""
        return json.dumps(
            self.to_dict(), indent=indent, ensure_ascii=False, sort_keys=False
        )
