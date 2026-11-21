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

import hashlib, json
START = datetime(2026, 11, 8, 1, 0, tzinfo=timezone.utc)
def payload(**kw):
    d = dict(id="E1", datetime=START.isoformat().replace("+00:00", "Z"), status=START.isoformat().replace("+00:00", "Z"),
             home_team={"id": 1}, visitor_team={"id": 2}); d.update(kw)
    return json.dumps({"data": d}, sort_keys=True).encode()
PAYLOAD = payload()
def event(**kw):
    e = dict(official_event_id="E1", home_team_id="1", away_team_id="2", event_start_time=START.isoformat(), event_status="scheduled",
             game_date="2026-11-07", source_snapshot_id="snap1", source_snapshot_timestamp=(START - timedelta(hours=5)).isoformat(),
             source_payload_sha256=hashlib.sha256(PAYLOAD).hexdigest())
    e.update(kw); return e
CAP = START - timedelta(hours=3)
def cap(ev=None, hist_=None, when=CAP, payload_=None, **kw):
    return fc.capture_forward_pregame("NBA", ev or event(), hist_ if hist_ is not None else hist(), when,
                                      source_payload=PAYLOAD if payload_ is None else payload_, **kw)

def test_valid_case_matches_retrospective_builder():
    h = hist(); r = cap(event(), h)
    assert r.can_execute is False and r.probability_publishable is False and r.captured_at < r.event_start_time
    retro = [x for x in build_pregame_features([*h, TrainingGame("E1", "NBA", 2026, date(2026, 11, 7), "1", "2", 1, 0)], "NBA") if x.game_id == "E1"][0]
    assert list(r.feature_payload["features"].values()) == list(retro.values)
    assert r.supersedes_feature_snapshot_sha256 is None and len(r.feature_snapshot_sha256) == 64

def test_deterministic_and_supersede_chain():
    h = hist(); a = cap(event(), h)
    b = cap(event(), h, CAP + timedelta(minutes=5), prior_feature_snapshot_sha256=a.feature_snapshot_sha256)
    assert a.feature_snapshot_sha256 == b.feature_snapshot_sha256 and b.supersedes_feature_snapshot_sha256 is None
    h2 = h + [TrainingGame("late", "NBA", 2026, date(2026, 11, 6), "1", "3", 130, 90)]
    c = cap(event(), h2, CAP, prior_feature_snapshot_sha256=a.feature_snapshot_sha256)
    assert c.feature_snapshot_sha256 != a.feature_snapshot_sha256 and c.supersedes_feature_snapshot_sha256 == a.feature_snapshot_sha256

def test_same_day_and_future_results_excluded():
    h = hist(); base = cap(event(), h)
    leak = h + [TrainingGame("same", "NBA", 2026, date(2026, 11, 7), "1", "3", 150, 50), TrainingGame("fut", "NBA", 2026, date(2026, 11, 9), "1", "2", 150, 50)]
    assert cap(event(), leak).feature_snapshot_sha256 == base.feature_snapshot_sha256

@pytest.mark.parametrize("kw,cap_,code", [
    ({}, START, "CAPTURE_AT_OR_AFTER_EVENT_START"),
    ({}, START + timedelta(minutes=1), "CAPTURE_AT_OR_AFTER_EVENT_START"),
    ({"event_status": "live"}, CAP, "CAPTURE_EVENT_NOT_SCHEDULED"),
    ({"event_status": "final"}, CAP, "CAPTURE_EVENT_NOT_SCHEDULED"),
    ({"source_snapshot_timestamp": (CAP + timedelta(minutes=1)).isoformat()}, CAP, "CAPTURE_SOURCE_SNAPSHOT_AFTER_CAPTURE"),
    ({"official_event_id": ""}, CAP, "CAPTURE_EVENT_IDENTITY_INCOMPLETE"),
    ({"away_team_id": "1"}, CAP, "CAPTURE_EVENT_IDENTITY_INCOMPLETE"),
    ({"event_start_time": "2026-11-08T01:00:00"}, CAP, "CAPTURE_TIMESTAMP_NOT_TZ_AWARE"),
])
def test_missing_mismatched_stale_evidence_fail_closed(kw, cap_, code):
    with pytest.raises(fc.ForwardCaptureError) as e:
        cap(event(**kw), hist(), cap_)
    assert e.value.code == code

