import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from ncaaf_feature_compiler import NCAAFFeatureCompileBlocked, compile_official_event_feature_row
from ncaaf_pregame_feature_acquisition import (
    REDUCED_MODEL_FEATURES,
    acquire_schedule_geometry,
    reduced_challenger_contract,
    schedule_geometry_feature_row,
)

KICKOFF = datetime(2026, 10, 10, 23, tzinfo=timezone.utc)
SCHEDULE_AT = KICKOFF - timedelta(days=1)
VENUE_AT = KICKOFF - timedelta(days=2)


def _hash(rows):
    body = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(body.encode("utf-8")).hexdigest()


def _game(event_id, start, home, away, venue, *, completed, neutral_site=False, **extra):
    row = {
        "id": event_id,
        "startDate": start.isoformat().replace("+00:00", "Z"),
        "homeTeam": home,
        "awayTeam": away,
        "venueId": venue,
        "neutralSite": neutral_site,
        "completed": completed,
    }
    row.update(extra)
    return row


def _receipt(endpoint, rows, retrieved, snapshot_id, digest=None):
    return {
        "source_snapshot_id": snapshot_id,
        "provider": "CFBD",
        "endpoint": endpoint,
        "retrieved_at": retrieved.isoformat().replace("+00:00", "Z"),
        "payload_sha256": digest or _hash(rows),
        "response_rows": rows,
    }


def _venues():
    return [
        {"id": "austin", "latitude": 30.2836, "longitude": -97.7325},
        {"id": "norman", "latitude": 35.2058, "longitude": -97.4422},
        {"id": "fort-worth", "latitude": 32.7092, "longitude": -97.3681},
    ]


def _slate(**scores):
    return [
        _game("prior-home", KICKOFF - timedelta(days=7), "Texas", "Georgia", "austin", completed=True, **scores),
        _game("prior-away", KICKOFF - timedelta(days=6), "Kansas", "Oklahoma", "fort-worth", completed=True),
        _game("target", KICKOFF, "Texas", "Oklahoma", "austin", completed=False, neutral_site=False),
    ]


