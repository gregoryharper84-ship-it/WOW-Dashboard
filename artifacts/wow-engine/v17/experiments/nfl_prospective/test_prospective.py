"""Contract tests for prospective NFL challenger capture/grade: no network or wagers."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from prospective import (
    parse_fixture, fit_research_challenger, score_future_event,
    reconcile_two_sources, immutable_grade_fields, canonical_source_bytes,
    choose_incumbent,
)

NOW = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)
H = "a" * 64

def historical(n=30, *, season=2024):
    return [dict(game_id=f"{season}_{i:03d}", season=season, gameday=f"{season}-10-{i%27+1:02d}",
                 feature_cutoff_date=f"{season}-10-{i%27+1:02d}",max_prior_gameday=f"{season}-09-29",
                 feature_order=["prior_epa", "prior_rest"], feature_vector=[(i%9-4)/10.,i%5],
                 target_outcome="HOME_WIN" if i%3 else "AWAY_WIN",
                 row_inputs_hash=sha256(f"{season}-{i}".encode()).hexdigest(),
                 source_content_sha256s=[H]) for i in range(n)]


def future_fixture(**overrides):
    d=dict(game_id="2026_11_IND_HOU", season="2026",week="11",gameday="2026-11-19",
           gametime="20:15",home_team="HOU",away_team="IND",game_type="REG",
           home_score="",away_score="")
    return d|overrides


def model():
    return fit_research_challenger(historical()+historical(18,season=2025),
                                   min_fit=20,min_cal=12,fit_steps=45,cal_steps=30)


def future_feature():
    return dict(game_id="2026_11_IND_HOU",season=2026,week=11,gameday="2026-11-19",
                home_team="HOU",away_team="IND",feature_order=["prior_epa","prior_rest"],
                feature_vector=[.12,4],training_eligible=True,
                max_prior_gameday="2026-11-16",row_inputs_hash=H,
                source_content_sha256s=[H])


def test_scheduled_fixture_utc_handles_nov_dst():
    r=parse_fixture(future_fixture(),NOW,horizon_hours=72)
    assert r["event_start_time_utc"]=="2026-11-20T01:15:00+00:00"
    assert r["official_event_id"]=="2026_11_IND_HOU"


def test_reject_started_score_mismatch_and_unknown_fixture():
    assert parse_fixture(future_fixture(home_score="20"),NOW,horizon_hours=72) is None
    assert parse_fixture(future_fixture(game_type="PRE"),NOW,horizon_hours=72) is None
    assert parse_fixture(future_fixture(away_team="HOU"),NOW,horizon_hours=72) is None
    assert parse_fixture(future_fixture(gametime="xx:yy"),NOW,horizon_hours=72) is None


def test_schedule_hash_verifies_original_csv_not_gzip_digest():
    data=b"game_id,home_team\n1,HOU\n"
    assert canonical_source_bytes(data,sha256(data).hexdigest()) == data
    with pytest.raises(ValueError,match="SOURCE_HASH_MISMATCH"):
        canonical_source_bytes(data,"f"*64)


def test_training_2026_not_used_and_separate_calibration():
    r=fit_research_challenger(historical()+historical(18,season=2025)+historical(7,season=2026),
                              min_fit=20,min_cal=12,fit_steps=45,cal_steps=30)
    assert r["training_rows"]==30
    assert r["calibration_rows"]==18
    assert r["artifact_sha256"]==model()["artifact_sha256"]
    assert r["can_execute"] is False
    assert r["probability_publishable"] is False


def test_training_future_feature_in_fit_rejected():
    rows=historical()+historical(18,season=2025)
    rows[0]["max_prior_gameday"]="2024-10-31"
    with pytest.raises(ValueError,match="FEATURE_CHRONOLOGY_INVALID"):
        fit_research_challenger(rows,min_fit=20,min_cal=12,fit_steps=45,cal_steps=30)


def test_fit_rejects_same_event_and_unverified_source():
    rows=historical()+historical(18,season=2025)
    rows[1]["game_id"]=rows[0]["game_id"]
    with pytest.raises(ValueError,match="DUPLICATE"):
        fit_research_challenger(rows,min_fit=20,min_cal=12,fit_steps=45,cal_steps=30)
    rows=historical()+historical(18,season=2025)
    rows[1]["source_content_sha256s"]=[]
    with pytest.raises(ValueError,match="SOURCE_MANIFEST"):
        fit_research_challenger(rows,min_fit=20,min_cal=12,fit_steps=45,cal_steps=30)


def test_capture_returns_real_calibrated_forecast_and_lower_bounds():
    f=parse_fixture(future_fixture(),NOW,horizon_hours=72)
    r=score_future_event(model(),f,future_feature(),NOW,
                         schedule_snapshot={"snapshot_id":"snapshot-1", "content_sha256":H,
                                            "fetched_at":"2026-11-17T18:00:00+00:00"})
    assert r["official_event_id"]==f["official_event_id"]
    assert r["prediction_created_at"]<r["event_start_time_utc"]
    assert .5<=r["calibrated_probability"]<=1
    assert 0<=r["composite_lower_bound"]<=r["calibrated_probability"]
    assert r["research_metadata"]["model_artifact_sha256"]==model()["artifact_sha256"]
    assert r["probability_publishable"] is False and r["can_execute"] is False


def test_capture_requires_current_source_and_no_postgame_feature():
    f=parse_fixture(future_fixture(),NOW,horizon_hours=72)
    with pytest.raises(ValueError,match="SOURCE_SNAPSHOT_STALE"):
        score_future_event(model(),f,future_feature(),NOW,schedule_snapshot={
            "snapshot_id":"s","content_sha256":H,"fetched_at":"2026-11-01T18:00:00+00:00"})
    bad=future_feature()|{"max_prior_gameday":"2026-11-19"}
    with pytest.raises(ValueError,match="FEATURE_CHRONOLOGY_INVALID"):
        score_future_event(model(),f,bad,NOW,schedule_snapshot={
            "snapshot_id":"s","content_sha256":H,"fetched_at":"2026-11-17T18:00:00+00:00"})


def test_capture_never_backdates_and_rejects_target_after_kickoff():
    now=datetime(2026,11,20,1,16,tzinfo=timezone.utc)
    f=parse_fixture(future_fixture(),NOW,horizon_hours=72)
    with pytest.raises(ValueError,match="PREGAME_CUTOFF"):
        score_future_event(model(),f,future_feature(),now,schedule_snapshot={
            "snapshot_id":"s","content_sha256":H,"fetched_at":"2026-11-19T18:00:00+00:00"})


def test_incumbent_requires_exact_event_kickoff_and_pregame():
    f=parse_fixture(future_fixture(),NOW,horizon_hours=72)
    old=dict(official_event_id=f["official_event_id"],event_start_time_utc=f["event_start_time_utc"],
             created_at="2026-11-17T19:00:00+00:00",calibrated_home_probability=.65,
             model_version="NFL_MAIN",event_prediction_id="id-1",home_team="Houston Texans",away_team="Indianapolis Colts")
    assert choose_incumbent([old],f,NOW)["event_prediction_id"]=="id-1"
    assert choose_incumbent([old|{"created_at":"2026-11-20T01:18:00+00:00"}],f,NOW) is None
    assert choose_incumbent([old|{"event_start_time_utc":"2026-11-20T02:15:00+00:00"}],f,NOW) is None


def test_consensus_two_provider_requires_final_exact_identity_and_scores():
    f=parse_fixture(future_fixture(),NOW,horizon_hours=72)
    frozen=dict(official_event_id=f["official_event_id"],event_start_time_utc=f["event_start_time_utc"],
                home_team="HOU",away_team="IND",selected_participant="HOU",
                calibrated_probability=.7,prediction_created_at=NOW.isoformat(),created_at=NOW.isoformat(),
                lifecycle_state="FORWARD_SHADOW")
    historic=dict(game_id=f["official_event_id"],home_team="HOU",away_team="IND",home_score=24,
                  away_score=21,schedule_content_sha256=H)
    espn=dict(event_id="espn-777",date=f["event_start_time_utc"],home_team="HOU",away_team="IND",
              home_score=24,away_score=21,completed=True,source_sha256="f"*64)
    grade=reconcile_two_sources(frozen,historic,espn,NOW+timedelta(days=4))
    assert grade["outcome"]==1
    assert grade["brier"]==pytest.approx(.09)
    assert grade["grade_source_manifest"]["sources"]==["ESPN","NFLVERSE"]
    assert grade["lifecycle_state"]=="GRADED"
    assert set(grade)==immutable_grade_fields()
    assert reconcile_two_sources(frozen,historic,espn|{"home_score":21},NOW+timedelta(days=4)) is None
    assert reconcile_two_sources(frozen,historic,espn|{"completed":False},NOW+timedelta(days=4)) is None
    assert reconcile_two_sources(frozen,historic,espn|{"home_team":"IND"},NOW+timedelta(days=4)) is None


def test_graded_ties_not_binary_and_no_forward_mutation():
    f=parse_fixture(future_fixture(),NOW,horizon_hours=72)
    frozen=dict(official_event_id=f["official_event_id"],event_start_time_utc=f["event_start_time_utc"],
                home_team="HOU",away_team="IND",selected_participant="HOU",calibrated_probability=.7,
                prediction_created_at=NOW.isoformat(),created_at=NOW.isoformat(),
                lifecycle_state="FORWARD_SHADOW")
    old=dict(game_id=f["official_event_id"],home_team="HOU",away_team="IND",home_score=20,away_score=20,
             schedule_content_sha256=H)
    espn=dict(event_id="z",date=f["event_start_time_utc"],home_team="HOU",away_team="IND",home_score=20,
              away_score=20,completed=True,source_sha256="f"*64)
    assert reconcile_two_sources(frozen,old,espn,NOW+timedelta(days=4)) is None


def test_postkickoff_database_created_forecast_is_not_graded():
    f=parse_fixture(future_fixture(),NOW,horizon_hours=72)
    frozen=dict(official_event_id=f["official_event_id"],event_start_time_utc=f["event_start_time_utc"],
                home_team="HOU",away_team="IND",selected_participant="HOU",
                calibrated_probability=.7,prediction_created_at=NOW.isoformat(),
                created_at="2026-11-20T02:00:00+00:00",lifecycle_state="FORWARD_SHADOW")
    old=dict(game_id=f["official_event_id"],home_team="HOU",away_team="IND",home_score=24,away_score=21,
             schedule_content_sha256=H)
    espn=dict(event_id="espn-7",date=f["event_start_time_utc"],home_team="HOU",away_team="IND",
              home_score=24,away_score=21,completed=True,source_sha256="f"*64)
    assert reconcile_two_sources(frozen,old,espn,NOW+timedelta(days=4)) is None
