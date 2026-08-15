# SPDX-License-Identifier: Apache-2.0
"""Directory scanning and the checks that need more than one file.

Three of the defects x12-doctor knows about are invisible in a single
document. A duplicate control number needs a second document to be duplicate
*of*. A missing acknowledgment is defined by absence. An out-of-order flow is a
property of the sequence, not of any one file in it. Those live here.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import catalog
from .diagnose import diagnose_document
from .envelope import (
    Envelope,
    FunctionalGroup,
    Interchange,
    TransactionSet,
    parse_envelope,
)
from .fa997 import decode_ack
from .partner import DEFAULT_PARTNER, PartnerProfile
from .report import SCHEMA_VERSION, Finding, Report, severity_at_least
from .semantics.common import format_decimal as _plain
from .semantics.common import parse_decimal as _decimal
from .tokenizer import Segment, TokenizedDocument, tokenize

__all__ = ["ScanEntry", "ScanResult", "find_documents", "scan", "scan_paths"]

#: Extensions that normally hold X12. Files with any other extension are still
#: scanned when they sniff as X12, so an extensionless drop file is not missed.
_EDI_SUFFIXES = frozenset(
    {".edi", ".x12", ".txt", ".dat", ".edifact", ".isa", ".997", ".ack", ""}
)

#: Directories that never hold trading-partner traffic.
_SKIP_DIRS = frozenset(
    {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
     ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build"}
)

#: How far a document is through the order-to-invoice flow. Used by the
#: chronology check; documents not in the flow are not ordered against it.
_FLOW_ORDER: dict[str, int] = {"850": 0, "855": 1, "856": 2, "810": 3}

#: The GS01 functional identifier of an acknowledgment.
_ACK_FUNCTIONAL_ID = "FA"


@dataclass
class ScanEntry:
    """One scanned file with everything derived from it."""

    path: Path
    report: Report
    envelope: Envelope
    doc: TokenizedDocument

    @property
    def name(self) -> str:
        return str(self.path)


@dataclass
class ScanResult:
    """The outcome of scanning a set of files."""

    entries: list[ScanEntry] = field(default_factory=list)
    #: Findings that belong to the set rather than to any single file.
    findings: list[Finding] = field(default_factory=list)
    #: Files that could not be read, as ``(path, reason)``.
    unreadable: list[tuple[Path, str]] = field(default_factory=list)
    root: Path | None = None

    def __iter__(self) -> Iterator[ScanEntry]:
        return iter(self.entries)

    @property
    def all_findings(self) -> list[Finding]:
        """Per-file findings and cross-file findings together."""
        collected: list[Finding] = []
        for entry in self.entries:
            collected.extend(entry.report.findings)
        collected.extend(self.findings)
        return collected

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.all_findings)

    def counts(self) -> dict[str, int]:
        counts = {"error": 0, "warning": 0, "info": 0}
        for finding in self.all_findings:
            if finding.severity in counts:
                counts[finding.severity] += 1
        return counts

    def exceeds(self, threshold: str) -> bool:
        return any(
            severity_at_least(f.severity, threshold) for f in self.all_findings
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "root": str(self.root) if self.root else None,
            "file_count": len(self.entries),
            "ok": self.ok,
            "counts": self.counts(),
            "files": [entry.report.to_dict() for entry in self.entries],
            "cross_file_findings": [f.to_dict() for f in self.findings],
            "unreadable": [
                {"path": str(path), "reason": reason}
                for path, reason in self.unreadable
            ],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_documents(root: Path, *, recursive: bool = True) -> list[Path]:
    """List the files under ``root`` that look like X12, sorted for stability."""
    if root.is_file():
        return [root]

    found: list[Path] = []
    walker = root.rglob("*") if recursive else root.glob("*")
    for path in walker:
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in _EDI_SUFFIXES or _sniffs_as_x12(path):
            found.append(path)

    # Sorted so that a scan of the same directory always produces the same
    # report, which matters for both diffing and CI.
    return sorted(found)


def _sniffs_as_x12(path: Path) -> bool:
    """Cheap content test: does the head of the file contain an ISA segment?"""
    try:
        with path.open("rb") as handle:
            head = handle.read(512)
    except OSError:
        return False
    return b"ISA" in head


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan(
    root: str | Path,
    *,
    partner: PartnerProfile = DEFAULT_PARTNER,
    recursive: bool = True,
    check_acknowledgments: bool | None = None,
) -> ScanResult:
    """Scan a directory (or a single file) and run the cross-file checks."""
    root_path = Path(root)
    paths = find_documents(root_path, recursive=recursive)
    result = scan_paths(
        paths, partner=partner, check_acknowledgments=check_acknowledgments
    )
    result.root = root_path
    return result


def scan_paths(
    paths: Iterable[Path],
    *,
    partner: PartnerProfile = DEFAULT_PARTNER,
    check_acknowledgments: bool | None = None,
) -> ScanResult:
    """Scan an explicit list of files."""
    result = ScanResult()

    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            result.unreadable.append((path, str(exc)))
            continue

        doc = tokenize(raw)
        envelope, structure_findings = parse_envelope(doc)
        report = diagnose_document(
            doc,
            envelope,
            structure_findings,
            partner=partner,
            source=str(path),
        )
        # Stamp the source onto every finding so that a flattened list stays
        # attributable to its file.
        report.findings = [
            _with_source(finding, str(path)) for finding in report.findings
        ]
        result.entries.append(
            ScanEntry(path=path, report=report, envelope=envelope, doc=doc)
        )

    result.findings.extend(_check_duplicate_controls(result))
    result.findings.extend(
        _check_acknowledgments(result, enabled=check_acknowledgments)
    )
    result.findings.extend(_check_chronology(result))
    result.findings.extend(_check_shipped_quantities(result))
    result.findings.sort(key=lambda f: (f.source or "", f.code, f.message))
    return result


def _with_source(finding: Finding, source: str) -> Finding:
    return replace(finding, source=source)


# ---------------------------------------------------------------------------
# A04 — duplicate interchange control numbers
# ---------------------------------------------------------------------------


def _check_duplicate_controls(result: ScanResult) -> list[Finding]:
    """A04 — the same ISA13 from the same sender to the same receiver twice."""
    seen: dict[tuple[str, str, str], list[tuple[ScanEntry, Interchange]]] = {}

    for entry in result.entries:
        for interchange in entry.envelope.interchanges:
            if interchange.isa.id != "ISA" or not interchange.control:
                continue
            key = (
                interchange.sender,
                interchange.receiver,
                interchange.control.lstrip("0") or "0",
            )
            seen.setdefault(key, []).append((entry, interchange))

    findings: list[Finding] = []
    for (sender, receiver, _control), occurrences in sorted(seen.items()):
        if len(occurrences) < 2:
            continue

        first_entry, _first = occurrences[0]
        for entry, interchange in occurrences[1:]:
            others = ", ".join(
                str(other.path) for other, _ in occurrences if other is not entry
            )
            findings.append(
                catalog.make_finding(
                    "A04",
                    f"Interchange control number {interchange.control!r} from "
                    f"{sender or '?'} to {receiver or '?'} is also used in "
                    f"{others}.",
                    segment_index=interchange.isa.index,
                    segment_id="ISA",
                    element=13,
                    line=interchange.isa.line,
                    context={
                        "isa13": interchange.control,
                        "sender": sender,
                        "receiver": receiver,
                        "also_in": [
                            str(other.path)
                            for other, _ in occurrences
                            if other is not entry
                        ],
                        "first_seen": str(first_entry.path),
                    },
                )
            )

    return findings


# ---------------------------------------------------------------------------
# C02 / C05 — acknowledgment coverage
# ---------------------------------------------------------------------------


def _check_acknowledgments(
    result: ScanResult, *, enabled: bool | None
) -> list[Finding]:
    """C05 — groups that asked for a 997 and never got one; C02 in reverse.

    ISA14 is the field that literally means "acknowledge this", so by default
    only groups whose interchange sets it to 1 are held to it. ``enabled=True``
    (the ``--missing-997`` flag) widens the check to every non-acknowledgment
    group, which is what you want when the partner agreement requires a 997
    regardless of what ISA14 says — the common case, since most senders leave
    ISA14 at 0 out of habit.
    """
    acknowledged: set[tuple[str, str]] = set()
    ack_targets: list[tuple[ScanEntry, str, str]] = []

    for entry in result.entries:
        for transaction in entry.envelope.transactions:
            if transaction.doc_type != "997":
                continue
            # An AckGroup is what a 997 says about a group; a FunctionalGroup
            # is a group that was actually sent. Keeping the two names apart
            # matters here, because the whole check is about pairing them.
            for ack_group in decode_ack(transaction, entry.doc):
                if ack_group.control:
                    acknowledged.add(
                        (
                            ack_group.functional_id,
                            ack_group.control.lstrip("0") or "0",
                        )
                    )
                    ack_targets.append(
                        (entry, ack_group.functional_id, ack_group.control)
                    )

    sent: list[tuple[ScanEntry, Interchange, FunctionalGroup]] = []
    for entry in result.entries:
        for interchange in entry.envelope.interchanges:
            for group in interchange.groups:
                if group.functional_id == _ACK_FUNCTIONAL_ID:
                    continue
                sent.append((entry, interchange, group))

    findings: list[Finding] = []

    if enabled is not False:
        for entry, interchange, group in sent:
            # Without --missing-997, an interchange that did not ask to be
            # acknowledged is not reported for going unacknowledged.
            if not enabled and not interchange.ack_requested:
                continue
            key = (group.functional_id, group.control.lstrip("0") or "0")
            if key in acknowledged:
                continue
            doc_types = ", ".join(
                sorted({t.doc_type for t in group.transactions if t.doc_type})
            )
            findings.append(
                catalog.make_finding(
                    "C05",
                    f"Functional group {group.control or '?'} "
                    f"({group.functional_id or '?'}"
                    + (f", {doc_types}" if doc_types else "")
                    + ") has no matching 997 anywhere in the scanned set."
                    + (
                        " ISA14 asked the partner to acknowledge it."
                        if interchange.ack_requested
                        else ""
                    ),
                    severity="warning" if interchange.ack_requested else "info",
                    segment_index=group.gs.index,
                    segment_id="GS",
                    element=6,
                    line=group.gs.line,
                    context={
                        "gs06": group.control,
                        "gs01": group.functional_id,
                        "ack_requested": interchange.ack_requested,
                        "doc_types": doc_types.split(", ") if doc_types else [],
                    },
                )
            )
            findings[-1] = _with_source(findings[-1], str(entry.path))

    # C02 from the other direction: a 997 in the set that acknowledges a group
    # the set never sent.
    known = {
        group.control.lstrip("0") or "0"
        for _entry, _interchange, group in sent
    }
    if known:
        for entry, functional_id, control in ack_targets:
            if (control.lstrip("0") or "0") in known:
                continue
            findings.append(
                _with_source(
                    catalog.make_finding(
                        "C02",
                        f"This 997 acknowledges group {control!r} "
                        f"({functional_id or '?'}), which does not appear in the "
                        "scanned set.",
                        context={
                            "ak102": control,
                            "ak101": functional_id,
                            "known_groups": sorted(known),
                        },
                    ),
                    str(entry.path),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# D03 — shipped quantities against the order
# ---------------------------------------------------------------------------


@dataclass
class _OrderedLine:
    """One line of a purchase order, as the ASN will have to match it."""

    entry: ScanEntry
    line_number: str
    part_number: str
    quantity: Decimal
    uom: str


def _check_shipped_quantities(result: ScanResult) -> list[Finding]:
    """D03 — what the ASN ships must be what the order asked for.

    Quantities are totalled across every ASN for an order before comparing, so
    a legitimate split shipment adds up rather than reporting each half as a
    shortfall.
    """
    ordered: dict[str, dict[str, _OrderedLine]] = {}

    for entry in result.entries:
        for transaction in entry.envelope.transactions:
            if transaction.doc_type != "850":
                continue
            reference = _po_number(transaction)
            if not reference:
                continue
            lines = ordered.setdefault(reference, {})
            for segment in transaction.by_id("PO1"):
                quantity = _decimal(segment.get(2))
                if quantity is None:
                    continue
                # PO106 qualifies PO107; BP is the buyer's part number, which is
                # what the ASN's LIN echoes back.
                part = segment.get(7).strip() or segment.get(9).strip()
                key = part or segment.get(1).strip()
                if not key:
                    continue
                lines[key] = _OrderedLine(
                    entry=entry,
                    line_number=segment.get(1).strip(),
                    part_number=part,
                    quantity=quantity,
                    uom=segment.get(3).strip(),
                )

    if not ordered:
        return []

    #: (reference, key) to the total shipped and the segment that last carried it.
    shipped: dict[
        tuple[str, str], tuple[Decimal, ScanEntry, TransactionSet, Segment | None]
    ] = {}
    findings: list[Finding] = []

    for entry in result.entries:
        for transaction in entry.envelope.transactions:
            if transaction.doc_type != "856":
                continue
            reference = _po_number(transaction)
            if not reference or reference not in ordered:
                continue

            for key, quantity, sn1 in _shipped_lines(transaction):
                previous = shipped.get((reference, key))
                total = quantity + (previous[0] if previous else Decimal(0))
                shipped[(reference, key)] = (total, entry, transaction, sn1)

    for (reference, key), (total, entry, transaction, sn1) in sorted(
        shipped.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        line = ordered[reference].get(key)
        # Point at the SN1 that carried the quantity when we have it, and at
        # the ST otherwise, so the finding always has somewhere to land.
        segment = sn1 if sn1 is not None else transaction.st

        if line is None:
            findings.append(
                _with_source(
                    catalog.make_finding(
                        "D03",
                        f"The ASN ships {_plain(total)} of {key!r} against order "
                        f"{reference}, which does not have that item on it.",
                        segment_index=segment.index,
                        segment_id=segment.id,
                        line=segment.line,
                        why=(
                            "A substitution was shipped without amending the "
                            "order, or the ASN was built against the wrong "
                            "purchase order number."
                        ),
                        hint=(
                            "Ship what was ordered, or have the order amended "
                            "before the ASN goes out."
                        ),
                        context={
                            "reference": reference,
                            "item": key,
                            "shipped": _plain(total),
                        },
                    ),
                    str(entry.path),
                )
            )
            continue

        if total == line.quantity:
            continue

        over = total > line.quantity
        difference = abs(total - line.quantity)
        findings.append(
            _with_source(
                catalog.make_finding(
                    "D03",
                    f"Order {reference} line {line.line_number or '?'} "
                    f"({key}) was ordered as {_plain(line.quantity)} "
                    f"{line.uom or ''}".rstrip()
                    + f" but the ASN ships {_plain(total)} — "
                    + (
                        f"{_plain(difference)} more than ordered."
                        if over
                        else f"{_plain(difference)} short."
                    ),
                    severity="error" if over else "info",
                    segment_index=segment.index,
                    segment_id=segment.id,
                    line=segment.line,
                    why=(
                        "An over-shipment is usually a unit mix-up — cases "
                        "counted as eaches — and the receiver's dock will reject "
                        "the overage at the door."
                        if over
                        else "A shortfall is normal for a split shipment, and a "
                        "problem only if no further ASN follows."
                    ),
                    hint=(
                        "Reconcile the ASN against the purchase order before "
                        "sending, and check that quantities are expressed in the "
                        "unit the order used."
                    ),
                    context={
                        "reference": reference,
                        "item": key,
                        "ordered": _plain(line.quantity),
                        "shipped": _plain(total),
                        "difference": _plain(difference),
                        "direction": "over" if over else "short",
                        "ordered_in": str(line.entry.path),
                    },
                ),
                str(entry.path),
            )
        )

    return findings


def _shipped_lines(
    transaction: TransactionSet,
) -> list[tuple[str, Decimal, Segment | None]]:
    """Pair each LIN with the SN1 that follows it inside the item HL loop."""
    found: list[tuple[str, Decimal, Segment | None]] = []
    key = ""

    for segment in transaction.segments:
        if segment.id == "LIN":
            # LIN alternates qualifier and value from LIN02 onwards; BP is the
            # buyer's part number, the same identifier PO107 carries.
            key = _lin_part_number(segment) or segment.get(1).strip()
        elif segment.id == "SN1" and key:
            quantity = _decimal(segment.get(2))
            if quantity is not None:
                found.append((key, quantity, segment))
            key = ""
        elif segment.id == "HL":
            key = ""

    return found


def _lin_part_number(segment: Segment) -> str:
    """The buyer's part number from a LIN, if it carries one."""
    position = 2
    while position + 1 <= segment.element_count:
        qualifier = segment.get(position).strip().upper()
        value = segment.get(position + 1).strip()
        if qualifier in ("BP", "IN", "VP", "UP", "EN") and value:
            return value
        position += 2
    return ""


