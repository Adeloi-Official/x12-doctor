# SPDX-License-Identifier: Apache-2.0
"""The diagnosis pipeline: tokenize, structure, envelope, content."""

from __future__ import annotations

from . import catalog, semantics
from .envelope import Envelope, check_envelope, parse_envelope
from .fa997 import diagnose_997
from .partner import DEFAULT_PARTNER, PartnerProfile
from .report import Finding, Report
from .semantics import DOC_NAMES
from .tokenizer import TokenizedDocument, tokenize

__all__ = ["describe_doc_type", "diagnose", "diagnose_document"]


def describe_doc_type(doc_type: str) -> str:
    """``"850"`` becomes ``"850 Purchase Order"``, unknown types pass through."""
    if not doc_type or doc_type == "unknown":
        return "unknown document type"
    name = DOC_NAMES.get(doc_type)
    return f"{doc_type} {name}" if name else doc_type


def diagnose(
    data: str | bytes,
    *,
    partner: PartnerProfile = DEFAULT_PARTNER,
    source: str | None = None,
) -> Report:
    """Diagnose one X12 document and return everything found.

    This never raises on malformed input. A file that is not X12 at all comes
    back as a report holding a single A00 finding, which is what lets the CLI
    distinguish "unreadable" from "readable but wrong".
    """
    doc = tokenize(data)
    envelope, structure_findings = parse_envelope(doc)
    return diagnose_document(
        doc,
        envelope,
        structure_findings,
        partner=partner,
        source=source,
    )


def diagnose_document(
    doc: TokenizedDocument,
    envelope: Envelope,
    structure_findings: list[Finding],
    *,
    partner: PartnerProfile = DEFAULT_PARTNER,
    source: str | None = None,
) -> Report:
    """Diagnose an already-tokenized document.

    Used by the directory scan, which tokenizes and parses once and then needs
    both the per-file report and the envelope tree for its cross-file checks.
    Parsing the envelope twice would double the structural findings.
    """
    findings = _tokenizer_findings(doc)

    # A00 means this is not an X12 interchange at all. Everything downstream
    # would then be reporting on lines of a CSV as though they were segments,
    # which buries the one finding that matters under noise.
    if any(finding.code == "A00" for finding in findings):
        report = _build_report(doc, envelope, findings, source)
        report.sort()
        return report

    findings.extend(structure_findings)
    findings.extend(check_envelope(envelope, doc))
    findings.extend(semantics.check_envelope_dates(doc))
    findings.extend(_content_findings(envelope, doc, partner))
    findings.extend(_partner_envelope_findings(envelope, partner))

    report = _build_report(doc, envelope, findings, source)
    report.sort()
    return report


def _tokenizer_findings(doc: TokenizedDocument) -> list[Finding]:
    """Promote the tokenizer's issues into catalog findings."""
    findings: list[Finding] = []
    for code, message, segment_index in doc.issues:
        segment = (
            doc.segments[segment_index]
            if segment_index is not None and segment_index < len(doc.segments)
            else None
        )
        findings.append(
            catalog.make_finding(
                code,
                message,
                segment_index=segment.index if segment else None,
                segment_id=segment.id if segment else None,
                line=segment.line if segment else None,
            )
        )
    return findings


def _content_findings(
    envelope: Envelope, doc: TokenizedDocument, partner: PartnerProfile
) -> list[Finding]:
    """Run the content checks for every transaction set in the document."""
    findings: list[Finding] = []

    for transaction in envelope.transactions:
        if transaction.doc_type == "997":
            # A 997 is diagnosed by decoding what it says, not by checking its
            # content against a spec. Without the original document the
            # positions stay numeric, which the findings say plainly.
            findings.extend(diagnose_997(transaction, doc))
            continue

        findings.extend(semantics.check_transaction(transaction, partner))

    return findings


def _partner_envelope_findings(
    envelope: Envelope, partner: PartnerProfile
) -> list[Finding]:
    """Envelope expectations that only a partner profile can supply."""
    if partner.is_empty:
        return []

    findings: list[Finding] = []
    for interchange in envelope.interchanges:
        isa = interchange.isa
        if isa.id != "ISA":
            continue

        checks = (
            (5, partner.expected_sender_qualifier, interchange.sender_qualifier,
             "sender ID qualifier"),
            (6, partner.sender_id, interchange.sender, "sender ID"),
            (7, partner.expected_receiver_qualifier,
             interchange.receiver_qualifier, "receiver ID qualifier"),
            (8, partner.receiver_id, interchange.receiver, "receiver ID"),
            (15, partner.usage_indicator, isa.get(15).strip(), "usage indicator"),
        )

        for position, expected, actual, label in checks:
            if not expected or expected == actual:
                continue
            findings.append(
                catalog.make_finding(
                    "B05",
                    f"ISA{position:02d} carries {actual!r} as the {label}, but "
                    f"the partner profile expects {expected!r}.",
                    segment_index=isa.index,
                    segment_id="ISA",
                    element=position,
                    line=isa.line,
                    why=(
                        "Interchange IDs are how the partner routes the file. A "
                        "mismatch means it either bounces at the VAN or lands in "
                        "the wrong trading relationship."
                    ),
                    hint=(
                        f"Set ISA{position:02d} to {expected!r}, and check "
                        "whether the environment configuration was picked up at "
                        "all."
                    ),
                    context={
                        "element": f"ISA{position:02d}",
                        "expected": expected,
                        "actual": actual,
                    },
                )
            )

        if partner.usage_indicator == "P" and interchange.is_test:
            findings.append(
                catalog.make_finding(
                    "B05",
                    "ISA15 marks this interchange as test data, but the partner "
                    "profile is configured for production.",
                    segment_index=isa.index,
                    segment_id="ISA",
                    element=15,
                    line=isa.line,
                    severity="error",
                    why=(
                        "A test flag on a production document is silently "
                        "discarded by most receivers. Nothing is rejected and "
                        "nothing arrives."
                    ),
                    hint="Set ISA15 to 'P' for production traffic.",
                    context={"element": "ISA15", "value": "T"},
                )
            )

        if partner.version:
            for group in interchange.groups:
                if group.version and not group.version.startswith(partner.version):
                    findings.append(
                        catalog.make_finding(
                            "A08",
                            f"GS08 is {group.version!r}, but the partner profile "
                            f"expects {partner.version!r}.",
                            segment_index=group.gs.index,
                            segment_id="GS",
                            element=8,
                            line=group.gs.line,
                            context={
                                "expected": partner.version,
                                "actual": group.version,
                            },
                        )
                    )

    return findings


def _build_report(
    doc: TokenizedDocument,
    envelope: Envelope,
    findings: list[Finding],
    source: str | None,
) -> Report:
    doc_types = envelope.doc_types()
    version = None
    for group in envelope.groups:
        if group.version:
            version = group.version
            break
    if version is None:
        for interchange in envelope.interchanges:
            if interchange.version:
                version = interchange.version
                break

    return Report(
        findings=findings,
        doc_type=doc_types[0] if doc_types else "unknown",
        doc_types=doc_types,
        version=version,
        source=source,
        delimiters=doc.delimiters.describe() if doc.segments else None,
        segment_count=len(doc.segments),
    )
