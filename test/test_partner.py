# SPDX-License-Identifier: Apache-2.0
"""Partner profile loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from x12doctor import PartnerProfile, ProfileError


def test_a_full_toml_profile_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "partner.toml"
    path.write_text(
        """
        name = "Example Partner"
        isa_qualifier = "ZZ"
        sender_id = "SENDERID"
        receiver_id = "RECEIVERID"
        required_refs = ["DP", "IA"]
        uom_whitelist = ["EA", "CS"]
        usage_indicator = "P"
        version = "004010"

        [required_refs_by_doc]
        856 = ["BM"]

        [required_segments]
        850 = ["FOB"]
        """
    )
    profile = PartnerProfile.load(path)

    assert profile.name == "Example Partner"
    assert profile.required_refs == ("DP", "IA")
    assert profile.uom_whitelist == ("EA", "CS")
    assert profile.refs_for("856") == ("DP", "IA", "BM")
    assert profile.refs_for("850") == ("DP", "IA")
    assert profile.segments_for("850") == ("FOB",)
    assert profile.segments_for("810") == ()


def test_a_nested_partner_table_is_accepted(tmp_path: Path) -> None:
    """A profile may live inside a larger config file."""
    path = tmp_path / "config.toml"
    path.write_text('[partner]\nrequired_refs = ["DP"]\n')
    assert PartnerProfile.load(path).required_refs == ("DP",)


def test_json_profiles_are_first_class(tmp_path: Path) -> None:
    path = tmp_path / "partner.json"
    path.write_text(json.dumps({"required_refs": ["DP"], "sender_id": "X"}))
    profile = PartnerProfile.load(path)
    assert profile.required_refs == ("DP",)
    assert profile.sender_id == "X"


def test_qualifiers_fall_back_to_the_shared_one() -> None:
    profile = PartnerProfile(isa_qualifier="ZZ")
    assert profile.expected_sender_qualifier == "ZZ"
    assert profile.expected_receiver_qualifier == "ZZ"

    split = PartnerProfile(isa_qualifier="ZZ", sender_qualifier="01")
    assert split.expected_sender_qualifier == "01"
    assert split.expected_receiver_qualifier == "ZZ"


def test_the_default_profile_constrains_nothing() -> None:
    assert PartnerProfile().is_empty
    assert not PartnerProfile(required_refs=("DP",)).is_empty


def test_merge_returns_a_copy() -> None:
    original = PartnerProfile(sender_id="A")
    merged = original.merge(sender_id="B")
    assert original.sender_id == "A"
    assert merged.sender_id == "B"


# ---------------------------------------------------------------------------
# Validation — a typo in a profile must fail loudly, not be ignored
# ---------------------------------------------------------------------------


def test_an_unknown_key_names_the_known_ones(tmp_path: Path) -> None:
    path = tmp_path / "partner.json"
    path.write_text(json.dumps({"required_ref": ["DP"]}))
    with pytest.raises(ProfileError) as error:
        PartnerProfile.load(path)
    assert "required_ref" in str(error.value)
    assert "required_refs" in str(error.value)


def test_a_string_where_a_list_belongs_is_rejected(tmp_path: Path) -> None:
    """`required_refs = "DP"` would otherwise silently mean ['D', 'P']."""
    path = tmp_path / "partner.json"
    path.write_text(json.dumps({"required_refs": "DP"}))
    with pytest.raises(ProfileError, match="list of strings"):
        PartnerProfile.load(path)


def test_a_bad_usage_indicator_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "partner.json"
    path.write_text(json.dumps({"usage_indicator": "X"}))
    with pytest.raises(ProfileError, match="production"):
        PartnerProfile.load(path)


def test_a_lowercase_usage_indicator_is_normalised(tmp_path: Path) -> None:
    path = tmp_path / "partner.json"
    path.write_text(json.dumps({"usage_indicator": "t"}))
    assert PartnerProfile.load(path).usage_indicator == "T"


def test_a_missing_file_raises_profile_error() -> None:
    with pytest.raises(ProfileError, match="cannot read"):
        PartnerProfile.load("/nonexistent/partner.toml")


def test_malformed_json_raises_profile_error(tmp_path: Path) -> None:
    path = tmp_path / "partner.json"
    path.write_text("{not json")
    with pytest.raises(ProfileError):
        PartnerProfile.load(path)


def test_malformed_toml_raises_profile_error(tmp_path: Path) -> None:
    path = tmp_path / "partner.toml"
    path.write_text("required_refs = [unquoted]")
    with pytest.raises(ProfileError):
        PartnerProfile.load(path)


def test_a_top_level_list_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "partner.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(ProfileError, match="table"):
        PartnerProfile.load(path)