# ---------------------------------------------------------------------------
# C06 — flow chronology
# ---------------------------------------------------------------------------


def _check_chronology(result: ScanResult) -> list[Finding]:
    """C06 — documents in one flow stamped in an impossible order."""
    #: PO number to the documents in its flow.
    flows: dict[str, list[tuple[ScanEntry, TransactionSet, datetime, int]]] = {}

    for entry in result.entries:
        for interchange in entry.envelope.interchanges:
            for group in interchange.groups:
                stamp = _timestamp(interchange, group)
                if stamp is None:
                    continue
                for transaction in group.transactions:
                    order = _FLOW_ORDER.get(transaction.doc_type)
                    if order is None:
                        continue
                    reference = _po_number(transaction)
                    if not reference:
                        continue
                    flows.setdefault(reference, []).append(
                        (entry, transaction, stamp, order)
                    )

    findings: list[Finding] = []
    for reference, documents in sorted(flows.items()):
        if len(documents) < 2:
            continue
        ordered = sorted(documents, key=lambda item: item[3])

        for index in range(1, len(ordered)):
            entry, transaction, stamp, order = ordered[index]
            previous_entry, previous_transaction, previous_stamp, previous_order = (
                ordered[index - 1]
            )
            if previous_order == order or stamp >= previous_stamp:
                continue

            gap = previous_stamp - stamp
            findings.append(
                _with_source(
                    catalog.make_finding(
                        "C06",
                        f"For order {reference}, the {transaction.doc_type} is "
                        f"stamped {stamp:%Y-%m-%d %H:%M}, which is "
                        f"{_describe_gap(gap.total_seconds())} before the "
                        f"{previous_transaction.doc_type} it must follow "
                        f"({previous_stamp:%Y-%m-%d %H:%M} in "
                        f"{previous_entry.path.name}).",
                        segment_index=transaction.st.index,
                        segment_id="ST",
                        line=transaction.st.line,
                        context={
                            "reference": reference,
                            "doc_type": transaction.doc_type,
                            "timestamp": stamp.isoformat(),
                            "precedes": previous_transaction.doc_type,
                            "precedes_timestamp": previous_stamp.isoformat(),
                            "precedes_file": str(previous_entry.path),
                        },
                    ),
                    str(entry.path),
                )
            )

    return findings


