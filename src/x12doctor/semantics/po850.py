# SPDX-License-Identifier: Apache-2.0
"""850 Purchase Order."""

from __future__ import annotations

from .. import catalog
from ..envelope import TransactionSet
from ..report import Finding
from .common import DocSpec

__all__ = ["SPEC", "check"]

SPEC = DocSpec(
    doc_type="850",
    name="Purchase Order",
    functional_id="PO",
    required_segments=("BEG",),
    line_segment="PO1",
    qty_element=2,
    uom_element=3,
    price_element=4,
)

#: BEG01 Transaction Set Purpose Code, restricted to the values that make sense
#: on a purchase order. The full element 353 list covers every transaction set.
_PURPOSE_CODES = {
    "00": "Original",
    "01": "Cancellation",
    "04": "Change",
    "05": "Replace",
    "06": "Confirmation",
    "07": "Duplicate",
    "22": "Information Copy",
    "ZZ": "Mutually Defined",
}

#: BEG02 Purchase Order Type Code, the subset in everyday use.
_ORDER_TYPES = {
    "BK": "Blanket Order",
    "BE": "Blanket Order — Estimated Quantity",
    "CF": "Confirmation",
    "DR": "Direct Ship",
    "KN": "Kanban",
    "NE": "New Order",
    "NP": "New Product",
    "OS": "Special Order",
    "RE": "Reorder",
    "RL": "Release Order",
    "RO": "Rush Order",
    "SA": "Stand-alone Order",
    "SS": "Supply/Service Order",
    "ZZ": "Mutually Defined",
}


def check(transaction: TransactionSet) -> list[Finding]:
    """850-specific content checks beyond the shared ones."""
    findings: list[Finding] = []
    beg = transaction.first("BEG")
    if beg is None:
        return findings

    purpose = beg.get(1).strip()
    if purpose and purpose not in _PURPOSE_CODES:
        findings.append(
            catalog.make_finding(
                "B06",
                f"BEG01 is {purpose!r}, which is not a transaction set purpose "
                "code an 850 uses.",
                segment_index=beg.index,
                segment_id="BEG",
                element=1,
                line=beg.line,
                why=(
                    "The purpose code tells the receiver whether this is a new "
                    "order, a change or a cancellation. An unrecognised value "
                    "usually means an internal status code leaked into it."
                ),
                hint=(
                    "Use 00 for an original order, 04 for a change, 01 for a "
                    "cancellation."
                ),
                context={"element": "BEG01", "value": purpose},
            )
        )

    order_type = beg.get(2).strip()
    if order_type and order_type not in _ORDER_TYPES:
        findings.append(
            catalog.make_finding(
                "B06",
                f"BEG02 is {order_type!r}, which is not a purchase order type code.",
                segment_index=beg.index,
                segment_id="BEG",
                element=2,
                line=beg.line,
                why=(
                    "Partners route on the order type, so an unknown value often "
                    "means the document lands in a manual review queue."
                ),
                hint="Use NE for a new order, RL for a release against a blanket.",
                context={"element": "BEG02", "value": order_type},
            )
        )

    if not beg.get(3).strip():
        findings.append(
            catalog.make_finding(
                "B06",
                "BEG03 carries no purchase order number.",
                segment_index=beg.index,
                segment_id="BEG",
                element=3,
                line=beg.line,
                why=(
                    "The PO number is how every later document in the flow — "
                    "the acknowledgment, the ASN, the invoice — refers back to "
                    "this order. Without it nothing can be matched."
                ),
                hint="Populate BEG03 with the purchase order number.",
                context={"element": "BEG03"},
            )
        )

    findings.extend(_check_line_numbering(transaction))
    return findings


def _check_line_numbering(transaction: TransactionSet) -> list[Finding]:
    """PO101 identifies the line and must be unique within the order."""
    findings: list[Finding] = []
    seen: dict[str, int] = {}

    for segment in transaction.by_id("PO1"):
        number = segment.get(1).strip()
        if not number:
            continue
        if number in seen:
            findings.append(
                catalog.make_finding(
                    "B06",
                    f"Line number {number!r} is used twice, at segment "
                    f"{seen[number] + 1} and segment {segment.index + 1}.",
                    segment_index=segment.index,
                    segment_id="PO1",
                    element=1,
                    line=segment.line,
                    why=(
                        "Line numbers are the key the acknowledgment and the ASN "
                        "use to refer back to a line. Duplicates make the "
                        "reference ambiguous, and receivers usually keep only "
                        "one of the two lines."
                    ),
                    hint="Number the lines sequentially from 1, without reuse.",
                    context={"line_number": number},
                )
            )
        else:
            seen[number] = segment.index

    return findings
