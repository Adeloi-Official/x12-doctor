# SPDX-License-Identifier: Apache-2.0
"""x12-doctor — diagnose rejected and malformed ANSI X12 documents.

    >>> from x12doctor import diagnose
    >>> report = diagnose(open("rejected_po.edi").read())
    >>> report.ok
    False
    >>> [f.code for f in report.findings]
    ['A02', 'B01']
    >>> print(report.findings[0].message)
    SE01 says 13 segments, but ST through SE actually contains 14 — 1 more than declared.

Nothing here raises on malformed input. A document too broken to parse comes
back as a report saying so, because the files this tool exists for are the ones
that are already wrong.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .catalog import CATALOG, CATEGORIES, Diagnostic
from .diagnose import describe_doc_type, diagnose
from .envelope import Envelope, FunctionalGroup, Interchange, TransactionSet
from .fa997 import (
    AckGroup,
    Acknowledgment,
    AckTransaction,
    ElementError,
    SegmentError,
    explain_997,
)
from .partner import DEFAULT_PARTNER, PartnerProfile, ProfileError
from .report import SCHEMA_VERSION, Finding, Report, Severity
from .scan import ScanEntry, ScanResult, scan
from .tokenizer import Delimiters, Segment, TokenizedDocument, tokenize

__all__ = [
    "__version__",
    # Diagnosis
    "diagnose",
    "explain_997",
    "scan",
    "describe_doc_type",
    # Results
    "Report",
    "Finding",
    "Severity",
    "SCHEMA_VERSION",
    "ScanResult",
    "ScanEntry",
    # Acknowledgments
    "Acknowledgment",
    "AckGroup",
    "AckTransaction",
    "SegmentError",
    "ElementError",
    # Partner profiles
    "PartnerProfile",
    "DEFAULT_PARTNER",
    "ProfileError",
    # The catalog
    "CATALOG",
    "CATEGORIES",
    "Diagnostic",
    # Parsing, for callers that want the structure rather than the diagnosis
    "tokenize",
    "TokenizedDocument",
    "Segment",
    "Delimiters",
    "Envelope",
    "Interchange",
    "FunctionalGroup",
    "TransactionSet",
]
