from datetime import datetime, timedelta, timezone
from v17.nhl_pregame_evidence_boundary import validate_nhl_pregame_evidence, REQUIRED, FULL_GAME_SETTLEMENT


NOW = datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)


def valid():
    d = {k: "verified" for k in REQUIRED}
    d.update(
        official_event_id="2026020219", home_team="BOS", away_team="NYR",
        event_start_time_utc=(NOW + timedelta(hours=3)).isoformat(),
        goalie_status="CONFIRMED", goalie_status_observed_at=NOW.isoformat(),
        goalie_identity_snapshot_id="starter-source-1", source_snapshot_id="source-1",
        settlement_basis=FULL_GAME_SETTLEMENT,
        home_elo=1510.0, away_elo=1490.0,
        home_goalie_sv_pct=.91, away_goalie_sv_pct=.92,
        home_pp_pct=.21, away_pk_pct=.80,
        injury_report={"status": "CURRENT"}, rest_travel={"status": "CURRENT"},
        calibration_artifact={"artifact_type": "V17_BINARY_CALIBRATION", "model_family": "NHL_ELO_GOALIE_SPECIAL_TEAMS_OT_V1"},
    )
    return d


def trusted_identity():
    # Synthetic resolver receipt used ONLY as a test fixture; never a live claim.
    return {
        "event_id": "2026020219",
        "event_start_time": (NOW + timedelta(hours=3)).isoformat(),
        "home_team": "BOS", "away_team": "NYR",
        "identity_provider": "NHL_PUBLIC_WEB_API",
        "identity_source": "https://api-web.nhle.com/v1/club-schedule-season/BOS/20262027",
        "identity_verified_at": NOW.isoformat(),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def verify(evidence, *, as_of_utc, **kwargs):
    return validate_nhl_pregame_evidence(
        evidence, as_of_utc=as_of_utc, resolved_identity=trusted_identity(), **kwargs
    )


def test_complete_input_is_not_certification():
    r = verify(valid(), as_of_utc=NOW)
    assert r["status"] == "INPUT_EVIDENCE_PASS_NOT_CERTIFICATION"
    assert not r["probability_publishable"] and not r["rank_eligible"] and not r["can_execute"]


def test_missing_goalie_and_calibrator_fail_closed():
    d = valid()
    del d["calibration_artifact"]
    d["goalie_status"] = "CHANGED"
    r = verify(d, as_of_utc=NOW)
    assert "calibration_artifact" in r["missing_fields"]
    assert "NHL_GOALIE_STATUS_UNVERIFIED" in r["blockers"]


def test_stale_goalie_and_started_event_fail_closed():
    d = valid()
    d["goalie_status_observed_at"] = (NOW - timedelta(hours=2)).isoformat()
    d["event_start_time_utc"] = (NOW - timedelta(minutes=1)).isoformat()
    r = verify(d, as_of_utc=NOW)
    assert "NHL_GOALIE_STATUS_STALE" in r["blockers"]
    assert "NHL_EVENT_NOT_PREGAME" in r["blockers"]


def test_duplicate_team_and_regulation_settlement_fail_closed():
    d = valid()
    d["away_team"] = d["home_team"]
    d["settlement_basis"] = "REGULATION_3_WAY"
    r = verify(d, as_of_utc=NOW)
    assert "NHL_DUPLICATE_OPPONENT_IDENTITY" in r["blockers"]
    assert "NHL_SETTLEMENT_BASIS_UNVERIFIED" in r["blockers"]


def test_missing_snapshot_never_passes():
    d = valid()
    d.pop("source_snapshot_id")
    assert "NHL_SOURCE_SNAPSHOT_MISSING" in verify(d, as_of_utc=NOW)["blockers"]


def test_unrecognized_goalie_status_fails_closed():
    d = valid()
    d["goalie_status"] = "verified"
    r = verify(d, as_of_utc=NOW)
    assert r["status"] == "HELD"
    assert "NHL_GOALIE_STATUS_UNVERIFIED" in r["blockers"]


def test_invalid_elo_and_whitespace_input_fail_closed():
    d = valid()
    d["home_elo"] = float("nan")
    d["away_elo"] = "not-a-number"
    d["injury_report"] = "  "
    r = verify(d, as_of_utc=NOW)
    assert r["status"] == "HELD"
    assert "NHL_NUMERIC_FEATURE_INVALID" in r["blockers"]
    assert "injury_report" in r["missing_fields"]


def test_invalid_goalie_timestamp_and_missing_identity_fail_closed():
    d = valid()
    d["goalie_status_observed_at"] = "bad-timestamp"
    d.pop("goalie_identity_snapshot_id")
    r = verify(d, as_of_utc=NOW)
    assert "NHL_GOALIE_STATUS_STALE" in r["blockers"]
    assert "NHL_GOALIE_IDENTITY_UNVERIFIED" in r["blockers"]

 
def test_empty_structured_evidence_fails_closed():
    for key in ("injury_report", "rest_travel", "calibration_artifact"):
        for empty in ({}, [], ()):
            row = valid()
            row[key] = empty
            result = verify(row, as_of_utc=NOW)
            assert result["status"] == "HELD"
            assert key in result["missing_fields"]
            assert "MODEL_INPUTS_INSUFFICIENT" in result["blockers"]


def test_whitespace_and_invalid_snapshot_identities_fail_closed():
    for key, reason in (("source_snapshot_id", "NHL_SOURCE_SNAPSHOT_MISSING"),
                        ("goalie_identity_snapshot_id", "NHL_GOALIE_IDENTITY_UNVERIFIED")):
        for invalid in ("  ", {}, [], 42):
            row = valid()
            row[key] = invalid
            result = verify(row, as_of_utc=NOW)
            assert result["status"] == "HELD"
            assert reason in result["blockers"]


def test_boolean_and_nonfinite_percentages_never_pass():
    for key in ("home_goalie_sv_pct", "away_goalie_sv_pct", "home_pp_pct", "away_pk_pct"):
        for invalid in (True, False, float("nan"), float("inf")):
            row = valid()
            row[key] = invalid
            result = verify(row, as_of_utc=NOW)
            assert result["status"] == "HELD"
            assert "NHL_NUMERIC_FEATURE_INVALID" in result["blockers"]


def test_teams_must_be_recognized_and_distinct_after_canonicalization():
    for home, away, code in (
        ("BOS", " bos ", "NHL_DUPLICATE_OPPONENT_IDENTITY"),
        ("Boston Bruins", "BOS", "NHL_DUPLICATE_OPPONENT_IDENTITY"),
        (["BOS"], "NYR", "NHL_TEAM_IDENTITY_UNVERIFIED"),
        ("UNKNOWN FRANCHISE", "NYR", "NHL_TEAM_IDENTITY_UNVERIFIED"),
        ("BOS", {}, "NHL_TEAM_IDENTITY_UNVERIFIED"),
    ):
        d = valid()
        d["home_team"], d["away_team"] = home, away
        result = verify(d, as_of_utc=NOW)
        assert result["status"] == "HELD"
        assert code in result["blockers"]
        assert result["probability_publishable"] is False


def test_provider_alias_cannot_pass_as_official_nhl_event_identity():
    for official_id in ("espn-401234", "202602021", 2026020219, "2026020219\u0660"):
        d = valid()
        d["official_event_id"] = official_id
        result = verify(d, as_of_utc=NOW)
        assert result["status"] == "HELD"
        assert "NHL_EVENT_IDENTITY_UNVERIFIED" in result["blockers"]


def test_required_structured_inputs_reject_truthy_wrong_shapes():
    for key in ("injury_report", "rest_travel", "calibration_artifact"):
        for invalid in (False, True, 1, "verified", ["claimed"], {}):
            d = valid()
            d[key] = invalid
            result = verify(d, as_of_utc=NOW)
            assert result["status"] == "HELD"
            assert key in result["missing_fields"]
            assert "MODEL_INPUTS_INSUFFICIENT" in result["blockers"]


def test_overflow_and_extreme_times_return_typed_hold_without_crashing():
    for key, val, blocker in (
        ("home_elo", 10 ** 400, "NHL_NUMERIC_FEATURE_INVALID"),
        ("away_elo", 10 ** 400, "NHL_NUMERIC_FEATURE_INVALID"),
        ("home_pp_pct", 10 ** 400, "NHL_NUMERIC_FEATURE_INVALID"),
        ("away_goalie_sv_pct", 10 ** 400, "NHL_NUMERIC_FEATURE_INVALID"),
        ("goalie_status_observed_at", "0001-01-01T00:00:00+01:00", "NHL_GOALIE_STATUS_STALE"),
        ("event_start_time_utc", "0001-01-01T00:00:00+01:00", "NHL_EVENT_NOT_PREGAME"),
    ):
        d = valid()
        d[key] = val
        result = verify(d, as_of_utc=NOW)
        assert result["status"] == "HELD"
        assert blocker in result["blockers"]
        assert result["can_execute"] is False


def test_server_owned_reconciled_identity_is_mandatory_even_for_formatted_id():
    evidence = valid()
    evidence["resolved_identity"] = trusted_identity()  # Caller field is NOT trusted
    held = validate_nhl_pregame_evidence(evidence, as_of_utc=NOW)
    assert held["status"] == "HELD"
    assert "NHL_CANONICAL_IDENTITY_RECEIPT_MISSING" in held["blockers"]
    evidence["official_event_id"] = "0000000000"
    result = validate_nhl_pregame_evidence(
        evidence, as_of_utc=NOW, resolved_identity=trusted_identity()
    )
    assert result["status"] == "HELD"
    assert "NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH" in result["blockers"]


def test_resolved_nhl_receipt_must_match_source_event_teams_time_and_provenance():
    from copy import deepcopy
    cases = (
        ("event_id", "0000000000"),
        ("home_team", "CAR"),
        ("away_team", "BOS"),
        ("event_start_time", (NOW + timedelta(hours=4)).isoformat()),
        ("identity_provider", "ESPN_SCOREBOARD"),
        ("identity_source", "https://site.api.espn.com/claims/123"),
        ("identity_verified_at", (NOW - timedelta(hours=48)).isoformat()),
        ("prediction_authority", True),
        ("market_features_used", True),
        ("can_execute", True),
    )
    for field, invalid in cases:
        receipt = deepcopy(trusted_identity())
        receipt[field] = invalid
        result = validate_nhl_pregame_evidence(
            valid(), as_of_utc=NOW, resolved_identity=receipt
        )
        assert result["status"] == "HELD", field
        assert "NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH" in result["blockers"], field
        assert result["probability_publishable"] is False


def test_resolver_receipt_allows_only_small_capture_clock_skew():
    receipt = trusted_identity()
    receipt["identity_verified_at"] = (NOW + timedelta(minutes=2)).isoformat()
    accepted = validate_nhl_pregame_evidence(
        valid(), as_of_utc=NOW, resolved_identity=receipt
    )
    assert accepted["status"] == "INPUT_EVIDENCE_PASS_NOT_CERTIFICATION"
    receipt["identity_verified_at"] = (NOW + timedelta(minutes=10)).isoformat()
    held = validate_nhl_pregame_evidence(
        valid(), as_of_utc=NOW, resolved_identity=receipt
    )
    assert held["status"] == "HELD"
    assert "NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH" in held["blockers"]
