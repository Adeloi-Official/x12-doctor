# SPDX-License-Identifier: Apache-2.0
"""Checks that apply across transaction sets, driven by per-document specs.

Most content rules differ between an 850 and an 810 only in which segment
carries the line items and which element within it holds the quantity. Rather
than reimplementing the same check four times, each transaction set declares a
:class:`DocSpec` and the checks here read it. What genuinely differs — the HL
hierarchy of an 856, the AK loop of a 997 — lives in its own module.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .. import catalog
from ..envelope import TransactionSet
from ..partner import PartnerProfile
from ..report import Finding
from ..tokenizer import Segment

__all__ = [
    "UOM_CODES",
    "UOM_SYNONYMS",
    "DocSpec",
    "check_dates",
    "check_implied_decimals",
    "check_line_totals",
    "check_partner_refs",
    "check_required_segments",
    "check_units_of_measure",
    "format_decimal",
    "parse_decimal",
]


@dataclass(frozen=True)
class DocSpec:
    """What a generic check needs to know about one transaction set."""

    doc_type: str
    #: Human-readable name for report headers, e.g. ``"Purchase Order"``.
    name: str
    #: GS01 functional identifier code this transaction set belongs under.
    functional_id: str
    #: Segments X12 requires in every instance of this transaction set.
    required_segments: tuple[str, ...] = ()
    #: Segment carrying line items, e.g. ``"PO1"``. ``None`` when there are none.
    line_segment: str | None = None
    #: Other segments whose count is also accepted in CTT01. Implementation
    #: guides do not agree for every transaction set.
    alt_count_segments: tuple[str, ...] = ()
    #: Element within the line segment holding the quantity.
    qty_element: int | None = None
    #: Element within the line segment holding the unit of measure.
    uom_element: int | None = None
    #: Element within the line segment holding the unit price.
    price_element: int | None = None
    #: Segment and element holding a document total in implied-decimal cents,
    #: e.g. ``("TDS", 1)`` for an 810. Enables the exact B02 cross-check.
    total_element: tuple[str, int] | None = field(default=None)


# ---------------------------------------------------------------------------
# Units of measure (X12 element 355)
# ---------------------------------------------------------------------------

#: A working subset of X12 element 355. Not the full list — the full list has
#: over 800 entries, most of which no purchase order has ever used — but wide
#: enough that a legitimate code is not flagged.
UOM_CODES = frozenset(
    ["AC", "AM", "AS", "AU", "AY", "BA", "BB", "BC", "BD", "BE", "BF", "BG", "BH", "BJ", "BK", "BL", "BO", "BP", "BR", "BS", "BT", "BU", "BX", "BZ", "C0", "C1", "C4", "C5", "C8", "CA", "CB", "CC", "CD", "CE", "CF", "CG", "CH", "CI", "CJ", "CK", "CL", "CM", "CN", "CO", "CP", "CQ", "CR", "CS", "CT", "CU", "CV", "CW", "CX", "CY", "CZ", "DA", "DB", "DC", "DE", "DF", "DG", "DH", "DI", "DJ", "DK", "DL", "DM", "DN", "DP", "DQ", "DR", "DS", "DT", "DU", "DW", "DX", "DY", "DZ", "E3", "EA", "EB", "EC", "EP", "EQ", "EV", "FA", "FB", "FC", "FD", "FE", "FF", "FG", "FH", "FJ", "FL", "FM", "FO", "FP", "FR", "FS", "FT", "FZ", "GA", "GB", "GC", "GD", "GE", "GG", "GH", "GI", "GJ", "GK", "GL", "GM", "GN", "GO", "GP", "GQ", "GR", "GS", "GT", "GU", "GW", "GX", "GY", "GZ", "HA", "HB", "HC", "HD", "HE", "HF", "HG", "HH", "HI", "HJ", "HK", "HL", "HM", "HN", "HO", "HP", "HQ", "HR", "HS", "HT", "HU", "HW", "HY", "HZ", "IA", "IC", "IE", "IF", "IH", "II", "IL", "IM", "IN", "IP", "IT", "JB", "JE", "JG", "JK", "JM", "JO", "JR", "K1", "K2", "K3", "K5", "K6", "KA", "KB", "KD", "KE", "KF", "KG", "KI", "KJ", "KL", "KM", "KO", "KP", "KR", "KS", "KT", "KU", "KV", "KW", "KX", "LA", "LB", "LC", "LE", "LF", "LG", "LH", "LI", "LJ", "LK", "LM", "LN", "LO", "LP", "LR", "LS", "LT", "LX", "LY", "M0", "M1", "M4", "M5", "M7", "M9", "MA", "MB", "MC", "MD", "ME", "MF", "MG", "MH", "MI", "MJ", "MK", "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "N1", "N2", "N3", "NA", "NB", "NC", "ND", "NE", "NF", "NG", "NH", "NI", "NJ", "NL", "NM", "NN", "NQ", "NR", "NS", "NT", "NU", "NV", "NW", "NX", "NY", "OA", "OP", "OT", "OZ", "P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "PA", "PB", "PC", "PD", "PE", "PF", "PG", "PH", "PI", "PJ", "PK", "PL", "PM", "PN", "PO", "PQ", "PR", "PS", "PT", "PU", "PV", "PW", "PY", "PZ", "QA", "QB", "QD", "QH", "QK", "QR", "QS", "QT", "RA", "RB", "RC", "RD", "RE", "RG", "RH", "RK", "RL", "RM", "RN", "RO", "RP", "RS", "RT", "RU", "SA", "SB", "SC", "SD", "SE", "SF", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SP", "SQ", "SR", "SS", "ST", "SV", "SW", "SX", "SY", "T0", "T1", "T3", "T4", "T5", "T6", "T7", "T8", "TA", "TB", "TC", "TD", "TE", "TF", "TG", "TH", "TI", "TJ", "TK", "TL", "TM", "TN", "TO", "TP", "TQ", "TR", "TS", "TT", "TU", "TV", "TW", "TX", "TY", "U1", "U2", "UA", "UB", "UC", "UD", "UE", "UF", "UH", "UL", "UM", "UN", "UP", "UR", "US", "UU", "VA", "VI", "VP", "VQ", "VR", "VS", "W2", "WA", "WB", "WC", "WE", "WG", "WH", "WI", "WM", "WR", "WW", "X1", "YD", "YL", "YT", "Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z8", "ZP", "ZZ"]
)

#: Spelled-out units and the X12 code they should have been. This is the
#: single most common content defect in real trading-partner traffic: an ERP
#: display value passed straight through into PO1-03.
UOM_SYNONYMS: dict[str, str] = {
    "EACH": "EA",
    "EACHES": "EA",
    "UNIT": "EA",
    "UNITS": "EA",
    "PIECE": "PC",
    "PIECES": "PC",
    "PCS": "PC",
    "CASE": "CS",
    "CASES": "CS",
    "BOX": "BX",
    "BOXES": "BX",
    "CARTON": "CT",
    "CARTONS": "CT",
    "FEET": "FT",
    "FOOT": "FT",
    "INCH": "IN",
    "INCHES": "IN",
    "YARD": "YD",
    "YARDS": "YD",
    "METER": "MR",
    "METERS": "MR",
    "METRE": "MR",
    "POUND": "LB",
    "POUNDS": "LB",
    "LBS": "LB",
    "KILO": "KG",
    "KILOS": "KG",
    "KILOGRAM": "KG",
    "KILOGRAMS": "KG",
    "GALLON": "GA",
    "GALLONS": "GA",
    "LITER": "LT",
    "LITERS": "LT",
    "LITRE": "LT",
    "DOZEN": "DZ",
    "PALLET": "PF",
    "PALLETS": "PF",
    "ROLL": "RL",
    "ROLLS": "RL",
    "PACK": "PK",
    "PACKS": "PK",
    "PAIR": "PR",
    "PAIRS": "PR",
    "SET": "ST",
    "SETS": "ST",
    "TON": "TN",
    "TONS": "TN",
    "HOUR": "HR",
    "HOURS": "HR",
    "DAY": "DA",
    "DAYS": "DA",
}


def check_required_segments(
    transaction: TransactionSet, spec: DocSpec, partner: PartnerProfile
) -> list[Finding]:
    """B06 — every segment the standard and the partner require must be present."""
    findings: list[Finding] = []
    present = {segment.id for segment in transaction.segments}

    for segment_id in spec.required_segments:
        if segment_id not in present:
            findings.append(
                catalog.make_finding(
                    "B06",
                    f"A {spec.doc_type} must contain a {segment_id} segment, "
                    "and this one does not.",
                    segment_index=transaction.st.index,
                    segment_id="ST",
                    line=transaction.st.line,
                    context={"missing": segment_id, "doc_type": spec.doc_type},
                )
            )

    for segment_id in partner.segments_for(spec.doc_type):
        if segment_id not in present:
            findings.append(
                catalog.make_finding(
                    "B06",
                    f"The partner profile requires a {segment_id} segment in a "
                    f"{spec.doc_type}, and this one does not have it.",
                    segment_index=transaction.st.index,
                    segment_id="ST",
                    line=transaction.st.line,
                    why=(
                        "The segment is optional in the X12 standard but required "
                        "by this partner, so only their validator rejects it."
                    ),
                    context={
                        "missing": segment_id,
                        "doc_type": spec.doc_type,
                        "source": "partner profile",
                    },
                )
            )

    return findings


def check_units_of_measure(
    transaction: TransactionSet, spec: DocSpec, partner: PartnerProfile
) -> list[Finding]:
    """B01 — unit of measure elements must carry X12 codes."""
    if spec.line_segment is None or spec.uom_element is None:
        return []

    findings: list[Finding] = []
    whitelist = {code.upper() for code in partner.uom_whitelist}

    for segment in transaction.by_id(spec.line_segment):
        raw = segment.get(spec.uom_element).strip()
        if not raw:
            continue
        value = raw.upper()

        if value in UOM_SYNONYMS:
            suggestion = UOM_SYNONYMS[value]
            findings.append(
                catalog.make_finding(
                    "B01",
                    f"Unit of measure {raw!r} is not an X12 code. "
                    f"Did you mean {suggestion!r}?",
                    segment_index=segment.index,
                    segment_id=segment.id,
                    element=spec.uom_element,
                    line=segment.line,
                    context={"value": raw, "suggestion": suggestion},
                )
            )
            continue

        if value not in UOM_CODES:
            findings.append(
                catalog.make_finding(
                    "B01",
                    f"Unit of measure {raw!r} is not a recognised X12 code.",
                    segment_index=segment.index,
                    segment_id=segment.id,
                    element=spec.uom_element,
                    line=segment.line,
                    context={"value": raw},
                )
            )
            continue

        if whitelist and value not in whitelist:
            findings.append(
                catalog.make_finding(
                    "B01",
                    f"Unit of measure {raw!r} is a valid X12 code but is not in "
                    f"the partner's accepted list "
                    f"({', '.join(sorted(whitelist))}).",
                    segment_index=segment.index,
                    segment_id=segment.id,
                    element=spec.uom_element,
                    line=segment.line,
                    why=(
                        "The partner's implementation guide narrows the standard "
                        "to the units they can actually receive."
                    ),
                    hint=(
                        "Convert the quantity into one of the accepted units "
                        "before sending, rather than passing the internal unit "
                        "through."
                    ),
                    context={"value": raw, "accepted": sorted(whitelist)},
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

#: Elements carrying a date, mapped to the width their 4010 definition demands.
#: The two widths coexisting in one document is exactly why a single shared
#: date formatter gets this wrong.
DATE_ELEMENTS: dict[str, dict[int, int]] = {
    "ISA": {9: 6},   # ISA09 predates the Y2K rewrite and stayed at YYMMDD.
    "GS": {4: 8},
    "BEG": {5: 8},   # 850 purchase order date
    "BAK": {5: 8},   # 855 acknowledgment date
    "BIG": {1: 8, 3: 8},  # 810 invoice date, purchase order date
    "BSN": {3: 8},   # 856 shipment date
    "DTM": {2: 8},
    "PRF": {4: 8},   # purchase order date on a referenced order
    "SCH": {6: 8},
}

#: Segment/element pairs holding a time, which must be HHMM or HHMMSS.
TIME_ELEMENTS: dict[str, dict[int, tuple[int, ...]]] = {
    "ISA": {10: (4,)},
    "GS": {5: (4, 6, 8)},
    "BSN": {4: (4, 6, 8)},
    "DTM": {3: (4, 6, 8)},
}


def check_dates(segments: Iterable[Segment]) -> list[Finding]:
    """B03 — every date element must have the width its definition demands.

    Takes segments rather than a transaction set because the envelope carries
    dates too: ISA09, ISA10, GS04 and GS05 sit outside every ST..SE and would
    otherwise never be looked at.
    """
    findings: list[Finding] = []

    for segment in segments:
        for position, width in DATE_ELEMENTS.get(segment.id, {}).items():
            value = segment.get(position).strip()
            if not value:
                continue

            if not value.isdigit():
                findings.append(
                    catalog.make_finding(
                        "B03",
                        f"{segment.id}{position:02d} is {value!r}, which is not a "
                        "numeric date.",
                        segment_index=segment.index,
                        segment_id=segment.id,
                        element=position,
                        line=segment.line,
                        context={"value": value, "expected_length": width},
                    )
                )
                continue

            if len(value) != width:
                shape = "CCYYMMDD" if width == 8 else "YYMMDD"
                actual_shape = "YYMMDD" if len(value) == 6 else f"{len(value)} digits"
                findings.append(
                    catalog.make_finding(
                        "B03",
                        f"{segment.id}{position:02d} is {value!r} "
                        f"({actual_shape}), but this element is {shape}.",
                        segment_index=segment.index,
                        segment_id=segment.id,
                        element=position,
                        line=segment.line,
                        context={
                            "value": value,
                            "expected_length": width,
                            "actual_length": len(value),
                        },
                    )
                )
                continue

            problem = _implausible_date(value, width)
            if problem:
                findings.append(
                    catalog.make_finding(
                        "B03",
                        f"{segment.id}{position:02d} is {value!r}: {problem}.",
                        segment_index=segment.index,
                        segment_id=segment.id,
                        element=position,
                        line=segment.line,
                        context={"value": value},
                    )
                )

        for position, widths in TIME_ELEMENTS.get(segment.id, {}).items():
            value = segment.get(position).strip()
            if not value:
                continue
            if not value.isdigit() or len(value) not in widths:
                findings.append(
                    catalog.make_finding(
                        "B03",
                        f"{segment.id}{position:02d} is {value!r}, which is not a "
                        f"valid time (expected {' or '.join(f'{w} digits' for w in widths)}).",
                        segment_index=segment.index,
                        segment_id=segment.id,
                        element=position,
                        line=segment.line,
                        context={"value": value},
                    )
                )
                continue
            hour, minute = int(value[:2]), int(value[2:4])
            if hour > 23 or minute > 59:
                findings.append(
                    catalog.make_finding(
                        "B03",
                        f"{segment.id}{position:02d} is {value!r}, which is not a "
                        "time that exists.",
                        segment_index=segment.index,
                        segment_id=segment.id,
                        element=position,
                        line=segment.line,
                        context={"value": value},
                    )
                )

    return findings


def _implausible_date(value: str, width: int) -> str | None:
    """Name what is wrong with a well-shaped but impossible date."""
    if width == 8:
        year, month, day = int(value[0:4]), int(value[4:6]), int(value[6:8])
        if not 1900 <= year <= 2199:
            return f"year {year} is outside any plausible range"
    else:
        year, month, day = int(value[0:2]), int(value[2:4]), int(value[4:6])

    if not 1 <= month <= 12:
        return f"month {month:02d} does not exist"
    if not 1 <= day <= _days_in_month(year if width == 8 else 2000 + year, month):
        return f"day {day:02d} does not exist in month {month:02d}"
    return None


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def check_line_totals(transaction: TransactionSet, spec: DocSpec) -> list[Finding]:
    """B04 — CTT01 counts the line items, CTT02 sums their quantities."""
    if spec.line_segment is None:
        return []

    ctt = transaction.first("CTT")
    if ctt is None:
        return []

    findings: list[Finding] = []
    lines = transaction.by_id(spec.line_segment)
    actual = len(lines)

    declared = ctt.get(1).strip()
    if not declared.isdigit():
        findings.append(
            catalog.make_finding(
                "B04",
                f"CTT01 is {declared!r}, which is not a line count. The document "
                f"has {actual} {spec.line_segment} segment"
                f"{'' if actual == 1 else 's'}.",
                segment_index=ctt.index,
                segment_id="CTT",
                element=1,
                line=ctt.line,
                context={"declared": declared, "actual": actual},
            )
        )
    elif int(declared) != actual and int(declared) not in {
        len(transaction.by_id(seg)) for seg in spec.alt_count_segments
    }:
        alternatives = "".join(
            f" or {len(transaction.by_id(seg))} {seg} segment"
            f"{'' if len(transaction.by_id(seg)) == 1 else 's'}"
            for seg in spec.alt_count_segments
        )
        findings.append(
            catalog.make_finding(
                "B04",
                f"CTT01 declares {int(declared)} line item"
                f"{'' if int(declared) == 1 else 's'}, but the document contains "
                f"{actual} {spec.line_segment} segment"
                f"{'' if actual == 1 else 's'}{alternatives}.",
                segment_index=ctt.index,
                segment_id="CTT",
                element=1,
                line=ctt.line,
                context={
                    "declared": int(declared),
                    "actual": actual,
                    "line_segment": spec.line_segment,
                },
            )
        )

    # CTT02 is the hash total of the quantities, and is optional.
    hash_total = ctt.get(2).strip()
    if hash_total and spec.qty_element is not None:
        expected = Decimal(0)
        readable = True
        for line in lines:
            quantity = parse_decimal(line.get(spec.qty_element))
            if quantity is None:
                readable = False
                break
            expected += quantity

        declared_total = parse_decimal(hash_total)
        if readable and declared_total is not None and declared_total != expected:
            findings.append(
                catalog.make_finding(
                    "B04",
                    f"CTT02 declares a quantity hash total of {hash_total}, but "
                    f"the line quantities add up to {format_decimal(expected)}.",
                    segment_index=ctt.index,
                    segment_id="CTT",
                    element=2,
                    line=ctt.line,
                    context={
                        "declared": hash_total,
                        "actual": format_decimal(expected),
                    },
                )
            )

    return findings


def check_partner_refs(
    transaction: TransactionSet, spec: DocSpec, partner: PartnerProfile
) -> list[Finding]:
    """B05 — REF qualifiers the partner's implementation guide requires."""
    required = partner.refs_for(spec.doc_type)
    if not required:
        return []

    present = {
        segment.get(1).strip().upper()
        for segment in transaction.by_id("REF")
        if segment.get(1).strip()
    }

    findings: list[Finding] = []
    for qualifier in required:
        if qualifier.upper() not in present:
            findings.append(
                catalog.make_finding(
                    "B05",
                    f"The partner requires a REF with qualifier {qualifier!r}, "
                    "and the document has none"
                    + (
                        f" (it has {', '.join(sorted(present))})."
                        if present
                        else " (it has no REF segments at all)."
                    ),
                    segment_index=transaction.st.index,
                    segment_id="ST",
                    line=transaction.st.line,
                    context={
                        "qualifier": qualifier,
                        "present": sorted(present),
                        "doc_type": spec.doc_type,
                    },
                )
            )

    return findings


