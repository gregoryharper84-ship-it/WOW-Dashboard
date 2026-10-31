from datetime import datetime, timedelta, timezone
from v17.nhl_pregame_evidence_boundary import validate_nhl_pregame_evidence, REQUIRED, FULL_GAME_SETTLEMENT


NOW = datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)


def valid():
    d = {k: "verified" for k in REQUIRED}
    d.update(
        official_event_id="2026020219", home_team="HOME", away_team="AWAY",
        event_start_time_utc=(NOW + timedelta(hours=3)).isoformat(),
        goalie_status="CONFIRMED", goalie_status_observed_at=NOW.isoformat(),
        goalie_identity_snapshot_id="starter-source-1", source_snapshot_id="source-1",
        settlement_basis=FULL_GAME_SETTLEMENT,
        home_goalie_sv_pct=.91, away_goalie_sv_pct=.92,
        home_pp_pct=.21, away_pk_pct=.80,
    )
    return d


def test_complete_input_is_not_certification():
    r = validate_nhl_pregame_evidence(valid(), as_of_utc=NOW)
    assert r["status"] == "INPUT_EVIDENCE_PASS_NOT_CERTIFICATION"
    assert not r["probability_publishable"] and not r["rank_eligible"] and not r["can_execute"]


def test_missing_goalie_and_calibrator_fail_closed():
    d = valid()
    del d["calibration_artifact"]
    d["goalie_status"] = "CHANGED"
    r = validate_nhl_pregame_evidence(d, as_of_utc=NOW)
    assert "calibration_artifact" in r["missing_fields"]
    assert "NHL_GOALIE_STATUS_UNVERIFIED" in r["blockers"]


def test_stale_goalie_and_started_event_fail_closed():
    d = valid()
    d["goalie_status_observed_at"] = (NOW - timedelta(hours=2)).isoformat()
    d["event_start_time_utc"] = (NOW - timedelta(minutes=1)).isoformat()
    r = validate_nhl_pregame_evidence(d, as_of_utc=NOW)
    assert "NHL_GOALIE_STATUS_STALE" in r["blockers"]
    assert "NHL_EVENT_NOT_PREGAME" in r["blockers"]


def test_duplicate_team_and_regulation_settlement_fail_closed():
    d = valid()
    d["away_team"] = d["home_team"]
    d["settlement_basis"] = "REGULATION_3_WAY"
    r = validate_nhl_pregame_evidence(d, as_of_utc=NOW)
    assert "NHL_DUPLICATE_OPPONENT_IDENTITY" in r["blockers"]
    assert "NHL_SETTLEMENT_BASIS_UNVERIFIED" in r["blockers"]


def test_missing_snapshot_never_passes():
    d = valid()
    d.pop("source_snapshot_id")
    assert "NHL_SOURCE_SNAPSHOT_MISSING" in validate_nhl_pregame_evidence(d, as_of_utc=NOW)["blockers"]
