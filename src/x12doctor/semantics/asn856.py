# SPDX-License-Identifier: Apache-2.0
"""856 Advance Ship Notice — hierarchy, container codes and shipment dates.

The 856 is the transaction set with real structure. Everything else in X12 is a
flat list of segments; the ASN carries a tree of shipment, order, pack and item
levels linked by HL parent pointers. That tree is where ASNs break, and a
broken tree is invisible in a text diff.
"""

from __future__ import annotations

from datetime import date

from .. import catalog
from ..envelope import TransactionSet
from ..report import Finding
from ..tokenizer import Segment
from .common import DocSpec

__all__ = ["SPEC", "check", "gs1_check_digit", "is_valid_sscc18"]

SPEC = DocSpec(
    doc_type="856",
    name="Advance Ship Notice",
    functional_id="SH",
    required_segments=("BSN", "HL"),
    # SN1 is the item detail: SN1-01 is an assigned identification that is
    # normally empty, SN1-02 the quantity shipped, SN1-03 the unit.
    line_segment="SN1",
    # Many 856 guides define CTT01 as the number of HL segments rather than
    # the number of SN1 lines, so either count is accepted.
    alt_count_segments=("HL",),
    qty_element=2,
    uom_element=3,
)

#: HL03 Hierarchical Level Codes used in an 856.
_LEVEL_NAMES = {
    "S": "Shipment",
    "O": "Order",
    "T": "Tare",
    "P": "Pack",
    "I": "Item",
}

#: Which levels may sit directly under which. A pack inside an order inside a
#: shipment is the common case; an item hanging straight off the shipment is
#: legal for simple ASNs.
_ALLOWED_PARENTS: dict[str, frozenset[str]] = {
    "S": frozenset(),
    "O": frozenset({"S"}),
    "T": frozenset({"S", "O"}),
    "P": frozenset({"S", "O", "T"}),
    "I": frozenset({"S", "O", "T", "P"}),
}

#: DTM qualifiers that carry a date the shipment timeline depends on.
_SHIPPED_QUALIFIERS = frozenset({"011"})
_DELIVERY_QUALIFIERS = frozenset({"017"})
_ORDER_QUALIFIERS = frozenset({"004"})


def check(transaction: TransactionSet) -> list[Finding]:
    """All 856-specific checks."""
    findings: list[Finding] = []
    findings.extend(_check_hierarchy(transaction))
    findings.extend(_check_container_codes(transaction))
    findings.extend(_check_timeline(transaction))
    return findings


# ---------------------------------------------------------------------------
# D01 — HL hierarchy
# ---------------------------------------------------------------------------


