# SPDX-License-Identifier: Apache-2.0
"""Envelope structure and the category A checks."""

from __future__ import annotations

import pytest

from x12doctor import diagnose
from x12doctor.envelope import parse_envelope
from x12doctor.tokenizer import tokenize

ISA = (
    "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
    "*240404*0538*U*00401*000000001*0*P*:~"
)


def build(*segments: str, isa: str = ISA, iea: str = "IEA*1*000000001~") -> str:
    """Assemble an interchange around the given transaction set segments."""
    inner = "".join(segments)
    count = len(segments) + 2  # ST and SE bracket the given segments
    return (
        isa
        + "GS*PO*SENDERID*RECEIVERID*20240404*0518*1*X*004010~"
        + "ST*850*0001~"
        + inner
        + f"SE*{count}*0001~"
        + "GE*1*1~"
        + iea
    )


ORDER_BODY = (
    "BEG*00*NE*PO1**20240404~",
    "PO1*1*10*EA*5.00*PE*BP*PART-1~",
    "CTT*1~",
)


def test_a_well_formed_interchange_produces_nothing() -> None:
    assert diagnose(build(*ORDER_BODY)).findings == []


def test_envelope_tree_nests_correctly() -> None:
    doc = tokenize(build(*ORDER_BODY))
    envelope, findings = parse_envelope(doc)

    assert findings == []
    assert len(envelope.interchanges) == 1
    interchange = envelope.interchanges[0]
    assert interchange.control == "000000001"
    assert interchange.sender == "SENDERID"
    assert interchange.receiver == "RECEIVERID"
    assert not interchange.ack_requested  # ISA14 is 0
    assert not interchange.is_test  # ISA15 is P

    group = interchange.groups[0]
    assert group.functional_id == "PO"
    assert group.version == "004010"

    transaction = group.transactions[0]
    assert transaction.doc_type == "850"
    assert transaction.control == "0001"
    # ST..SE inclusive
    assert len(transaction.segments) == 5


# ---------------------------------------------------------------------------
# A01 — ISA width
# ---------------------------------------------------------------------------


def test_short_isa_element_is_reported_with_the_element_named() -> None:
    truncated = ISA.replace("RECEIVERID     ", "RECEIVERID")
    report = diagnose(build(*ORDER_BODY, isa=truncated))

    finding = next(f for f in report.findings if f.code == "A01")
    assert "ISA08" in finding.message
    assert finding.context["actual_length"] == 101
    assert finding.severity == "error"


def test_isa_with_too_few_elements_is_reported() -> None:
    report = diagnose(build(*ORDER_BODY, isa="ISA*00*  *00*  *ZZ*S*ZZ*R~"))
    assert report.has("A01")


# ---------------------------------------------------------------------------
# A02, A06, A07 — counts
# ---------------------------------------------------------------------------


def test_se_count_too_high() -> None:
    document = build(*ORDER_BODY).replace("SE*5*0001", "SE*9*0001")
    finding = next(f for f in diagnose(document).findings if f.code == "A02")
    assert finding.context == {
        "declared": 9,
        "actual": 5,
        "st02": "0001",
        "doc_type": "850",
    }
    assert "4 fewer than declared" in finding.message


def test_se_count_that_is_not_a_number() -> None:
    document = build(*ORDER_BODY).replace("SE*5*0001", "SE*five*0001")
    assert diagnose(document).has("A02")


def test_ge_count_wrong() -> None:
    document = build(*ORDER_BODY).replace("GE*1*1~", "GE*3*1~")
    finding = next(f for f in diagnose(document).findings if f.code == "A06")
    assert finding.context["declared"] == 3
    assert finding.context["actual"] == 1


def test_iea_count_wrong() -> None:
    document = build(*ORDER_BODY).replace("IEA*1*", "IEA*2*")
    assert diagnose(document).has("A07")


