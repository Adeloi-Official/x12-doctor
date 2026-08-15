# SPDX-License-Identifier: Apache-2.0
"""Tokenizer behaviour, especially on input it was never promised."""

from __future__ import annotations

import pytest

from x12doctor.tokenizer import ISA_LENGTH, decode, tokenize

# A conforming ISA, used as a base for mutation.
ISA = (
    "ISA*00*          *00*          *ZZ*SENDERID       *ZZ*RECEIVERID     "
    "*240404*0538*U*00401*530335606*0*P*:~"
)
BODY = "GS*PO*S*R*20240404*0518*1*X*004010~ST*850*0001~SE*2*0001~GE*1*1~IEA*1*530335606~"


def test_conforming_isa_is_106_characters() -> None:
    """Guards the test's own fixture as much as the constant."""
    assert len(ISA) == ISA_LENGTH


def test_delimiters_are_read_positionally_from_the_isa() -> None:
    doc = tokenize(ISA + BODY)
    assert doc.delimiters.element == "*"
    assert doc.delimiters.segment == "~"
    assert doc.delimiters.component == ":"
    assert doc.delimiters.from_isa


def test_unusual_delimiters_are_honoured() -> None:
    """Separators are declared per interchange, so anything legal must work."""
    text = (ISA + BODY).replace("*", "|").replace("~", ">")
    # The component separator inside the ISA is positional, not substituted.
    doc = tokenize(text)
    assert doc.delimiters.element == "|"
    assert doc.delimiters.segment == ">"
    assert [s.id for s in doc.segments][:3] == ["ISA", "GS", "ST"]


def test_segments_are_indexed_and_elements_are_one_based() -> None:
    doc = tokenize(ISA + BODY)
    isa = doc.segments[0]
    assert isa.id == "ISA"
    assert isa.index == 0
    assert isa.get(13) == "530335606"
    assert isa.get(16) == ":"
    # Out of range is empty, never an exception — a short segment is normal.
    assert isa.get(99) == ""
    assert isa.get(0) == ""


def test_newline_formatted_documents_carry_line_numbers() -> None:
    doc = tokenize((ISA + BODY).replace("~", "~\n"))
    assert doc.segments[0].line == 1
    assert doc.segments[1].line == 2
    assert doc.segments[-1].line == 6


def test_trailing_newlines_do_not_create_empty_segments() -> None:
    doc = tokenize(ISA + BODY + "\n\n\n")
    assert all(segment.id for segment in doc.segments)
    assert len(doc.segments) == 6


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "   \n\t ",
        "not edi at all",
        "ISA",
        "ISA*",
        "ISA*00*",
        ISA[:50],
        ISA + "GS*PO",
        "~~~~~~",
        "***",
        "\x00\x01\x02",
    ],
)
def test_tokenizer_never_raises(payload: str) -> None:
    """The invariant the whole tool rests on: damage produces a report, not a crash."""
    doc = tokenize(payload)
    assert isinstance(doc.segments, list)


def test_bytes_and_str_agree() -> None:
    text = ISA + BODY
    assert [s.raw for s in tokenize(text)] == [
        s.raw for s in tokenize(text.encode("utf-8"))
    ]


def test_utf8_bom_is_stripped_so_isa_offsets_survive() -> None:
    """A BOM would shift every fixed-width ISA offset by one character."""
    doc = tokenize("﻿" + ISA + BODY)
    assert doc.segments[0].id == "ISA"
    assert doc.delimiters.component == ":"


def test_undecodable_bytes_do_not_raise() -> None:
    doc = decode(b"\xff\xfe\x00ISA*00*")
    assert isinstance(doc, str)


def test_missing_isa_is_recorded_as_an_issue() -> None:
    doc = tokenize("GS*PO*S*R*20240404*0518*1*X*004010~")
    assert any(code == "A00" for code, _message, _index in doc.issues)


def test_by_id_and_first_find_segments() -> None:
    doc = tokenize(ISA + BODY)
    assert len(doc.by_id("GS", "GE")) == 2
    assert doc.first("ST") is not None
    assert doc.first("PO1") is None