def test_cold_start_and_empty_history_fail_closed():
    with pytest.raises(fc.ForwardCaptureError) as e: cap(event(), [])
    assert e.value.code == "CAPTURE_HISTORY_EMPTY"
    with pytest.raises(fc.ForwardCaptureError) as e: cap(event(), hist(n_days=4))
    assert e.value.code == "CAPTURE_INSUFFICIENT_PRIOR_GAMES"

def test_sport_isolation_and_unsupported():
    wnba = [TrainingGame(g.game_id, "WNBA", g.season, g.game_date, g.home_team_id, g.away_team_id, g.home_score, g.away_score) for g in hist()]
    with pytest.raises(fc.ForwardCaptureError): cap(event(), wnba)
    with pytest.raises(Exception): fc.capture_forward_pregame("NFL", event(), hist(), CAP, source_payload=PAYLOAD)

def test_migration_is_append_only_and_non_executing():
    sql = (Path(__file__).parent / "migrations" / "20261109_basketball_forward_pregame_capture.sql").read_text()
    for needle in ("BASKETBALL_FORWARD_CAPTURE_IMMUTABLE", "CAPTURE_AT_OR_AFTER_EVENT_START", "clock_timestamp()", "can_execute = false", "before update or delete", "REVIEW_ONLY"):
        assert needle in sql
    assert "on conflict" not in sql.lower()


def test_stale_history_fail_closed_with_consistent_source():
    late = datetime(2026, 12, 21, 1, 0, tzinfo=timezone.utc); p = payload(datetime=late.isoformat(), status=late.isoformat())
    ev = event(game_date="2026-12-20", event_start_time=late.isoformat(), source_snapshot_timestamp="2026-12-20T20:00:00+00:00",
               source_payload_sha256=hashlib.sha256(p).hexdigest())
    with pytest.raises(fc.ForwardCaptureError) as e: cap(ev, hist(), datetime(2026, 12, 20, 22, tzinfo=timezone.utc), payload_=p)
    assert e.value.code == "CAPTURE_HISTORY_STALE"


def _bad(code, ev=None, p=None, **kw):
    with pytest.raises(fc.ForwardCaptureError) as e:
        if p is not None and ev is None: ev = event(source_payload_sha256=hashlib.sha256(p).hexdigest())
        cap(ev, payload_=p, **kw)
    assert e.value.code == code


def test_caller_inflated_future_event_start_is_rejected():
    inflated = START + timedelta(days=30)  # claim says far future so a late capture would look "pre-lock"
    _bad("CAPTURE_SOURCE_EVENT_MISMATCH_EVENT_START_TIME", ev=event(event_start_time=inflated.isoformat()))


def test_manufactured_source_digest_is_rejected():
    _bad("CAPTURE_SOURCE_PAYLOAD_HASH_MISMATCH", ev=event(source_payload_sha256="b" * 64))


def test_tampered_payload_with_original_digest_is_rejected():
    _bad("CAPTURE_SOURCE_PAYLOAD_HASH_MISMATCH", ev=event(), p=payload(id="E1", home_team={"id": 9}))


