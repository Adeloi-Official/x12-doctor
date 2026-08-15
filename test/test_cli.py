# SPDX-License-Identifier: Apache-2.0
"""Command line behaviour, especially exit codes — CI depends on them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import CLEAN, EDGE, FAULTS, FLOWS
from x12doctor.cli import EXIT_FINDINGS, EXIT_OK, EXIT_UNUSABLE, main

CLEAN_850 = CLEAN / "850_seed42_lines3.edi"
BROKEN_850 = FAULTS / "850_seed42_A02_seCountWrong.edi"
UOM_850 = FAULTS / "850_seed42_B01_uomNonstandard.edi"


@pytest.fixture(autouse=True)
def no_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assert on text, not on escape codes."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_clean_document_exits_zero() -> None:
    assert main(["check", str(CLEAN_850)]) == EXIT_OK


def test_document_with_an_error_exits_one() -> None:
    assert main(["check", str(BROKEN_850)]) == EXIT_FINDINGS


def test_warnings_alone_exit_zero_by_default() -> None:
    """--fail-on defaults to error, so a warning must not break a build."""
    assert main(["check", str(UOM_850)]) == EXIT_OK


def test_fail_on_warning_promotes_them() -> None:
    assert main(["check", str(UOM_850), "--fail-on", "warning"]) == EXIT_FINDINGS


def test_unreadable_file_exits_two() -> None:
    assert main(["check", "/nonexistent/order.edi"]) == EXIT_UNUSABLE


def test_non_x12_input_exits_two_not_one() -> None:
    """CI needs 'not a document' distinguishable from 'a document with problems'."""
    assert main(["check", str(EDGE / "not_x12.edi")]) == EXIT_UNUSABLE


def test_empty_file_exits_two() -> None:
    assert main(["check", str(EDGE / "empty.edi")]) == EXIT_UNUSABLE


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_check_human_output_names_code_and_location(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["check", str(BROKEN_850)])
    out = capsys.readouterr().out
    assert "A02" in out
    assert "ERROR" in out
    assert "850 Purchase Order" in out
    assert "Why this happens:" in out
    assert "Fix:" in out


def test_brief_drops_the_explanation(capsys: pytest.CaptureFixture[str]) -> None:
    main(["check", str(BROKEN_850), "--brief"])
    out = capsys.readouterr().out
    assert "A02" in out
    assert "Why this happens:" not in out


def test_check_json_output_is_parseable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["check", str(BROKEN_850), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema_version"] == "1.0"
    assert payload["doc_type"] == "850"
    assert payload["ok"] is False
    assert payload["findings"][0]["code"] == "A02"
    assert payload["findings"][0]["why"]
    assert payload["counts"]["error"] == 1


def test_json_output_is_byte_identical_across_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Determinism is a promise the README makes; hold it to that."""
    main(["check", str(BROKEN_850), "--format", "json"])
    first = capsys.readouterr().out
    main(["check", str(BROKEN_850), "--format", "json"])
    assert capsys.readouterr().out == first


def test_several_files_at_once(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["check", str(CLEAN_850), str(BROKEN_850), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["reports"]) == 2
    assert code == EXIT_FINDINGS


def test_stdin_is_read_with_a_dash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io
    import sys

    data = BROKEN_850.read_bytes()
    stream = io.TextIOWrapper(io.BytesIO(data))
    monkeypatch.setattr(sys, "stdin", stream)

    assert main(["check", "-"]) == EXIT_FINDINGS
    assert "(stdin)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


def test_explain_translates_an_accepted_997(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["explain", str(CLEAN / "997_seed42.edi")])
    out = capsys.readouterr().out
    assert "Accepted" in out
    assert code == EXIT_OK


def test_explain_reports_a_rejection(capsys: pytest.CaptureFixture[str]) -> None:
    path = next((FLOWS / "C03_fa997Rejected").glob("997_*.edi"))
    code = main(["explain", str(path)])
    assert "Rejected" in capsys.readouterr().out
    assert code == EXIT_FINDINGS


def test_explain_json_carries_the_decoded_structure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["explain", str(CLEAN / "997_seed42.edi"), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "accepted"
    assert payload["groups"][0]["functional_id"] == "PO"


def test_explaining_a_non_997_exits_two() -> None:
    assert main(["explain", str(CLEAN_850)]) == EXIT_UNUSABLE


def test_explain_with_a_missing_original_exits_two() -> None:
    code = main(
        ["explain", str(CLEAN / "997_seed42.edi"), "--original", "/nope.edi"]
    )
    assert code == EXIT_UNUSABLE


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def test_scan_reports_across_files(capsys: pytest.CaptureFixture[str]) -> None:
    main(["scan", str(FLOWS / "D03_asnQtyMismatch")])
    out = capsys.readouterr().out
    assert "Across files" in out
    assert "D03" in out


def test_scan_json(capsys: pytest.CaptureFixture[str]) -> None:
    main(["scan", str(FLOWS / "clean"), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["file_count"] == 5
    assert payload["ok"] is True


def test_scan_of_a_missing_directory_exits_two() -> None:
    assert main(["scan", "/nonexistent/outbox"]) == EXIT_UNUSABLE


def test_scan_of_a_directory_with_no_edi_exits_two(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("nothing here")
    assert main(["scan", str(tmp_path)]) == EXIT_UNUSABLE


# ---------------------------------------------------------------------------
# codes
# ---------------------------------------------------------------------------


def test_codes_lists_the_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["codes"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "A02" in out
    assert "Structure & envelope" in out
    assert "Acknowledgments (997)" in out


def test_codes_explains_one_diagnosis(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["codes", "B01"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "What it checks" in out
    assert "Why it happens in production" in out
    assert "How to fix it" in out
    assert "uomNonstandard" in out  # the matching edi-fixtures fault


def test_codes_is_case_insensitive(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["codes", "b01"]) == EXIT_OK


def test_unknown_code_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["codes", "Z99"]) == EXIT_UNUSABLE
    assert "unknown diagnostic code" in capsys.readouterr().err


def test_codes_json(capsys: pytest.CaptureFixture[str]) -> None:
    main(["codes", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["diagnostics"]) >= 24
    assert payload["categories"]["A"] == "Structure & envelope"


# ---------------------------------------------------------------------------
# Partner profiles
# ---------------------------------------------------------------------------


def test_partner_profile_is_applied(tmp_path: Path) -> None:
    profile = tmp_path / "partner.toml"
    profile.write_text('required_refs = ["ZZ"]\n')
    code = main(["check", str(CLEAN_850), "--partner", str(profile)])
    assert code == EXIT_FINDINGS


def test_a_broken_profile_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / "partner.toml"
    profile.write_text("required_refs = not-a-list\n")
    assert main(["check", str(CLEAN_850), "--partner", str(profile)]) == EXIT_UNUSABLE
    assert "x12doctor:" in capsys.readouterr().err


def test_json_profiles_work_too(tmp_path: Path) -> None:
    profile = tmp_path / "partner.json"
    profile.write_text(json.dumps({"required_refs": ["ZZ"]}))
    assert main(["check", str(CLEAN_850), "--partner", str(profile)]) == EXIT_FINDINGS


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def test_no_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_OK
    assert "x12doctor" in capsys.readouterr().out


def test_version_flag() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0


def test_no_color_strips_escape_codes(capsys: pytest.CaptureFixture[str]) -> None:
    main(["check", str(BROKEN_850), "--no-color"])
    assert "\x1b[" not in capsys.readouterr().out
