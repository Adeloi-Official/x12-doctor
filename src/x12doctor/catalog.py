# SPDX-License-Identifier: Apache-2.0
"""The diagnostic catalog — single source of truth for every code.

Every check in x12-doctor resolves its explanatory text from here, the ``codes``
CLI command reads from here, and ``docs/diagnostics.md`` is generated from here.
Adding a diagnosis means adding an entry to :data:`CATALOG` and nothing else.

Text conventions, applied to every entry:

``what``  states the rule in one sentence, present tense.
``why``   explains how a real integration produces this, because knowing the
          cause is usually what actually unblocks the person reading it.
``hint``  is an instruction, not a suggestion. It says what to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .report import Finding, Severity

__all__ = ["CATALOG", "CATEGORIES", "Diagnostic", "codes", "get", "make_finding"]


@dataclass(frozen=True)
class Diagnostic:
    """Static metadata for one diagnostic code."""

    code: str
    title: str
    severity: Severity
    category: str
    what: str
    why: str
    hint: str
    #: Name of the corresponding fault in ``@adeloi/edi-fixtures``, when the
    #: two projects mirror each other. ``None`` for checks with no fixture.
    fixture: str | None = None
    #: Transaction sets this applies to; empty means "any document".
    applies_to: tuple[str, ...] = ()

    @property
    def scope(self) -> str:
        return ", ".join(self.applies_to) if self.applies_to else "any"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "severity": self.severity,
            "category": self.category,
            "what": self.what,
            "why": self.why,
            "hint": self.hint,
            "fixture": self.fixture,
            "applies_to": list(self.applies_to),
        }


CATEGORIES: dict[str, str] = {
    "A": "Structure & envelope",
    "B": "Semantics & content",
    "C": "Acknowledgments (997)",
    "D": "Advance ship notices (856)",
}


def _entries() -> dict[str, Diagnostic]:
    items = [
        # ------------------------------------------------------------------
        # A — Structure & envelope
        # ------------------------------------------------------------------
        Diagnostic(
            code="A00",
            title="Not an X12 interchange",
            severity="error",
            category="A",
            what="The file contains no ISA segment, or no readable segments at all.",
            why=(
                "Usually the wrong file was picked up: an EDIFACT message, a CSV "
                "export, an HTML error page saved by a failed download, or a "
                "zero-byte file left behind by an interrupted transfer."
            ),
            hint=(
                "Confirm the file is the X12 payload itself and not an envelope, "
                "archive or transport artefact wrapping it."
            ),
        ),
        Diagnostic(
            code="A01",
            title="ISA is not 106 characters",
            severity="error",
            category="A",
            what=(
                "The ISA segment must be exactly 106 characters including its "
                "terminator, with every element padded to a fixed width."
            ),
            why=(
                "The ISA is the only fixed-width segment in X12, so code that "
                "builds it by joining trimmed strings produces a short ISA that "
                "looks fine to the eye. Receivers read ISA16 and the terminator "
                "by byte offset, so a single missing space shifts everything."
            ),
            hint=(
                "Pad each ISA element to its defined width with spaces — notably "
                "ISA02 and ISA04 to 10, ISA06 and ISA08 to 15, ISA13 to 9 digits. "
                "Never trim the ISA."
            ),
            fixture="isaTruncated",
        ),
        Diagnostic(
            code="A02",
            title="SE segment count is wrong",
            severity="error",
            category="A",
            what=(
                "SE01 must equal the number of segments from ST through SE "
                "inclusive."
            ),
            why=(
                "The count is written when the transaction set is assembled, then "
                "a segment is added or removed afterwards — a conditional REF, an "
                "extra line item, a stripped-out empty segment — and the count is "
                "never recalculated."
            ),
            hint=(
                "Count ST through SE inclusive and write that number into SE01. "
                "Compute it after the transaction set is complete, never before."
            ),
            fixture="seCountWrong",
        ),
        Diagnostic(
            code="A03",
            title="Control numbers do not match",
            severity="error",
            category="A",
            what=(
                "ISA13 must equal IEA02, GS06 must equal GE02, and ST02 must "
                "equal SE02."
            ),
            why=(
                "The trailer is generated separately from the header, often from a "
                "different counter or a re-read of a sequence table. Any retry or "
                "restart between the two writes leaves the pair out of step."
            ),
            hint=(
                "Carry the control number through in one variable and write the "
                "trailer from that same value rather than fetching it twice."
            ),
            fixture="controlNumberMismatch",
        ),
        Diagnostic(
            code="A04",
            title="Duplicate interchange control number",
            severity="error",
            category="A",
            what=(
                "The same ISA13 appears on more than one interchange from the same "
                "sender within the scanned set."
            ),
            why=(
                "A counter reset after a redeploy, a database restore, or a "
                "re-send that reused the original number. Most receivers reject "
                "duplicates outright as replay protection, so the second document "
                "silently never arrives."
            ),
            hint=(
                "Store the interchange counter where it survives restarts and "
                "never reuse a number, not even for a corrected resend."
            ),
            fixture="duplicateIsaControl",
        ),
        Diagnostic(
            code="A05",
            title="Nonstandard or inconsistent separators",
            severity="warning",
            category="A",
            what=(
                "The element, segment or component separator is unusual, or it "
                "differs from what the ISA declares."
            ),
            why=(
                "Separators are declared per interchange, so anything is legal in "
                "principle — but a hand-edited file, a spreadsheet round trip, or "
                "a concatenation of documents written by two different systems "
                "leaves the declaration and the body disagreeing."
            ),
            hint=(
                "Make the writer take its separators from one place and check them "
                "against ISA16 and the ISA terminator before sending."
            ),
            fixture="nonstandardSeparators",
        ),
        Diagnostic(
            code="A06",
            title="GE transaction set count is wrong",
            severity="error",
            category="A",
            what=(
                "GE01 must equal the number of ST segments in the functional "
                "group."
            ),
            why=(
                "Transaction sets are appended to a group in a loop while GE01 is "
                "written from a count captured before the loop, or from a filtered "
                "list that dropped an empty document."
            ),
            hint="Write GE01 from the length of the list you actually serialised.",
        ),
        Diagnostic(
            code="A07",
            title="IEA functional group count is wrong",
            severity="error",
            category="A",
            what="IEA01 must equal the number of GS segments in the interchange.",
            why=(
                "Same cause as a wrong GE01, one level up: groups are added or "
                "merged after the trailer count was decided."
            ),
            hint="Write IEA01 from the number of groups actually written.",
        ),
        Diagnostic(
            code="A08",
            title="Unexpected version identifier",
            severity="warning",
            category="A",
            what=(
                "ISA12 and GS08 must carry a recognised X12 version, and the two "
                "must describe the same release."
            ),
            why=(
                "Version strings get hard-coded once and then survive an upgrade. "
                "An ISA claiming 00401 under a GS claiming 005010 typically means "
                "the envelope writer and the document writer were upgraded at "
                "different times."
            ),
            hint=(
                "Set ISA12 and GS08 from the same configured version. For 4010 "
                "these are 00401 and 004010 respectively."
            ),
        ),
        Diagnostic(
            code="A09",
            title="Content outside the interchange",
            severity="warning",
            category="A",
            what=(
                "Characters appear before the ISA or after the IEA that are not "
                "part of any segment."
            ),
            why=(
                "A transport header, a shell banner, or a log line got prepended "
                "when the file was written or copied. Strict receivers refuse the "
                "whole interchange on the first unexpected byte."
            ),
            hint=(
                "Write the payload with no wrapper. If a transport adds framing, "
                "strip it before the file reaches the translator."
            ),
        ),
        Diagnostic(
            code="A10",
            title="Envelope is not closed",
            severity="error",
            category="A",
            what="Every ISA needs an IEA, every GS a GE, and every ST an SE.",
            why=(
                "The file was truncated in transit, the writer crashed mid-stream, "
                "or an exception skipped the code that emits the trailer. A "
                "truncated file often still parses far enough to look plausible."
            ),
            hint=(
                "Compare the byte count against what the sender recorded and "
                "request a resend. Emit trailers from a finally block so a partial "
                "document is never written."
            ),
        ),
        Diagnostic(
            code="A11",
            title="Envelope segments out of order",
            severity="error",
            category="A",
            what=(
                "Envelope segments must nest strictly: ISA, GS, ST, business "
                "segments, SE, GE, IEA."
            ),
            why=(
                "Two interchanges were concatenated without their trailers, or a "
                "batch writer interleaved documents. It also happens when files "
                "are merged by a shell script that assumes one document per file."
            ),
            hint=(
                "Split the file back into one interchange per ISA/IEA pair and "
                "check whether the merge step is supposed to exist at all."
            ),
        ),
        # ------------------------------------------------------------------
        # B — Semantics & content
        # ------------------------------------------------------------------
        Diagnostic(
            code="B01",
            title="Nonstandard unit of measure",
            severity="warning",
            category="B",
            what=(
                "Unit of measure elements must carry a two-character X12 code, "
                "not a spelled-out word."
            ),
            why=(
                "The value came straight from an ERP field that stores display "
                "text, so a spelled-out unit passes internal validation — "
                "internally it was never a code at all."
            ),
            hint=(
                "Map the internal unit to its X12 code at the boundary and reject "
                "unmapped values instead of passing them through."
            ),
            fixture="uomNonstandard",
        ),
        Diagnostic(
            code="B02",
            title="Suspected implied decimal error",
            severity="warning",
            category="B",
            what=(
                "A monetary or quantity value looks scaled by a factor of 100 or "
                "1000 relative to its neighbours."
            ),
            why=(
                "Some partners expect implied decimals, where 1000 means 10.00. "
                "Mixing a system that applies the convention with one that does "
                "not multiplies or divides every amount on the document."
            ),
            hint=(
                "Confirm the partner's decimal convention, then apply it in one "
                "place. Cross-check the line extended price against quantity times "
                "unit price."
            ),
            fixture="impliedDecimals",
        ),
        Diagnostic(
            code="B03",
            title="Wrong date format",
            severity="error",
            category="B",
            what=(
                "Date elements must match the length their version defines: "
                "six digits (YYMMDD) or eight (CCYYMMDD), depending on the element."
            ),
            why=(
                "The 4010 release mixes both widths across segments — ISA09 is "
                "six digits while DTM02 is eight — so a single shared date "
                "formatter emits the wrong width somewhere. Y2K-era code that "
                "was never revisited produces the same result."
            ),
            hint=(
                "Format each date from its element definition rather than from one "
                "global pattern. Do not zero-pad a short year into a long one."
            ),
            fixture="dateFormatShort",
        ),
        Diagnostic(
            code="B04",
            title="CTT totals do not match",
            severity="error",
            category="B",
            what=(
                "CTT01 must equal the number of line items, and CTT02 when present "
                "must equal the sum of the quantities."
            ),
            why=(
                "Line items are filtered after the totals are computed — cancelled "
                "lines removed, zero-quantity lines dropped — and CTT is not "
                "recomputed. It is the check most translators run first."
            ),
            hint=(
                "Compute CTT from the serialised line items as the last step "
                "before writing the trailer."
            ),
            fixture="cttMismatch",
        ),
        Diagnostic(
            code="B05",
            title="Required partner reference is missing",
            severity="error",
            category="B",
            what=(
                "A REF qualifier the partner profile marks as required does not "
                "appear in the document."
            ),
            why=(
                "The reference is optional in the X12 standard but mandatory in "
                "the partner's implementation guide, so nothing but the partner's "
                "own validator catches it. Often the source field is simply empty "
                "for a subset of orders."
            ),
            hint=(
                "Add the qualifier to the document and make the missing source "
                "value a hard failure upstream rather than an omitted segment."
            ),
            fixture="missingRef",
        ),
        Diagnostic(
            code="B06",
            title="Required segment is missing",
            severity="error",
            category="B",
            what=(
                "A segment mandatory for this transaction set is absent — BEG in "
                "an 850, BIG in an 810, BSN in an 856, BAK in an 855."
            ),
            why=(
                "A conditional branch in the writer skipped it, or the document "
                "was assembled from a template that predates the segment being "
                "required."
            ),
            hint=(
                "Add the segment. Validate the required set before serialising so "
                "the document is never produced without it."
            ),
        ),
        # ------------------------------------------------------------------
        # C — Acknowledgments (997)
        # ------------------------------------------------------------------
        Diagnostic(
            code="C01",
            title="997 reports errors",
            severity="info",
            category="C",
            what=(
                "The functional acknowledgment carries AK3, AK4, AK5 or AK9 codes "
                "describing what the receiver objected to."
            ),
            why=(
                "This is the receiver telling you why your document did not make "
                "it through. The codes are numeric and undocumented in the file "
                "itself, which is why a 997 is usually forwarded to a human "
                "unread."
            ),
            hint=(
                "Read the decoded reason below, fix the named segment or element "
                "in the original document, and resend under a new control number."
            ),
            applies_to=("997",),
        ),
        Diagnostic(
            code="C02",
            title="997 references an unknown group",
            severity="error",
            category="C",
            what=(
                "AK102 does not correspond to any functional group control number "
                "that was sent."
            ),
            why=(
                "The acknowledgment belongs to a different sender, the group "
                "counter was reset so the number is ambiguous, or the 997 was "
                "generated against a document that never left the outbox."
            ),
            hint=(
                "Match AK101/AK102 against your sent-group log. If nothing "
                "matches, the acknowledgment was misrouted — check the ISA sender "
                "and receiver IDs on both documents."
            ),
            fixture="fa997UnknownGroup",
            applies_to=("997",),
        ),
        Diagnostic(
            code="C03",
            title="997 rejected the document",
            severity="error",
            category="C",
            what=(
                "AK501 or AK901 reports status R (rejected) or E (accepted with "
                "errors), so the document was not processed as sent."
            ),
            why=(
                "The receiver's translator found a structural or syntactic problem "
                "it would not accept. Nothing downstream of it ever saw the "
                "document, which is why the order appears to have vanished."
            ),
            hint=(
                "Fix the segments named in the AK3/AK4 detail, then resend with a "
                "new ST02 and GS06. Do not resend the identical control numbers."
            ),
            fixture="fa997Rejected",
            applies_to=("997",),
        ),
        Diagnostic(
            code="C04",
            title="Rejection located in the original document",
            severity="error",
            category="C",
            what=(
                "An AK3/AK4 position was resolved against the original document, "
                "naming the exact segment and element the receiver objected to."
            ),
            why=(
                "The 997 reports positions, not content, so on its own it says "
                "'segment 14, element 3' and nothing more. Pairing it with the "
                "original turns that into the actual value that was rejected."
            ),
            hint=(
                "Correct the value shown here in the source system, not just in "
                "the outbound file, or the next document will repeat it."
            ),
            applies_to=("997",),
        ),
        Diagnostic(
            code="C05",
            title="No acknowledgment received",
            severity="warning",
            category="C",
            what=(
                "An interchange requested an acknowledgment (ISA14 = 1) but no "
                "997 in the scanned set references its group."
            ),
            why=(
                "The document never reached the partner, or their acknowledgment "
                "never reached you. Both look identical from your side, and both "
                "are usually noticed only when someone asks about a missing order."
            ),
            hint=(
                "Check the transport logs for the interchange, then ask the "
                "partner whether they received the group. Alert on unacknowledged "
                "interchanges rather than waiting for the phone call."
            ),
            fixture="fa997Missing",
        ),
        Diagnostic(
            code="C06",
            title="Interchanges out of chronological order",
            severity="warning",
            category="C",
            what=(
                "Documents in one business flow carry envelope timestamps that "
                "contradict the order the flow must happen in — an ASN stamped "
                "before the acknowledgment of the order it ships."
            ),
            why=(
                "Documents are generated in a batch that stamps them all at run "
                "time rather than at the moment of the business event, or two "
                "systems in the flow disagree about the time zone. Receivers that "
                "process by envelope timestamp then apply them in the wrong order."
            ),
            hint=(
                "Stamp ISA09/ISA10 and GS04/GS05 at the moment the document is "
                "produced, in UTC, and never backdate a resend."
            ),
            fixture="outOfOrderInterchange",
        ),
        # ------------------------------------------------------------------
        # D — Advance ship notices (856)
        # ------------------------------------------------------------------
        Diagnostic(
            code="D01",
            title="Broken HL hierarchy",
            severity="error",
            category="D",
            what=(
                "Every HL02 parent reference must point at an HL01 that appears "
                "earlier in the document, and HL numbering must be sequential."
            ),
            why=(
                "The shipment tree is built recursively and a level is emitted "
                "before its parent, or a pack level is dropped for single-item "
                "shipments while its children keep pointing at it. The item then "
                "belongs to nothing."
            ),
            hint=(
                "Number HL01 sequentially from 1 in document order and set HL02 "
                "from the parent's already-assigned number."
            ),
            fixture="hlOrphan",
            applies_to=("856",),
        ),
        Diagnostic(
            code="D02",
            title="Invalid SSCC-18 check digit",
            severity="error",
            category="D",
            what=(
                "An SSCC-18 in a MAN segment fails its GS1 mod-10 check digit, or "
                "is not 18 digits long."
            ),
            why=(
                "The check digit was computed with the wrong weighting, or the "
                "label number was truncated or zero-padded on the way into the "
                "document. Receiving docks scan the physical label, so a mismatch "
                "stops the pallet at the door."
            ),
            hint=(
                "Recompute the check digit over the first 17 digits with weights "
                "3 and 1 from the right, and verify the value matches the printed "
                "label."
            ),
            fixture="ssccInvalid",
            applies_to=("856",),
        ),
        Diagnostic(
            code="D03",
            title="Shipped quantity does not match the order",
            severity="error",
            category="D",
            what=(
                "A quantity in the 856 differs from the quantity ordered for the "
                "same line in the referenced 850."
            ),
            why=(
                "A partial shipment was sent without splitting the line, units and "
                "cases were mixed up, or the ASN was built from the pick list "
                "while the order was later amended."
            ),
            hint=(
                "Reconcile against the purchase order before sending. For genuine "
                "partial shipments, ship the correct quantity and let the balance "
                "follow on its own ASN."
            ),
            fixture="asnQtyMismatch",
            applies_to=("856",),
        ),
        Diagnostic(
            code="D04",
            title="Implausible shipment dates",
            severity="warning",
            category="D",
            what=(
                "The ASN's dates are not in a possible order — shipped after "
                "delivered, or an ASN issued after the goods arrived."
            ),
            why=(
                "The ASN is generated in a nightly batch rather than at dispatch, "
                "so it is stamped later than the delivery it announces. Time zones "
                "applied to only one of the two dates do the same thing."
            ),
            hint=(
                "Send the ASN at dispatch, and stamp every DTM from the same clock "
                "and time zone."
            ),
            fixture="asnAfterDelivery",
            applies_to=("856",),
        ),
    ]
    return {item.code: item for item in items}


CATALOG: dict[str, Diagnostic] = _entries()


def get(code: str) -> Diagnostic:
    """Look up a diagnostic, raising a clear error for an unknown code."""
    try:
        return CATALOG[code.upper()]
    except KeyError:
        raise KeyError(f"unknown diagnostic code: {code!r}") from None


def codes() -> list[str]:
    """All codes in catalog order."""
    return list(CATALOG)


def make_finding(
    code: str,
    message: str,
    *,
    severity: Severity | None = None,
    segment_index: int | None = None,
    segment_id: str | None = None,
    element: int | None = None,
    component: int | None = None,
    line: int | None = None,
    why: str | None = None,
    hint: str | None = None,
    context: dict[str, Any] | None = None,
) -> Finding:
    """Build a finding, filling explanatory text from the catalog.

    ``severity``, ``why`` and ``hint`` may be overridden by a check that has
    more specific knowledge than the catalog entry — an otherwise-fatal problem
    downgraded because a partner profile allows it, for instance.
    """
    entry = get(code)
    return Finding(
        code=entry.code,
        severity=severity or entry.severity,
        message=message,
        why=why if why is not None else entry.why,
        hint=hint if hint is not None else entry.hint,
        segment_index=segment_index,
        segment_id=segment_id,
        element=element,
        component=component,
        line=line,
        context=dict(context) if context else {},
    )