def _check_hierarchy(transaction: TransactionSet) -> list[Finding]:
    findings: list[Finding] = []
    hls = transaction.by_id("HL")
    if not hls:
        return findings

    #: HL01 to the segment that declared it, built as we walk so that a parent
    #: reference pointing forward is caught rather than silently accepted.
    declared: dict[str, Segment] = {}
    levels: dict[str, str] = {}
    expected_number = 1

    for segment in hls:
        number = segment.get(1).strip()
        parent = segment.get(2).strip()
        level = segment.get(3).strip().upper()

        # A parent must already have been declared when this level appears, so
        # the check runs against the levels seen *before* this one — a segment
        # is never its own parent, and a forward reference is an orphan.
        previously = list(declared)

        if not number:
            findings.append(
                catalog.make_finding(
                    "D01",
                    "HL01 is empty, so nothing can reference this level.",
                    segment_index=segment.index,
                    segment_id="HL",
                    element=1,
                    line=segment.line,
                )
            )
        elif number in declared:
            findings.append(
                catalog.make_finding(
                    "D01",
                    f"HL01 {number!r} is used twice, at segment "
                    f"{declared[number].index + 1} and segment {segment.index + 1}. "
                    "A parent reference to it is ambiguous.",
                    segment_index=segment.index,
                    segment_id="HL",
                    element=1,
                    line=segment.line,
                    context={"hl01": number},
                )
            )
        else:
            if number.isdigit() and int(number) != expected_number:
                findings.append(
                    catalog.make_finding(
                        "D01",
                        f"HL01 is {number!r} where {expected_number} was expected; "
                        "hierarchical ID numbers must run sequentially from 1 in "
                        "document order.",
                        segment_index=segment.index,
                        segment_id="HL",
                        element=1,
                        line=segment.line,
                        severity="warning",
                        context={"hl01": number, "expected": expected_number},
                    )
                )
            declared[number] = segment
            levels[number] = level
            if number.isdigit():
                expected_number = int(number) + 1
            else:
                expected_number += 1

        if level and level not in _LEVEL_NAMES:
            findings.append(
                catalog.make_finding(
                    "D01",
                    f"HL03 is {level!r}, which is not a hierarchical level code "
                    "an 856 uses.",
                    segment_index=segment.index,
                    segment_id="HL",
                    element=3,
                    line=segment.line,
                    why=(
                        "The level code tells the receiver what this node of the "
                        "tree represents. An unknown code means the whole subtree "
                        "beneath it cannot be interpreted."
                    ),
                    hint=(
                        "Use S for shipment, O for order, P for pack, I for item, "
                        "T for tare."
                    ),
                    context={"hl03": level},
                )
            )

        if not parent:
            # Only the shipment level legitimately has no parent.
            if level and level != "S":
                findings.append(
                    catalog.make_finding(
                        "D01",
                        f"A {_LEVEL_NAMES.get(level, level)} level has no parent "
                        "in HL02, so it hangs off nothing.",
                        segment_index=segment.index,
                        segment_id="HL",
                        element=2,
                        line=segment.line,
                        context={"hl01": number, "hl03": level},
                    )
                )
            continue

        if parent not in previously:
            findings.append(
                catalog.make_finding(
                    "D01",
                    f"HL02 points at parent {parent!r}, which is not declared "
                    "anywhere before this segment"
                    + (
                        f". The levels that do exist are "
                        f"{', '.join(sorted(previously, key=_numeric_key))}."
                        if previously
                        else "; no level has been declared yet."
                    ),
                    segment_index=segment.index,
                    segment_id="HL",
                    element=2,
                    line=segment.line,
                    context={
                        "hl01": number,
                        "hl02": parent,
                        "declared": sorted(previously, key=_numeric_key),
                    },
                )
            )
            continue

        parent_level = levels.get(parent, "")
        allowed = _ALLOWED_PARENTS.get(level)
        if level and parent_level and allowed is not None and parent_level not in allowed:
            findings.append(
                catalog.make_finding(
                    "D01",
                    f"A {_LEVEL_NAMES.get(level, level)} level sits directly "
                    f"under a {_LEVEL_NAMES.get(parent_level, parent_level)} "
                    "level, which the hierarchy does not allow.",
                    segment_index=segment.index,
                    segment_id="HL",
                    element=2,
                    line=segment.line,
                    severity="warning",
                    why=(
                        "The tree is assembled from a flat pick list and the "
                        "intermediate level was dropped for shipments that did "
                        "not need it, without re-pointing its children."
                    ),
                    hint=(
                        "Nest the levels shipment, order, pack, item. When a "
                        "level is omitted, re-point its children at the level "
                        "above."
                    ),
                    context={"level": level, "parent_level": parent_level},
                )
            )

    shipment_levels = [n for n, level in levels.items() if level == "S"]
    if not shipment_levels:
        findings.append(
            catalog.make_finding(
                "D01",
                "No shipment-level HL (HL03 = S) exists, so the hierarchy has no "
                "root.",
                segment_index=hls[0].index,
                segment_id="HL",
                line=hls[0].line,
                context={"levels": sorted(set(levels.values()))},
            )
        )
    elif len(shipment_levels) > 1:
        findings.append(
            catalog.make_finding(
                "D01",
                f"{len(shipment_levels)} shipment-level HL segments exist. An 856 "
                "describes one shipment.",
                segment_index=declared[shipment_levels[1]].index,
                segment_id="HL",
                line=declared[shipment_levels[1]].line,
                severity="warning",
                why=(
                    "Two ASNs were merged into one transaction set, usually by a "
                    "batch step that concatenated documents instead of enveloping "
                    "them separately."
                ),
                hint="Send one 856 per shipment.",
                context={"shipment_levels": shipment_levels},
            )
        )

    return findings


def _numeric_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


# ---------------------------------------------------------------------------
# D02 — SSCC-18 container codes
# ---------------------------------------------------------------------------


def gs1_check_digit(body: str) -> str:
    """Return the GS1 mod-10 check digit for a numeric body.

    Weights alternate 3 and 1 from the rightmost body digit leftwards, which is
    the same algorithm behind GTIN, SSCC and GLN. Getting the weighting the
    wrong way round produces a digit that is right about one time in ten, which
    is exactly often enough to pass a casual test.
    """
    total = 0
    for position, char in enumerate(reversed(body)):
        weight = 3 if position % 2 == 0 else 1
        total += int(char) * weight
    return str((10 - total % 10) % 10)


def is_valid_sscc18(value: str) -> bool:
    """True when ``value`` is 18 digits with a correct check digit."""
    if len(value) != 18 or not value.isdigit():
        return False
    return gs1_check_digit(value[:17]) == value[17]


