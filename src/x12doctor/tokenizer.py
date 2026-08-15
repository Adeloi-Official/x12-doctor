# SPDX-License-Identifier: Apache-2.0
"""Tolerant tokenizer for ANSI X12 interchanges.

Broken documents are the normal case here, not the exception. Nothing in this
module raises on malformed input: every problem becomes a finding and the
tokenizer keeps going with the best guess it can make.

Delimiter detection follows the X12 rule that the ISA segment is fixed width::

    ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *...
    ^^^^
    |  `- element separator lives at offset 3
    `---- segment id

    ...*00401*000000001*0*P*>~
                          ^ ^
                          | `- segment terminator at offset 105
                          `--- component separator at offset 104

A conforming ISA is exactly 106 characters including the terminator. When it is
not, we fall back to scanning for plausible delimiters so that the rest of the
document can still be diagnosed.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

__all__ = [
    "ISA_ELEMENT_WIDTHS",
    "ISA_LENGTH",
    "Delimiters",
    "Segment",
    "TokenizedDocument",
    "decode",
    "tokenize",
]

#: Length of a conforming ISA segment including its terminator.
ISA_LENGTH = 106

#: Fixed widths of ISA01..ISA16 as defined by X12. The ISA is the only
#: fixed-width segment in the standard, which is exactly why it breaks so often.
ISA_ELEMENT_WIDTHS: tuple[int, ...] = (
    2,   # ISA01 Authorization Information Qualifier
    10,  # ISA02 Authorization Information
    2,   # ISA03 Security Information Qualifier
    10,  # ISA04 Security Information
    2,   # ISA05 Interchange ID Qualifier (sender)
    15,  # ISA06 Interchange Sender ID
    2,   # ISA07 Interchange ID Qualifier (receiver)
    15,  # ISA08 Interchange Receiver ID
    6,   # ISA09 Interchange Date (YYMMDD)
    4,   # ISA10 Interchange Time (HHMM)
    1,   # ISA11 Repetition Separator / Standards Identifier
    5,   # ISA12 Interchange Control Version Number
    9,   # ISA13 Interchange Control Number
    1,   # ISA14 Acknowledgment Requested
    1,   # ISA15 Usage Indicator
    1,   # ISA16 Component Element Separator
)

#: Offset of the component separator inside a conforming ISA.
_ISA_COMPONENT_OFFSET = 104
#: Offset of the segment terminator inside a conforming ISA.
_ISA_TERMINATOR_OFFSET = 105

#: Default delimiters used when nothing better can be determined.
_DEFAULT_ELEMENT = "*"
_DEFAULT_SEGMENT = "~"
_DEFAULT_COMPONENT = ">"

#: Characters that may never serve as a delimiter.
_ILLEGAL_DELIMITERS = frozenset(" \r\n\t")

#: Delimiter characters seen in the wild, most common first. Used only by the
#: fallback scanner when the ISA is too damaged to read positionally.
_ELEMENT_CANDIDATES = "*|\x1f^+"
_SEGMENT_CANDIDATES = "~\x1c\n'"
_COMPONENT_CANDIDATES = ">:\x1d^"


@dataclass(frozen=True)
class Delimiters:
    """The four separator characters governing a single interchange."""

    element: str = _DEFAULT_ELEMENT
    segment: str = _DEFAULT_SEGMENT
    component: str = _DEFAULT_COMPONENT
    repetition: str | None = None
    #: True when the values were read from a conforming fixed-width ISA rather
    #: than guessed by the fallback scanner.
    from_isa: bool = True

    def describe(self) -> str:
        """Render the delimiters for human output, escaping unprintables."""
        return (
            f"element={_printable(self.element)} "
            f"segment={_printable(self.segment)} "
            f"component={_printable(self.component)}"
        )


def _printable(char: str | None) -> str:
    if char is None:
        return "none"
    if char.isprintable() and not char.isspace():
        return f"'{char}'"
    return repr(char)


@dataclass(frozen=True)
class Segment:
    """One X12 segment with positional bookkeeping for diagnostics."""

    #: Zero-based position within the whole document.
    index: int
    #: Segment identifier, upper-cased and stripped (``"PO1"``).
    id: str
    #: Elements 1..n. ``elements[0]`` is element 01.
    elements: tuple[str, ...]
    #: One-based line number the segment starts on, for terminal output.
    line: int
    #: Character offset of the segment start within the decoded source.
    offset: int
    #: Raw text of the segment, terminator excluded.
    raw: str

    def get(self, position: int) -> str:
        """Return element ``position`` (1-based) or ``""`` when absent."""
        if position < 1 or position > len(self.elements):
            return ""
        return self.elements[position - 1]

    def component(self, position: int, sub: int, delimiters: Delimiters) -> str:
        """Return sub-element ``sub`` (1-based) of element ``position``."""
        parts = self.get(position).split(delimiters.component)
        if sub < 1 or sub > len(parts):
            return ""
        return parts[sub - 1]

    def has(self, position: int) -> bool:
        """True when the element exists and is not blank."""
        return bool(self.get(position).strip())

    @property
    def element_count(self) -> int:
        return len(self.elements)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return self.raw


@dataclass
class TokenizedDocument:
    """Result of tokenizing a source document."""

    segments: list[Segment] = field(default_factory=list)
    delimiters: Delimiters = field(default_factory=Delimiters)
    #: Problems detected while tokenizing, as ``(code, message, segment_index)``.
    #: They are converted into findings by the diagnosis layer so that the
    #: tokenizer stays free of any dependency on the catalog.
    issues: list[tuple[str, str, int | None]] = field(default_factory=list)
    #: The decoded source text the offsets refer to.
    source: str = ""

    def __iter__(self) -> Iterator[Segment]:
        return iter(self.segments)

    def __len__(self) -> int:
        return len(self.segments)

    def by_id(self, *ids: str) -> list[Segment]:
        """All segments whose id matches one of ``ids``."""
        wanted = {i.upper() for i in ids}
        return [s for s in self.segments if s.id in wanted]

    def first(self, segment_id: str) -> Segment | None:
        for segment in self.segments:
            if segment.id == segment_id.upper():
                return segment
        return None

    @property
    def is_empty(self) -> bool:
        return not self.segments


def decode(data: str | bytes) -> str:
    """Decode input to text without ever raising.

    X12 travels as bytes far more often than as text, and trading partners are
    inconsistent about encoding. UTF-8 is tried first, then cp1252 (the usual
    Windows source of smart quotes in free-text fields), then latin-1 which
    cannot fail. A UTF-8 BOM is stripped because it would otherwise corrupt the
    fixed-width ISA offsets.
    """
    if isinstance(data, bytes):
        for encoding in ("utf-8", "cp1252"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode("latin-1", errors="replace")
    else:
        text = data

    if text.startswith("﻿"):
        text = text[1:]
    return text


def tokenize(data: str | bytes) -> TokenizedDocument:
    """Split a document into segments, detecting delimiters as we go."""
    source = decode(data)
    doc = TokenizedDocument(source=source)

    stripped = source.lstrip()
    if not stripped:
        doc.issues.append(("A00", "The file is empty.", None))
        return doc

    lead = len(source) - len(stripped)
    isa_start = source.find("ISA")

    if isa_start < 0:
        doc.issues.append(
            (
                "A00",
                "No ISA segment found — this does not look like an X12 interchange.",
                None,
            )
        )
        doc.delimiters = _scan_delimiters(source)
        doc.segments = _split_segments(source, doc.delimiters, 0)
        return doc

    if isa_start > lead:
        doc.issues.append(
            (
                "A09",
                f"{isa_start} characters of junk precede the ISA segment.",
                None,
            )
        )

    doc.delimiters = _delimiters_from_isa(source, isa_start, doc.issues)
    doc.segments = _split_segments(source, doc.delimiters, isa_start)
    return doc


def _delimiters_from_isa(
    source: str,
    isa_start: int,
    issues: list[tuple[str, str, int | None]],
) -> Delimiters:
    """Read the delimiters out of the fixed-width ISA, falling back on damage."""
    head = source[isa_start:]

    element = head[3] if len(head) > 3 else ""
    if not element or element in _ILLEGAL_DELIMITERS:
        issues.append(
            (
                "A05",
                "The character after 'ISA' is not usable as an element separator.",
                0,
            )
        )
        return _scan_delimiters(source)

    # A conforming ISA puts the component separator and terminator at fixed
    # offsets. Trust them only when they are actually usable characters.
    component = head[_ISA_COMPONENT_OFFSET] if len(head) > _ISA_COMPONENT_OFFSET else ""
    terminator = (
        head[_ISA_TERMINATOR_OFFSET] if len(head) > _ISA_TERMINATOR_OFFSET else ""
    )
    repetition = head[82] if len(head) > 82 else ""

    # An X12 delimiter is always punctuation or a control character. Requiring
    # that is what stops a short ISA from being read positionally anyway: the
    # offsets then land inside the following segment, where a letter would
    # otherwise be accepted as the segment terminator and shred the document.
    positional_ok = (
        bool(component)
        and bool(terminator)
        and component not in _ILLEGAL_DELIMITERS
        and terminator not in _ILLEGAL_DELIMITERS
        and not component.isalnum()
        and not terminator.isalnum()
        and component != element
        and terminator != element
    )

    if positional_ok:
        # ISA11 carries the repetition separator from 005010 onwards; in 004010
        # it is the standards identifier "U" and must not be treated as one.
        repetition_sep: str | None = None
        if (
            repetition
            and repetition not in _ILLEGAL_DELIMITERS
            and repetition not in {"U", element, component, terminator}
        ):
            repetition_sep = repetition
        return Delimiters(
            element=element,
            segment=terminator,
            component=component,
            repetition=repetition_sep,
            from_isa=True,
        )

    # The ISA is damaged. Recover the element separator (which we did read) and
    # scan the body for the other two so the document stays diagnosable.
    #
    # No issue is recorded here on purpose. Whatever damaged the ISA also makes
    # it fail the width check in envelope.py, which reports it as A01 naming the
    # exact element at fault. Recording it here too would report one defect
    # three times.
    return _scan_delimiters(source, element_hint=element)


def _scan_delimiters(source: str, element_hint: str = "") -> Delimiters:
    """Guess delimiters by frequency when the ISA cannot be trusted."""
    sample = source[:4096]

    element = element_hint if element_hint and element_hint not in _ILLEGAL_DELIMITERS else ""
    if not element:
        element = _most_frequent(sample, _ELEMENT_CANDIDATES, _DEFAULT_ELEMENT)

    segment = _most_frequent(
        sample, [c for c in _SEGMENT_CANDIDATES if c != element], _DEFAULT_SEGMENT
    )
    # A newline-terminated file is common enough that it deserves an explicit
    # check: if no terminator candidate occurs but lines do, use the newline.
    if segment not in sample and "\n" in sample:
        segment = "\n"

    component = _most_frequent(
        sample,
        [c for c in _COMPONENT_CANDIDATES if c not in (element, segment)],
        _DEFAULT_COMPONENT,
    )

    return Delimiters(
        element=element,
        segment=segment,
        component=component,
        repetition=None,
        from_isa=False,
    )


def _most_frequent(sample: str, candidates: Sequence[str], default: str) -> str:
    best = ""
    best_count = 0
    for char in candidates:
        count = sample.count(char)
        if count > best_count:
            best, best_count = char, count
    return best or default


def _split_segments(
    source: str, delimiters: Delimiters, start: int
) -> list[Segment]:
    """Split the source into segments, tolerating stray whitespace."""
    segments: list[Segment] = []
    terminator = delimiters.segment
    element_sep = delimiters.element

    # Pre-compute line starts once so each segment's line number is an O(log n)
    # bisect rather than a scan of everything before it.
    line_starts = _line_starts(source)

    position = start
    index = 0
    length = len(source)

    while position < length:
        end = source.find(terminator, position)
        if end == -1:
            chunk = source[position:]
            next_position = length
        else:
            chunk = source[position:end]
            next_position = end + len(terminator)

        # Segments are frequently written one per line; the newline belongs to
        # the formatting, not to the data.
        offset = position + (len(chunk) - len(chunk.lstrip("\r\n \t")))
        raw = chunk.strip("\r\n \t")

        if raw:
            parts = raw.split(element_sep)
            segment_id = parts[0].strip().upper()
            segments.append(
                Segment(
                    index=index,
                    id=segment_id,
                    elements=tuple(parts[1:]),
                    line=_line_of(line_starts, offset),
                    offset=offset,
                    raw=raw,
                )
            )
            index += 1

        position = next_position

    return segments


def _line_starts(source: str) -> list[int]:
    starts = [0]
    position = source.find("\n")
    while position != -1:
        starts.append(position + 1)
        position = source.find("\n", position + 1)
    return starts


def _line_of(line_starts: list[int], offset: int) -> int:
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1
