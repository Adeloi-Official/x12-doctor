# SPDX-License-Identifier: Apache-2.0
"""997 decoding and pairing — the part people actually reach for this tool for."""

from __future__ import annotations

import pytest

from x12doctor import diagnose, explain_997
from x12doctor.fa997 import (
    ELEMENT_SYNTAX_ERRORS,
    GROUP_ACK_CODES,
    SEGMENT_SYNTAX_ERRORS,
    TRANSACTION_SET_ACK_CODES,
)

ISA_FROM_PARTNER = (
    "ISA*00*          *00*          *ZZ*RECEIVERID     *ZZ*SENDERID       "
    "*240404*0600*U*00401*000000811*0*P*:~"
)


def ack(*ak_segments: str) -> str:
    inner = "".join(ak_segments)
    return (
        ISA_FROM_PARTNER
        + "GS*FA*RECEIVERID*SENDERID*20240404*0600*811*X*004010~"
        + "ST*997*0001~"
        + inner
        + f"SE*{len(ak_segments) + 2}*0001~GE*1*811~IEA*1*000000811~"
    )


ORIGINAL = (
    "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
    "*240404*0538*U*00401*000000001*0*P*:~"
    "GS*PO*SENDERID*RECEIVERID*20240404*0518*27124*X*004010~"
    "ST*850*0001~"
    "BEG*00*NE*PO173006**20240404~"
    "PO1*1*228*EA*220.64*PE*BP*AX-31566~"
    "PO1*2*5*CASE*209.50*PE*BP*SKU-50201~"
    "CTT*2~"
    "SE*6*0001~GE*1*27124~IEA*1*000000001~"
)

ACCEPTED = ack("AK1*PO*27124~", "AK2*850*0001~", "AK5*A~", "AK9*A*1*1*1~")

REJECTED = ack(
    "AK1*PO*27124~",
    "AK2*850*0001~",
    "AK3*PO1*4**8~",  # 4th segment of ST..SE, the PO1 with the bad unit
    "AK4*3*355*7*CASE~",  # element 3, invalid code value, value CASE
    "AK5*R*5~",
    "AK9*R*1*1*0~",
)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_accepted_acknowledgment_is_clean() -> None:
    result = explain_997(ACCEPTED)
    assert result.status == "accepted"
    assert result.ok
    assert result.findings == []


def test_rejected_acknowledgment_reports_the_rejection() -> None:
    result = explain_997(REJECTED)
    assert result.status == "rejected"
    assert not result.ok
    assert "C03" in {f.code for f in result.findings}


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("A", "accepted"),
        ("E", "accepted_with_errors"),
        ("P", "partially_accepted"),
        ("R", "rejected"),
        ("M", "rejected"),
        ("W", "rejected"),
    ],
)
def test_every_group_ack_code_maps_to_a_status(code: str, status: str) -> None:
    document = ack("AK1*PO*1~", "AK2*850*0001~", "AK5*A~", f"AK9*{code}*1*1*1~")
    assert explain_997(document).status == status


def test_the_worst_group_status_wins() -> None:
    document = ack(
        "AK1*PO*1~", "AK2*850*0001~", "AK5*A~", "AK9*A*1*1*1~",
        "AK1*IN*2~", "AK2*810*0002~", "AK5*R~", "AK9*R*1*1*0~",
    )
    assert explain_997(document).status == "rejected"


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def test_ak_structure_is_decoded_into_objects() -> None:
    result = explain_997(REJECTED)

    group = result.groups[0]
    assert group.functional_id == "PO"
    assert group.control == "27124"
    assert group.code == "R"
    assert (group.included, group.received, group.accepted) == ("1", "1", "0")

    transaction = group.transactions[0]
    assert transaction.doc_type == "850"
    assert transaction.control == "0001"
    assert not transaction.accepted

    segment_error = transaction.segment_errors[0]
    assert segment_error.segment_id == "PO1"
    assert segment_error.position == 4
    assert segment_error.errors == [("8", SEGMENT_SYNTAX_ERRORS["8"])]

    element_error = segment_error.elements[0]
    assert element_error.position == 3
    assert element_error.reference == "355"
    assert element_error.meaning == ELEMENT_SYNTAX_ERRORS["7"]
    assert element_error.bad_value == "CASE"


def test_composite_ak401_yields_a_component_position() -> None:
    document = ack(
        "AK1*PO*1~", "AK2*850*0001~", "AK3*DTM*4~", "AK4*2:1*373*8~",
        "AK5*R~", "AK9*R*1*1*0~",
    )
    element_error = explain_997(document).groups[0].transactions[0].segment_errors[
        0
    ].elements[0]
    assert element_error.position == 2
    assert element_error.component == 1


