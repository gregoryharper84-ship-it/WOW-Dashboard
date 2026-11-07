from datetime import datetime, timedelta, timezone
from pathlib import Path

from ncaaf_feature_compiler import NCAAFFeatureCompileBlocked, compile_official_event_feature_row
from ncaaf_pregame_feature_acquisition import (
    acquire_schedule_geometry,
    reduced_challenger_contract,
    schedule_geometry_feature_row,
)

KICKOFF = datetime(2026, 10, 10, 23, tzinfo=timezone.utc)
SCHEDULE_AT = KICKOFF - timedelta(days=1)
VENUE_AT = KICKOFF - timedelta(days=2)


def _game(event_id, start, home, away, venue, **extra):
    return {
        "official_event_id": event_id,
        "event_start_time": start.isoformat(),
        "home_team": home,
        "away_team": away,
        "venue_id": venue,
        "neutral_site": False,
        **extra,
    }


def _venues():
    return [
        {"id": "austin", "latitude": 30.2836, "longitude": -97.7325},
        {"id": "norman", "latitude": 35.2058, "longitude": -97.4422},
        {"id": "fort-worth", "latitude": 32.7092, "longitude": -97.3681},
    ]


def _slate(**scores):
    return [
        _game("prior-home", KICKOFF - timedelta(days=7), "Texas", "Georgia", "austin", **scores),
        _game("prior-away", KICKOFF - timedelta(days=6), "Kansas", "Oklahoma", "fort-worth"),
        _game("target", KICKOFF, "Texas", "Oklahoma", "austin"),
    ]


def _acquire(games=None, **kwargs):
    return acquire_schedule_geometry(
        _slate() if games is None else games,
        schedule_retrieved_at=kwargs.pop("schedule_retrieved_at", SCHEDULE_AT),
        venues=kwargs.pop("venues", _venues()),
        venue_retrieved_at=kwargs.pop("venue_retrieved_at", VENUE_AT),
    )


def test_one_event_gets_both_rest_slots_and_v1_stays_incomplete():
    receipt = _acquire()
    assert receipt["schedule_geometry_complete_rows"] == 1
    assert receipt["v1_complete_rows"] == 0
    assert receipt["validation_db_rows_written"] == 0
    assert receipt["registry_applied"] is False
    assert receipt["scores_used"] is False
    assert receipt["can_execute"] is False
    rows = [row for row in receipt["evidence_rows"] if row["official_event_id"] == "target"]
    assert {row["scope"] for row in rows} == {"HOME", "AWAY"}
    home = next(row for row in rows if row["scope"] == "HOME")
    away = next(row for row in rows if row["scope"] == "AWAY")
    assert home["payload"]["prior_event_id"] == "prior-home"
    assert away["payload"]["prior_event_id"] == "prior-away"
    assert home["payload"]["rest_days"] == 7.0
    assert away["payload"]["rest_days"] == 6.0
    assert away["payload"]["travel_distance_miles"] == 168.962
    assert home["evidence_timestamp"] < home["event_start_time"]
    assert away["evidence_timestamp"] < away["event_start_time"]
    feature = schedule_geometry_feature_row(receipt, "target")
    assert feature["feature_schema_version"] == "NCAAF_FEATURES_SCHEDULE_GEOMETRY_V1"
    assert feature["v1_compatible"] is False
    assert feature["travel_distance_miles"] == away["payload"]["travel_distance_miles"]
    game = {
        "official_event_id": "target",
        "event_start_time": KICKOFF.isoformat(),
        "home_team": "Texas",
        "away_team": "Oklahoma",
    }
    try:
        compile_official_event_feature_row(game, rows)
    except NCAAFFeatureCompileBlocked as exc:
        assert exc.code == "NCAAF_MODEL_FEATURE_EVIDENCE_INCOMPLETE"
        assert "TEAM_POWER:HOME" in exc.blockers
        assert "REST_TRAVEL:HOME" not in exc.blockers
        assert "WEATHER:EVENT" in exc.blockers
    else:
        raise AssertionError("partial evidence must not compile as NCAAF_FEATURES_V1")
    missing = {
        item["slot"]
        for item in receipt["coverage"]
        if item["official_event_id"] == "target" and item["status"] == "MISSING"
    }
    assert "TEAM_POWER:HOME" in missing
    assert "OFF_EPA:AWAY" in missing
    assert "QB_VALUE:HOME" in missing
    assert "WEATHER:EVENT" in missing
    assert "REST_TRAVEL:HOME" not in missing


