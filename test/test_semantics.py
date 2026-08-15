# SPDX-License-Identifier: Apache-2.0
"""Category B and D content checks."""

from __future__ import annotations

import pytest

from x12doctor import PartnerProfile, diagnose
from x12doctor.semantics.asn856 import gs1_check_digit, is_valid_sscc18

ISA = (
    "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
    "*240404*0538*U*00401*000000001*0*P*:~"
)


def wrap(doc_type: str, functional_id: str, *segments: str) -> str:
    inner = "".join(segments)
    return (
        ISA
        + f"GS*{functional_id}*SENDERID*RECEIVERID*20240404*0518*1*X*004010~"
        + f"ST*{doc_type}*0001~"
        + inner
        + f"SE*{len(segments) + 2}*0001~GE*1*1~IEA*1*000000001~"
    )


def order(*segments: str) -> str:
    return wrap("850", "PO", "BEG*00*NE*PO1**20240404~", *segments)


def asn(*segments: str) -> str:
    return wrap("856", "SH", "BSN*00*SH1*20240404*1056~", *segments)


# ---------------------------------------------------------------------------
# B01 — units of measure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "suggestion"),
    [("EACH", "EA"), ("CASE", "CS"), ("BOX", "BX"), ("FEET", "FT"), ("each", "EA")],
)
def test_spelled_out_units_are_named_with_their_code(
    value: str, suggestion: str
) -> None:
    report = diagnose(order(f"PO1*1*10*{value}*5.00*PE*BP*P1~", "CTT*1~"))
    finding = next(f for f in report.findings if f.code == "B01")
    assert finding.context["suggestion"] == suggestion
    assert finding.element == 3


@pytest.mark.parametrize("value", ["EA", "CS", "BX", "FT", "LB", "DZ"])
def test_valid_unit_codes_pass(value: str) -> None:
    report = diagnose(order(f"PO1*1*10*{value}*5.00*PE*BP*P1~", "CTT*1~"))
    assert not report.has("B01")


def test_unrecognised_unit_without_a_suggestion() -> None:
    report = diagnose(order("PO1*1*10*QQ*5.00*PE*BP*P1~", "CTT*1~"))
    finding = next(f for f in report.findings if f.code == "B01")
    assert "suggestion" not in finding.context


def test_partner_whitelist_narrows_the_standard() -> None:
    document = order("PO1*1*10*LB*5.00*PE*BP*P1~", "CTT*1~")
    partner = PartnerProfile(uom_whitelist=("EA", "CS"))

    assert not diagnose(document).has("B01")  # LB is a valid X12 code
    finding = next(f for f in diagnose(document, partner=partner).findings)
    assert finding.code == "B01"
    assert finding.context["accepted"] == ["CS", "EA"]


# ---------------------------------------------------------------------------
# B03 — dates
# ---------------------------------------------------------------------------


def test_six_digit_date_where_eight_is_required() -> None:
    report = diagnose(order("DTM*002*240412~", "PO1*1*1*EA*1.00~", "CTT*1~"))
    finding = next(f for f in report.findings if f.code == "B03")
    assert finding.context == {
        "value": "240412",
        "expected_length": 8,
        "actual_length": 6,
    }


def test_impossible_month_is_reported() -> None:
    report = diagnose(order("DTM*002*20241301~", "PO1*1*1*EA*1.00~", "CTT*1~"))
    assert report.has("B03")


def test_impossible_day_is_reported() -> None:
    report = diagnose(order("DTM*002*20240230~", "PO1*1*1*EA*1.00~", "CTT*1~"))
    assert report.has("B03")


def test_leap_day_is_accepted() -> None:
    report = diagnose(order("DTM*002*20240229~", "PO1*1*1*EA*1.00~", "CTT*1~"))
    assert not report.has("B03")


def test_non_leap_year_february_29_is_rejected() -> None:
    report = diagnose(order("DTM*002*20230229~", "PO1*1*1*EA*1.00~", "CTT*1~"))
    assert report.has("B03")


def test_isa09_stays_six_digits() -> None:
    """ISA09 predates the Y2K rewrite; eight digits there is the error."""
    document = order("PO1*1*1*EA*1.00~", "CTT*1~").replace("*240404*0538*", "*20240404*0538*")
    assert diagnose(document).has("B03")


