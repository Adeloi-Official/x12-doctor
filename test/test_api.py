# SPDX-License-Identifier: Apache-2.0
"""The public API surface — the accessors a library caller reaches for.

These are exported, so they are tested. An exported helper nobody has ever
called is a bug waiting for the first person who trusts the documentation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import x12doctor
from conftest import CLEAN, FAULTS, read
from x12doctor import PartnerProfile, diagnose, tokenize
from x12doctor.envelope import parse_envelope
from x12doctor.pytest_plugin import (
    assert_diagnoses,
    assert_no_diagnosis,
    assert_valid_x12,
    describe,
)
from x12doctor.semantics import spec_for

CLEAN_850 = read(CLEAN / "850_seed42_lines3.edi")
BROKEN_850 = read(FAULTS / "850_seed42_A02_seCountWrong.edi")
UOM_850 = read(FAULTS / "850_seed42_B01_uomNonstandard.edi")


# ---------------------------------------------------------------------------
# Package surface
# ---------------------------------------------------------------------------


def test_everything_in_dunder_all_is_importable() -> None:
    for name in x12doctor.__all__:
        assert hasattr(x12doctor, name), f"{name} is exported but missing"


def test_version_is_a_release_number() -> None:
    parts = x12doctor.__version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_the_package_ships_a_typed_marker() -> None:
    assert (Path(x12doctor.__file__).parent / "py.typed").exists()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def test_report_accessors() -> None:
    report = diagnose(BROKEN_850, source="order.edi")

    assert report.doc_type == "850"
    assert report.doc_types == ["850"]
    assert report.version == "004010"
    assert report.source == "order.edi"
    assert report.segment_count > 0
    assert report.delimiters is not None

    assert len(report) == len(report.findings)
    assert list(report) == report.findings
    assert report.worst_severity() == "error"
    assert report.codes() == ["A02"]
    assert report.has("A02")
    assert not report.has("B01")


def test_severity_thresholds() -> None:
    warnings_only = diagnose(UOM_850)
    assert warnings_only.ok  # no errors
    assert not warnings_only.exceeds("error")
    assert warnings_only.exceeds("warning")
    assert warnings_only.exceeds("info")


def test_finding_location_reads_as_prose() -> None:
    finding = diagnose(BROKEN_850).findings[0]
    assert finding.location.startswith("segment ")
    assert "(SE)" in finding.location
    assert "element 01" in finding.location


def test_report_json_round_trips() -> None:
    payload = json.loads(diagnose(BROKEN_850, source="x.edi").to_json())
    assert payload["schema_version"] == x12doctor.SCHEMA_VERSION
    assert payload["findings"][0]["context"]["declared"] == 16


def test_a_clean_report_has_an_empty_worst_severity() -> None:
    assert diagnose(CLEAN_850).worst_severity() is None


# ---------------------------------------------------------------------------
# Tokenizer and envelope accessors
# ---------------------------------------------------------------------------


def test_segment_component_splits_composites() -> None:
    doc = tokenize("ISA*00*a>b>c~")
    segment = doc.segments[0]
    assert segment.component(2, 1, doc.delimiters) == "a"
    assert segment.component(2, 3, doc.delimiters) == "c"
    assert segment.component(2, 9, doc.delimiters) == ""


def test_segment_has_distinguishes_blank_from_absent() -> None:
    segment = tokenize("ISA*00*   *value~").segments[0]
    assert segment.has(1)
    assert not segment.has(2)  # present but blank
    assert not segment.has(9)  # absent
    assert segment.element_count == 3


def test_tokenized_document_is_empty() -> None:
    assert tokenize("").is_empty
    assert not tokenize(CLEAN_850).is_empty


def test_transaction_body_excludes_the_brackets() -> None:
    doc = tokenize(CLEAN_850)
    envelope, _ = parse_envelope(doc)
    transaction = envelope.transactions[0]

    assert transaction.segments[0].id == "ST"
    assert transaction.segments[-1].id == "SE"
    assert {s.id for s in transaction.body}.isdisjoint({"ST", "SE"})
    assert len(transaction.body) == len(transaction.segments) - 2


def test_envelope_walks_down_and_back_up() -> None:
    doc = tokenize(CLEAN_850)
    envelope, _ = parse_envelope(doc)

    assert envelope.doc_types() == ["850"]
    assert len(envelope.groups) == 1
    assert len(envelope.transactions) == 1
    assert envelope.orphans == []


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc_type", ["850", "855", "856", "810"])
def test_known_transaction_sets_have_a_spec(doc_type: str) -> None:
    spec = spec_for(doc_type)
    assert spec is not None
    assert spec.doc_type == doc_type
    assert spec.functional_id


def test_unknown_transaction_sets_have_none() -> None:
    assert spec_for("999") is None


# ---------------------------------------------------------------------------
# Partner profile
# ---------------------------------------------------------------------------


def test_from_toml_is_an_alias_for_load(tmp_path: Path) -> None:
    path = tmp_path / "partner.json"
    path.write_text(json.dumps({"required_refs": ["DP"]}))
    assert PartnerProfile.from_toml(path) == PartnerProfile.load(path)


# ---------------------------------------------------------------------------
# The pytest plugin
# ---------------------------------------------------------------------------


def test_assert_valid_x12_passes_a_clean_document() -> None:
    report = assert_valid_x12(CLEAN_850)
    assert report.ok


def test_assert_valid_x12_tolerates_warnings_by_default() -> None:
    assert_valid_x12(UOM_850)
    with pytest.raises(AssertionError, match="B01"):
        assert_valid_x12(UOM_850, allow_warnings=False)


def test_assert_valid_x12_reports_what_was_wrong() -> None:
    with pytest.raises(AssertionError) as error:
        assert_valid_x12(BROKEN_850)
    assert "A02" in str(error.value)
    assert "SE01" in str(error.value)


def test_assert_diagnoses_finds_the_code() -> None:
    assert_diagnoses(BROKEN_850, "A02")
    assert_diagnoses(BROKEN_850, "A02", exactly=True)


def test_assert_diagnoses_rejects_a_missing_code() -> None:
    with pytest.raises(AssertionError, match="expected B01"):
        assert_diagnoses(BROKEN_850, "B01")


def test_assert_diagnoses_exactly_rejects_extras() -> None:
    document = BROKEN_850.replace("*EA*", "*EACH*")
    assert_diagnoses(document, "A02", "B01", exactly=True)
    with pytest.raises(AssertionError, match="but also got"):
        assert_diagnoses(document, "A02", exactly=True)


def test_a_typo_in_the_expected_code_fails_loudly() -> None:
    """Otherwise the assertion could never pass and nobody would know why."""
    with pytest.raises(KeyError, match="unknown diagnostic code"):
        assert_diagnoses(BROKEN_850, "A99")


def test_assert_no_diagnosis() -> None:
    assert_no_diagnosis(CLEAN_850, "A02", "B01")
    with pytest.raises(AssertionError, match="not to be diagnosed"):
        assert_no_diagnosis(BROKEN_850, "A02")


def test_describe_renders_a_readable_summary() -> None:
    assert describe(diagnose(CLEAN_850)) == "no findings"
    text = describe(diagnose(BROKEN_850, source="order.edi"))
    assert "order.edi" in text
    assert "A02" in text