def _describe_gap(seconds: float) -> str:
    days = int(seconds // 86400)
    if days >= 1:
        return f"{days} day{'' if days == 1 else 's'}"
    hours = int(seconds // 3600)
    if hours >= 1:
        return f"{hours} hour{'' if hours == 1 else 's'}"
    minutes = max(1, int(seconds // 60))
    return f"{minutes} minute{'' if minutes == 1 else 's'}"


def _po_number(transaction: TransactionSet) -> str:
    """The purchase order number this document belongs to, if it names one."""
    doc_type = transaction.doc_type
    if doc_type == "850":
        beg = transaction.first("BEG")
        return beg.get(3).strip() if beg else ""
    if doc_type == "855":
        bak = transaction.first("BAK")
        return bak.get(3).strip() if bak else ""
    if doc_type == "856":
        prf = transaction.first("PRF")
        return prf.get(1).strip() if prf else ""
    if doc_type == "810":
        big = transaction.first("BIG")
        return big.get(4).strip() if big else ""
    return ""


def _timestamp(
    interchange: Interchange, group: FunctionalGroup
) -> datetime | None:
    """Envelope timestamp, preferring GS04/GS05 for its four-digit year."""
    stamp = _combine(group.gs.get(4).strip(), group.gs.get(5).strip())
    if stamp is not None:
        return stamp
    if interchange.isa.id != "ISA":
        return None
    return _combine(interchange.isa.get(9).strip(), interchange.isa.get(10).strip())


def _combine(day: str, clock: str) -> datetime | None:
    parsed_day = _parse_date(day)
    if parsed_day is None:
        return None
    return datetime.combine(parsed_day, _parse_time(clock))


def _parse_date(value: str) -> date | None:
    if not value.isdigit():
        return None
    try:
        if len(value) == 8:
            return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
        if len(value) == 6:
            # A two-digit year is read against the X12 convention: 00-49 is
            # 21st century, 50-99 is 20th.
            year = int(value[0:2])
            century = 2000 if year < 50 else 1900
            return date(century + year, int(value[2:4]), int(value[4:6]))
    except ValueError:
        return None
    return None


def _parse_time(value: str) -> time:
    if len(value) >= 4 and value[:4].isdigit():
        hour, minute = int(value[0:2]), int(value[2:4])
        second = int(value[4:6]) if len(value) >= 6 and value[4:6].isdigit() else 0
        if hour < 24 and minute < 60 and second < 60:
            return time(hour, minute, second)
    return time(0, 0)
