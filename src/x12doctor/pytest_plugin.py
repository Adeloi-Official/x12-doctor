# SPDX-License-Identifier: Apache-2.0
"""A pytest plugin for asserting on X12 documents.

Installed as a pytest11 entry point, so it is available as soon as x12-doctor
is on the path — no conftest wiring needed.

It exists to close the loop with ``@adeloi/edi-fixtures``: that library
generates the documents, this one checks them, and an integration test can
assert on the result without hand-rolling the comparison::

    from x12doctor.pytest_plugin import assert_valid_x12, assert_diagnoses

    def test_our_writer_emits_valid_orders():
        assert_valid_x12(build_purchase_order(order))

    def test_we_notice_a_bad_unit():
        assert_diagnoses(document_with_spelled_out_uom, "B01")

Failures print the findings rather than a bare ``assert False``, because the
point of a failing EDI assertion is to say what is wrong with the document.
"""

from __future__ import annotations

from .catalog import get as get_diagnostic
from .diagnose import diagnose
from .partner import DEFAULT_PARTNER, PartnerProfile
from .report import Report

__all__ = [
    "assert_diagnoses",
    "assert_no_diagnosis",
    "assert_valid_x12",
    "describe",
]


def describe(report: Report) -> str:
    """Render a report as a compact assertion message."""
    if not report.findings:
        return "no findings"
    lines = [
        f"{len(report.findings)} finding"
        f"{'' if len(report.findings) == 1 else 's'} in "
        f"{report.source or 'the document'}:"
    ]
    for finding in report.findings:
        lines.append(
            f"  {finding.severity.upper():<7} {finding.code}  "
            f"{finding.location}: {finding.message}"
        )
    return "\n".join(lines)


def assert_valid_x12(
    data: str | bytes,
    *,
    partner: PartnerProfile = DEFAULT_PARTNER,
    allow_warnings: bool = True,
    source: str | None = None,
) -> Report:
    """Assert a document has no errors, and return the report.

    Warnings are tolerated by default: a nonstandard unit of measure is worth
    knowing about but is not usually what a test about document generation
    should fail on. Pass ``allow_warnings=False`` to demand a clean report.
    """
    report = diagnose(data, partner=partner, source=source)

    offending = report.errors if allow_warnings else report.findings
    if offending:
        raise AssertionError(
            "expected a valid X12 document, but " + describe(report)
        )
    return report


def assert_diagnoses(
    data: str | bytes,
    *codes: str,
    partner: PartnerProfile = DEFAULT_PARTNER,
    exactly: bool = False,
    source: str | None = None,
) -> Report:
    """Assert every code in ``codes`` is diagnosed, and return the report.

    With ``exactly=True`` the report must contain those codes and nothing else,
    which is how the golden corpus pins a fault to a single diagnosis.
    """
    for code in codes:
        # Fail loudly on a typo in the test rather than silently never matching.
        get_diagnostic(code)

    report = diagnose(data, partner=partner, source=source)
    found = set(report.codes())
    missing = [code for code in codes if code.upper() not in found]

    if missing:
        raise AssertionError(
            f"expected {', '.join(missing)} to be diagnosed, but got "
            + describe(report)
        )

    if exactly:
        unexpected = sorted(found - {code.upper() for code in codes})
        if unexpected:
            raise AssertionError(
                f"expected only {', '.join(codes)}, but also got "
                f"{', '.join(unexpected)}:\n" + describe(report)
            )

    return report


def assert_no_diagnosis(
    data: str | bytes,
    *codes: str,
    partner: PartnerProfile = DEFAULT_PARTNER,
    source: str | None = None,
) -> Report:
    """Assert none of ``codes`` is diagnosed."""
    report = diagnose(data, partner=partner, source=source)
    found = set(report.codes())
    present = [code for code in codes if code.upper() in found]

    if present:
        raise AssertionError(
            f"expected {', '.join(present)} not to be diagnosed, but "
            + describe(report)
        )
    return report
