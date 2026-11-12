"""Synthetic NHL official-schema tests; not live API rights or feed proof."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.nhl_player_box_outcome_research import (
    normalize_nhl_player_box_outcomes, NHLPlayerBoxResearchHold,
)

GAME_ID = "2025020910"
START = "2026-02-28T00:00:00Z"
FETCHED = "2026-03-01T01:00:00Z"


def fixture():
    return {
        "id": int(GAME_ID), "season": 20252026, "gameType": 2,
        "gameState": "OFF", "startTimeUTC": START,
        "homeTeam": {"abbrev": "TOR"}, "awayTeam": {"abbrev": "MTL"},
        "playerByGameStats": {
            "homeTeam": {
                "forwards": [{"playerId": 8479999, "goals": 1,
                              "assists": 2, "points": 3, "sog": 4}],
                "defense": [{"playerId": 8471111, "goals": 0,
                             "assists": 1, "points": 1, "sog": 2}],
                "goalies": [{"playerId": 8470001, "saves": 26, "shotsAgainst": 28}],
            },
            "awayTeam": {
                "forwards": [{"playerId": 8472222, "goals": 1,
                              "assists": 0, "points": 1, "sog": 3}],
                "defense": [], "goalies": [],
            },
        },
    }


def run(data=None, **kw):
    return normalize_nhl_player_box_outcomes(
        fixture() if data is None else data,
        expected_game_id=kw.get("expected_game_id", GAME_ID),
        expected_event_start_utc=kw.get("expected_event_start_utc", START),
        retrieved_at=kw.get("retrieved_at", FETCHED),
    )


def test_normalization_no_market_authority():
    r = run()
    assert r["player_count"] == 4 and r["outcome_count"] == 13
    assert {x["stat_type"] for x in r["rows"]} == {
        "GOALS", "ASSISTS", "POINTS", "SHOTS_ON_GOAL", "GOALIE_SAVES"
    }
    assert all(x["historical_closing_line"] is None for x in r["rows"])
    assert all(x["can_execute"] is False for x in r["rows"])
    assert all(r[k] is False for k in (
        "source_rights_verified", "historical_training_authorized",
        "historical_market_line_verified", "probability_publishable", "can_execute",
    ))
    assert len(r["source_payload_sha256"]) == 64
    assert r["event_start_verified_by_box"] is True


@pytest.mark.parametrize("mutate,code", [
    (lambda x: x.update({"id": 2025020911}), "NHL_PLAYER_BOX_IDENTITY_MISMATCH"),
    (lambda x: x.update({"season": 20242025}), "NHL_PLAYER_BOX_NOT_FINAL_REGULAR_SEASON"),
    (lambda x: x.update({"gameState": "LIVE"}), "NHL_PLAYER_BOX_NOT_FINAL_REGULAR_SEASON"),
    (lambda x: x.update({"gameType": 3}), "NHL_PLAYER_BOX_NOT_FINAL_REGULAR_SEASON"),
    (lambda x: x["playerByGameStats"]["homeTeam"]["forwards"][0].update({"points": 4}),
     "NHL_PLAYER_BOX_STAT_CONTRADICTION"),
    (lambda x: x["playerByGameStats"]["homeTeam"]["goalies"][0].update({"saves": 29}),
     "NHL_PLAYER_BOX_STAT_CONTRADICTION"),
    (lambda x: x["playerByGameStats"]["awayTeam"]["forwards"][0].update({"playerId": 8479999}),
     "NHL_PLAYER_BOX_DUPLICATE_PLAYER"),
    (lambda x: x["playerByGameStats"]["homeTeam"].pop("forwards"),
     "NHL_PLAYER_BOX_SCHEMA_INVALID"),
    (lambda x: x["playerByGameStats"]["homeTeam"]["forwards"][0].update({"sog": -1}),
     "NHL_PLAYER_BOX_STAT_INVALID"),
    (lambda x: x.update({"startTimeUTC": "2026-03-01T00:00:00Z"}),
     "NHL_PLAYER_BOX_IDENTITY_MISMATCH"),
])
def test_invalid_box_fails_closed(mutate, code):
    x = fixture()
    mutate(x)
    with pytest.raises(NHLPlayerBoxResearchHold, match=code):
        run(x)


def test_rejects_pregame_and_naive_capture():
    with pytest.raises(NHLPlayerBoxResearchHold, match="NHL_PLAYER_BOX_NOT_POSTGAME"):
        run(retrieved_at="2026-02-27T23:00:00Z")
    with pytest.raises(NHLPlayerBoxResearchHold, match="NHL_PLAYER_BOX_TIMESTAMP_INVALID"):
        run(retrieved_at="2026-03-01T00:00:00")


def test_missing_source_start_is_explicitly_unverified():
    x = fixture()
    del x["startTimeUTC"]
    r = run(x)
    assert r["event_start_verified_by_box"] is False
    assert r["historical_training_authorized"] is False
