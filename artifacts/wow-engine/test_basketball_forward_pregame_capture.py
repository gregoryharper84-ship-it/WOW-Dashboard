from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import pytest
import basketball_forward_pregame_capture as fc
from basketball_team_event_specialist import TrainingGame, build_pregame_features

def hist(n_days=12, teams=("1", "2", "3", "4"), start=date(2026, 10, 20)):
    out, gid = [], 0
    for d in range(n_days):
        day = start + timedelta(days=d)
        for h, a in ((teams[0], teams[1]), (teams[2], teams[3])) if d % 2 == 0 else ((teams[1], teams[2]), (teams[3], teams[0])):
            gid += 1
            out.append(TrainingGame(f"h{gid}", "NBA", 2026, day, h, a, 100 + gid % 7, 95 + gid % 5))
    return out

START = datetime(2026, 11, 8, 1, 0, tzinfo=timezone.utc)
def event(**kw):
    e = dict(official_event_id="E1", home_team_id="1", away_team_id="2", event_start_time=START.isoformat(), event_status="scheduled",
             game_date="2026-11-07", source_snapshot_id="snap1", source_snapshot_timestamp=(START - timedelta(hours=5)).isoformat(),
             source_payload_sha256="a" * 64)
    e.update(kw); return e
CAP = START - timedelta(hours=3)

def test_valid_case_matches_retrospective_builder():
    h = hist(); r = fc.capture_forward_pregame("NBA", event(), h, CAP)
    assert r.can_execute is False and r.probability_publishable is False and r.captured_at < r.event_start_time
    retro = [x for x in build_pregame_features([*h, TrainingGame("E1", "NBA", 2026, date(2026, 11, 7), "1", "2", 1, 0)], "NBA") if x.game_id == "E1"][0]
    assert list(r.feature_payload["features"].values()) == list(retro.values)
    assert r.supersedes_feature_snapshot_sha256 is None and len(r.feature_snapshot_sha256) == 64

def test_deterministic_and_supersede_chain():
    h = hist(); a = fc.capture_forward_pregame("NBA", event(), h, CAP)
    b = fc.capture_forward_pregame("NBA", event(), h, CAP + timedelta(minutes=5), prior_feature_snapshot_sha256=a.feature_snapshot_sha256)
    assert a.feature_snapshot_sha256 == b.feature_snapshot_sha256 and b.supersedes_feature_snapshot_sha256 is None
    h2 = h + [TrainingGame("late", "NBA", 2026, date(2026, 11, 6), "1", "3", 130, 90)]
    c = fc.capture_forward_pregame("NBA", event(), h2, CAP, prior_feature_snapshot_sha256=a.feature_snapshot_sha256)
    assert c.feature_snapshot_sha256 != a.feature_snapshot_sha256 and c.supersedes_feature_snapshot_sha256 == a.feature_snapshot_sha256

def test_same_day_and_future_results_excluded():
    h = hist(); base = fc.capture_forward_pregame("NBA", event(), h, CAP)
    leak = h + [TrainingGame("same", "NBA", 2026, date(2026, 11, 7), "1", "3", 150, 50), TrainingGame("fut", "NBA", 2026, date(2026, 11, 9), "1", "2", 150, 50)]
    assert fc.capture_forward_pregame("NBA", event(), leak, CAP).feature_snapshot_sha256 == base.feature_snapshot_sha256

@pytest.mark.parametrize("kw,cap,code", [
    ({}, START, "CAPTURE_AT_OR_AFTER_EVENT_START"),
    ({}, START + timedelta(minutes=1), "CAPTURE_AT_OR_AFTER_EVENT_START"),
    ({"event_status": "live"}, CAP, "CAPTURE_EVENT_NOT_SCHEDULED"),
    ({"event_status": "final"}, CAP, "CAPTURE_EVENT_NOT_SCHEDULED"),
    ({"source_snapshot_timestamp": (CAP + timedelta(minutes=1)).isoformat()}, CAP, "CAPTURE_SOURCE_SNAPSHOT_AFTER_CAPTURE"),
    ({"official_event_id": ""}, CAP, "CAPTURE_EVENT_IDENTITY_INCOMPLETE"),
    ({"away_team_id": "1"}, CAP, "CAPTURE_EVENT_IDENTITY_INCOMPLETE"),
    ({"event_start_time": "2026-11-08T01:00:00"}, CAP, "CAPTURE_TIMESTAMP_NOT_TZ_AWARE"),
    ({"game_date": "2026-12-20", "event_start_time": "2026-12-21T01:00:00+00:00", "source_snapshot_timestamp": "2026-12-20T20:00:00+00:00"},
     datetime(2026, 12, 20, 22, tzinfo=timezone.utc), "CAPTURE_HISTORY_STALE"),
])
def test_missing_mismatched_stale_evidence_fail_closed(kw, cap, code):
    with pytest.raises(fc.ForwardCaptureError) as e:
        fc.capture_forward_pregame("NBA", event(**kw), hist(), cap)
    assert e.value.code == code

def test_cold_start_and_empty_history_fail_closed():
    with pytest.raises(fc.ForwardCaptureError) as e: fc.capture_forward_pregame("NBA", event(), [], CAP)
    assert e.value.code == "CAPTURE_HISTORY_EMPTY"
    with pytest.raises(fc.ForwardCaptureError) as e: fc.capture_forward_pregame("NBA", event(), hist(n_days=4), CAP)
    assert e.value.code == "CAPTURE_INSUFFICIENT_PRIOR_GAMES"

def test_sport_isolation_and_unsupported():
    wnba = [TrainingGame(g.game_id, "WNBA", g.season, g.game_date, g.home_team_id, g.away_team_id, g.home_score, g.away_score) for g in hist()]
    with pytest.raises(fc.ForwardCaptureError): fc.capture_forward_pregame("NBA", event(), wnba, CAP)
    with pytest.raises(Exception): fc.capture_forward_pregame("NFL", event(), hist(), CAP)

def test_migration_is_append_only_and_non_executing():
    sql = (Path(__file__).parent / "migrations" / "20261109_basketball_forward_pregame_capture.sql").read_text()
    for needle in ("BASKETBALL_FORWARD_CAPTURE_IMMUTABLE", "CAPTURE_AT_OR_AFTER_EVENT_START", "clock_timestamp()", "can_execute = false", "before update or delete", "REVIEW_ONLY"):
        assert needle in sql
    assert "on conflict" not in sql.lower()