def test_invalid_time_is_reported() -> None:
    document = order("PO1*1*1*EA*1.00~", "CTT*1~").replace("*0538*", "*2599*")
    assert diagnose(document).has("B03")


# ---------------------------------------------------------------------------
# B04 — totals
# ---------------------------------------------------------------------------


def test_ctt_line_count_mismatch() -> None:
    report = diagnose(
        order("PO1*1*1*EA*1.00~", "PO1*2*1*EA*1.00~", "CTT*3~")
    )
    finding = next(f for f in report.findings if f.code == "B04")
    assert finding.context["declared"] == 3
    assert finding.context["actual"] == 2


def test_ctt_hash_total_is_checked_when_present() -> None:
    report = diagnose(order("PO1*1*10*EA*1.00~", "PO1*2*5*EA*1.00~", "CTT*2*99~"))
    finding = next(f for f in report.findings if f.code == "B04")
    assert finding.element == 2
    assert finding.context["actual"] == "15"


def test_correct_hash_total_passes() -> None:
    report = diagnose(order("PO1*1*10*EA*1.00~", "PO1*2*5*EA*1.00~", "CTT*2*15~"))
    assert not report.has("B04")


# ---------------------------------------------------------------------------
# B02 — implied decimals
# ---------------------------------------------------------------------------


def test_implied_decimals_caught_exactly_via_the_invoice_total() -> None:
    """An 810 carries its total in cents, which makes this arithmetic, not a guess."""
    document = wrap(
        "810",
        "IN",
        "BIG*20240404*INV1*20240404*PO1~",
        "IT1*1*10*EA*2000*PE*BP*P1~",  # 20.00 scaled by 100
        "TDS*20000~",  # 200.00 in cents
        "CTT*1~",
    )
    finding = next(f for f in diagnose(document).findings if f.code == "B02")
    assert finding.severity == "error"
    assert finding.context["basis"] == "total cross-check"


def test_correct_invoice_totals_pass() -> None:
    document = wrap(
        "810",
        "IN",
        "BIG*20240404*INV1*20240404*PO1~",
        "IT1*1*10*EA*20.00*PE*BP*P1~",
        "TDS*20000~",
        "CTT*1~",
    )
    assert diagnose(document).findings == []


def test_heuristic_needs_no_decimal_point_anywhere() -> None:
    """A document that uses decimals elsewhere is not guessed at."""
    document = order(
        "PO1*1*10*EA*22064*PE*BP*P1~", "PO1*2*5*EA*20.95*PE*BP*P2~", "CTT*2~"
    )
    assert not diagnose(document).has("B02")


def test_round_integer_prices_are_not_flagged() -> None:
    document = order("PO1*1*10*EA*2500*PE*BP*P1~", "CTT*1~")
    assert not diagnose(document).has("B02")


# ---------------------------------------------------------------------------
# B05 / B06 — required content
# ---------------------------------------------------------------------------


def test_missing_partner_ref_is_reported_with_what_is_present() -> None:
    document = order("REF*IA*123~", "PO1*1*1*EA*1.00~", "CTT*1~")
    partner = PartnerProfile(required_refs=("DP",))
    finding = next(f for f in diagnose(document, partner=partner).findings)
    assert finding.code == "B05"
    assert finding.context["present"] == ["IA"]


def test_per_document_refs_apply_only_to_that_transaction_set() -> None:
    partner = PartnerProfile(required_refs_by_doc={"856": ("BM",)})
    assert not diagnose(order("PO1*1*1*EA*1.00~", "CTT*1~"), partner=partner).has("B05")
    assert diagnose(asn("HL*1**S~"), partner=partner).has("B05")


def test_missing_mandatory_segment() -> None:
    document = wrap("850", "PO", "PO1*1*1*EA*1.00~", "CTT*1~")  # no BEG
    finding = next(f for f in diagnose(document).findings if f.code == "B06")
    assert finding.context["missing"] == "BEG"


def test_duplicate_line_numbers_are_reported() -> None:
    report = diagnose(order("PO1*1*1*EA*1.00~", "PO1*1*2*EA*1.00~", "CTT*2~"))
    assert report.has("B06")


def test_partner_envelope_ids_are_checked() -> None:
    partner = PartnerProfile(sender_id="OTHERID", isa_qualifier="ZZ")
    finding = next(
        f
        for f in diagnose(order("PO1*1*1*EA*1.00~", "CTT*1~"), partner=partner).findings
        if f.code == "B05"
    )
    assert finding.context["expected"] == "OTHERID"


