"""Tests that source rows cannot fabricate a valid NCAAB training event."""
import pytest
from v17.ncaab_source_pair_integrity import NCAABSourcePairError, validated_team_box_pair


def row(side, team, source="a" * 64, season="2026", score="74", gid="401"):
    return {"team_home_away": side, "team_id": team, "_source_sha256": source,
            "_source_url": "https://example.test/team_box_2026.csv", "season": season,
            "team_score": score, "game_id": gid}


def test_complete_unambiguous_pair_is_accepted():
    home, away = validated_team_box_pair("401", [row("home", "H"), row("away", "A")])
    assert home["team_id"] == "H" and away["team_id"] == "A"


def test_duplicate_home_does_not_select_first_record():
    with pytest.raises(NCAABSourcePairError, match="NCAAB_SOURCE_GAME_PAIR_AMBIGUOUS"):
        validated_team_box_pair("401", [row("home", "H"), row("home", "OTHER"), row("away", "A")])


def test_same_team_on_both_sides_does_not_create_win_label():
    with pytest.raises(NCAABSourcePairError, match="NCAAB_SOURCE_PARTICIPANT_COLLISION"):
        validated_team_box_pair("401", [row("home", "H"), row("away", "H")])


def test_cross_season_source_mix_is_rejected():
    with pytest.raises(NCAABSourcePairError, match="NCAAB_SOURCE_PAIR_PROVENANCE_MISMATCH"):
        validated_team_box_pair("401", [row("home", "H"), row("away", "A", season="2025")])
    with pytest.raises(NCAABSourcePairError, match="NCAAB_SOURCE_PAIR_PROVENANCE_MISMATCH"):
        validated_team_box_pair("401", [row("home", "H"), row("away", "A", source="b" * 64)])


def test_incomplete_or_unsettled_game_is_skipped_without_label():
    assert validated_team_box_pair("401", [row("home", "H")]) is None
    assert validated_team_box_pair("401", [row("home", "H"), row("away", "A", score="")]) is None


def test_mismatched_row_identity_is_rejected():
    with pytest.raises(NCAABSourcePairError, match="NCAAB_SOURCE_GAME_ID_CONFLICT"):
        validated_team_box_pair("401", [row("home", "H"), row("away", "A", gid="402")])