def check_implied_decimals(
    transaction: TransactionSet, spec: DocSpec
) -> list[Finding]:
    """B02 — money that looks scaled by 100.

    Two signals, in order of confidence. When the document carries its own
    total in implied-decimal cents, the mismatch is arithmetic and certain.
    Otherwise this falls back to a shape heuristic, which is why the finding is
    a warning rather than an error.
    """
    if spec.line_segment is None or spec.price_element is None:
        return []

    lines = transaction.by_id(spec.line_segment)
    if not lines:
        return []

    prices: list[tuple[Segment, Decimal, str]] = []
    for line in lines:
        raw = line.get(spec.price_element).strip()
        value = parse_decimal(raw)
        if value is not None:
            prices.append((line, value, raw))

    if not prices:
        return []

    # -- exact check: line extensions against a declared document total ----
    if spec.total_element is not None and spec.qty_element is not None:
        finding = _check_against_total(transaction, spec, prices)
        if finding is not None:
            return [finding]

    # -- heuristic: nothing carries a decimal point and values look inflated
    if any("." in raw for _, _, raw in prices):
        return []

    inflated = [
        (line, value, raw)
        for line, value, raw in prices
        if value >= 1000 and value % 100 != 0
    ]
    if not inflated:
        return []

    line, value, raw = inflated[0]
    scaled = value / 100
    return [
        catalog.make_finding(
            "B02",
            f"{spec.line_segment}{spec.price_element:02d} is {raw!r} with no "
            f"decimal point. If the partner does not use implied decimals this "
            f"is {raw}, not {format_decimal(scaled)}"
            + (
                f" ({len(inflated)} line items look the same way)."
                if len(inflated) > 1
                else "."
            ),
            segment_index=line.index,
            segment_id=line.id,
            element=spec.price_element,
            line=line.line,
            context={
                "value": raw,
                "as_implied_decimal": format_decimal(scaled),
                "affected_lines": len(inflated),
                "basis": "heuristic",
            },
        )
    ]


