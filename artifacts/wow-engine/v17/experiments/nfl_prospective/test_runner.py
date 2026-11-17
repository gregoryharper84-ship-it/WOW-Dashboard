"""Runner wiring tests against deterministic in-memory database, no external APIs."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sys
import csv, io

sys.path.insert(0,str(Path(__file__).resolve().parent))
from runner import extract_espn_event, capture_clock_eligible, status_from_counts, make_capture_receipt

NOW=datetime(2026,11,17,20,0,tzinfo=timezone.utc)


def test_extract_espn_strict_final_fixture_identity():
    event={"id":"512345","date":"2026-11-20T01:15:00Z","status":{"type":{"completed":True}},
           "competitions":[{"competitors":[
              {"homeAway":"home","team":{"abbreviation":"HOU"},"score":"24"},
              {"homeAway":"away","team":{"abbreviation":"IND"},"score":"21"}]}]}
    fixture={"official_event_id":"2026_11_IND_HOU","event_start_time_utc":"2026-11-20T01:15:00+00:00",
             "home_team":"HOU","away_team":"IND"}
    out=extract_espn_event({"events":[event]},fixture,raw_sha256="a"*64)
    assert out["completed"] and out["event_id"]=="512345"
    assert (out["home_score"],out["away_score"])==(24,21)
    assert extract_espn_event({"events":[event|{"date":"2026-11-20T03:15:00Z"}]},fixture,raw_sha256="a"*64) is None
    assert extract_espn_event({"events":[event|{"status":{"type":{"completed":False}}}]},fixture,raw_sha256="a"*64) is None


def test_capture_window_cannot_shift_past_kickoff():
    at="2026-11-20T01:15:00+00:00"
    assert capture_clock_eligible(at,NOW,72)
    assert not capture_clock_eligible(at,datetime(2026,11,20,1,15,tzinfo=timezone.utc),72)
    assert not capture_clock_eligible(at,NOW,12)


def test_no_events_reports_idle_without_success_claim():
    d=status_from_counts(0,0,0,0)
    assert d["status"]=="NO_EVENTS_IN_CAPTURE_WINDOW"
    assert d["certification_eligible"] is False
    assert d["can_execute"] is False


def test_receipt_retains_frozen_incumbent_and_source_hash_without_relabeling():
    prediction=dict(official_event_id="2026_11_IND_HOU",event_start_time_utc="2026-11-20T01:15:00Z",
                    prediction_created_at="2026-11-17T20:00:00Z",research_metadata={"status":"PREGAME_SHADOW_UNCERTIFIED"})
    incumbent=dict(event_prediction_id="id-5",model_version="NFL_CHAMPION",calibrated_home_probability=.6,
                   created_at="2026-11-17T19:00:00Z",event_start_time_utc="2026-11-20T01:15:00Z")
    rec=make_capture_receipt(prediction,incumbent)
    assert rec["research_metadata"]["incumbent"]["event_prediction_id"]=="id-5"
    assert rec["research_metadata"]["incumbent"]["calibrated_home_probability"]==.6
    assert rec["research_metadata"]["incumbent"]["is_scientifically_certified"] is False
    assert rec["can_execute"] is False


def test_nflverse_final_must_match_original_hashed_snapshot_and_training_row():
    from runner import verify_nflverse_final
    fixture={"official_event_id":"2026_11_IND_HOU","event_start_time_utc":"2026-11-20T01:15:00Z",
             "home_team":"HOU","away_team":"IND"}
    raw=dict(game_id="2026_11_IND_HOU",home_team="HOU",away_team="IND",
             home_score="24",away_score="21")
    training=dict(game_id="2026_11_IND_HOU",home_team="HOU",away_team="IND",home_score=24,away_score=21)
    snap=dict(content_sha256="a"*64,fetched_at="2026-11-20T04:30:00Z")
    evidence=verify_nflverse_final(fixture,raw,training,snap)
    assert evidence["schedule_content_sha256"]==snap["content_sha256"]
    assert verify_nflverse_final(fixture,raw|{"home_score":"25"},training,snap) is None
    assert verify_nflverse_final(fixture,raw,training,snap|{"fetched_at":"2026-11-19T22:30:00Z"}) is None
    assert verify_nflverse_final(fixture,raw,training,snap|{"content_sha256":"bad"}) is None
