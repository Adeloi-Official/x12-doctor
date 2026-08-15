# SPDX-License-Identifier: Apache-2.0
"""Catalog integrity.

``catalog.py`` is the single source of truth for every diagnosis: the checks
read their explanatory text from it, ``x12doctor codes`` prints it, and
``docs/diagnostics.md`` is generated from it. These tests keep it honest, so
that the three can never disagree.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from x12doctor import CATALOG, CATEGORIES
from x12doctor.catalog import Diagnostic, get, make_finding

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "x12doctor"
DOCS = ROOT / "docs" / "diagnostics.md"


@pytest.mark.parametrize("code", sorted(CATALOG), ids=sorted(CATALOG))
def test_every_entry_is_complete(code: str) -> None:
    entry = CATALOG[code]
    assert entry.code == code
    assert entry.title
    assert entry.severity in ("error", "warning", "info")
    assert entry.category in CATEGORIES
    for field in ("what", "why", "hint"):
        text = getattr(entry, field)
        assert text, f"{code} has no {field}"
        assert text[0].isupper(), f"{code}.{field} does not start with a capital"
        assert text.rstrip().endswith("."), f"{code}.{field} does not end with a period"


@pytest.mark.parametrize("code", sorted(CATALOG), ids=sorted(CATALOG))
def test_codes_follow_the_category_letter(code: str) -> None:
    assert code[0] == CATALOG[code].category
    assert re.fullmatch(r"[A-Z]\d{2}", code)


def test_codes_are_contiguous_within_each_category() -> None:
    """A gap means a code was removed, which breaks anyone pinning it."""
    for letter in CATEGORIES:
        numbers = sorted(
            int(code[1:]) for code in CATALOG if code.startswith(letter)
        )
        if not numbers:
            continue
        assert numbers == list(range(numbers[0], numbers[0] + len(numbers))), (
            f"category {letter} has a gap: {numbers}"
        )


def test_titles_are_unique() -> None:
    titles = [entry.title for entry in CATALOG.values()]
    assert len(titles) == len(set(titles))


def test_fixture_attributions_are_unique() -> None:
    """Two diagnoses claiming the same fault would make the mapping ambiguous."""
    fixtures = [e.fixture for e in CATALOG.values() if e.fixture]
    assert len(fixtures) == len(set(fixtures))


def test_lookup_is_case_insensitive_and_fails_loudly() -> None:
    assert get("b01") is get("B01")
    with pytest.raises(KeyError, match="unknown diagnostic code"):
        get("Z99")


def test_make_finding_fills_text_from_the_catalog() -> None:
    finding = make_finding("A02", "SE01 says 13, actually 14.")
    entry = get("A02")
    assert finding.severity == entry.severity
    assert finding.why == entry.why
    assert finding.hint == entry.hint


def test_make_finding_allows_a_check_to_override() -> None:
    finding = make_finding("A02", "message", severity="warning", hint="Do this.")
    assert finding.severity == "warning"
    assert finding.hint == "Do this."
    assert finding.why == get("A02").why  # untouched


# ---------------------------------------------------------------------------
# The catalog and the code must agree
# ---------------------------------------------------------------------------


def _codes_used_in_source() -> set[str]:
    """Every code passed to make_finding anywhere in the package."""
    used: set[str] = set()
    pattern = re.compile(r'make_finding\(\s*"([A-Z]\d{2})"')
    for path in SOURCE.rglob("*.py"):
        used |= set(pattern.findall(path.read_text(encoding="utf-8")))
    return used


def test_every_catalog_entry_is_actually_raised_somewhere() -> None:
    """A documented diagnosis no check can produce is a promise not kept."""
    unused = sorted(set(CATALOG) - _codes_used_in_source())
    assert not unused, f"in the catalog but never raised: {', '.join(unused)}"


def test_every_raised_code_is_in_the_catalog() -> None:
    """make_finding would raise KeyError at runtime; catch it at test time."""
    unknown = sorted(_codes_used_in_source() - set(CATALOG))
    assert not unknown, f"raised but not in the catalog: {', '.join(unknown)}"


# ---------------------------------------------------------------------------
# The catalog and the docs must agree
# ---------------------------------------------------------------------------


def test_generated_docs_are_up_to_date() -> None:
    """docs/diagnostics.md is generated; a stale copy is worse than none."""
    generator = ROOT / "tools" / "gen-docs.py"
    result = subprocess.run(
        [sys.executable, str(generator), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, (
        "docs/diagnostics.md is out of date — run 'python tools/gen-docs.py'\n"
        + result.stdout
        + result.stderr
    )


def test_readme_lists_every_code() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [code for code in CATALOG if f"`{code}`" not in readme]
    assert not missing, f"not listed in the README: {', '.join(missing)}"


def test_readme_states_the_right_number_of_diagnoses() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert str(len(CATALOG)) in readme.split("## Commands")[0]


# ---------------------------------------------------------------------------
# Wording conventions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", CATALOG.values(), ids=lambda e: e.code)
def test_the_hint_is_an_instruction(entry: Diagnostic) -> None:
    """'Fix:' is followed by what to do, not by a hedge about what might help."""
    for weasel in ("you should", "you may want", "consider ", "perhaps", "maybe"):
        assert weasel not in entry.hint.lower(), (
            f"{entry.code} hedges instead of instructing: {entry.hint!r}"
        )


@pytest.mark.parametrize("entry", CATALOG.values(), ids=lambda e: e.code)
def test_text_is_not_truncated_mid_sentence(entry: Diagnostic) -> None:
    for field in ("what", "why", "hint"):
        text = getattr(entry, field)
        assert "  " not in text, f"{entry.code}.{field} has a double space"
        assert not text.endswith(" ."), f"{entry.code}.{field} has a floating period"