def test_test_flag_on_a_production_partner_is_an_error() -> None:
    document = order("PO1*1*1*EA*1.00~", "CTT*1~").replace("*0*P*:", "*0*T*:")
    partner = PartnerProfile(usage_indicator="P")
    finding = next(
        f for f in diagnose(document, partner=partner).findings if f.element == 15
    )
    assert finding.severity == "error"


# ---------------------------------------------------------------------------
# D01 — HL hierarchy
# ---------------------------------------------------------------------------


def test_orphaned_hl_parent_is_reported_with_what_exists() -> None:
    document = asn("HL*1**S~", "HL*2*1*O~", "HL*3*9999*I~", "SN1**1*EA~")
    finding = next(f for f in diagnose(document).findings if f.code == "D01")
    assert finding.context["hl02"] == "9999"
    assert finding.context["declared"] == ["1", "2"]


def test_forward_parent_reference_is_an_orphan() -> None:
    """HL02 must point at a level already declared, not one further down."""
    document = asn("HL*1**S~", "HL*2*3*I~", "HL*3*1*O~")
    assert diagnose(document).has("D01")


def test_valid_hierarchy_passes() -> None:
    document = asn(
        "HL*1**S~", "HL*2*1*O~", "HL*3*2*P~", "HL*4*3*I~", "SN1**1*EA~"
    )
    assert not diagnose(document).has("D01")


def test_missing_shipment_level_is_reported() -> None:
    assert diagnose(asn("HL*1**O~", "HL*2*1*I~")).has("D01")


def test_duplicate_hl_ids_are_reported() -> None:
    assert diagnose(asn("HL*1**S~", "HL*1*1*O~")).has("D01")


def test_unknown_level_code_is_reported() -> None:
    assert diagnose(asn("HL*1**S~", "HL*2*1*Z~")).has("D01")


# ---------------------------------------------------------------------------
# D02 — SSCC-18
# ---------------------------------------------------------------------------


def test_gs1_check_digit_matches_known_values() -> None:
    # The check digit of a GS1 body is the digit that makes the weighted sum a
    # multiple of ten; these are worked examples, not round-tripped output.
    assert gs1_check_digit("00000000000000000") == "0"
    assert gs1_check_digit("34005526237852220") == "2"


def test_valid_sscc_passes() -> None:
    body = "34005526237852220"
    assert is_valid_sscc18(body + gs1_check_digit(body))


def test_wrong_check_digit_is_reported_with_the_correction() -> None:
    body = "34005526237852220"
    wrong = body + str((int(gs1_check_digit(body)) + 1) % 10)
    finding = next(
        f for f in diagnose(asn("HL*1**S~", f"MAN*GM*{wrong}~")).findings
        if f.code == "D02"
    )
    assert finding.context["corrected"] == body + gs1_check_digit(body)


def test_short_sscc_mentions_stripped_leading_zeros() -> None:
    finding = next(
        f for f in diagnose(asn("HL*1**S~", "MAN*GM*123456789~")).findings
        if f.code == "D02"
    )
    assert "Leading zeros" in finding.message


def test_non_sscc_qualifiers_are_left_alone() -> None:
    """Only GM and SSCC carry a check digit worth verifying."""
    assert not diagnose(asn("HL*1**S~", "MAN*CP*CARRIER-MARK-7~")).has("D02")


# ---------------------------------------------------------------------------
# D04 — shipment timeline
# ---------------------------------------------------------------------------


def test_asn_dated_after_the_delivery_it_announces() -> None:
    document = asn("HL*1**S~", "DTM*011*20240404~", "DTM*017*20240401~")
    codes = [f.code for f in diagnose(document).findings]
    assert codes.count("D04") == 2  # BSN03 after delivery, and shipped after delivery


def test_plausible_timeline_passes() -> None:
    document = asn("HL*1**S~", "DTM*011*20240404~", "DTM*017*20240410~")
    assert not diagnose(document).has("D04")


def test_short_dates_do_not_produce_a_second_timeline_finding() -> None:
    """A YYMMDD date is B03's problem; guessing a century would double-report it."""
    document = asn("HL*1**S~", "DTM*011*240404~", "DTM*017*240401~")
    report = diagnose(document)
    assert report.has("B03")
    assert not report.has("D04")
