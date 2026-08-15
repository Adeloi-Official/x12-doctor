# SPDX-License-Identifier: Apache-2.0
"""The invariant everything else rests on: damage never becomes an exception.

x12-doctor exists to be pointed at files that are already wrong. If a
sufficiently mangled document could crash it, it would be useless exactly when
it is needed. These tests mutate real documents in every way that occurs in
practice — truncation, byte corruption, delimiter loss, encoding damage — and
assert only that a report comes back.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from conftest import CLEAN, corpus_files, read
from x12doctor import diagnose, explain_997, tokenize

#: A fixed seed, so a failure is reproducible from the test name alone.
SEED = 20260815

SOURCES = corpus_files(CLEAN)


def _mutations(text: str, rng: random.Random, count: int = 40) -> list[str]:
    """Realistic corruptions, not arbitrary noise."""
    mutations: list[str] = []
    for _ in range(count):
        kind = rng.choice(
            [
                "truncate",
                "cut_middle",
                "drop_char",
                "swap_chars",
                "insert_junk",
                "strip_delimiters",
                "duplicate_chunk",
                "upper",
                "nulls",
            ]
        )
        if not text:
            break
        position = rng.randrange(len(text))

        if kind == "truncate":
            mutations.append(text[:position])
        elif kind == "cut_middle":
            end = rng.randrange(position, len(text))
            mutations.append(text[:position] + text[end:])
        elif kind == "drop_char":
            mutations.append(text[:position] + text[position + 1 :])
        elif kind == "swap_chars":
            if position + 2 <= len(text):
                mutations.append(
                    text[:position]
                    + text[position + 1 : position + 2]
                    + text[position : position + 1]
                    + text[position + 2 :]
                )
        elif kind == "insert_junk":
            mutations.append(text[:position] + rng.choice("~*:|>^\x00\n") + text[position:])
        elif kind == "strip_delimiters":
            mutations.append(text.replace(rng.choice("~*:"), ""))
        elif kind == "duplicate_chunk":
            end = min(len(text), position + 120)
            mutations.append(text[:end] + text[position:end] + text[end:])
        elif kind == "upper":
            mutations.append(text.swapcase())
        elif kind == "nulls":
            mutations.append(text[:position] + "\x00\x00\x00" + text[position:])

    return mutations


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.stem)
def test_mutated_documents_always_produce_a_report(path: Path) -> None:
    rng = random.Random(SEED)
    original = read(path)

    for mutated in _mutations(original, rng):
        report = diagnose(mutated, source=str(path))
        # The only contract: a report comes back, and it is self-consistent.
        assert isinstance(report.findings, list)
        assert report.counts()["error"] == len(report.errors)
        assert report.to_json()


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.stem)
def test_mutated_documents_never_break_the_tokenizer(path: Path) -> None:
    rng = random.Random(SEED + 1)
    for mutated in _mutations(read(path), rng, count=20):
        doc = tokenize(mutated)
        for segment in doc.segments:
            # Positions must stay inside the source, or later slicing lies.
            assert 0 <= segment.offset <= len(doc.source)
            assert segment.line >= 1


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00" * 512,
        b"\xff\xfe\xfd",
        "ISA".encode("utf-16"),
        b"ISA*" + b"A" * 100_000,
        b"~" * 10_000,
        b"ISA" + b"*" * 500,
        "ISA*00*ünïcödé*".encode(),
        b"ISA*00*\x80\x81\x82*",
    ],
    ids=[
        "empty",
        "nulls",
        "invalid-utf8",
        "utf16",
        "very-long-element",
        "only-terminators",
        "only-separators",
        "non-ascii",
        "cp1252-bytes",
    ],
)
def test_hostile_bytes_never_raise(payload: bytes) -> None:
    report = diagnose(payload)
    assert isinstance(report.findings, list)
    assert report.to_json()


def test_deeply_repeated_segments_stay_linear(tmp_path: Path) -> None:
    """A large document must not take quadratic time to locate line numbers."""
    body = "PO1*1*1*EA*1.00~" * 20_000
    document = (
        "ISA*00*          *00*          *ZZ*S              *ZZ*R              "
        "*240404*0538*U*00401*000000001*0*P*:~"
        "GS*PO*S*R*20240404*0518*1*X*004010~ST*850*0001~" + body + "SE*2*0001~"
    )
    doc = tokenize(document)
    assert len(doc.segments) == 20_004


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.stem)
def test_explain_997_survives_any_input(path: Path) -> None:
    rng = random.Random(SEED + 2)
    for mutated in _mutations(read(path), rng, count=10):
        result = explain_997(mutated)
        assert isinstance(result.findings, list)
        assert result.to_dict()


def test_diagnosis_is_deterministic() -> None:
    """The same bytes must always give the same report, byte for byte."""
    for path in SOURCES[:6]:
        data = path.read_bytes()
        assert diagnose(data).to_json() == diagnose(data).to_json()


def test_findings_are_ordered_by_position() -> None:
    document = read(CLEAN / "850_seed42_lines3.edi")
    broken = document.replace("SE*15*", "SE*99*").replace("*EA*", "*EACH*")
    report = diagnose(broken)

    positions = [f.segment_index for f in report.findings if f.segment_index is not None]
    assert positions == sorted(positions)