def test_unknown_codes_are_reported_verbatim_not_guessed() -> None:
    document = ack("AK1*PO*1~", "AK2*850*0001~", "AK3*PO1*4**99~", "AK5*R~")
    meaning = explain_997(document).groups[0].transactions[0].segment_errors[0].errors[
        0
    ][1]
    assert "99" in meaning
    assert "not in the 004010 code list" in meaning


def test_group_and_transaction_lexicons_are_distinct() -> None:
    """P is valid for a functional group but not for a single transaction set."""
    assert "P" in GROUP_ACK_CODES
    assert "P" not in TRANSACTION_SET_ACK_CODES


# ---------------------------------------------------------------------------
# Pairing with the original
# ---------------------------------------------------------------------------


def test_pairing_names_the_value_that_was_rejected() -> None:
    result = explain_997(REJECTED, ORIGINAL)
    assert result.paired

    segment_error = result.rejections[0]
    assert segment_error.original is not None
    assert segment_error.original.id == "PO1"
    assert "SKU-50201" in segment_error.original.raw
    assert segment_error.elements[0].original_value == "CASE"


def test_pairing_produces_a_located_finding() -> None:
    findings = explain_997(REJECTED, ORIGINAL).findings
    located = [f for f in findings if f.code == "C04"]
    assert located
    assert any("CASE" in f.message for f in located)
    assert all(f.segment_index is not None for f in located)


def test_without_the_original_positions_stay_numeric() -> None:
    result = explain_997(REJECTED)
    assert not result.paired
    assert result.rejections[0].original is None
    # The finding is C01, not C04: nothing was located.
    assert "C04" not in {f.code for f in result.findings}


def test_unknown_group_is_reported_when_paired() -> None:
    document = ack("AK1*PO*999999~", "AK2*850*0001~", "AK5*A~", "AK9*A*1*1*1~")
    result = explain_997(document, ORIGINAL)
    finding = next(f for f in result.findings if f.code == "C02")
    assert finding.context["ak102"] == "999999"


def test_a_single_document_original_pairs_despite_a_control_mismatch() -> None:
    """After a resend the control numbers differ but the document is unambiguous."""
    document = ack(
        "AK1*PO*27124~", "AK2*850*9999~", "AK3*PO1*4**8~", "AK5*R~", "AK9*R*1*1*0~"
    )
    result = explain_997(document, ORIGINAL)
    assert result.paired
    assert result.rejections[0].original is not None


def test_out_of_range_positions_are_ignored_rather_than_crashing() -> None:
    document = ack(
        "AK1*PO*27124~", "AK2*850*0001~", "AK3*PO1*999**8~", "AK5*R~", "AK9*R*1*1*0~"
    )
    result = explain_997(document, ORIGINAL)
    assert result.rejections[0].original is None


# ---------------------------------------------------------------------------
# Non-997 input
# ---------------------------------------------------------------------------


def test_explaining_a_non_997_says_so() -> None:
    result = explain_997(ORIGINAL)
    finding = next(f for f in result.findings if f.code == "A00")
    assert "850" in finding.message
    assert result.status == "unknown"


def test_explaining_junk_does_not_crash() -> None:
    result = explain_997("not edi at all")
    assert result.groups == []
    assert result.findings


# ---------------------------------------------------------------------------
# `check` on a 997
# ---------------------------------------------------------------------------


def test_check_decodes_a_997_too() -> None:
    """`x12doctor check` on an acknowledgment must not be silent about it."""
    report = diagnose(REJECTED)
    assert report.doc_type == "997"
    assert report.has("C03")


def test_check_on_an_accepted_997_is_clean() -> None:
    assert diagnose(ACCEPTED).findings == []


def test_redundant_summary_codes_are_suppressed() -> None:
    """AK502=5 and AK304=8 only say 'see the detail below'; the detail follows."""
    messages = [f.message for f in diagnose(REJECTED).findings]
    assert not any("one or more segments are in error" in m for m in messages)
    assert not any(
        m.endswith("segment has data element errors.") for m in messages
    )


def test_position_pointing_at_a_different_segment_is_not_trusted() -> None:
    """AK301 and the segment actually at AK302 must agree, or nothing is claimed."""
    document = ack(
        "AK1*PO*27124~", "AK2*850*0001~", "AK3*PO1*5**8~", "AK4*3*355*7~",
        "AK5*R~", "AK9*R*1*1*0~",
    )
    result = explain_997(document, ORIGINAL)

    error = result.rejections[0]
    assert error.position_conflict == "CTT"  # position 5 is the CTT, not a PO1
    assert error.original is None
    assert any(
        "could not be resolved" in f.message for f in result.findings
    )