@pytest.mark.parametrize("overrides,code", [
    ({"id": "E2"}, "CAPTURE_SOURCE_EVENT_MISMATCH_OFFICIAL_EVENT_ID"),
    ({"home_team": {"id": 9}}, "CAPTURE_SOURCE_EVENT_MISMATCH_HOME_TEAM_ID"),
    ({"visitor_team": {"id": 9}}, "CAPTURE_SOURCE_EVENT_MISMATCH_AWAY_TEAM_ID"),
    ({"datetime": None}, "CAPTURE_SOURCE_EVENT_MISMATCH_EVENT_START_TIME"),
    ({"datetime": "2026-11-08T02:00:00Z"}, "CAPTURE_SOURCE_EVENT_MISMATCH_EVENT_START_TIME"),
    ({"status": "Final"}, "CAPTURE_SOURCE_EVENT_NOT_SCHEDULED"),
    ({"status": "Q3 5:00"}, "CAPTURE_SOURCE_EVENT_NOT_SCHEDULED"),
    ({"home_team": "x"}, "CAPTURE_SOURCE_PAYLOAD_MALFORMED"),
])
def test_source_that_disagrees_with_claimed_event_is_rejected(overrides, code):
    _bad(code, p=payload(**overrides))


def test_unknown_authority_missing_and_malformed_payload():
    with pytest.raises(fc.ForwardCaptureError) as e: cap(source_authority="MANUAL_ENTRY")
    assert e.value.code == "CAPTURE_SOURCE_AUTHORITY_UNVERIFIED"
    with pytest.raises(fc.ForwardCaptureError) as e: cap(payload_=b"")
    assert e.value.code == "CAPTURE_SOURCE_PAYLOAD_MISSING"
    bad = b"{not json"
    _bad("CAPTURE_SOURCE_PAYLOAD_MALFORMED", ev=event(source_payload_sha256=hashlib.sha256(bad).hexdigest()), p=bad)
    arr = b"[1,2]"
    _bad("CAPTURE_SOURCE_PAYLOAD_MALFORMED", ev=event(source_payload_sha256=hashlib.sha256(arr).hexdigest()), p=arr)


def test_record_binds_persisted_bytes_and_flags():
    r = cap()
    assert hashlib.sha256(r.source_payload_text.encode()).hexdigest() == r.source_payload_sha256
    assert hashlib.sha256(r.feature_payload_canonical.encode()).hexdigest() == r.feature_snapshot_sha256
    assert json.loads(r.feature_payload_canonical) == r.feature_payload
    assert r.source_authority == fc.SOURCE_AUTHORITY_BDL and r.source_event_identity_verified is True


def test_source_obtained_after_capture_rejected_even_when_event_verifies():
    _bad("CAPTURE_SOURCE_SNAPSHOT_AFTER_CAPTURE", ev=event(source_snapshot_timestamp=(CAP + timedelta(seconds=1)).isoformat()))


def test_migration_reverifies_persisted_bytes_and_denies_public_access():
    sql = (Path(__file__).parent / "migrations" / "20261109_basketball_forward_pregame_capture.sql").read_text()
    for needle in ("CAPTURE_SOURCE_PAYLOAD_HASH_MISMATCH", "CAPTURE_FEATURE_HASH_MISMATCH", "CAPTURE_SOURCE_EVENT_MISMATCH_EVENT_START_TIME",
                   "CAPTURE_SOURCE_EVENT_MISMATCH_OFFICIAL_EVENT_ID", "source_event_identity_verified = true",
                   "revoke all on table public.wow_basketball_forward_pregame_capture from public, anon, authenticated", "source_payload_text"):
        assert needle in sql


def test_unparseable_timestamp_fails_closed():
    _bad("CAPTURE_TIMESTAMP_UNPARSEABLE", ev=event(event_start_time="tomorrow-ish"))


def test_source_start_inside_data_wrapper_or_flat_payload_both_verify():
    flat = json.dumps(dict(id="E1", datetime=START.isoformat(), status="scheduled", home_team={"id": 1}, visitor_team={"id": 2})).encode()
    r = cap(event(source_payload_sha256=hashlib.sha256(flat).hexdigest()), payload_=flat)
    assert r.source_event_identity_verified is True


def test_as_row_exposes_flags_and_fails_closed_defaults():
    row = cap().as_row()
    assert row["can_execute"] is False and row["probability_publishable"] is False and row["source_event_identity_verified"] is True
