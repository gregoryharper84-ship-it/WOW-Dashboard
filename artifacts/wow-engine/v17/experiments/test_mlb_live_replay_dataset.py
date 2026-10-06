from copy import deepcopy

import pytest

from v17.experiments.mlb_live_replay_dataset import (
    MLBReplayError,
    build_mlb_live_replay_rows,
)


HASH = "a" * 64


def _movement(runner_id, start, end, *, is_out=False):
    return {
        "details": {"runner": {"id": runner_id}},
        "movement": {"start": start, "end": end, "isOut": is_out},
    }


def _play(
    index,
    *,
    inning,
    half,
    outs,
    home_score,
    away_score,
    runners=None,
    batter=10,
    pitcher=20,
):
    return {
        "about": {
            "atBatIndex": index,
            "inning": inning,
            "halfInning": half,
            "isComplete": True,
            "endTime": f"2026-07-01T00:00:{index:02d}Z",
        },
        "count": {"outs": outs},
        "result": {"homeScore": home_score, "awayScore": away_score},
        "matchup": {
            "batter": {"id": batter},
            "pitcher": {"id": pitcher},
        },
        "runners": runners or [],
    }


def _feed(*, final_home=4, final_away=2, later_home=4, later_away=2):
    plays = [
        _play(
            0,
            inning=1,
            half="top",
            outs=0,
            home_score=0,
            away_score=0,
            runners=[_movement(101, None, "1B")],
        ),
        _play(
            1,
            inning=1,
            half="top",
            outs=1,
            home_score=0,
            away_score=0,
            runners=[_movement(101, "1B", "2B")],
        ),
        _play(
            2,
            inning=1,
            half="top",
            outs=3,
            home_score=0,
            away_score=0,
            runners=[_movement(101, "2B", None, is_out=True)],
        ),
        _play(
            3,
            inning=8,
            half="bottom",
            outs=1,
            home_score=later_home,
            away_score=later_away,
        ),
    ]
    return {
        "gameData": {
            "status": {"abstractGameState": "Final"},
            "teams": {
                "home": {"name": "Home Club"},
                "away": {"name": "Away Club"},
            },
        },
        "liveData": {
            "linescore": {
                "teams": {
                    "home": {"runs": final_home},
                    "away": {"runs": final_away},
                }
            },
            "plays": {"allPlays": plays},
        },
    }


def test_builds_chronological_point_in_time_rows_without_execution():
    rows, receipt = build_mlb_live_replay_rows(
        _feed(),
        official_event_id="777",
        source_payload_hash=HASH,
    )

    assert len(rows) == 4
    assert receipt.row_count == 4
    assert receipt.final_outcome == "HOME_WIN"
    assert receipt.probability_publishable is False
    assert receipt.automatic_promotion is False
    assert receipt.can_execute is False

    first = rows[0]
    assert first["label"] == "HOME_WIN"
    assert first["features"]["plate_appearance_index"] == 0
    assert first["features"]["home_score"] == 0
    assert first["features"]["away_score"] == 0
    assert first["features"]["base_occupancy"] == {
        "first": True,
        "second": False,
        "third": False,
    }
    assert first["probability_publishable"] is False
    assert first["automatic_promotion"] is False
    assert first["can_execute"] is False

    second = rows[1]["features"]
    assert second["base_occupancy"] == {
        "first": False,
        "second": True,
        "third": False,
    }

    end_half = rows[2]["features"]
    assert end_half["outs"] == 3
    assert end_half["base_occupancy"] == {
        "first": False,
        "second": False,
        "third": False,
    }


def test_future_scoring_and_final_label_cannot_modify_earlier_features():
    home_win = _feed(final_home=4, final_away=2, later_home=4, later_away=2)
    away_win = deepcopy(home_win)
    away_win["liveData"]["linescore"]["teams"]["home"]["runs"] = 1
    away_win["liveData"]["linescore"]["teams"]["away"]["runs"] = 5
    away_win["liveData"]["plays"]["allPlays"][3]["result"] = {
        "homeScore": 1,
        "awayScore": 5,
    }

    home_rows, _ = build_mlb_live_replay_rows(
        home_win,
        official_event_id="777",
        source_payload_hash=HASH,
    )
    away_rows, _ = build_mlb_live_replay_rows(
        away_win,
        official_event_id="777",
        source_payload_hash=HASH,
    )

    assert home_rows[0]["features"] == away_rows[0]["features"]
    assert home_rows[1]["features"] == away_rows[1]["features"]
    assert home_rows[2]["features"] == away_rows[2]["features"]
    assert home_rows[0]["label"] == "HOME_WIN"
    assert away_rows[0]["label"] == "AWAY_WIN"


def test_feature_payload_never_contains_final_outcome_or_probability():
    rows, _ = build_mlb_live_replay_rows(
        _feed(),
        official_event_id="777",
        source_payload_hash=HASH,
    )
    forbidden = {
        "final_home_score",
        "final_away_score",
        "final_outcome",
        "model_probability",
        "market_probability",
        "sportsbook_probability",
        "calibrated_probability",
    }
    for row in rows:
        assert forbidden.isdisjoint(row["features"])


def test_nonfinal_feed_fails_closed():
    feed = _feed()
    feed["gameData"]["status"]["abstractGameState"] = "Live"

    with pytest.raises(MLBReplayError) as caught:
        build_mlb_live_replay_rows(
            feed,
            official_event_id="777",
            source_payload_hash=HASH,
        )

    assert caught.value.code == "MLB_LIVE_REPLAY_EVENT_NOT_FINAL"


def test_final_tie_fails_closed_instead_of_inventing_binary_label():
    with pytest.raises(MLBReplayError) as caught:
        build_mlb_live_replay_rows(
            _feed(final_home=3, final_away=3),
            official_event_id="777",
            source_payload_hash=HASH,
        )

    assert caught.value.code == "MLB_LIVE_REPLAY_FINAL_TIE_UNSUPPORTED"


def test_source_hash_is_mandatory_and_typed():
    with pytest.raises(MLBReplayError) as caught:
        build_mlb_live_replay_rows(
            _feed(),
            official_event_id="777",
            source_payload_hash="not-a-hash",
        )

    assert caught.value.code == "MLB_LIVE_REPLAY_SOURCE_HASH_INVALID"


def test_non_strict_event_order_is_rejected():
    feed = _feed()
    feed["liveData"]["plays"]["allPlays"][1]["about"]["atBatIndex"] = 0

    with pytest.raises(MLBReplayError) as caught:
        build_mlb_live_replay_rows(
            feed,
            official_event_id="777",
            source_payload_hash=HASH,
        )

    assert caught.value.code == "MLB_LIVE_REPLAY_SEQUENCE_NOT_STRICT"