def test_scores_do_not_change_the_evidence_hash():
    plain = _acquire()
    scored = _acquire(games=_slate(home_points=41, away_points=10, home_won=True))
    assert [row["payload_sha256"] for row in plain["evidence_rows"]] == [
        row["payload_sha256"] for row in scored["evidence_rows"]
    ]


def test_later_game_and_late_snapshot_do_not_become_pregame_evidence():
    games = _slate() + [_game("after", KICKOFF + timedelta(days=7), "Texas", "Alabama", "austin")]
    receipt = _acquire(games=games)
    target = [row for row in receipt["evidence_rows"] if row["official_event_id"] == "target"]
    assert {row["payload"]["prior_event_id"] for row in target} == {"prior-home", "prior-away"}
    late = acquire_schedule_geometry(
        games,
        schedule_retrieved_at=KICKOFF + timedelta(hours=1),
        venues=_venues(),
        venue_retrieved_at=VENUE_AT,
    )
    assert [(row["official_event_id"], row["scope"]) for row in late["evidence_rows"]] == [("after", "HOME")]
    target_home = next(
        item for item in late["coverage"]
        if item["official_event_id"] == "target" and item["slot"] == "REST_TRAVEL:HOME"
    )
    assert target_home["status"] == "MISSING"
    assert target_home["reason"] == "NCAAF_SOURCE_RETRIEVED_AFTER_KICKOFF"


def test_opener_and_ambiguous_prior_stay_missing():
    opener = acquire_schedule_geometry(
        [_game("target", KICKOFF, "Texas", "Oklahoma", "austin")],
        schedule_retrieved_at=SCHEDULE_AT,
        venues=_venues(),
        venue_retrieved_at=VENUE_AT,
    )
    assert opener["evidence_rows"] == []
    assert {item["reason"] for item in opener["coverage"] if item["evidence_kind"] == "REST_TRAVEL"} == {
        "NCAAF_PRIOR_GAME_NOT_ON_SCHEDULE"
    }
    tied = [
        _game("a", KICKOFF - timedelta(days=7), "Texas", "Georgia", "austin"),
        _game("b", KICKOFF - timedelta(days=7), "Texas", "Florida", "norman"),
        _game("target", KICKOFF, "Texas", "Oklahoma", "austin"),
    ]
    ambiguous = acquire_schedule_geometry(
        tied,
        schedule_retrieved_at=SCHEDULE_AT,
        venues=_venues(),
        venue_retrieved_at=VENUE_AT,
    )
    home = next(
        item for item in ambiguous["coverage"]
        if item["official_event_id"] == "target" and item["slot"] == "REST_TRAVEL:HOME"
    )
    assert home["status"] == "MISSING"
    assert home["reason"] == "NCAAF_PRIOR_GAME_AMBIGUOUS"


def test_venue_catalog_after_kickoff_blocks_travel_and_keeps_home_rest():
    receipt = acquire_schedule_geometry(
        _slate(),
        schedule_retrieved_at=SCHEDULE_AT,
        venues=_venues(),
        venue_retrieved_at=KICKOFF + timedelta(minutes=5),
    )
    scopes = {row["scope"] for row in receipt["evidence_rows"] if row["official_event_id"] == "target"}
    assert scopes == {"HOME"}
    away = next(item for item in receipt["coverage"] if item["official_event_id"] == "target" and item["slot"] == "REST_TRAVEL:AWAY")
    assert away["status"] == "MISSING"
    assert away["reason"] == "NCAAF_VENUE_SNAPSHOT_NOT_PREGAME"
    assert schedule_geometry_feature_row(receipt, "target") is None


def test_reduced_contract_does_not_replace_v1_or_fit():
    contract = reduced_challenger_contract()
    assert contract["replaces_v1"] is False
    assert contract["parent_contract"] == "NCAAF_FEATURES_V1"
    assert contract["fit_invoked"] is False
    assert contract["minimum_complete_rows"] == 300
    assert contract["can_execute"] is False
    receipt = _acquire()
    assert receipt["reduced_challenger_fit_allowed"] is False
    assert receipt["reduced_challenger_fit_block"] == "NCAAF_SCHEDULE_GEOMETRY_ROWS_INSUFFICIENT"
    source = Path(__file__).with_name("ncaaf_pregame_feature_acquisition.py").read_text()
    assert "register_team_event_bridge" not in source
    assert "train_and_persist_candidate" not in source
    sql = Path(__file__).with_name("ncaaf_schedule_geometry_source.sql").read_text()
    assert "array['REST_TRAVEL']" in sql
    assert "can_execute" in sql
