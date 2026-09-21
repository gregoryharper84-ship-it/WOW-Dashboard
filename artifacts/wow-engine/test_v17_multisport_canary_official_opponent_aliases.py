from v17 import multisport_prop_live_canary as subject


def test_mlb_canary_uses_official_team_abbreviation_opponents():
    assert {row["opponent"] for row in subject.CONFIGS["MLB"]} == {"BAL", "TOR"}


def test_wnba_canary_uses_official_team_tricode_opponents():
    assert {row["opponent"] for row in subject.CONFIGS["WNBA"]} == {"ATL", "NYL"}