def _acquire(games=None, venues=None, schedule_at=SCHEDULE_AT, venue_at=VENUE_AT, schedule_id="sched-1", venue_id="venue-1"):
    games = _slate() if games is None else games
    venues = _venues() if venues is None else venues
    return acquire_schedule_geometry(
        [_receipt("/games", games, schedule_at, schedule_id)],
        [_receipt("/venues", venues, venue_at, venue_id)] if venues is not None else [],
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
    assert home["schedule_source_snapshot_id"] == "sched-1"
    assert home["schedule_payload_sha256"] == _hash(_slate())
    assert away["payload"]["venue_source_snapshot_id"] == "venue-1"
    assert away["payload"]["venue_payload_sha256"] == _hash(_venues())
    assert home["evidence_timestamp"] < home["event_start_time"]
    feature = schedule_geometry_feature_row(receipt, "target")
    assert feature["status"] == "REDUCED_ROW_HELD"
    assert tuple(feature["model_features"]) == REDUCED_MODEL_FEATURES
    assert feature["model_features"]["neutral_site"] is False
    assert feature["model_features"]["travel_distance_miles"] == 168.962
    assert feature["v1_compatible"] is False
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
    else:
        raise AssertionError("partial evidence must not compile as NCAAF_FEATURES_V1")


def test_liberty_snapshot_does_not_count_a_future_game_as_played():
    retrieved = datetime(2026, 10, 1, 18, 35, 38, tzinfo=timezone.utc)
    prior_start = datetime(2026, 10, 2, 23, tzinfo=timezone.utc)
    target_start = datetime(2026, 10, 8, 23, tzinfo=timezone.utc)
    games = [
        _game("401871050", prior_start, "Liberty", "Opponent", "venue-a", completed=False),
        _game("401870766", target_start, "Liberty", "Later Opponent", "venue-b", completed=False, neutral_site=False),
    ]
    receipt = acquire_schedule_geometry([
        _receipt("/games", games, retrieved, "143b8bfe-740e-44d7-8e7e-015afd5e3187"),
    ], [])
    assert receipt["evidence_rows"] == []
    home = next(item for item in receipt["coverage"] if item["official_event_id"] == "401870766" and item["slot"] == "REST_TRAVEL:HOME")
    assert home["status"] == "MISSING"
    assert home["reason"] == "NCAAF_PRIOR_GAME_NOT_OCCURRED_AT_CUTOFF"
    assert home["blocked_prior_event_id"] == "401871050"
    completed = [
        _game("401871050", prior_start, "Liberty", "Opponent", "venue-a", completed=True),
        _game("401870766", target_start, "Liberty", "Later Opponent", "venue-b", completed=False, neutral_site=False),
    ]
    venues = [
        {"id": "venue-a", "latitude": 37.35, "longitude": -79.18},
        {"id": "venue-b", "latitude": 35.78, "longitude": -78.64},
    ]
    proved = acquire_schedule_geometry(
        [
            _receipt("/games", games, retrieved, "143b8bfe-740e-44d7-8e7e-015afd5e3187"),
            _receipt("/games", completed, datetime(2026, 10, 3, 2, tzinfo=timezone.utc), "completion-after-oct-2"),
        ],
        [_receipt("/venues", venues, datetime(2026, 10, 1, 12, tzinfo=timezone.utc), "venue-catalog")],
    )
    rows = [row for row in proved["evidence_rows"] if row["official_event_id"] == "401870766"]
    assert [row["scope"] for row in rows] == ["HOME"]
    assert rows[0]["payload"]["prior_event_id"] == "401871050"
    assert rows[0]["occurrence_source_snapshot_id"] == "completion-after-oct-2"
    assert rows[0]["schedule_source_snapshot_id"] == "completion-after-oct-2"


def test_canceled_prior_and_missing_neutral_site_are_held():
    games = [
        _game("prior-home", KICKOFF - timedelta(days=7), "Texas", "Georgia", "austin", completed=True, status="postponed"),
        _game("prior-away", KICKOFF - timedelta(days=6), "Kansas", "Oklahoma", "fort-worth", completed=True),
        {key: value for key, value in _game("target", KICKOFF, "Texas", "Oklahoma", "austin", completed=False).items() if key != "neutralSite"},
    ]
    receipt = _acquire(games=games)
    home = next(item for item in receipt["coverage"] if item["official_event_id"] == "target" and item["slot"] == "REST_TRAVEL:HOME")
    assert home["reason"] == "NCAAF_PRIOR_GAME_NOT_PLAYED"
    away = next(row for row in receipt["evidence_rows"] if row["scope"] == "AWAY")
    assert away["payload"]["neutral_site"] is None
    held = schedule_geometry_feature_row(receipt, "target")
    assert held["code"] == "NCAAF_SCHEDULE_GEOMETRY_INCOMPLETE"
    assert held["model_features"] is None


def test_same_venue_without_coordinates_is_not_zero_distance():
    games = [
        _game("prior-away", KICKOFF - timedelta(days=6), "Kansas", "Oklahoma", "austin", completed=True),
        _game("target", KICKOFF, "Texas", "Oklahoma", "austin", completed=False),
    ]
    receipt = acquire_schedule_geometry([
        _receipt("/games", games, SCHEDULE_AT, "sched-same"),
    ], [])
    away = next(item for item in receipt["coverage"] if item["slot"] == "REST_TRAVEL:AWAY" and item["official_event_id"] == "target")
    assert away["reason"] == "NCAAF_VENUE_SNAPSHOT_NOT_ARCHIVED"
    assert receipt["evidence_rows"] == [] or all(row["payload"].get("travel_distance_miles") != 0.0 for row in receipt["evidence_rows"])


def test_conflicting_venue_coordinates_are_not_averaged():
    venues = [
        {"id": "austin", "latitude": 30.2836, "longitude": -97.7325},
        {"id": "austin", "latitude": 30.30, "longitude": -97.70},
        {"id": "fort-worth", "latitude": 32.7092, "longitude": -97.3681},
    ]
    receipt = _acquire(venues=venues)
    away = next(item for item in receipt["coverage"] if item["official_event_id"] == "target" and item["slot"] == "REST_TRAVEL:AWAY")
    assert away["status"] == "MISSING"
    assert away["reason"] == "NCAAF_VENUE_COORDINATE_CONFLICT"


def test_scores_do_not_change_rest_or_travel():
    plain = _acquire()
    scored = _acquire(games=_slate(home_points=41, away_points=10, home_won=True))

    def sporting(receipt):
        return [
            (row["scope"], row["payload"]["rest_days"], row["payload"].get("travel_distance_miles"), row["payload"]["prior_event_id"])
            for row in receipt["evidence_rows"]
        ]

    assert sporting(plain) == sporting(scored)
    assert plain["evidence_rows"][0]["schedule_payload_sha256"] != scored["evidence_rows"][0]["schedule_payload_sha256"]


def test_later_game_and_late_snapshot_do_not_become_pregame_evidence():
    games = _slate() + [_game("after", KICKOFF + timedelta(days=7), "Texas", "Alabama", "austin", completed=False)]
    games[2]["completed"] = True
    receipt = _acquire(games=games)
    target = [row for row in receipt["evidence_rows"] if row["official_event_id"] == "target"]
    assert {row["payload"]["prior_event_id"] for row in target} == {"prior-home", "prior-away"}
    late = _acquire(games=games, schedule_at=KICKOFF + timedelta(hours=1))
    assert [(row["official_event_id"], row["scope"]) for row in late["evidence_rows"]] == [("after", "HOME")]
    target_home = next(
        item for item in late["coverage"]
        if item["official_event_id"] == "target" and item["slot"] == "REST_TRAVEL:HOME"
    )
    assert target_home["reason"] == "NCAAF_SOURCE_RETRIEVED_AFTER_KICKOFF"


def test_bad_provenance_duplicate_and_future_occurrence_are_rejected():
    games = _slate()
    bad_hash = _receipt("/games", games, SCHEDULE_AT, "sched-bad", digest="0" * 64)
    rejected = acquire_schedule_geometry([bad_hash], [])
    assert rejected["evidence_rows"] == []
    assert rejected["rejected_receipts"][0]["reason"] == "NCAAF_SOURCE_RECEIPT_HASH_MISMATCH"
    first = _receipt("/games", games, SCHEDULE_AT, "sched-dup")
    other = _receipt("/games", games + [_game("extra", KICKOFF - timedelta(days=3), "Texas", "Navy", "austin", completed=True)], SCHEDULE_AT, "sched-dup")
    duplicate = acquire_schedule_geometry([first, other], [])
    assert duplicate["evidence_rows"] == []
    assert duplicate["rejected_receipts"][0]["reason"] == "NCAAF_SOURCE_RECEIPT_DUPLICATE"
    missing = dict(first)
    missing["source_snapshot_id"] = ""
    bare = acquire_schedule_geometry([missing], [])
    assert bare["rejected_receipts"][0]["reason"] == "NCAAF_SOURCE_RECEIPT_PROVENANCE_MISSING"
    future_proof = _receipt("/games", [
        _game("prior-home", KICKOFF - timedelta(days=7), "Texas", "Georgia", "austin", completed=True),
        _game("target", KICKOFF, "Texas", "Oklahoma", "austin", completed=False),
    ], KICKOFF + timedelta(hours=2), "after-target")
    early = _receipt("/games", [
        _game("prior-home", KICKOFF - timedelta(days=7), "Texas", "Georgia", "austin", completed=False),
        _game("target", KICKOFF, "Texas", "Oklahoma", "austin", completed=False),
    ], KICKOFF - timedelta(days=8), "before-prior")
    future = acquire_schedule_geometry([early, future_proof], [])
    home = next(item for item in future["coverage"] if item["official_event_id"] == "target" and item["slot"] == "REST_TRAVEL:HOME")
    assert future["evidence_rows"] == []
    assert home["reason"] == "NCAAF_PRIOR_OCCURRENCE_NOT_VERIFIED"


def test_opener_and_ambiguous_prior_stay_missing():
    opener = _acquire(games=[_game("target", KICKOFF, "Texas", "Oklahoma", "austin", completed=False)])
    assert opener["evidence_rows"] == []
    assert {item["reason"] for item in opener["coverage"] if item["evidence_kind"] == "REST_TRAVEL"} == {
        "NCAAF_PRIOR_GAME_NOT_ON_SCHEDULE"
    }
    tied = [
        _game("a", KICKOFF - timedelta(days=7), "Texas", "Georgia", "austin", completed=True),
        _game("b", KICKOFF - timedelta(days=7), "Texas", "Florida", "norman", completed=True),
        _game("target", KICKOFF, "Texas", "Oklahoma", "austin", completed=False),
    ]
    ambiguous = _acquire(games=tied)
    home = next(item for item in ambiguous["coverage"] if item["official_event_id"] == "target" and item["slot"] == "REST_TRAVEL:HOME")
    assert home["reason"] == "NCAAF_PRIOR_GAME_AMBIGUOUS"


def test_venue_catalog_after_kickoff_blocks_travel_and_keeps_home_rest():
    receipt = _acquire(venue_at=KICKOFF + timedelta(minutes=5))
    scopes = {row["scope"] for row in receipt["evidence_rows"] if row["official_event_id"] == "target"}
    assert scopes == {"HOME"}
    away = next(item for item in receipt["coverage"] if item["official_event_id"] == "target" and item["slot"] == "REST_TRAVEL:AWAY")
    assert away["reason"] == "NCAAF_VENUE_SNAPSHOT_NOT_PREGAME"
    assert schedule_geometry_feature_row(receipt, "target")["code"] == "NCAAF_SCHEDULE_GEOMETRY_INCOMPLETE"


def test_reduced_contract_matches_emitted_fields_and_does_not_fit():
    contract = reduced_challenger_contract()
    assert contract["model_features"] == list(REDUCED_MODEL_FEATURES)
    assert contract["replaces_v1"] is False
    assert contract["fit_invoked"] is False
    assert contract["can_execute"] is False
    receipt = _acquire()
    assert receipt["reduced_challenger_fit_allowed"] is False
    assert receipt["reduced_challenger_fit_block"] == "NCAAF_SCHEDULE_GEOMETRY_ROWS_INSUFFICIENT"
    feature = schedule_geometry_feature_row(receipt, "target")
    assert tuple(feature["model_features"]) == tuple(contract["model_features"])
    source = Path(__file__).with_name("ncaaf_pregame_feature_acquisition.py").read_text()
    assert "register_team_event_bridge" not in source
    assert "train_and_persist_candidate" not in source
