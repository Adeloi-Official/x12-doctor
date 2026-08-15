# SPDX-License-Identifier: Apache-2.0
"""810 Invoice."""

from __future__ import annotations

from decimal import Decimal

from .. import catalog
from ..envelope import TransactionSet
from ..report import Finding
from .common import DocSpec, format_decimal, parse_decimal

__all__ = ["SPEC", "check"]

SPEC = DocSpec(
    doc_type="810",
    name="Invoice",
    functional_id="IN",
    required_segments=("BIG", "TDS"),
    line_segment="IT1",
    qty_element=2,
    uom_element=3,
    price_element=4,
    # TDS01 is the invoice total in cents. It is the one place implied decimals
    # are correct by definition, which makes it a reliable cross-check for the
    # line prices that surround it.
    total_element=("TDS", 1),
)


def check(transaction: TransactionSet) -> list[Finding]:
    """810-specific content checks."""
    findings: list[Finding] = []

    big = transaction.first("BIG")
    if big is not None:
        if not big.get(2).strip():
            findings.append(
                catalog.make_finding(
                    "B06",
                    "BIG02 carries no invoice number.",
                    segment_index=big.index,
                    segment_id="BIG",
                    element=2,
                    line=big.line,
                    why=(
                        "Accounts payable files by invoice number. Without it the "
                        "invoice cannot be entered, and it will not be paid."
                    ),
                    hint="Populate BIG02 with the invoice number.",
                    context={"element": "BIG02"},
                )
            )
        if not big.get(4).strip():
            findings.append(
                catalog.make_finding(
                    "B06",
                    "BIG04 carries no purchase order number.",
                    segment_index=big.index,
                    segment_id="BIG",
                    element=4,
                    line=big.line,
                    severity="warning",
                    why=(
                        "Without the PO number the invoice cannot be matched "
                        "automatically against the order and the receipt. It goes "
                        "to a human, who will eventually ask for it."
                    ),
                    hint="Copy the PO number from the original 850 into BIG04.",
                    context={"element": "BIG04"},
                )
            )

    findings.extend(_check_total(transaction))
    return findings


def _check_total(transaction: TransactionSet) -> list[Finding]:
    """TDS01 must equal the sum of the line extensions, in cents.

    Only the arithmetic mismatches that are not an implied-decimal scaling are
    reported here — the factor-of-100 case is B02 and is raised by the shared
    check, which has more to say about it.
    """
    tds = transaction.first("TDS")
    lines = transaction.by_id("IT1")
    if tds is None or not lines:
        return []

    declared = parse_decimal(tds.get(1))
    if declared is None:
        return [
            catalog.make_finding(
                "B04",
                f"TDS01 is {tds.get(1)!r}, which is not an amount.",
                segment_index=tds.index,
                segment_id="TDS",
                element=1,
                line=tds.line,
                why=(
                    "The total is written from a formatted string somewhere in "
                    "the pipeline and picked up a currency symbol or a thousands "
                    "separator on the way."
                ),
                hint=(
                    "Write TDS01 as an integer number of cents with no symbols, "
                    "separators or decimal point."
                ),
                context={"value": tds.get(1)},
            )
        ]

    extended = Decimal(0)
    for line in lines:
        quantity = parse_decimal(line.get(2))
        price = parse_decimal(line.get(4))
        if quantity is None or price is None:
            return []
        extended += quantity * price

    expected = extended * 100
    if declared == expected:
        return []

    # A clean factor of 100 in either direction is the implied-decimal problem,
    # which B02 explains far better than a bare total mismatch would.
    if expected != 0:
        ratio = declared / expected
        if ratio in (Decimal(100), Decimal(1) / Decimal(100)):
            return []

    difference = (declared - expected) / 100
    return [
        catalog.make_finding(
            "B04",
            f"TDS01 declares a total of {format_decimal(declared / 100)}, but the line "
            f"items add up to {format_decimal(extended)} — a difference of "
            f"{format_decimal(abs(difference))}.",
            segment_index=tds.index,
            segment_id="TDS",
            element=1,
            line=tds.line,
            why=(
                "Charges, allowances or taxes were added to the total without a "
                "matching SAC or TXI segment, or a line was amended after the "
                "total was computed. Either way the receiver's three-way match "
                "fails and the invoice stops."
            ),
            hint=(
                "Recompute TDS01 from the serialised line items as the last step, "
                "and carry any charge or allowance in its own SAC segment so the "
                "receiver can see where the difference comes from."
            ),
            context={
                "declared_total": format_decimal(declared / 100),
                "line_total": format_decimal(extended),
                "difference": format_decimal(difference),
            },
        )
    ]
