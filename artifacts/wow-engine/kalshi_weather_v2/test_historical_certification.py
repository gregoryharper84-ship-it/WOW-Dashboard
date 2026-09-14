from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_weather_v2.historical_certification import (
    HistoricalCertificationError,
    PredicateResult,
    REPLAY_LEAD_TIME_BUCKET,
    REPLAY_MODEL_VERSION,
    ReplaySample,
    ReplayTarget,
    _build_settled_event,
    build_certification_report,
    select_safe_run_initialization,
)


def _market(*, floor: float, result: str, source: str = "Synoptic Data", expiration: str = "87.80"):
    return {
        "ticker": f"KXTEMPMIAH-26SEP1414-T{floor}",
        "event_ticker": "KXTEMPMIAH-26SEP1414",
        "occurrence_datetime": "2026-09-14T18:05:00Z",
        "expiration_value": expiration,
        "result": result,
        "strike_type": "greater",
        "floor_strike": floor,
        "rules_primary": (
            "If the temperature recorded at Miami, FL for Sep 14, 2026 at 2 PM EDT "
            f"as reported by {source}, is above {floor}°, then the market resolves to Yes."
        ),
        "rules_secondary": "",
    }


def test_safe_run_selection_is_strictly_predecision():
    decision = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    run = select_safe_run_initialization(decision)
    assert run == datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
    assert run + timedelta(hours=6) <= decision


def test_synoptic_expiration_value_and_rule_time_control_event():
    target = ReplayTarget("miami", "Miami, FL", "KXTEMPMIAH", 25.7617, -80.1918)
    event = _build_settled_event(
        target,
        "KXTEMPMIAH-26SEP1414",
        [_market(floor=87.99, result="no"), _market(floor=86.99, result="yes")],
    )
    assert event.settlement_source_name == "Synoptic Data"
    assert event.settled_value_f == 87.8
    assert event.observation_time_utc == "2026-09-14T18:00:00Z"
    assert len(event.predicates) == 2


def test_accuweather_regime_is_rejected():
    target = ReplayTarget("miami", "Miami, FL", "KXTEMPMIAH", 25.7617, -80.1918)
    with pytest.raises(HistoricalCertificationError) as exc:
        _build_settled_event(
            target,
            "KXTEMPMIAH-26APR2020",
            [_market(floor=72.99, result="yes", source="AccuWeather", expiration="73.00")],
        )
    assert exc.value.code == "SETTLEMENT_SOURCE_REGIME_MISMATCH"


def test_threshold_result_conflict_is_rejected():
    target = ReplayTarget("miami", "Miami, FL", "KXTEMPMIAH", 25.7617, -80.1918)
    with pytest.raises(HistoricalCertificationError) as exc:
        _build_settled_event(target, "KXTEMPMIAH-26SEP1414", [_market(floor=86.99, result="no")])
    assert exc.value.code == "SETTLEMENT_PREDICATE_CONFLICT"


def _sample(i: int, residual: float) -> ReplaySample:
    obs = datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc) + timedelta(hours=i)
    settled = 75.0 + ((i % 6) - 3) * 0.2
    predicates = tuple(
        PredicateResult(
            ticker=f"T{i}-{delta}",
            strike_type="greater",
            floor_strike=settled + delta - 0.01,
            cap_strike=None,
            yes_outcome=settled > settled + delta - 0.01,
        )
        for delta in (-2, -1, 0, 1, 2)
    )
    return ReplaySample(
        sample_id=f"sample-{i}",
        station_id="KALSHI_WEATHER_INDEX:miami",
        series_ticker="KXTEMPMIAH",
        event_ticker=f"EVENT-{i}",
        observation_time_utc=obs.isoformat().replace("+00:00", "Z"),
        settlement_source_name="Synoptic Data",
        settled_value_f=settled,
        decision_time=(obs - timedelta(hours=18)).isoformat().replace("+00:00", "Z"),
        run_initialization=(obs - timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        safe_available_at=(obs - timedelta(hours=18)).isoformat().replace("+00:00", "Z"),
        gfs_f=settled + residual - 0.1,
        ecmwf_f=settled + residual + 0.1,
        central_estimate_f=settled + residual,
        archive_source_url="https://single-runs-api.open-meteo.com/test",
        archive_payload_hash="abc123",
        predicates=predicates,
        no_lookahead_verified=True,
        source_identity_verified=True,
    )


def test_49_unique_targets_cannot_certify():
    report = build_certification_report([_sample(i, 0.0) for i in range(49)])
    assert report["status"] == "CERTIFICATION_NOT_READY"
    assert report["remaining_sample_n"] == 1
    assert report["promotion_eligible"] is False


def test_chronological_30_20_replay_can_pass_frozen_gates():
    train = ([-1.2, -0.8, -0.4, 0.0, 0.4, 0.8, 1.2] * 5)[:30]
    holdout = [-1.0, -0.5, 0.0, 0.5, 1.0] * 4
    report = build_certification_report(
        [_sample(i, residual) for i, residual in enumerate(train + holdout)]
    )
    assert report["model_version"] == REPLAY_MODEL_VERSION
    assert report["lead_time_bucket"] == REPLAY_LEAD_TIME_BUCKET
    assert report["train_n"] == 30
    assert report["holdout_n"] == 20
    assert report["status"] == "CERTIFICATION_ACCEPTANCE_PASS"
    assert report["promotion_eligible"] is True
    assert report["probability_publishable"] is False
    assert report["market_price_used_as_model_input"] is False
    assert report["can_execute"] is False


def test_lookahead_row_blocks_promotion():
    rows = [_sample(i, (-1, -0.5, 0, 0.5, 1)[i % 5]) for i in range(50)]
    bad = rows[-1]
    rows[-1] = ReplaySample(**{**bad.__dict__, "no_lookahead_verified": False})
    report = build_certification_report(rows)
    assert report["promotion_eligible"] is False
    assert "LOOKAHEAD_ROW_PRESENT" in report["blockers"]
