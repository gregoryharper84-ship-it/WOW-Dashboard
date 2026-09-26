from datetime import datetime, timedelta, timezone

import pytest

from v17.spread_exact_line_evidence import (
    ExactSpreadEvidence,
    bind_exact_home_spreads,
    evaluate_exact_line_candidate,
)
from v17.spread_margin_challenger import MarginDistributionArtifact, MarginTrainingRow, SpreadChallengerUnavailable


def _artifact():
    return MarginDistributionArtifact(
        sport="NFL",
        model_family="NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version="NFL_SPREAD_MARGIN_TEAM_STATE_V1",
        feature_names=("x",),
        scaler_mean=(0.0,),
        scaler_scale=(1.0,),
        coefficients=(0.0,),
        intercept=0.0,
        calibration_residuals=(-7.0, -3.0, 0.0, 3.0, 7.0),
        train_rows=100,
        calibration_rows=5,
        test_rows=2,
        training_dataset_hash="abc",
        ridge_alpha=4.0,
    )


def _row(event_id, start, margin):
    return MarginTrainingRow(
        event_id=event_id,
        event_start_time=start.isoformat(),
        feature_as_of=(start - timedelta(seconds=1)).isoformat(),
        margin=margin,
        features={"x": 0.0},
        source_manifest_sha256="sha",
    )


def _market_rows(start):
    quote = (start - timedelta(hours=1)).isoformat()
    base = {
        "provider_event_id": "rd-1",
        "event_start_utc": start.isoformat(),
        "market_id": "2",
        "market_name": "handicap",
        "sportsbook": "Pinnacle",
        "price_updated_at": quote,
        "fetched_at": quote,
        "snapshot_kind": "CURRENT",
        "is_live": False,
        "is_main_line": True,
        "is_available": True,
    }
    return [
        {**base, "participant_type": "home", "participant_name": "Dallas Cowboys", "selection": "Dallas Cowboys", "line_value": -3.5},
        {**base, "participant_type": "away", "participant_name": "Philadelphia Eagles", "selection": "Philadelphia Eagles", "line_value": 3.5},
    ]


def test_binder_accepts_one_unambiguous_main_line_pregame_spread():
    start = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)
    evidence, audit = bind_exact_home_spreads(
        sport="NFL",
        event_identities=[{
            "event_id": "2026_04_PHI_DAL",
            "event_start_time": start.isoformat(),
            "home_team": "DAL",
            "away_team": "PHI",
        }],
        market_rows=_market_rows(start),
    )
    bound = evidence["2026_04_PHI_DAL"]
    assert bound.home_spread == -3.5
    assert bound.sportsbook == "Pinnacle"
    assert audit["bound_event_n"] == 1
    assert audit["coverage"] == 1.0
    assert audit["can_execute"] is False


def test_binder_fails_closed_on_ambiguous_provider_event_identity():
    start = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)
    rows = _market_rows(start)
    duplicate = [{**row, "provider_event_id": "rd-2"} for row in rows]
    evidence, audit = bind_exact_home_spreads(
        sport="NFL",
        event_identities=[{
            "event_id": "event-1",
            "event_start_time": start.isoformat(),
            "home_team": "DAL",
            "away_team": "PHI",
        }],
        market_rows=rows + duplicate,
    )
    assert evidence == {}
    assert audit["blocker_counts"]["SPREAD_EVENT_IDENTITY_AMBIGUOUS"] == 1


def test_binder_rejects_mismatched_participant_even_when_row_claims_home_side():
    start = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)
    rows = _market_rows(start)
    poisoned = {
        **rows[0],
        "participant_type": "home",
        "participant_name": "Philadelphia Eagles",
        "selection": "Philadelphia Eagles",
        "line_value": 9.5,
        "price_updated_at": (start - timedelta(minutes=5)).isoformat(),
        "fetched_at": (start - timedelta(minutes=5)).isoformat(),
    }
    evidence, audit = bind_exact_home_spreads(
        sport="NFL",
        event_identities=[{
            "event_id": "event-1",
            "event_start_time": start.isoformat(),
            "home_team": "DAL",
            "away_team": "PHI",
        }],
        market_rows=rows + [poisoned],
    )
    assert evidence["event-1"].home_spread == -3.5
    assert audit["bound_event_n"] == 1


def test_exact_line_evaluation_uses_provider_line_only_as_threshold():
    start = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)
    rows = [_row("e1", start, 7), _row("e2", start + timedelta(days=1), -3)]
    evidence = {
        "e1": ExactSpreadEvidence(
            event_id="e1", provider_event_id="rd1", sport="NFL",
            event_start_time=start.isoformat(), sportsbook="Pinnacle",
            quote_timestamp=(start - timedelta(hours=1)).isoformat(),
            home_team="DAL", away_team="PHI", home_spread=-3.5,
            market_id="2", snapshot_kind="CURRENT",
        ),
        "e2": ExactSpreadEvidence(
            event_id="e2", provider_event_id="rd2", sport="NFL",
            event_start_time=(start + timedelta(days=1)).isoformat(), sportsbook="Pinnacle",
            quote_timestamp=(start + timedelta(days=1, hours=-1)).isoformat(),
            home_team="GB", away_team="CHI", home_spread=3.5,
            market_id="2", snapshot_kind="CURRENT",
        ),
    }
    metrics = evaluate_exact_line_candidate(_artifact(), rows, evidence)
    assert metrics["evaluation_mode"] == "PROVIDER_BOUND_EXACT_SPREAD"
    assert metrics["evidence_row_n"] == 2
    assert metrics["exact_line_coverage"] == 1.0
    assert isinstance(metrics["cover_brier"], float)
    assert isinstance(metrics["cover_log_loss"], float)
    assert metrics["spread_line_used_as_feature"] is False
    assert metrics["market_probability_substitution_used"] is False
    assert metrics["moneyline_probability_used"] is False
    assert metrics["automatic_certification"] is False
    assert metrics["probability_publishable"] is False
    assert metrics["certification_ready"] is False
    assert metrics["can_execute"] is False


def test_exact_line_post_start_quote_is_typed_failure():
    start = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)
    row = _row("e1", start, 7)
    evidence = {
        "e1": ExactSpreadEvidence(
            event_id="e1", provider_event_id="rd1", sport="NFL",
            event_start_time=start.isoformat(), sportsbook="Pinnacle",
            quote_timestamp=(start + timedelta(seconds=1)).isoformat(),
            home_team="DAL", away_team="PHI", home_spread=-3.5,
            market_id="2", snapshot_kind="CURRENT",
        )
    }
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        evaluate_exact_line_candidate(_artifact(), [row], evidence)
    assert exc.value.code == "SPREAD_EXACT_LINE_POST_START_EVIDENCE"


def test_missing_exact_lines_do_not_fall_back_to_synthetic_grid():
    start = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        evaluate_exact_line_candidate(_artifact(), [_row("e1", start, 7)], {})
    assert exc.value.code == "SPREAD_EXACT_LINE_EVIDENCE_UNAVAILABLE"
