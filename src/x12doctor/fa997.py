# SPDX-License-Identifier: Apache-2.0
"""997 Functional Acknowledgment — decoding and pairing.

A 997 is the receiver telling you what they thought of your document, encoded
as bare numbers. ``AK3*PO1*9**8`` and ``AK4*3*355*7*CASE`` between them say
"the ninth segment, a PO1, had a data element error; element 3 carried an
invalid code value, and the value was CASE" — which is a sentence nobody
should have to assemble by hand from a code list.

Two things happen here. The 997 is decoded on its own, and — when the original
document is supplied — the positions it reports are resolved against that
document so the finding names the actual value that was rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import catalog
from .envelope import Envelope, TransactionSet, parse_envelope
from .partner import DEFAULT_PARTNER, PartnerProfile
from .report import Finding, Severity
from .tokenizer import Segment, TokenizedDocument, tokenize

__all__ = [
    "ELEMENT_SYNTAX_ERRORS",
    "GROUP_ACK_CODES",
    "GROUP_SYNTAX_ERRORS",
    "SEGMENT_SYNTAX_ERRORS",
    "TRANSACTION_SET_ACK_CODES",
    "TRANSACTION_SET_SYNTAX_ERRORS",
    "AckGroup",
    "AckTransaction",
    "Acknowledgment",
    "ElementError",
    "SegmentError",
    "decode_ack",
    "diagnose_997",
    "explain_997",
]

# ---------------------------------------------------------------------------
# The code lexicon
#
# These are the 004010 code lists a 997 draws on. Codes outside them are
# reported verbatim with a note rather than guessed at — a wrong translation of
# a rejection reason is worse than an untranslated one.
# ---------------------------------------------------------------------------

#: AK501 — Transaction Set Acknowledgment Code.
TRANSACTION_SET_ACK_CODES: dict[str, str] = {
    "A": "accepted",
    "E": "accepted, but errors were noted",
    "M": "rejected — message authentication code failed",
    "R": "rejected",
    "W": "rejected — assurance failed validity tests",
    "X": "rejected — content after decryption could not be analysed",
}

#: AK901 — Functional Group Acknowledge Code.
GROUP_ACK_CODES: dict[str, str] = {
    "A": "accepted",
    "E": "accepted, but errors were noted",
    "M": "rejected — message authentication code failed",
    "P": "partially accepted — at least one transaction set was rejected",
    "R": "rejected",
    "W": "rejected — assurance failed validity tests",
    "X": "rejected — content after decryption could not be analysed",
}

#: AK304 — Segment Syntax Error Code.
SEGMENT_SYNTAX_ERRORS: dict[str, str] = {
    "1": "unrecognised segment ID",
    "2": "unexpected segment",
    "3": "mandatory segment missing",
    "4": "loop occurs over maximum times",
    "5": "segment exceeds maximum use",
    "6": "segment not in the defined transaction set",
    "7": "segment not in proper sequence",
    "8": "segment has data element errors",
}

#: AK403 — Data Element Syntax Error Code.
ELEMENT_SYNTAX_ERRORS: dict[str, str] = {
    "1": "mandatory data element missing",
    "2": "conditional required data element missing",
    "3": "too many data elements",
    "4": "data element too short",
    "5": "data element too long",
    "6": "invalid character in data element",
    "7": "invalid code value",
    "8": "invalid date",
    "9": "invalid time",
    "10": "exclusion condition violated",
}

#: AK502 through AK506 — Transaction Set Syntax Error Code.
TRANSACTION_SET_SYNTAX_ERRORS: dict[str, str] = {
    "1": "transaction set not supported",
    "2": "transaction set trailer missing",
    "3": "transaction set control numbers in the header and trailer do not match",
    "4": "number of included segments does not match the actual count",
    "5": "one or more segments are in error",
    "6": "missing or invalid transaction set identifier",
    "7": "missing or invalid transaction set control number",
    "23": "transaction set control number is not unique within the functional group",
}

#: AK905 through AK909 — Functional Group Syntax Error Code.
GROUP_SYNTAX_ERRORS: dict[str, str] = {
    "1": "functional group not supported",
    "2": "functional group version not supported",
    "3": "functional group trailer missing",
    "4": "group control numbers in the header and trailer do not agree",
    "5": "number of included transaction sets does not match the actual count",
    "6": "group control number violates syntax",
}

#: Overall outcomes, most severe last, so the worst one wins when a 997 covers
#: several groups.
_STATUS_RANK = {
    "accepted": 0,
    "accepted_with_errors": 1,
    "partially_accepted": 2,
    "rejected": 3,
    "unknown": 4,
}

_ACK_CODE_TO_STATUS = {
    "A": "accepted",
    "E": "accepted_with_errors",
    "P": "partially_accepted",
    "R": "rejected",
    "M": "rejected",
    "W": "rejected",
    "X": "rejected",
}


def _describe(code: str, lexicon: dict[str, str]) -> str:
    """Translate a code, saying so plainly when it is not in the list."""
    meaning = lexicon.get(code)
    if meaning is not None:
        return meaning
    return f"error code {code} (not in the 004010 code list)"


# ---------------------------------------------------------------------------
# Decoded structure
# ---------------------------------------------------------------------------


@dataclass
class ElementError:
    """One AK4 — a data element the receiver objected to."""

    #: AK401 element position within the segment, 1-based.
    position: int | None
    #: Component position within a composite element, when the 997 gives one.
    component: int | None
    #: AK402 data element reference number, e.g. ``"355"`` for a unit of measure.
    reference: str
    #: AK403 syntax error code.
    code: str
    #: Plain-English reading of ``code``.
    meaning: str
    #: AK404 copy of the offending value, when the receiver echoed it back.
    bad_value: str
    #: The value found at this position in the original document, when paired.
    original_value: str | None = None

    def describe(self) -> str:
        where = f"element {self.position:02d}" if self.position else "an element"
        if self.component:
            where += f", component {self.component:02d}"
        text = f"{where}: {self.meaning}"
        shown = self.bad_value or self.original_value
        if shown:
            text += f" — the value was {shown!r}"
        if self.reference:
            text += f" (data element {self.reference})"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "component": self.component,
            "reference": self.reference,
            "code": self.code,
            "meaning": self.meaning,
            "bad_value": self.bad_value,
            "original_value": self.original_value,
        }


@dataclass
class SegmentError:
    """One AK3 loop — a segment the receiver objected to."""

    #: AK301 segment identifier, e.g. ``"PO1"``.
    segment_id: str
    #: AK302 position within the transaction set, counting ST as 1.
    position: int | None
    #: AK303 loop identifier, when the segment sits in a loop.
    loop_id: str
    #: AK304 codes with their meanings.
    errors: list[tuple[str, str]] = field(default_factory=list)
    elements: list[ElementError] = field(default_factory=list)
    #: The matching segment from the original document, when paired.
    original: Segment | None = None
    #: Set when AK302 resolved to a segment whose id is not the AK301 the 997
    #: names. The position is then untrustworthy and is not used.
    position_conflict: str | None = None

    def describe(self) -> str:
        where = (
            f"segment {self.position} of the transaction set"
            if self.position
            else "a segment"
        )
        if self.segment_id:
            where += f" ({self.segment_id})"
        reasons = "; ".join(meaning for _code, meaning in self.errors)
        return f"{where}: {reasons}" if reasons else where

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "position": self.position,
            "loop_id": self.loop_id,
            "errors": [{"code": c, "meaning": m} for c, m in self.errors],
            "elements": [e.to_dict() for e in self.elements],
            "original_segment": self.original.raw if self.original else None,
        }


@dataclass
class AckTransaction:
    """One AK2 loop — the verdict on a single transaction set."""

    #: AK201 transaction set identifier, e.g. ``"850"``.
    doc_type: str
    #: AK202 transaction set control number, matching the original's ST02.
    control: str
    #: AK501 acknowledgment code.
    code: str = ""
    #: Plain-English reading of ``code``.
    meaning: str = ""
    #: AK502..AK506 codes with their meanings.
    syntax_errors: list[tuple[str, str]] = field(default_factory=list)
    segment_errors: list[SegmentError] = field(default_factory=list)

    @property
    def status(self) -> str:
        return _ACK_CODE_TO_STATUS.get(self.code, "unknown")

    @property
    def accepted(self) -> bool:
        return self.status in ("accepted", "accepted_with_errors")

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_type": self.doc_type,
            "control": self.control,
            "code": self.code,
            "meaning": self.meaning,
            "status": self.status,
            "syntax_errors": [
                {"code": c, "meaning": m} for c, m in self.syntax_errors
            ],
            "segment_errors": [s.to_dict() for s in self.segment_errors],
        }


@dataclass
class AckGroup:
    """One AK1/AK9 pair — the verdict on a functional group."""

    #: AK101 functional identifier code, e.g. ``"PO"``.
    functional_id: str
    #: AK102 group control number, matching the original's GS06.
    control: str
    transactions: list[AckTransaction] = field(default_factory=list)
    #: AK901 functional group acknowledge code.
    code: str = ""
    meaning: str = ""
    #: AK902, AK903, AK904 — included, received, accepted.
    included: str = ""
    received: str = ""
    accepted: str = ""
    #: AK905..AK909 codes with their meanings.
    syntax_errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def status(self) -> str:
        return _ACK_CODE_TO_STATUS.get(self.code, "unknown")

    def to_dict(self) -> dict[str, Any]:
        return {
            "functional_id": self.functional_id,
            "control": self.control,
            "code": self.code,
            "meaning": self.meaning,
            "status": self.status,
            "counts": {
                "included": self.included,
                "received": self.received,
                "accepted": self.accepted,
            },
            "syntax_errors": [
                {"code": c, "meaning": m} for c, m in self.syntax_errors
            ],
            "transactions": [t.to_dict() for t in self.transactions],
        }


@dataclass
class Acknowledgment:
    """The decoded 997, with the findings that follow from it."""

    groups: list[AckGroup] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    #: Set when an original document was supplied and successfully paired.
    paired: bool = False
    source: str | None = None

    @property
    def status(self) -> str:
        """Worst outcome across every group, or ``"unknown"`` if empty."""
        if not self.groups:
            return "unknown"
        return max(
            (group.status for group in self.groups),
            key=lambda s: _STATUS_RANK.get(s, 99),
        )

    @property
    def ok(self) -> bool:
        """True when nothing was rejected and no errors were noted."""
        return self.status == "accepted"

    @property
    def transactions(self) -> list[AckTransaction]:
        return [t for g in self.groups for t in g.transactions]

    @property
    def rejections(self) -> list[SegmentError]:
        """Every located objection, across all transaction sets."""
        return [e for t in self.transactions for e in t.segment_errors]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "ok": self.ok,
            "paired": self.paired,
            "groups": [g.to_dict() for g in self.groups],
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def decode_ack(transaction: TransactionSet, doc: TokenizedDocument) -> list[AckGroup]:
    """Turn the AK segments of one 997 transaction set into groups.

    A 997 acknowledges exactly one functional group, so in practice this
    returns a single group. It returns a list anyway because a document may
    carry several 997 transaction sets, and the caller concatenates them.
    """
    groups: list[AckGroup] = []
    group: AckGroup | None = None
    ack_transaction: AckTransaction | None = None
    segment_error: SegmentError | None = None
    component = doc.delimiters.component

    for segment in transaction.segments:
        if segment.id == "AK1":
            group = AckGroup(
                functional_id=segment.get(1).strip(),
                control=segment.get(2).strip(),
            )
            groups.append(group)
            ack_transaction = None
            segment_error = None

        elif segment.id == "AK2":
            if group is None:
                # An AK2 with no AK1 is malformed, but the verdict it carries
                # is still worth decoding, so attach it to a placeholder.
                group = AckGroup(functional_id="", control="")
                groups.append(group)
            ack_transaction = AckTransaction(
                doc_type=segment.get(1).strip(),
                control=segment.get(2).strip(),
            )
            group.transactions.append(ack_transaction)
            segment_error = None

        elif segment.id == "AK3":
            if ack_transaction is None:
                continue
            position = segment.get(2).strip()
            segment_error = SegmentError(
                segment_id=segment.get(1).strip(),
                position=int(position) if position.isdigit() else None,
                loop_id=segment.get(3).strip(),
                errors=[
                    (code, _describe(code, SEGMENT_SYNTAX_ERRORS))
                    for code in _codes(segment, 4, 4)
                ],
            )
            ack_transaction.segment_errors.append(segment_error)

        elif segment.id == "AK4":
            if segment_error is None:
                continue
            # AK401 is a composite: element position, then optionally the
            # component position within it.
            parts = [p for p in segment.get(1).split(component) if p.strip()]
            position = parts[0].strip() if parts else ""
            sub = parts[1].strip() if len(parts) > 1 else ""
            code = segment.get(3).strip()
            segment_error.elements.append(
                ElementError(
                    position=int(position) if position.isdigit() else None,
                    component=int(sub) if sub.isdigit() else None,
                    reference=segment.get(2).strip(),
                    code=code,
                    meaning=_describe(code, ELEMENT_SYNTAX_ERRORS),
                    bad_value=segment.get(4),
                )
            )

        elif segment.id == "AK5":
            if ack_transaction is None:
                continue
            code = segment.get(1).strip().upper()
            ack_transaction.code = code
            ack_transaction.meaning = _describe(code, TRANSACTION_SET_ACK_CODES)
            ack_transaction.syntax_errors = [
                (c, _describe(c, TRANSACTION_SET_SYNTAX_ERRORS))
                for c in _codes(segment, 2, 6)
            ]

        elif segment.id == "AK9":
            if group is None:
                group = AckGroup(functional_id="", control="")
                groups.append(group)
            code = segment.get(1).strip().upper()
            group.code = code
            group.meaning = _describe(code, GROUP_ACK_CODES)
            group.included = segment.get(2).strip()
            group.received = segment.get(3).strip()
            group.accepted = segment.get(4).strip()
            group.syntax_errors = [
                (c, _describe(c, GROUP_SYNTAX_ERRORS)) for c in _codes(segment, 5, 9)
            ]

    return groups


def _ordinal(number: int) -> str:
    """1 becomes '1st', 12 becomes '12th' — for positions read aloud in prose."""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _codes(segment: Segment, first: int, last: int) -> list[str]:
    """Collect the non-empty codes from a run of repeating elements."""
    found: list[str] = []
    for position in range(first, last + 1):
        value = segment.get(position).strip()
        if value:
            found.append(value)
    return found


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def explain_997(
    data: str | bytes,
    original: str | bytes | None = None,
    *,
    partner: PartnerProfile = DEFAULT_PARTNER,
    source: str | None = None,
) -> Acknowledgment:
    """Decode a 997 into plain English, optionally against the original.

    With ``original``, every position the 997 reports is resolved against that
    document, so a finding names the segment and the value that was objected
    to rather than a number. Pairing is by control number, falling back to the
    only transaction set present when there is exactly one.
    """
    doc = tokenize(data)
    envelope, _structure_findings = parse_envelope(doc)

    groups: list[AckGroup] = []
    for transaction in envelope.transactions:
        if transaction.doc_type == "997":
            groups.extend(decode_ack(transaction, doc))

    ack = Acknowledgment(groups=groups, source=source)

    if not groups:
        ack.findings.append(
            catalog.make_finding(
                "A00",
                "This document contains no 997 transaction set, so there is no "
                "acknowledgment to explain."
                + (
                    f" It looks like a {envelope.doc_types()[0]}."
                    if envelope.doc_types()
                    else ""
                ),
                why=(
                    "The wrong file was passed, or the acknowledgment and the "
                    "business document were swapped."
                ),
                hint=(
                    "Pass the 997 as the first argument and the document it "
                    "acknowledges via --original."
                ),
            )
        )
        return ack

    original_envelope: Envelope | None = None
    if original is not None:
        original_doc = tokenize(original)
        original_envelope, _ = parse_envelope(original_doc)
        ack.paired = _pair(ack, original_envelope)

    ack.findings.extend(_findings_for(ack, original_envelope))
    return ack


def _pair(ack: Acknowledgment, original: Envelope) -> bool:
    """Resolve reported positions against the original document."""
    by_control: dict[str, TransactionSet] = {}
    for transaction in original.transactions:
        control = transaction.control
        if control:
            by_control.setdefault(control, transaction)

    all_transactions = original.transactions
    paired_any = False

    for ack_transaction in ack.transactions:
        target = by_control.get(ack_transaction.control)
        if target is None and len(all_transactions) == 1:
            # A single-document original is unambiguous even when the control
            # numbers do not line up, which is common after a resend.
            target = all_transactions[0]
        if target is None:
            continue

        paired_any = True
        for segment_error in ack_transaction.segment_errors:
            position = segment_error.position
            if position is None or not 1 <= position <= len(target.segments):
                continue
            # AK302 counts from the ST segment as position 1.
            found = target.segments[position - 1]

            # AK301 names the segment the receiver objected to. When the
            # segment at that position is a different one, the two sides are
            # counting differently and every value read from it would be
            # wrong. Better to say so than to name the wrong element.
            if segment_error.segment_id and found.id != segment_error.segment_id:
                segment_error.position_conflict = found.id
                continue

            segment_error.original = found
            for element_error in segment_error.elements:
                if element_error.position is not None:
                    value = found.get(element_error.position)
                    if element_error.component is not None:
                        parts = value.split(":")
                        value = (
                            parts[element_error.component - 1]
                            if 0 < element_error.component <= len(parts)
                            else value
                        )
                    element_error.original_value = value

    return paired_any


def _findings_for(
    ack: Acknowledgment, original: Envelope | None
) -> list[Finding]:
    """Turn the decoded acknowledgment into findings."""
    findings: list[Finding] = []

    known_groups: set[str] = set()
    if original is not None:
        known_groups = {
            group.control for group in original.groups if group.control
        }

    for group in ack.groups:
        # C02 — the group being acknowledged does not exist in the original.
        if known_groups and group.control and group.control not in known_groups:
            findings.append(
                catalog.make_finding(
                    "C02",
                    f"AK102 acknowledges group control number {group.control!r}, "
                    f"but the original document sent "
                    + (
                        f"{', '.join(sorted(known_groups))}."
                        if known_groups
                        else "no groups."
                    ),
                    context={
                        "ak102": group.control,
                        "sent_groups": sorted(known_groups),
                    },
                )
            )

        if group.code and group.status not in ("accepted",):
            severity: Severity = (
                "error" if group.status == "rejected" else "warning"
            )
            findings.append(
                catalog.make_finding(
                    "C03",
                    f"AK901 is {group.code!r}: the functional group "
                    f"{group.control or '?'} was {group.meaning}."
                    + (
                        f" {group.accepted} of {group.received} transaction sets "
                        "were accepted."
                        if group.received and group.accepted
                        else ""
                    ),
                    severity=severity,
                    context={
                        "ak901": group.code,
                        "meaning": group.meaning,
                        "group_control": group.control,
                        "included": group.included,
                        "received": group.received,
                        "accepted": group.accepted,
                    },
                )
            )

        for code, meaning in group.syntax_errors:
            findings.append(
                catalog.make_finding(
                    "C01",
                    f"Functional group error {code}: {meaning}.",
                    severity="error",
                    context={"element": "AK905-AK909", "code": code},
                )
            )

        for ack_transaction in group.transactions:
            findings.extend(_transaction_findings(ack_transaction))

    return findings


def _transaction_findings(ack_transaction: AckTransaction) -> list[Finding]:
    findings: list[Finding] = []
    label = (
        f"{ack_transaction.doc_type or 'transaction set'} "
        f"{ack_transaction.control or '?'}"
    )

    if ack_transaction.code and ack_transaction.status != "accepted":
        findings.append(
            catalog.make_finding(
                "C03",
                f"AK501 is {ack_transaction.code!r}: {label} was "
                f"{ack_transaction.meaning}.",
                severity="error" if not ack_transaction.accepted else "warning",
                context={
                    "ak501": ack_transaction.code,
                    "meaning": ack_transaction.meaning,
                    "doc_type": ack_transaction.doc_type,
                    "control": ack_transaction.control,
                },
            )
        )

    for code, meaning in ack_transaction.syntax_errors:
        # Code 5 says only "one or more segments are in error", which is what
        # the AK3/AK4 detail below spells out. Repeating it adds a finding that
        # points at nothing.
        if code == "5" and ack_transaction.segment_errors:
            continue
        findings.append(
            catalog.make_finding(
                "C01",
                f"Transaction set error {code}: {meaning}.",
                severity="error",
                context={"element": "AK502-AK506", "code": code},
            )
        )

    for segment_error in ack_transaction.segment_errors:
        findings.extend(_segment_findings(segment_error, label))

    return findings


def _segment_findings(error: SegmentError, label: str) -> list[Finding]:
    findings: list[Finding] = []
    original = error.original

    if error.position_conflict is not None:
        findings.append(
            catalog.make_finding(
                "C01",
                f"The 997 says the {_ordinal(error.position or 0)} segment of "
                f"{label} is a {error.segment_id}, but in the document supplied "
                f"that position holds a {error.position_conflict}. The reported "
                "positions could not be resolved.",
                severity="warning",
                why=(
                    "The two sides are counting segments differently, or the "
                    "acknowledgment belongs to a different version of the "
                    "document than the one supplied — a resend after an edit is "
                    "the usual cause."
                ),
                hint=(
                    "Check that --original is the exact document that was sent, "
                    "byte for byte, rather than a regenerated one."
                ),
                context={
                    "ak301": error.segment_id,
                    "ak302": error.position,
                    "found": error.position_conflict,
                },
            )
        )

    # AK302 counts from the ST segment, not from the start of the file, so say
    # so. When the original is paired the finding also carries the absolute
    # position, and two different numbers for one place is worse than none.
    where = (
        f"the {_ordinal(error.position)} segment of the transaction set"
        if error.position
        else "an unnamed segment"
    )
    if error.segment_id:
        where += f", a {error.segment_id}"

    for code, meaning in error.errors:
        # Code 8 says only "this segment has element errors", which the AK4
        # detail below states properly. Repeating it adds noise.
        if code == "8" and error.elements:
            continue
        findings.append(
            catalog.make_finding(
                "C04" if original is not None else "C01",
                f"In {label}, {where}: {meaning}."
                + (f" The segment reads {original.raw!r}." if original else ""),
                severity="error",
                segment_index=original.index if original else None,
                segment_id=error.segment_id or (original.id if original else None),
                line=original.line if original else None,
                context={
                    "ak304": code,
                    "meaning": meaning,
                    "segment_position": error.position,
                    "loop_id": error.loop_id,
                },
            )
        )

    for element_error in error.elements:
        value = element_error.bad_value or element_error.original_value or ""
        detail = f"element {element_error.position:02d}" if element_error.position else "an element"
        if element_error.component:
            detail += f", component {element_error.component:02d}"

        findings.append(
            catalog.make_finding(
                "C04" if original is not None else "C01",
                f"In {label}, {where}, {detail}: {element_error.meaning}"
                + (f" — the value is {value!r}." if value else ".")
                + (
                    f" Data element {element_error.reference}."
                    if element_error.reference
                    else ""
                ),
                severity="error",
                segment_index=original.index if original else None,
                segment_id=error.segment_id or (original.id if original else None),
                element=element_error.position,
                component=element_error.component,
                line=original.line if original else None,
                context={
                    "ak403": element_error.code,
                    "meaning": element_error.meaning,
                    "reference": element_error.reference,
                    "value": value,
                    "segment_position": error.position,
                },
            )
        )

    return findings


def diagnose_997(transaction: TransactionSet, doc: TokenizedDocument) -> list[Finding]:
    """Findings for one 997 transaction set, decoded on its own.

    This is what ``x12doctor check`` produces for a 997: the acknowledgment
    translated into plain English, with positions left numeric because there is
    no original document to resolve them against. ``explain_997`` with an
    ``original`` is the version that names values.
    """
    groups = decode_ack(transaction, doc)
    return _findings_for(Acknowledgment(groups=groups), None)
