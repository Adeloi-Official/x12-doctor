# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures and corpus access."""

from __future__ import annotations

from pathlib import Path

import pytest

from x12doctor import PartnerProfile

CORPUS = Path(__file__).parent / "corpus"
CLEAN = CORPUS / "clean"
FAULTS = CORPUS / "faults"
FLOWS = CORPUS / "flows"
SCANS = CORPUS / "scans"
EDGE = CORPUS / "edge"


def corpus_files(directory: Path, pattern: str = "*.edi") -> list[Path]:
    """Corpus files, sorted so parametrised test IDs stay stable."""
    return sorted(directory.glob(pattern))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture
def clean_850() -> str:
    return read(CLEAN / "850_seed42_lines3.edi")


@pytest.fixture
def clean_856() -> str:
    return read(CLEAN / "856_seed42_lines3.edi")


@pytest.fixture
def clean_810() -> str:
    return read(CLEAN / "810_seed42_lines3.edi")


@pytest.fixture
def clean_997() -> str:
    return read(CLEAN / "997_seed42.edi")


@pytest.fixture
def dp_partner() -> PartnerProfile:
    """The profile matching edi-fixtures' default partner, which requires REF*DP."""
    return PartnerProfile(name="Test Partner", required_refs=("DP",))