def _check_against_total(
    transaction: TransactionSet,
    spec: DocSpec,
    prices: list[tuple[Segment, Decimal, str]],
) -> Finding | None:
    """Compare the sum of quantity times price against the declared total."""
    assert spec.total_element is not None and spec.qty_element is not None
    segment_id, element = spec.total_element

    total_segment = transaction.first(segment_id)
    if total_segment is None:
        return None
    declared = parse_decimal(total_segment.get(element))
    if declared is None:
        return None

    extended = Decimal(0)
    for line, price, _raw in prices:
        quantity = parse_decimal(line.get(spec.qty_element))
        if quantity is None:
            return None
        extended += quantity * price

    if extended == 0:
        return None

    # The total is carried in cents, so a document that agrees satisfies
    # declared == extended * 100.
    expected = extended * 100
    if declared == expected:
        return None

    ratio = declared / expected
    if ratio == Decimal(1) / Decimal(100):
        line, _price, _raw = prices[0]
        return catalog.make_finding(
            "B02",
            f"The line items add up to {format_decimal(extended)}, but {segment_id}"
            f"{element:02d} declares {format_decimal(declared / 100)} — exactly one "
            f"hundredth. The line prices carry implied decimals that "
            f"{segment_id}{element:02d} does not expect.",
            segment_index=line.index,
            segment_id=line.id,
            element=spec.price_element,
            line=line.line,
            severity="error",
            context={
                "line_total": format_decimal(extended),
                "declared_total": format_decimal(declared),
                "ratio": "1/100",
                "basis": "total cross-check",
            },
        )

    if ratio == Decimal(100):
        return catalog.make_finding(
            "B02",
            f"The line items add up to {format_decimal(extended)}, but {segment_id}"
            f"{element:02d} declares {format_decimal(declared / 100)} — exactly one "
            f"hundred times as much. One side of the document applies implied "
            f"decimals and the other does not.",
            segment_index=total_segment.index,
            segment_id=segment_id,
            element=element,
            line=total_segment.line,
            severity="error",
            context={
                "line_total": format_decimal(extended),
                "declared_total": format_decimal(declared),
                "ratio": "100",
                "basis": "total cross-check",
            },
        )

    return None


def parse_decimal(raw: str) -> Decimal | None:
    """Parse an X12 numeric, returning ``None`` when it is not one."""
    text = raw.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def format_decimal(value: Decimal) -> str:
    """Render a Decimal without exponent notation or trailing zeros."""
    normalised = value.normalize()
    if normalised == normalised.to_integral_value():
        try:
            return str(normalised.quantize(Decimal(1)))
        except InvalidOperation:
            return str(normalised)
    return format(normalised, "f")
