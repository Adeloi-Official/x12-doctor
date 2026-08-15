# SPDX-License-Identifier: Apache-2.0
"""Directory scanning and the checks that need more than one file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from x12doctor import scan
from x12doctor.scan import find_documents

ISA_TEMPLATE = (
    "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
    "*{isa_date}*{isa_time}*U*00401*{control}*{ack}*P*:~"
)


def order(
    *,
    control: str = "000000001",
    group: str = "1",
    po: str = "PO1",
    date: str = "240404",
    time: str = "0538",
    ack: str = "0",
    lines: str = "PO1*1*10*EA*5.00*PE*BP*PART-1~",
) -> str:
    return (
        ISA_TEMPLATE.format(isa_date=date, isa_time=time, control=control, ack=ack)
        + f"GS*PO*SENDERID*RECEIVERID*20{date}*{time}*{group}*X*004010~"
        + "ST*850*0001~"
        + f"BEG*00*NE*{po}**20{date}~"
        + lines
        + "CTT*1~SE*5*0001~"
        + f"GE*1*{group}~IEA*1*{control}~"
    )


def asn(
    *, po: str = "PO1", date: str = "240405", qty: str = "10", group: str = "2"
) -> str:
    return (
        ISA_TEMPLATE.format(
            isa_date=date, isa_time="0900", control="000000002", ack="0"
        )
        + f"GS*SH*SENDERID*RECEIVERID*20{date}*0900*{group}*X*004010~"
        + "ST*856*0001~"
        + f"BSN*00*SH1*20{date}*0900~"
        + "HL*1**S~"
        + f"DTM*011*20{date}~"
        + "HL*2*1*O~"
        + f"PRF*{po}~"
        + "HL*3*2*I~"
        + "LIN*1*BP*PART-1~"
        + f"SN1**{qty}*EA~"
        + "CTT*1~SE*11*0001~"
        + f"GE*1*{group}~IEA*1*000000002~"
    )


@pytest.fixture
def outbox(tmp_path: Path) -> Path:
    directory = tmp_path / "outbox"
    directory.mkdir()
    return directory


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_only_x12_looking_files_are_scanned(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order())
    (outbox / "notes.md").write_text("# not edi\n")
    (outbox / "archive.zip").write_bytes(b"PK\x03\x04nothing")

    assert [p.name for p in find_documents(outbox)] == ["order.edi"]


def test_extensionless_drop_files_are_found(outbox: Path) -> None:
    (outbox / "0001").write_text(order())
    assert [p.name for p in find_documents(outbox)] == ["0001"]


def test_noise_directories_are_skipped(outbox: Path) -> None:
    (outbox / "node_modules").mkdir()
    (outbox / "node_modules" / "x.edi").write_text(order())
    (outbox / "real.edi").write_text(order())
    assert [p.name for p in find_documents(outbox)] == ["real.edi"]


def test_scanning_is_recursive_by_default(outbox: Path) -> None:
    (outbox / "2024").mkdir()
    (outbox / "2024" / "a.edi").write_text(order())
    (outbox / "b.edi").write_text(order(control="000000002", group="2"))

    assert len(scan(outbox).entries) == 2
    assert len(scan(outbox, recursive=False).entries) == 1


def test_discovery_order_is_stable(outbox: Path) -> None:
    for name in ("c.edi", "a.edi", "b.edi"):
        (outbox / name).write_text(order())
    assert [p.name for p in find_documents(outbox)] == ["a.edi", "b.edi", "c.edi"]


# ---------------------------------------------------------------------------
# A04
# ---------------------------------------------------------------------------


def test_duplicate_control_number_across_files(outbox: Path) -> None:
    (outbox / "first.edi").write_text(order(control="000000001", po="PO-A"))
    (outbox / "second.edi").write_text(order(control="000000001", po="PO-B"))

    result = scan(outbox)
    duplicates = [f for f in result.findings if f.code == "A04"]
    assert len(duplicates) == 1
    assert "first.edi" in duplicates[0].context["also_in"][0]


def test_same_number_to_a_different_receiver_is_not_a_duplicate(
    outbox: Path,
) -> None:
    """Control numbers are unique per trading relationship, not globally."""
    (outbox / "a.edi").write_text(order(control="000000001"))
    (outbox / "b.edi").write_text(
        order(control="000000001").replace("RECEIVERID     ", "OTHERPARTY     ")
    )
    assert not [f for f in scan(outbox).findings if f.code == "A04"]


def test_zero_padding_does_not_hide_a_duplicate(outbox: Path) -> None:
    (outbox / "a.edi").write_text(order(control="000000042"))
    (outbox / "b.edi").write_text(order(control="000000042"))
    assert [f for f in scan(outbox).findings if f.code == "A04"]


# ---------------------------------------------------------------------------
# C05
# ---------------------------------------------------------------------------


def test_unacknowledged_group_is_quiet_unless_isa14_asked(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(ack="0"))
    assert not [f for f in scan(outbox).findings if f.code == "C05"]


def test_isa14_requesting_an_ack_makes_the_absence_a_warning(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(ack="1"))
    finding = next(f for f in scan(outbox).findings if f.code == "C05")
    assert finding.severity == "warning"
    assert finding.context["ack_requested"] is True


def test_the_flag_demands_acknowledgment_of_everything(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(ack="0"))
    assert list(scan(outbox, check_acknowledgments=True).findings)


def test_disabling_the_check_silences_it(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(ack="1"))
    assert not [
        f for f in scan(outbox, check_acknowledgments=False).findings if f.code == "C05"
    ]


# ---------------------------------------------------------------------------
# C06
# ---------------------------------------------------------------------------


def test_asn_dated_before_its_order_is_reported(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(po="PO-9", date="240410"))
    (outbox / "asn.edi").write_text(asn(po="PO-9", date="240401"))

    finding = next(f for f in scan(outbox).findings if f.code == "C06")
    assert finding.context["reference"] == "PO-9"
    assert finding.context["doc_type"] == "856"


def test_a_correctly_ordered_flow_is_quiet(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(po="PO-9", date="240401"))
    (outbox / "asn.edi").write_text(asn(po="PO-9", date="240410"))
    assert not [f for f in scan(outbox).findings if f.code == "C06"]


def test_documents_for_different_orders_are_not_compared(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(po="PO-A", date="240410"))
    (outbox / "asn.edi").write_text(asn(po="PO-B", date="240401"))
    assert not [f for f in scan(outbox).findings if f.code == "C06"]


# ---------------------------------------------------------------------------
# D03
# ---------------------------------------------------------------------------


def test_over_shipment_is_an_error(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(po="PO-9"))  # 10 ordered
    (outbox / "asn.edi").write_text(asn(po="PO-9", qty="17"))

    finding = next(f for f in scan(outbox).findings if f.code == "D03")
    assert finding.severity == "error"
    assert finding.context["direction"] == "over"
    assert finding.context["difference"] == "7"


def test_under_shipment_is_informational(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(po="PO-9"))
    (outbox / "asn.edi").write_text(asn(po="PO-9", qty="4"))

    finding = next(f for f in scan(outbox).findings if f.code == "D03")
    assert finding.severity == "info"
    assert finding.context["direction"] == "short"


def test_split_shipments_are_totalled_before_comparing(outbox: Path) -> None:
    """Two ASNs of 5 against an order of 10 is a correct split shipment."""
    (outbox / "order.edi").write_text(order(po="PO-9"))
    (outbox / "asn1.edi").write_text(asn(po="PO-9", qty="5", date="240405"))
    (outbox / "asn2.edi").write_text(asn(po="PO-9", qty="5", date="240406"))

    assert not [f for f in scan(outbox).findings if f.code == "D03"]


def test_shipping_an_unordered_item_is_reported(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(po="PO-9"))
    (outbox / "asn.edi").write_text(
        asn(po="PO-9").replace("LIN*1*BP*PART-1~", "LIN*1*BP*PART-SUBSTITUTE~")
    )
    finding = next(f for f in scan(outbox).findings if f.code == "D03")
    assert "does not have that item" in finding.message


def test_matching_quantities_are_quiet(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order(po="PO-9"))
    (outbox / "asn.edi").write_text(asn(po="PO-9", qty="10"))
    assert not [f for f in scan(outbox).findings if f.code == "D03"]


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


def test_findings_carry_the_file_they_came_from(outbox: Path) -> None:
    (outbox / "broken.edi").write_text(order().replace("SE*5*", "SE*9*"))
    result = scan(outbox)
    assert all(f.source for f in result.all_findings)
    assert "broken.edi" in result.entries[0].report.findings[0].source


def test_unreadable_files_are_collected_not_raised(
    outbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = outbox / "locked.edi"
    path.write_text(order())

    original = Path.read_bytes

    def deny(self: Path) -> bytes:
        if self.name == "locked.edi":
            raise PermissionError(13, "Permission denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", deny)
    result = scan(outbox)
    assert result.unreadable
    assert result.entries == []


def test_json_output_is_valid_and_versioned(outbox: Path) -> None:
    (outbox / "order.edi").write_text(order())
    payload = json.loads(scan(outbox).to_json())
    assert payload["schema_version"] == "1.0"
    assert payload["file_count"] == 1
    assert "cross_file_findings" in payload


def test_scanning_a_single_file_works(tmp_path: Path) -> None:
    path = tmp_path / "order.edi"
    path.write_text(order())
    assert len(scan(path).entries) == 1


def test_an_empty_directory_scans_to_nothing(outbox: Path) -> None:
    result = scan(outbox)
    assert result.entries == []
    assert result.ok