# ---------------------------------------------------------------------------
# A03 — control numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("IEA*1*000000001~", "IEA*1*000000002~"),
        ("GE*1*1~", "GE*1*2~"),
        ("SE*5*0001~", "SE*5*0002~"),
    ],
)
def test_control_number_mismatches(before: str, after: str) -> None:
    document = build(*ORDER_BODY).replace(before, after)
    assert diagnose(document).has("A03")


def test_zero_padding_is_not_a_mismatch() -> None:
    """ISA13 is padded to nine digits; IEA02 usually is not."""
    document = build(*ORDER_BODY).replace("IEA*1*000000001~", "IEA*1*1~")
    assert not diagnose(document).has("A03")


# ---------------------------------------------------------------------------
# A08 — versions
# ---------------------------------------------------------------------------


def test_isa_and_gs_versions_must_agree() -> None:
    document = build(*ORDER_BODY).replace("X*004010~", "X*005010~")
    finding = next(f for f in diagnose(document).findings if f.code == "A08")
    assert finding.context == {"isa12": "00401", "gs08": "005010"}


def test_industry_suffix_on_gs08_is_accepted() -> None:
    document = build(*ORDER_BODY).replace("X*004010~", "X*004010VICS~")
    assert not diagnose(document).has("A08")


def test_unknown_isa_version_is_reported() -> None:
    document = build(*ORDER_BODY, isa=ISA.replace("*00401*", "*09999*"))
    assert diagnose(document).has("A08")


# ---------------------------------------------------------------------------
# A09, A10, A11 — placement and closure
# ---------------------------------------------------------------------------


def test_content_before_the_isa_is_reported() -> None:
    report = diagnose("MIME-Version: 1.0\n" + build(*ORDER_BODY))
    assert report.has("A09")


def test_content_after_the_iea_is_reported() -> None:
    report = diagnose(build(*ORDER_BODY) + "\n--boundary--\n")
    assert report.has("A09")


def test_unclosed_envelopes_are_each_reported() -> None:
    document = build(*ORDER_BODY)
    truncated = document[: document.index("SE*5*0001")]
    codes = [f.code for f in diagnose(truncated).findings]
    # One A10 each for the unclosed ST, GS and ISA.
    assert codes.count("A10") == 3


def test_two_interchanges_in_one_file_are_both_parsed() -> None:
    document = build(*ORDER_BODY) + build(*ORDER_BODY)
    doc = tokenize(document)
    envelope, findings = parse_envelope(doc)
    assert len(envelope.interchanges) == 2
    assert findings == []


def test_a_second_isa_before_the_first_iea_is_out_of_order() -> None:
    document = build(*ORDER_BODY).replace("IEA*1*000000001~", "") + build(*ORDER_BODY)
    report = diagnose(document)
    assert report.has("A11")


def test_segment_outside_a_transaction_set_is_reported() -> None:
    document = build(*ORDER_BODY).replace("ST*850*0001~", "N1*ST*Acme~ST*850*0001~")
    assert diagnose(document).has("A11")


# ---------------------------------------------------------------------------
# A05 — separators
# ---------------------------------------------------------------------------


def test_a_damaged_isa_does_not_shred_the_document() -> None:
    """A short ISA must not have letters of the next segment read as delimiters.

    The component separator and terminator sit at fixed offsets, so a truncated
    ISA puts those offsets inside the GS that follows. Accepting whatever is
    there would split the file on a letter and turn one defect into a hundred.
    """
    document = build(*ORDER_BODY, isa=ISA.replace("RECEIVERID     ", "RECEIVERID"))
    doc = tokenize(document)

    assert doc.delimiters.segment == "~"
    assert [s.id for s in doc.segments] == [
        "ISA",
        "GS",
        "ST",
        "BEG",
        "PO1",
        "CTT",
        "SE",
        "GE",
        "IEA",
    ]


def test_a_pipe_element_separator_is_flagged_as_unusual() -> None:
    document = build(*ORDER_BODY).replace("*", "|")
    report = diagnose(document)
    assert report.has("A05")
