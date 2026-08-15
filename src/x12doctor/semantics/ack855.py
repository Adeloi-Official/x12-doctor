# SPDX-License-Identifier: Apache-2.0
"""855 Purchase Order Acknowledgment."""

from __future__ import annotations

from .. import catalog
from ..envelope import TransactionSet
from ..report import Finding
from ..tokenizer import Segment
from .common import DocSpec

__all__ = ["SPEC", "check"]

SPEC = DocSpec(
    doc_type="855",
    name="Purchase Order Acknowledgment",
    functional_id="PR",
    required_segments=("BAK",),
    line_segment="PO1",
    qty_element=2,
    uom_element=3,
    price_element=4,
)

#: BAK02 Acknowledgment Type Code.
_ACK_TYPES = {
    "AC": "Acknowledge — With Detail and Change",
    "AD": "Acknowledge — With Detail, No Change",
    "AE": "Acknowledge — With Exception Detail Only",
    "AH": "Acknowledge — Hold Status",
    "AK": "Acknowledge — No Detail or Change",
    "AP": "Acknowledge — Product Replaced",
    "AT": "Accepted",
    "NA": "No Acknowledgment Needed",
    "RD": "Reject with Detail",
    "RF": "Reject with Exception Detail Only",
    "RJ": "Rejected — No Detail",
    "RO": "Reject Order",
    "ZZ": "Mutually Defined",
}

#: ACK01 Line Item Status Code, the values that appear in practice.
_LINE_STATUS = {
    "AC": "Item Accepted and Shipped",
    "AR": "Item Accepted and Released for Shipment",
    "IA": "Item Accepted",
    "IB": "Item Backordered",
    "IC": "Item Accepted — Changes Made",
    "ID": "Item Deleted",
    "IE": "Item Accepted, Price Pending",
    "IF": "Item on Hold, Incomplete Description",
    "IH": "Item on Hold",
    "IP": "Item Accepted — Partial Shipment",
    "IQ": "Item Accepted — Quantity Changed",
    "IR": "Item Rejected",
    "IS": "Item Accepted — Substitution Made",
    "IW": "Item on Hold, Waiver Required",
    "R1": "Item Rejected, Not a Contract Item",
    "R2": "Item Rejected, Invalid Item Product Number",
    "R3": "Item Rejected, Invalid Unit of Issue",
}


def check(transaction: TransactionSet) -> list[Finding]:
    """855-specific content checks."""
    findings: list[Finding] = []

    bak = transaction.first("BAK")
    if bak is not None:
        ack_type = bak.get(2).strip()
        if ack_type and ack_type not in _ACK_TYPES:
            findings.append(
                catalog.make_finding(
                    "B06",
                    f"BAK02 is {ack_type!r}, which is not an acknowledgment type "
                    "code.",
                    segment_index=bak.index,
                    segment_id="BAK",
                    element=2,
                    line=bak.line,
                    why=(
                        "BAK02 is what tells the buyer whether the order was "
                        "accepted at all. An unknown code leaves the order in an "
                        "indeterminate state on their side."
                    ),
                    hint=(
                        "Use AC when acknowledging with detail and changes, AD "
                        "when acknowledging unchanged, RJ when rejecting."
                    ),
                    context={"element": "BAK02", "value": ack_type},
                )
            )

        if not bak.get(3).strip():
            findings.append(
                catalog.make_finding(
                    "B06",
                    "BAK03 carries no purchase order number, so this "
                    "acknowledgment cannot be matched to an order.",
                    segment_index=bak.index,
                    segment_id="BAK",
                    element=3,
                    line=bak.line,
                    why=(
                        "The acknowledgment exists to close the loop on a "
                        "specific order. Without the number it is filed against "
                        "nothing and the buyer's order stays unacknowledged."
                    ),
                    hint="Copy BEG03 from the original 850 into BAK03.",
                    context={"element": "BAK03"},
                )
            )

    findings.extend(_check_line_status(transaction))
    return findings


def _check_line_status(transaction: TransactionSet) -> list[Finding]:
    """Every PO1 needs an ACK, and every ACK01 must be a known status."""
    findings: list[Finding] = []

    # ACK segments follow the PO1 they describe, so walk the body in order and
    # remember whether the line we are inside has been answered.
    current: Segment | None = None
    answered = True

    for segment in transaction.segments:
        if segment.id == "PO1":
            if current is not None and not answered:
                findings.extend(_unanswered(current))
            current = segment
            answered = False
        elif segment.id == "ACK":
            answered = True
            status = segment.get(1).strip()
            if status and status not in _LINE_STATUS:
                findings.append(
                    catalog.make_finding(
                        "B06",
                        f"ACK01 is {status!r}, which is not a line item status code.",
                        segment_index=segment.index,
                        segment_id="ACK",
                        element=1,
                        line=segment.line,
                        why=(
                            "The buyer's system branches on this code to decide "
                            "whether to expect the goods. An unknown value is "
                            "usually treated as a rejection."
                        ),
                        hint=(
                            "Use IA for an accepted item, IQ when the quantity "
                            "changed, IR when the item is rejected."
                        ),
                        context={"element": "ACK01", "value": status},
                    )
                )

    if current is not None and not answered:
        findings.extend(_unanswered(current))

    return findings


def _unanswered(po1: Segment) -> list[Finding]:
    return [
        catalog.make_finding(
            "B06",
            f"Line {po1.get(1).strip() or '?'} has no ACK segment, so the "
            "acknowledgment says nothing about it.",
            segment_index=po1.index,
            segment_id="PO1",
            line=po1.line,
            severity="warning",
            why=(
                "The buyer reads line status per line. A line with no ACK is "
                "neither accepted nor rejected, and most systems leave it open "
                "indefinitely rather than raising anything."
            ),
            hint="Emit one ACK segment for every PO1 in the acknowledgment.",
            context={"line_number": po1.get(1).strip()},
        )
    ]
