# SPDX-License-Identifier: Apache-2.0
"""Content checks, dispatched by transaction set.

A transaction set x12-doctor has no spec for still gets the checks that do not
depend on one — dates, envelope-adjacent content — rather than being skipped.
Diagnosing half of an unknown document beats diagnosing none of it.
"""

from __future__ import annotations

from ..envelope import TransactionSet
from ..partner import PartnerProfile
from ..report import Finding
from ..tokenizer import TokenizedDocument
from . import ack855, asn856, invoice810, po850
from .common import (
    DocSpec,
    check_dates,
    check_implied_decimals,
    check_line_totals,
    check_partner_refs,
    check_required_segments,
    check_units_of_measure,
)

__all__ = ["DOC_NAMES", "SPECS", "check_envelope_dates", "check_transaction", "spec_for"]

#: Transaction sets with a full content spec.
SPECS: dict[str, DocSpec] = {
    po850.SPEC.doc_type: po850.SPEC,
    ack855.SPEC.doc_type: ack855.SPEC,
    asn856.SPEC.doc_type: asn856.SPEC,
    invoice810.SPEC.doc_type: invoice810.SPEC,
}

#: Per-transaction-set checks beyond the shared ones.
_SPECIFIC = {
    "850": po850.check,
    "855": ack855.check,
    "856": asn856.check,
    "810": invoice810.check,
}

#: Names for transaction sets we recognise but do not check in depth, used so
#: that report headers can still say what the document is.
DOC_NAMES: dict[str, str] = {
    "810": "Invoice",
    "820": "Payment Order / Remittance Advice",
    "824": "Application Advice",
    "830": "Planning Schedule",
    "832": "Price/Sales Catalog",
    "846": "Inventory Inquiry/Advice",
    "850": "Purchase Order",
    "855": "Purchase Order Acknowledgment",
    "856": "Ship Notice/Manifest",
    "860": "Purchase Order Change",
    "861": "Receiving Advice",
    "864": "Text Message",
    "865": "Purchase Order Change Acknowledgment",
    "869": "Order Status Inquiry",
    "870": "Order Status Report",
    "875": "Grocery Products Purchase Order",
    "880": "Grocery Products Invoice",
    "940": "Warehouse Shipping Order",
    "943": "Warehouse Stock Transfer Shipment Advice",
    "944": "Warehouse Stock Transfer Receipt Advice",
    "945": "Warehouse Shipping Advice",
    "947": "Warehouse Inventory Adjustment Advice",
    "990": "Response to a Load Tender",
    "997": "Functional Acknowledgment",
    "999": "Implementation Acknowledgment",
}

def check_envelope_dates(doc: TokenizedDocument) -> list[Finding]:
    """B03 over the envelope segments, which sit outside every transaction set.

    ISA09 is six digits while GS04 is eight, and that pairing inside one
    envelope is exactly what a single shared date formatter gets wrong.
    """
    return check_dates(
        segment for segment in doc.segments if segment.id in ("ISA", "GS")
    )


def spec_for(doc_type: str) -> DocSpec | None:
    """The content spec for a transaction set, or ``None`` if there is none."""
    return SPECS.get(doc_type)


def check_transaction(
    transaction: TransactionSet, partner: PartnerProfile
) -> list[Finding]:
    """Run every content check that applies to this transaction set."""
    doc_type = transaction.doc_type
    spec = SPECS.get(doc_type)

    if spec is None:
        # No spec: run only what is spec-independent. A date is a date in any
        # transaction set, and a partner-required REF applies regardless.
        generic = DocSpec(
            doc_type=doc_type,
            name=DOC_NAMES.get(doc_type, "Unknown"),
            functional_id="",
        )
        generic_findings = check_dates(transaction.segments)
        generic_findings.extend(check_partner_refs(transaction, generic, partner))
        return generic_findings

    findings: list[Finding] = []
    findings.extend(check_required_segments(transaction, spec, partner))
    findings.extend(check_units_of_measure(transaction, spec, partner))
    findings.extend(check_dates(transaction.segments))
    findings.extend(check_line_totals(transaction, spec))
    findings.extend(check_partner_refs(transaction, spec, partner))
    findings.extend(check_implied_decimals(transaction, spec))

    specific = _SPECIFIC.get(doc_type)
    if specific is not None:
        findings.extend(specific(transaction))

    return findings
