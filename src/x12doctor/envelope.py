# SPDX-License-Identifier: Apache-2.0
"""Envelope parsing and the category A structural checks.

The envelope is the part of X12 that every transaction set shares, and it is
where most rejections originate: counts that were written before the content
was final, control numbers fetched twice, an ISA that lost its padding. This
module builds the ISA/GS/ST nesting into a tree and checks it.

The tree is deliberately tolerant. A missing trailer, a stray segment or a
second ISA inside an open interchange all produce findings while the tree keeps
whatever structure could be recovered, so that later semantic checks still have
something to work with.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import catalog
from .report import Finding
from .tokenizer import (
    ISA_ELEMENT_WIDTHS,
    ISA_LENGTH,
    Delimiters,
    Segment,
    TokenizedDocument,
)

__all__ = [
    "Envelope",
    "FunctionalGroup",
    "Interchange",
    "TransactionSet",
    "check_envelope",
    "parse_envelope",
]

#: ISA12 values defined by X12. Anything else is a typo or a future release.
_KNOWN_ISA_VERSIONS = frozenset(
    {"00200", "00204", "00300", "00301", "00302", "00303", "00304", "00305",
     "00306", "00307", "00400", "00401", "00402", "00403", "00404", "00405",
     "00406", "00500", "00501", "00502", "00503", "00504", "00505", "00600",
     "00601", "00602", "00603", "00604", "00605", "00606", "00607"}
)


@dataclass
class TransactionSet:
    """One ST..SE transaction set."""

    st: Segment
    se: Segment | None = None
    #: Every segment from ST through SE inclusive, in document order.
    segments: list[Segment] = field(default_factory=list)

    @property
    def doc_type(self) -> str:
        """Transaction set identifier from ST01, e.g. ``"850"``."""
        return self.st.get(1).strip()

    @property
    def control(self) -> str:
        """Transaction set control number from ST02."""
        return self.st.get(2).strip()

    @property
    def body(self) -> list[Segment]:
        """The segments between ST and SE, exclusive."""
        return [s for s in self.segments if s.id not in ("ST", "SE")]

    def by_id(self, *ids: str) -> list[Segment]:
        wanted = {i.upper() for i in ids}
        return [s for s in self.segments if s.id in wanted]

    def first(self, segment_id: str) -> Segment | None:
        target = segment_id.upper()
        for segment in self.segments:
            if segment.id == target:
                return segment
        return None


@dataclass
class FunctionalGroup:
    """One GS..GE functional group."""

    gs: Segment
    ge: Segment | None = None
    transactions: list[TransactionSet] = field(default_factory=list)

    @property
    def functional_id(self) -> str:
        """GS01, e.g. ``"PO"`` for purchase orders, ``"FA"`` for 997s."""
        return self.gs.get(1).strip()

    @property
    def control(self) -> str:
        """Group control number from GS06."""
        return self.gs.get(6).strip()

    @property
    def version(self) -> str:
        """Version/release from GS08, e.g. ``"004010"``."""
        return self.gs.get(8).strip()


@dataclass
class Interchange:
    """One ISA..IEA interchange."""

    isa: Segment
    iea: Segment | None = None
    groups: list[FunctionalGroup] = field(default_factory=list)

    @property
    def control(self) -> str:
        """Interchange control number from ISA13, without its zero padding."""
        return self.isa.get(13).strip()

    @property
    def sender(self) -> str:
        return self.isa.get(6).strip()

    @property
    def receiver(self) -> str:
        return self.isa.get(8).strip()

    @property
    def sender_qualifier(self) -> str:
        return self.isa.get(5).strip()

    @property
    def receiver_qualifier(self) -> str:
        return self.isa.get(7).strip()

    @property
    def version(self) -> str:
        return self.isa.get(12).strip()

    @property
    def ack_requested(self) -> bool:
        """True when ISA14 asks the receiver for a 997."""
        return self.isa.get(14).strip() == "1"

    @property
    def is_test(self) -> bool:
        """True when ISA15 marks the interchange as test data."""
        return self.isa.get(15).strip().upper() == "T"

    @property
    def transactions(self) -> list[TransactionSet]:
        return [t for g in self.groups for t in g.transactions]


@dataclass
class Envelope:
    """The recovered envelope structure of a whole document."""

    interchanges: list[Interchange] = field(default_factory=list)
    #: Segments that appeared outside any ST..SE, other than envelope segments.
    orphans: list[Segment] = field(default_factory=list)

    @property
    def transactions(self) -> list[TransactionSet]:
        return [t for i in self.interchanges for t in i.transactions]

    @property
    def groups(self) -> list[FunctionalGroup]:
        return [g for i in self.interchanges for g in i.groups]

    def doc_types(self) -> list[str]:
        """Transaction set ids in document order, duplicates preserved."""
        return [t.doc_type for t in self.transactions if t.doc_type]


def parse_envelope(doc: TokenizedDocument) -> tuple[Envelope, list[Finding]]:
    """Build the envelope tree, reporting anything that does not nest.

    The parser keeps going through every kind of damage: a GS with no ISA is
    attached to a synthetic interchange, an unclosed ST is closed implicitly at
    the next boundary, and segments that belong nowhere land in ``orphans``.
    """
    envelope = Envelope()
    findings: list[Finding] = []

    interchange: Interchange | None = None
    group: FunctionalGroup | None = None
    transaction: TransactionSet | None = None

    def close_transaction(reason_segment: Segment | None) -> None:
        nonlocal transaction
        if transaction is not None and transaction.se is None:
            findings.append(
                catalog.make_finding(
                    "A10",
                    f"Transaction set {transaction.doc_type or '?'} opened at "
                    f"segment {transaction.st.index + 1} is never closed by an SE"
                    + (
                        f" — {reason_segment.id} appears first."
                        if reason_segment is not None
                        else "; the document ends first."
                    ),
                    segment_index=transaction.st.index,
                    segment_id="ST",
                    line=transaction.st.line,
                    context={"expected": "SE", "st02": transaction.control},
                )
            )
        transaction = None

    def close_group(reason_segment: Segment | None) -> None:
        nonlocal group
        close_transaction(reason_segment)
        if group is not None and group.ge is None:
            findings.append(
                catalog.make_finding(
                    "A10",
                    f"Functional group {group.functional_id or '?'} opened at "
                    f"segment {group.gs.index + 1} is never closed by a GE"
                    + (
                        f" — {reason_segment.id} appears first."
                        if reason_segment is not None
                        else "; the document ends first."
                    ),
                    segment_index=group.gs.index,
                    segment_id="GS",
                    line=group.gs.line,
                    context={"expected": "GE", "gs06": group.control},
                )
            )
        group = None

    def close_interchange(reason_segment: Segment | None) -> None:
        nonlocal interchange
        close_group(reason_segment)
        if interchange is not None and interchange.iea is None:
            findings.append(
                catalog.make_finding(
                    "A10",
                    f"Interchange {interchange.control or '?'} opened at segment "
                    f"{interchange.isa.index + 1} is never closed by an IEA"
                    + (
                        f" — {reason_segment.id} appears first."
                        if reason_segment is not None
                        else "; the document ends first."
                    ),
                    segment_index=interchange.isa.index,
                    segment_id="ISA",
                    line=interchange.isa.line,
                    context={"expected": "IEA", "isa13": interchange.control},
                )
            )
        interchange = None

    def out_of_order(segment: Segment, expected: str) -> None:
        findings.append(
            catalog.make_finding(
                "A11",
                f"{segment.id} appears where it cannot: {expected}.",
                segment_index=segment.index,
                segment_id=segment.id,
                line=segment.line,
                context={"segment": segment.id},
            )
        )

    for segment in doc.segments:
        if segment.id == "ISA":
            if interchange is not None:
                out_of_order(segment, "the previous interchange has no IEA")
                close_interchange(segment)
            interchange = Interchange(isa=segment)
            envelope.interchanges.append(interchange)

        elif segment.id == "IEA":
            if interchange is None:
                out_of_order(segment, "there is no open ISA to close")
                continue
            close_group(segment)
            interchange.iea = segment
            interchange = None

        elif segment.id == "GS":
            if interchange is None:
                out_of_order(segment, "a functional group must sit inside an ISA")
                interchange = Interchange(isa=segment)
                envelope.interchanges.append(interchange)
            if group is not None:
                out_of_order(segment, "the previous functional group has no GE")
                close_group(segment)
            group = FunctionalGroup(gs=segment)
            interchange.groups.append(group)

        elif segment.id == "GE":
            if group is None:
                out_of_order(segment, "there is no open GS to close")
                continue
            close_transaction(segment)
            group.ge = segment
            group = None

        elif segment.id == "ST":
            if group is None:
                out_of_order(segment, "a transaction set must sit inside a GS")
                if interchange is None:
                    interchange = Interchange(isa=segment)
                    envelope.interchanges.append(interchange)
                group = FunctionalGroup(gs=segment)
                interchange.groups.append(group)
            if transaction is not None:
                out_of_order(segment, "the previous transaction set has no SE")
                close_transaction(segment)
            transaction = TransactionSet(st=segment, segments=[segment])
            group.transactions.append(transaction)

        elif segment.id == "SE":
            if transaction is None:
                out_of_order(segment, "there is no open ST to close")
                continue
            transaction.se = segment
            transaction.segments.append(segment)
            transaction = None

        else:
            if transaction is not None:
                transaction.segments.append(segment)
            else:
                envelope.orphans.append(segment)

    close_interchange(None)

    for orphan in envelope.orphans:
        findings.append(
            catalog.make_finding(
                "A11",
                f"{orphan.id} sits outside any transaction set.",
                segment_index=orphan.index,
                segment_id=orphan.id,
                line=orphan.line,
                context={"segment": orphan.id},
            )
        )

    return envelope, findings


def check_envelope(
    envelope: Envelope, doc: TokenizedDocument
) -> list[Finding]:
    """Run the category A checks that need a parsed envelope."""
    findings: list[Finding] = []
    isa_findings: list[Finding] = []

    for interchange in envelope.interchanges:
        if interchange.isa.id == "ISA":
            isa_findings.extend(_check_isa_width(interchange.isa))
            findings.extend(_check_version(interchange))
        findings.extend(_check_control_numbers(interchange))
        findings.extend(_check_counts(interchange))

    findings.extend(isa_findings)
    # A damaged ISA is why the separators had to be inferred, and A01 above
    # already says so precisely. Reporting the inference separately would turn
    # one defect into two findings.
    findings.extend(
        _check_separators(doc, isa_reported=any(f.code == "A01" for f in isa_findings))
    )
    findings.extend(_check_stray_content(doc, envelope))
    return findings


def _check_isa_width(isa: Segment) -> list[Finding]:
    """A01 — the ISA is fixed width; verify the whole segment and each element."""
    findings: list[Finding] = []
    # ``raw`` excludes the terminator, so a conforming ISA is 105 characters.
    expected_raw = ISA_LENGTH - 1
    actual_raw = len(isa.raw)

    bad_widths: list[tuple[int, int, int]] = []
    for position, width in enumerate(ISA_ELEMENT_WIDTHS, start=1):
        # ISA16 is the component separator: present as the final character but
        # not followed by an element separator, so it is always length 1 when
        # the segment is well formed.
        value = isa.get(position)
        if position > isa.element_count:
            bad_widths.append((position, width, 0))
        elif len(value) != width:
            bad_widths.append((position, width, len(value)))

    if actual_raw != expected_raw or bad_widths:
        detail = ""
        if bad_widths:
            shown = ", ".join(
                f"ISA{position:02d} is {actual} characters, must be {width}"
                for position, width, actual in bad_widths[:4]
            )
            if len(bad_widths) > 4:
                shown += f", and {len(bad_widths) - 4} more"
            detail = f" ({shown})"
        findings.append(
            catalog.make_finding(
                "A01",
                f"The ISA segment is {actual_raw + 1} characters including its "
                f"terminator, not {ISA_LENGTH}{detail}.",
                segment_index=isa.index,
                segment_id="ISA",
                element=bad_widths[0][0] if bad_widths else None,
                line=isa.line,
                context={
                    "expected_length": ISA_LENGTH,
                    "actual_length": actual_raw + 1,
                    "bad_elements": [
                        {"element": p, "expected": w, "actual": a}
                        for p, w, a in bad_widths
                    ],
                },
            )
        )

    if isa.element_count != len(ISA_ELEMENT_WIDTHS):
        findings.append(
            catalog.make_finding(
                "A01",
                f"The ISA segment has {isa.element_count} elements, not "
                f"{len(ISA_ELEMENT_WIDTHS)}.",
                segment_index=isa.index,
                segment_id="ISA",
                line=isa.line,
                context={
                    "expected_elements": len(ISA_ELEMENT_WIDTHS),
                    "actual_elements": isa.element_count,
                },
            )
        )

    return findings


def _check_control_numbers(interchange: Interchange) -> list[Finding]:
    """A03 — ISA13/IEA02, GS06/GE02 and ST02/SE02 must agree."""
    findings: list[Finding] = []

    if interchange.iea is not None:
        isa13 = interchange.control
        iea02 = interchange.iea.get(2).strip()
        # Control numbers are numeric with optional zero padding, so compare
        # numerically when both sides look like numbers.
        if not _same_control(isa13, iea02):
            findings.append(
                catalog.make_finding(
                    "A03",
                    f"ISA13 is {isa13!r} but IEA02 is {iea02!r}.",
                    segment_index=interchange.iea.index,
                    segment_id="IEA",
                    element=2,
                    line=interchange.iea.line,
                    context={"isa13": isa13, "iea02": iea02},
                )
            )

    for group in interchange.groups:
        if group.ge is not None:
            gs06 = group.control
            ge02 = group.ge.get(2).strip()
            if not _same_control(gs06, ge02):
                findings.append(
                    catalog.make_finding(
                        "A03",
                        f"GS06 is {gs06!r} but GE02 is {ge02!r}.",
                        segment_index=group.ge.index,
                        segment_id="GE",
                        element=2,
                        line=group.ge.line,
                        context={"gs06": gs06, "ge02": ge02},
                    )
                )

        for transaction in group.transactions:
            if transaction.se is None:
                continue
            st02 = transaction.control
            se02 = transaction.se.get(2).strip()
            if not _same_control(st02, se02):
                findings.append(
                    catalog.make_finding(
                        "A03",
                        f"ST02 is {st02!r} but SE02 is {se02!r}.",
                        segment_index=transaction.se.index,
                        segment_id="SE",
                        element=2,
                        line=transaction.se.line,
                        context={"st02": st02, "se02": se02},
                    )
                )

    return findings


def _same_control(left: str, right: str) -> bool:
    """Compare control numbers, ignoring zero padding but not content."""
    if left == right:
        return True
    if left.isdigit() and right.isdigit():
        return int(left) == int(right)
    return False


def _check_counts(interchange: Interchange) -> list[Finding]:
    """A02, A06, A07 — every declared count against what is actually there."""
    findings: list[Finding] = []

    if interchange.iea is not None:
        declared = interchange.iea.get(1).strip()
        actual = len(interchange.groups)
        if declared.isdigit() and int(declared) != actual:
            findings.append(
                catalog.make_finding(
                    "A07",
                    f"IEA01 declares {int(declared)} functional group"
                    f"{'' if int(declared) == 1 else 's'}, but the interchange "
                    f"contains {actual}.",
                    segment_index=interchange.iea.index,
                    segment_id="IEA",
                    element=1,
                    line=interchange.iea.line,
                    context={"declared": int(declared), "actual": actual},
                )
            )
        elif not declared.isdigit():
            findings.append(
                catalog.make_finding(
                    "A07",
                    f"IEA01 is {declared!r}, which is not a group count.",
                    segment_index=interchange.iea.index,
                    segment_id="IEA",
                    element=1,
                    line=interchange.iea.line,
                    context={"declared": declared, "actual": actual},
                )
            )

    for group in interchange.groups:
        if group.ge is not None:
            declared = group.ge.get(1).strip()
            actual = len(group.transactions)
            if declared.isdigit() and int(declared) != actual:
                findings.append(
                    catalog.make_finding(
                        "A06",
                        f"GE01 declares {int(declared)} transaction set"
                        f"{'' if int(declared) == 1 else 's'}, but the group "
                        f"contains {actual}.",
                        segment_index=group.ge.index,
                        segment_id="GE",
                        element=1,
                        line=group.ge.line,
                        context={"declared": int(declared), "actual": actual},
                    )
                )
            elif not declared.isdigit():
                findings.append(
                    catalog.make_finding(
                        "A06",
                        f"GE01 is {declared!r}, which is not a transaction set count.",
                        segment_index=group.ge.index,
                        segment_id="GE",
                        element=1,
                        line=group.ge.line,
                        context={"declared": declared, "actual": actual},
                    )
                )

        for transaction in group.transactions:
            findings.extend(_check_se_count(transaction))

    return findings


def _check_se_count(transaction: TransactionSet) -> list[Finding]:
    """A02 — SE01 counts ST through SE inclusive."""
    if transaction.se is None:
        return []

    declared = transaction.se.get(1).strip()
    actual = len(transaction.segments)

    if not declared.isdigit():
        return [
            catalog.make_finding(
                "A02",
                f"SE01 is {declared!r}, which is not a segment count. "
                f"ST through SE contains {actual} segments.",
                segment_index=transaction.se.index,
                segment_id="SE",
                element=1,
                line=transaction.se.line,
                context={"declared": declared, "actual": actual},
            )
        ]

    if int(declared) == actual:
        return []

    difference = actual - int(declared)
    direction = (
        f"{difference} more than declared"
        if difference > 0
        else f"{-difference} fewer than declared"
    )
    return [
        catalog.make_finding(
            "A02",
            f"SE01 says {int(declared)} segments, but ST through SE actually "
            f"contains {actual} — {direction}.",
            segment_index=transaction.se.index,
            segment_id="SE",
            element=1,
            line=transaction.se.line,
            context={
                "declared": int(declared),
                "actual": actual,
                "st02": transaction.control,
                "doc_type": transaction.doc_type,
            },
        )
    ]


def _check_version(interchange: Interchange) -> list[Finding]:
    """A08 — ISA12 and GS08 must be known and must agree."""
    findings: list[Finding] = []
    isa12 = interchange.version

    if isa12 and isa12 not in _KNOWN_ISA_VERSIONS:
        findings.append(
            catalog.make_finding(
                "A08",
                f"ISA12 is {isa12!r}, which is not a recognised X12 version.",
                segment_index=interchange.isa.index,
                segment_id="ISA",
                element=12,
                line=interchange.isa.line,
                context={"isa12": isa12},
            )
        )

    for group in interchange.groups:
        gs08 = group.version
        if not gs08:
            continue
        # GS08 is the version plus an optional industry identifier, e.g.
        # "004010VICS". Only the leading release matters for consistency.
        release = gs08[:6]
        if not release.isdigit() or len(release) < 6:
            findings.append(
                catalog.make_finding(
                    "A08",
                    f"GS08 is {gs08!r}, which does not start with a six-digit "
                    "version and release.",
                    segment_index=group.gs.index,
                    segment_id="GS",
                    element=8,
                    line=group.gs.line,
                    context={"gs08": gs08},
                )
            )
            continue

        if isa12 and release[:5] != isa12:
            findings.append(
                catalog.make_finding(
                    "A08",
                    f"ISA12 declares version {isa12!r} but GS08 declares "
                    f"{gs08!r}; the envelope and the group disagree.",
                    segment_index=group.gs.index,
                    segment_id="GS",
                    element=8,
                    line=group.gs.line,
                    context={"isa12": isa12, "gs08": gs08},
                )
            )

    return findings


def _check_separators(
    doc: TokenizedDocument, *, isa_reported: bool = False
) -> list[Finding]:
    """A05 — separators must be usable and must match what the ISA declares."""
    findings: list[Finding] = []
    delimiters = doc.delimiters

    isa = doc.first("ISA")
    if isa is None:
        return findings

    if not delimiters.from_isa:
        if isa_reported:
            return findings
        findings.append(
            catalog.make_finding(
                "A05",
                "The ISA is too damaged to read its separators positionally; "
                f"they were inferred as {delimiters.describe()}.",
                segment_index=isa.index,
                segment_id="ISA",
                line=isa.line,
                context={"inferred": True},
            )
        )
        return findings

    # ISA16 declares the component separator. When the tokenizer read a
    # different character at the fixed offset, the ISA contradicts itself.
    declared_component = isa.get(16)
    if declared_component and declared_component != delimiters.component:
        findings.append(
            catalog.make_finding(
                "A05",
                f"ISA16 declares {declared_component!r} as the component "
                f"separator, but {delimiters.component!r} is used.",
                segment_index=isa.index,
                segment_id="ISA",
                element=16,
                line=isa.line,
                context={
                    "declared": declared_component,
                    "used": delimiters.component,
                },
            )
        )

    unusual = _unusual_separators(delimiters)
    if unusual:
        findings.append(
            catalog.make_finding(
                "A05",
                "Unusual separators in use: "
                + "; ".join(unusual)
                + ". They are legal because the ISA declares them, but partners "
                "with hard-coded parsers will not read the file.",
                segment_index=isa.index,
                segment_id="ISA",
                line=isa.line,
                context={"delimiters": delimiters.describe()},
            )
        )

    return findings


def _unusual_separators(delimiters: Delimiters) -> list[str]:
    """Name any separator outside the set partners actually expect."""
    # The conventional set is "*" / "~" / ">" or ":", plus the EDI control
    # characters. Everything else parses fine here but trips hand-rolled
    # parsers on the far end, which is the whole point of the warning.
    notes: list[str] = []
    if delimiters.element not in "*\x1f":
        notes.append(f"element separator {delimiters.element!r} instead of '*'")
    if delimiters.segment not in "~\x1c\n":
        notes.append(f"segment terminator {delimiters.segment!r} instead of '~'")
    if delimiters.component not in ">:\x1d":
        notes.append(f"component separator {delimiters.component!r}")
    # Alphanumeric separators are the dangerous case: they collide with data.
    for name, char in (
        ("element separator", delimiters.element),
        ("segment terminator", delimiters.segment),
        ("component separator", delimiters.component),
    ):
        if char.isalnum():
            notes.append(f"{name} {char!r} is alphanumeric and will collide with data")
    return notes


def _check_stray_content(
    doc: TokenizedDocument, envelope: Envelope
) -> list[Finding]:
    """A09 — nothing may sit before the ISA or after the final IEA."""
    findings: list[Finding] = []
    source = doc.source

    isa = doc.first("ISA")
    if isa is not None and isa.offset > 0:
        leading = source[: isa.offset]
        if leading.strip():
            findings.append(
                catalog.make_finding(
                    "A09",
                    f"{len(leading)} characters appear before the ISA: "
                    f"{_preview(leading)}.",
                    segment_index=isa.index,
                    segment_id="ISA",
                    line=isa.line,
                    context={"position": "before ISA", "length": len(leading)},
                )
            )

    last_iea: Segment | None = None
    for interchange in envelope.interchanges:
        if interchange.iea is not None:
            last_iea = interchange.iea

    if last_iea is not None:
        tail_start = last_iea.offset + len(last_iea.raw)
        tail = source[tail_start:].strip(doc.delimiters.segment).strip()
        if tail:
            findings.append(
                catalog.make_finding(
                    "A09",
                    f"{len(tail)} characters appear after the final IEA: "
                    f"{_preview(tail)}.",
                    segment_index=last_iea.index,
                    segment_id="IEA",
                    line=last_iea.line,
                    context={"position": "after IEA", "length": len(tail)},
                )
            )

    return findings


def _preview(text: str, limit: int = 40) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1] + "…"
    return repr(collapsed)