def _check_container_codes(transaction: TransactionSet) -> list[Finding]:
    findings: list[Finding] = []

    for segment in transaction.by_id("MAN"):
        qualifier = segment.get(1).strip().upper()
        value = segment.get(2).strip()
        if not value:
            continue

        # GM and SSCC carry an SSCC-18; other qualifiers are free-form carrier
        # or customer marks with no check digit to verify.
        if qualifier not in ("GM", "SSCC"):
            continue

        if not value.isdigit():
            findings.append(
                catalog.make_finding(
                    "D02",
                    f"MAN02 is {value!r}, which is not numeric. An SSCC-18 is "
                    "18 digits.",
                    segment_index=segment.index,
                    segment_id="MAN",
                    element=2,
                    line=segment.line,
                    context={"value": value},
                )
            )
            continue

        if len(value) != 18:
            findings.append(
                catalog.make_finding(
                    "D02",
                    f"MAN02 is {len(value)} digits, but an SSCC-18 is 18. "
                    + (
                        "Leading zeros were probably stripped by a numeric field "
                        "somewhere upstream."
                        if len(value) < 18
                        else "The value carries more digits than an SSCC holds."
                    ),
                    segment_index=segment.index,
                    segment_id="MAN",
                    element=2,
                    line=segment.line,
                    context={"value": value, "length": len(value)},
                )
            )
            continue

        expected = gs1_check_digit(value[:17])
        if expected != value[17]:
            findings.append(
                catalog.make_finding(
                    "D02",
                    f"SSCC {value} has check digit {value[17]}, but the first 17 "
                    f"digits require {expected}.",
                    segment_index=segment.index,
                    segment_id="MAN",
                    element=2,
                    line=segment.line,
                    context={
                        "value": value,
                        "declared_check_digit": value[17],
                        "expected_check_digit": expected,
                        "corrected": value[:17] + expected,
                    },
                )
            )

    return findings


# ---------------------------------------------------------------------------
# D04 — shipment timeline
# ---------------------------------------------------------------------------


def _check_timeline(transaction: TransactionSet) -> list[Finding]:
    findings: list[Finding] = []

    bsn = transaction.first("BSN")
    issued = _date_of(bsn, 3) if bsn is not None else None

    shipped: tuple[Segment, date] | None = None
    delivery: tuple[Segment, date] | None = None
    ordered: tuple[Segment, date] | None = None

    for segment in transaction.by_id("DTM"):
        qualifier = segment.get(1).strip()
        value = _date_of(segment, 2)
        if value is None:
            continue
        if qualifier in _SHIPPED_QUALIFIERS and shipped is None:
            shipped = (segment, value)
        elif qualifier in _DELIVERY_QUALIFIERS and delivery is None:
            delivery = (segment, value)
        elif qualifier in _ORDER_QUALIFIERS and ordered is None:
            ordered = (segment, value)

    if delivery is not None and issued is not None and bsn is not None:
        segment, delivered_on = delivery
        if issued > delivered_on:
            gap = (issued - delivered_on).days
            findings.append(
                catalog.make_finding(
                    "D04",
                    f"BSN03 dates this ASN {issued.isoformat()}, which is {gap} "
                    f"day{'' if gap == 1 else 's'} after the delivery date "
                    f"{delivered_on.isoformat()} it announces.",
                    segment_index=bsn.index,
                    segment_id="BSN",
                    element=3,
                    line=bsn.line,
                    context={
                        "bsn03": issued.isoformat(),
                        "delivery": delivered_on.isoformat(),
                        "days_late": gap,
                    },
                )
            )

    if shipped is not None and delivery is not None:
        shipped_segment, shipped_on = shipped
        _delivery_segment, delivered_on = delivery
        if shipped_on > delivered_on:
            gap = (shipped_on - delivered_on).days
            findings.append(
                catalog.make_finding(
                    "D04",
                    f"The shipped date {shipped_on.isoformat()} is {gap} "
                    f"day{'' if gap == 1 else 's'} after the delivery date "
                    f"{delivered_on.isoformat()}.",
                    segment_index=shipped_segment.index,
                    segment_id="DTM",
                    element=2,
                    line=shipped_segment.line,
                    context={
                        "shipped": shipped_on.isoformat(),
                        "delivery": delivered_on.isoformat(),
                    },
                )
            )

    if ordered is not None and shipped is not None:
        _ordered_segment, ordered_on = ordered
        shipped_segment, shipped_on = shipped
        if ordered_on > shipped_on:
            findings.append(
                catalog.make_finding(
                    "D04",
                    f"The shipment is dated {shipped_on.isoformat()}, before the "
                    f"order date {ordered_on.isoformat()} it fulfils.",
                    segment_index=shipped_segment.index,
                    segment_id="DTM",
                    element=2,
                    line=shipped_segment.line,
                    context={
                        "ordered": ordered_on.isoformat(),
                        "shipped": shipped_on.isoformat(),
                    },
                )
            )

    return findings


def _date_of(segment: Segment | None, position: int) -> date | None:
    """Read a CCYYMMDD element as a date, or ``None`` when it is not one.

    Short dates are deliberately not accepted here. A YYMMDD value in an
    element defined as CCYYMMDD is B03's finding, and guessing a century in
    order to run a timeline check on top of it would produce a second,
    misleading finding from the same defect.
    """
    if segment is None:
        return None
    value = segment.get(position).strip()
    if len(value) != 8 or not value.isdigit():
        return None
    try:
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None
