from __future__ import annotations

from types import SimpleNamespace

import pytest

from kalshi_weather_v2.calibration_registry import (
    MINIMUM_CANDIDATE_SAMPLES,
    ROW_LIMIT,
    collect_unique_residual_samples,
    fit_candidate_profile_sets,
    load_scoring_evidence_rows,
    persist_candidate_calibration_profiles,
    probability_scoring_summary,
)
from kalshi_weather_v2.calibration_fit import ForecastResidual, fit_candidate_calibration_profile
from kalshi_weather_v2.models import ContractSnapshot, WeatherEvidenceSnapshot
from kalshi_weather_v2.persistence import KalshiWeatherPersistence, KalshiWeatherPersistenceError
from kalshi_weather_v2.probability_core import CalibrationProfile
from kalshi_weather_v2.runtime import RESEARCH_PROBABILITY_STATUS, _resolve_probability

STATION = "KALSHI_WEATHER_INDEX:miami"
LANE = "HOURLY_TEMPERATURE"
FITTED_AT = "2026-12-01T00:00:00Z"


class _Table:
    def __init__(self, primary_key: str):
        self.primary_key = primary_key
        self.rows: dict[str, dict] = {}


class _Query:
    def __init__(self, table: _Table):
        self.table = table
        self.ops: list[tuple[str, str, object]] = []
        self.insert_row = None
        self.limit_n = None
        self.order_key = None
        self.desc = False

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.ops.append(("eq", key, value))
        return self

    def gte(self, key, value):
        self.ops.append(("gte", key, value))
        return self

    def lte(self, key, value):
        self.ops.append(("lte", key, value))
        return self

    def order(self, key, desc=False):
        self.order_key = key
        self.desc = desc
        return self

    def limit(self, value):
        self.limit_n = value
        return self

    def insert(self, row):
        self.insert_row = dict(row)
        return self

    def execute(self):
        if self.insert_row is not None:
            key = self.insert_row[self.table.primary_key]
            if key in self.table.rows:
                raise RuntimeError("duplicate")
            self.table.rows[key] = dict(self.insert_row)
            return SimpleNamespace(data=[dict(self.insert_row)])
        rows = list(self.table.rows.values())
        for op, key, value in self.ops:
            if op == "eq":
                rows = [row for row in rows if row.get(key) == value]
            elif op == "gte":
                rows = [row for row in rows if row.get(key) is not None and row.get(key) >= value]
            else:
                rows = [row for row in rows if row.get(key) is not None and row.get(key) <= value]
        if self.order_key is not None:
            rows = sorted(rows, key=lambda row: row.get(self.order_key), reverse=self.desc)
        if self.limit_n is not None:
            rows = rows[: self.limit_n]
        return SimpleNamespace(data=[dict(row) for row in rows])


class _Client:
    def __init__(self):
        self.tables = {
            "wow_kalshi_weather_predictions": _Table("prediction_id"),
            "wow_kalshi_weather_outcomes": _Table("outcome_id"),
            "wow_kalshi_weather_calibration_profiles": _Table("calibration_profile_id"),
        }

    def table(self, name):
        return _Query(self.tables.setdefault(name, _Table("id")))


def _target_time(index: int) -> str:
    # 2026-11-01T15:00Z + index hours.
    hour = 15 + index
    day = 1 + hour // 24
    return f"2026-11-{day:02d}T{hour % 24:02d}:00:00Z"


def _prediction(
    index: int,
    *,
    ticker: str | None = None,
    bucket: str = "H0",
    station: str = STATION,
    target_index: int | None = None,
    forecast: float | None = None,
    p_yes: float | None = None,
) -> dict:
    target = _target_time(index if target_index is None else target_index)
    settled = 80.0 + (index % 7)
    return {
        "prediction_id": f"pred-{index}-{ticker or 'a'}",
        "ticker": ticker or f"KXTEMPMIAH-{index}",
        "decision_time": f"2026-10-31T{index % 24:02d}:00:00Z",
        "central_estimate_f": settled + ((index % 5) - 2) * 0.4 + 0.3 if forecast is None else forecast,
        "p_yes": p_yes,
        "model_payload": {
            "lead_time_bucket": bucket,
            "contract": {
                "lane": LANE,
                "settlement_location_code": station,
                "observation_window": f"POINT_IN_TIME:{target}",
            },
        },
    }


def _outcome(index: int, *, yes_outcome=True) -> dict:
    return {
        "outcome_id": f"out-{index}",
        "prediction_id": f"pred-{index}-a",
        "settled_at": f"2026-11-30T{index % 24:02d}:00:00Z",
        "settled_value": 80.0 + (index % 7),
        "yes_outcome": yes_outcome,
    }


def _evidence_rows(count: int) -> tuple[list[dict], list[dict]]:
    predictions = [_prediction(index) for index in range(count)]
    outcomes = [_outcome(index) for index in range(count)]
    return predictions, outcomes


def _contract() -> ContractSnapshot:
    return ContractSnapshot(
        market_title="Temperature in Miami today at 3pm?",
        contract_title="80 or above",
        ticker="KXTEMPMIAH-80",
        lane=LANE,
        yes_condition="index >= 80",
        no_condition="index < 80",
        location="Miami",
        metric="kalshi_weather_index_point_temperature",
        units="F",
        observation_window="POINT_IN_TIME:2026-11-01T20:00:00Z",
        timezone="EST(UTC-05:00)",
        settlement_source="Synoptic Data",
        settlement_station_id=None,
        settlement_station_name=None,
        rounding_convention="USE_OFFICIAL_KALSHI_WEATHER_INDEX_AS_PUBLISHED",
        trace_measurement_rules="POINT_INDEX_VALUE",
        market_close_time="2026-11-01T21:00:00Z",
        rule_snapshot_id="rule-hourly",
        threshold_lower=80.0,
        settlement_location_code=STATION,
    )


def _evidence() -> WeatherEvidenceSnapshot:
    return WeatherEvidenceSnapshot(
        analysis_time="2026-11-01T19:00:00Z",
        latest_official_observation_time=None,
        forecast_issue_time="2026-11-01T18:00:00Z",
        source_snapshot_ids=("forecast-1",),
        central_estimate=80.5,
        evidence_complete=True,
        settlement_location_verified=True,
        settlement_source_verified=True,
        temporal_provenance_verified=True,
    )


class _StubPersistence:
    def __init__(self, *, certified=None, candidate=None):
        self.certified = certified
        self.candidate = candidate

    def load_latest_certified_calibration(self, **_kwargs):
        return self.certified

    def load_latest_candidate_calibration(self, **_kwargs):
        return self.candidate


def _profile(*, certified: bool) -> CalibrationProfile:
    return CalibrationProfile(
        station_id=STATION,
        lane=LANE,
        lead_time_bucket="H0",
        bias_f=0.0,
        sigma_f=1.0,
        lower_sigma_f=1.0,
        upper_sigma_f=1.0,
        sample_n=MINIMUM_CANDIDATE_SAMPLES,
        method="STATION_HORIZON_EMPIRICAL_RESIDUAL_CANDIDATE_V1",
        certified=certified,
    )




def test_candidate_fit_rejects_fractional_second_lookahead_as_an_instant():
    result = fit_candidate_calibration_profile(
        [
            ForecastResidual(
                forecast_value_f=80.5,
                settled_value_f=80.0,
                forecast_as_of="2026-11-01T00:00:00.500000Z",
                settlement_time="2026-11-01T00:00:00Z",
            )
        ],
        station_id=STATION,
        lane=LANE,
        lead_time_bucket="H0",
        minimum_n=1,
    )
    assert result.profile is None
    assert "CALIBRATION_LOOKAHEAD_LEAKAGE" in result.blockers

def test_threshold_siblings_collapse_to_one_residual():
    predictions = [
        _prediction(0, ticker="KXTEMPMIAH-80"),
        _prediction(0, ticker="KXTEMPMIAH-81"),
        _prediction(0, ticker="KXTEMPMIAH-82"),
    ]
    outcomes = [
        {"outcome_id": "o0", "prediction_id": "pred-0-KXTEMPMIAH-80", "settled_at": "2026-11-30T00:00:00Z", "settled_value": 80.0},
        {"outcome_id": "o1", "prediction_id": "pred-0-KXTEMPMIAH-81", "settled_at": "2026-11-30T00:00:00Z", "settled_value": 80.0},
        {"outcome_id": "o2", "prediction_id": "pred-0-KXTEMPMIAH-82", "settled_at": "2026-11-30T00:00:00Z", "settled_value": 80.0},
    ]
    groups = collect_unique_residual_samples(predictions, outcomes)
    assert list(groups) == [(STATION, LANE, "H0")]
    assert len(groups[(STATION, LANE, "H0")]) == 1


def test_lookahead_and_unsettled_rows_are_excluded():
    prediction = _prediction(0)
    prediction["decision_time"] = "2026-12-01T00:00:00Z"
    outcome = _outcome(0)
    outcome["settled_at"] = "2026-11-30T00:00:00Z"
    assert collect_unique_residual_samples([prediction], [outcome]) == {}
    assert collect_unique_residual_samples([_prediction(1)], []) == {}


def test_below_minimum_sample_never_produces_a_candidate_profile():
    predictions, outcomes = _evidence_rows(MINIMUM_CANDIDATE_SAMPLES - 1)
    fitted = fit_candidate_profile_sets(predictions, outcomes)
    assert len(fitted) == 1
    assert fitted[0]["fit"].profile is None
    assert "CALIBRATION_SAMPLE_INSUFFICIENT" in fitted[0]["fit"].blockers


def test_candidate_profile_is_persisted_uncertified_and_non_executable():
    client = _Client()
    predictions, outcomes = _evidence_rows(MINIMUM_CANDIDATE_SAMPLES)
    client.tables["wow_kalshi_weather_predictions"].rows = {
        row["prediction_id"]: row for row in predictions
    }
    client.tables["wow_kalshi_weather_outcomes"].rows = {
        row["outcome_id"]: row for row in outcomes
    }

    result = persist_candidate_calibration_profiles(
        client, model_version="TEST_MODEL_V1", fitted_as_of=FITTED_AT
    )

    assert result["status"] == "CALIBRATION_CANDIDATES_PERSISTED"
    assert result["probability_publishable"] is False
    assert result["capability_promotion_allowed"] is False
    assert result["can_execute"] is False
    persisted = result["profiles_persisted"]
    assert len(persisted) == 1
    assert persisted[0]["unique_sample_n"] == MINIMUM_CANDIDATE_SAMPLES
    assert persisted[0]["certified"] is False

    rows = list(client.tables["wow_kalshi_weather_calibration_profiles"].rows.values())
    assert len(rows) == 1
    row = rows[0]
    assert row["certified"] is False
    assert row["can_execute"] is False
    assert row["sample_n"] == MINIMUM_CANDIDATE_SAMPLES
    assert row["certification_evidence"]["profile_scope"] == "RESEARCH_CANDIDATE_UNCERTIFIED"
    assert row["certification_evidence"]["probability_publishable"] is False


def test_identical_evidence_is_an_idempotent_append():
    client = _Client()
    predictions, outcomes = _evidence_rows(MINIMUM_CANDIDATE_SAMPLES)
    client.tables["wow_kalshi_weather_predictions"].rows = {
        row["prediction_id"]: row for row in predictions
    }
    client.tables["wow_kalshi_weather_outcomes"].rows = {
        row["outcome_id"]: row for row in outcomes
    }
    first = persist_candidate_calibration_profiles(
        client, model_version="TEST_MODEL_V1", fitted_as_of=FITTED_AT
    )
    second = persist_candidate_calibration_profiles(
        client, model_version="TEST_MODEL_V1", fitted_as_of="2026-12-01T00:15:00Z"
    )
    assert len(client.tables["wow_kalshi_weather_calibration_profiles"].rows) == 1
    assert first["profiles_persisted"][0]["calibration_profile_id"] == second["profiles_persisted"][0]["calibration_profile_id"]
    assert second["profiles_persisted"][0]["reused_existing"] is True
    stored = next(iter(client.tables["wow_kalshi_weather_calibration_profiles"].rows.values()))
    assert stored["fitted_as_of"] == "2026-12-01T00:00:00.000000Z"


def test_new_residual_evidence_appends_a_new_profile_version():
    client = _Client()
    predictions, outcomes = _evidence_rows(MINIMUM_CANDIDATE_SAMPLES)
    client.tables["wow_kalshi_weather_predictions"].rows = {
        row["prediction_id"]: row for row in predictions
    }
    client.tables["wow_kalshi_weather_outcomes"].rows = {
        row["outcome_id"]: row for row in outcomes
    }
    first = persist_candidate_calibration_profiles(
        client, model_version="TEST_MODEL_V1", fitted_as_of=FITTED_AT
    )
    extra_prediction, extra_outcome = _evidence_rows(MINIMUM_CANDIDATE_SAMPLES + 1)
    client.tables["wow_kalshi_weather_predictions"].rows.update(
        {row["prediction_id"]: row for row in extra_prediction}
    )
    client.tables["wow_kalshi_weather_outcomes"].rows.update(
        {row["outcome_id"]: row for row in extra_outcome}
    )
    second = persist_candidate_calibration_profiles(
        client, model_version="TEST_MODEL_V1", fitted_as_of=FITTED_AT
    )
    assert len(client.tables["wow_kalshi_weather_calibration_profiles"].rows) == 2
    assert (
        first["profiles_persisted"][0]["calibration_profile_id"]
        != second["profiles_persisted"][0]["calibration_profile_id"]
    )




def test_candidate_refit_with_unchanged_group_does_not_block_a_later_new_group():
    client = _Client()
    h0_predictions, h0_outcomes = _evidence_rows(MINIMUM_CANDIDATE_SAMPLES)
    h1_predictions = [
        {**_prediction(i, bucket="H1", ticker="H1"), "prediction_id": f"pred-h1-{i}"}
        for i in range(MINIMUM_CANDIDATE_SAMPLES)
    ]
    h1_outcomes = [
        {
            "outcome_id": f"out-h1-{i}",
            "prediction_id": f"pred-h1-{i}",
            "settled_at": f"2026-11-30T{ i % 24:02d}:00:00Z",
            "settled_value": 80.0 + (i % 7),
            "yes_outcome": True,
        }
        for i in range(MINIMUM_CANDIDATE_SAMPLES)
    ]
    client.tables["wow_kalshi_weather_predictions"].rows = {
        row["prediction_id"]: row for row in h0_predictions + h1_predictions
    }
    client.tables["wow_kalshi_weather_outcomes"].rows = {
        row["outcome_id"]: row for row in h0_outcomes + h1_outcomes
    }
    first = persist_candidate_calibration_profiles(
        client, model_version="TEST_MODEL_V1", fitted_as_of=FITTED_AT
    )
    assert len(first["profiles_persisted"]) == 2

    new_prediction = {
        **_prediction(MINIMUM_CANDIDATE_SAMPLES, bucket="H1", ticker="H1"),
        "prediction_id": f"pred-h1-{MINIMUM_CANDIDATE_SAMPLES}",
    }
    new_outcome = {
        "outcome_id": f"out-h1-{MINIMUM_CANDIDATE_SAMPLES}",
        "prediction_id": new_prediction["prediction_id"],
        "settled_at": "2026-11-30T23:00:00Z",
        "settled_value": 80.0 + (MINIMUM_CANDIDATE_SAMPLES % 7),
        "yes_outcome": True,
    }
    client.tables["wow_kalshi_weather_predictions"].rows[new_prediction["prediction_id"]] = new_prediction
    client.tables["wow_kalshi_weather_outcomes"].rows[new_outcome["outcome_id"]] = new_outcome

    second = persist_candidate_calibration_profiles(
        client, model_version="TEST_MODEL_V1", fitted_as_of="2026-12-01T00:15:00Z"
    )
    assert len(client.tables["wow_kalshi_weather_calibration_profiles"].rows) == 3
    assert any(row["reused_existing"] for row in second["profiles_persisted"])
    assert any(not row["reused_existing"] for row in second["profiles_persisted"])


def test_candidate_evidence_identity_includes_target_sample_keys():
    client = _Client()
    predictions, outcomes = _evidence_rows(MINIMUM_CANDIDATE_SAMPLES)
    client.tables["wow_kalshi_weather_predictions"].rows = {
        row["prediction_id"]: row for row in predictions
    }
    client.tables["wow_kalshi_weather_outcomes"].rows = {
        row["outcome_id"]: row for row in outcomes
    }
    first = persist_candidate_calibration_profiles(
        client, model_version="TEST_MODEL_V1", fitted_as_of=FITTED_AT
    )
    first_id = first["profiles_persisted"][0]["calibration_profile_id"]

    changed = {}
    for row in predictions:
        copied = dict(row)
        copied["model_payload"] = {
            **row["model_payload"],
            "contract": {
                **row["model_payload"]["contract"],
                "observation_window": row["model_payload"]["contract"]["observation_window"] + "-REV",
            },
        }
        changed[copied["prediction_id"]] = copied
    client.tables["wow_kalshi_weather_predictions"].rows = changed
    # Restore valid point-in-time target labels with distinct target hours.
    for index, row in enumerate(changed.values()):
        target_day = 3 + index // 24
        target_hour = index % 24
        row["model_payload"]["contract"]["observation_window"] = f"POINT_IN_TIME:2026-11-{target_day:02d}T{target_hour:02d}:00:00Z"

    second = persist_candidate_calibration_profiles(
        client, model_version="TEST_MODEL_V1", fitted_as_of="2026-12-01T00:15:00Z"
    )
    assert second["profiles_persisted"][0]["calibration_profile_id"] != first_id


def test_scoring_input_read_failure_is_not_reported_as_an_empty_ledger():
    class _FailingClient:
        def table(self, _name):
            raise RuntimeError("database unavailable")

    with pytest.raises(KalshiWeatherPersistenceError) as caught:
        load_scoring_evidence_rows(_FailingClient())
    assert caught.value.code == "KALSHI_WEATHER_CALIBRATION_LEDGER_READ_FAILED"


def test_scoring_input_at_row_limit_fails_closed_as_potentially_truncated():
    client = _Client()
    table = client.tables["wow_kalshi_weather_predictions"]
    table.rows = {
        f"pred-{index:05d}": {"prediction_id": f"pred-{index:05d}"}
        for index in range(ROW_LIMIT)
    }
    with pytest.raises(KalshiWeatherPersistenceError) as caught:
        load_scoring_evidence_rows(client)
    assert caught.value.code == "KALSHI_WEATHER_CALIBRATION_INPUT_TRUNCATED"


def test_mixed_precision_decisions_choose_the_earliest_instant():
    earlier = _prediction(0, ticker="EARLY")
    later = _prediction(0, ticker="LATE")
    earlier["decision_time"] = "2026-10-31T00:00:00Z"
    later["decision_time"] = "2026-10-31T00:00:00.500000Z"
    outcomes = [
        {
            "outcome_id": "early-out",
            "prediction_id": earlier["prediction_id"],
            "settled_at": "2026-11-30T00:00:00Z",
            "settled_value": 80.0,
        },
        {
            "outcome_id": "late-out",
            "prediction_id": later["prediction_id"],
            "settled_at": "2026-11-30T00:00:00Z",
            "settled_value": 80.0,
        },
    ]
    groups = collect_unique_residual_samples([later, earlier], outcomes)
    sample = groups[(STATION, LANE, "H0")][0]
    assert sample.decision_time == "2026-10-31T00:00:00.000000Z"

def test_candidate_loader_cannot_return_a_certified_profile():
    client = _Client()
    table = client.tables["wow_kalshi_weather_calibration_profiles"]
    table.rows["certified-1"] = {
        "calibration_profile_id": "certified-1",
        "station_id": STATION,
        "lane": LANE,
        "lead_time_bucket": "H0",
        "method": "CERTIFIED",
        "bias_f": 0.0,
        "sigma_f": 1.0,
        "lower_sigma_f": 1.0,
        "upper_sigma_f": 1.0,
        "sample_n": 100,
        "certified": True,
        "fitted_as_of": "2026-11-01T00:00:00Z",
    }
    store = KalshiWeatherPersistence(client)
    assert (
        store.load_latest_candidate_calibration(
            station_id=STATION,
            lane=LANE,
            lead_time_bucket="H0",
            fitted_before=FITTED_AT,
        )
        is None
    )
    assert store.load_latest_certified_calibration(
        station_id=STATION,
        lane=LANE,
        lead_time_bucket="H0",
        fitted_before=FITTED_AT,
    ) is not None


def test_research_candidate_loader_respects_the_sample_minimum():
    client = _Client()
    table = client.tables["wow_kalshi_weather_calibration_profiles"]
    table.rows["candidate-thin"] = {
        "calibration_profile_id": "candidate-thin",
        "station_id": STATION,
        "lane": LANE,
        "lead_time_bucket": "H0",
        "method": "CANDIDATE",
        "bias_f": 0.0,
        "sigma_f": 1.0,
        "lower_sigma_f": 1.0,
        "upper_sigma_f": 1.0,
        "sample_n": 3,
        "certified": False,
        "fitted_as_of": "2026-11-01T00:00:00Z",
    }
    store = KalshiWeatherPersistence(client)
    assert (
        store.load_latest_candidate_calibration(
            station_id=STATION,
            lane=LANE,
            lead_time_bucket="H0",
            fitted_before=FITTED_AT,
            minimum_sample_n=MINIMUM_CANDIDATE_SAMPLES,
        )
        is None
    )


def test_research_probability_is_numeric_but_never_calibrated():
    probability, profile_id, scope, blockers = _resolve_probability(
        contract=_contract(),
        evidence=_evidence(),
        persistence=_StubPersistence(candidate=("candidate-1", _profile(certified=False))),
        station_id=STATION,
        lead_time_bucket="H0",
        decision_time="2026-11-01T19:00:00Z",
    )
    assert profile_id == "candidate-1"
    assert scope == "RESEARCH_UNCERTIFIED"
    assert probability.p_yes is not None
    assert 0.0 < probability.p_yes < 1.0
    assert abs((probability.p_yes + probability.p_no) - 1.0) <= 1e-9
    assert probability.calibrated is False
    assert blockers == ["RESEARCH_CALIBRATION_PROFILE_UNCERTIFIED"]
    assert RESEARCH_PROBABILITY_STATUS == "SHADOW_RESEARCH_UNCERTIFIED"


def test_certified_profile_takes_precedence_over_candidate():
    probability, profile_id, scope, blockers = _resolve_probability(
        contract=_contract(),
        evidence=_evidence(),
        persistence=_StubPersistence(
            certified=("certified-1", _profile(certified=True)),
            candidate=("candidate-1", _profile(certified=False)),
        ),
        station_id=STATION,
        lead_time_bucket="H0",
        decision_time="2026-11-01T19:00:00Z",
    )
    assert profile_id == "certified-1"
    assert scope == "CERTIFIED"
    assert probability.calibrated is True
    assert blockers == []


def test_boundary_probability_is_not_written_to_the_immutable_ledger():
    contract = _contract()
    contract = ContractSnapshot(**{**contract.__dict__, "threshold_lower": 1e6})
    probability, profile_id, scope, blockers = _resolve_probability(
        contract=contract,
        evidence=_evidence(),
        persistence=_StubPersistence(candidate=("candidate-1", _profile(certified=False))),
        station_id=STATION,
        lead_time_bucket="H0",
        decision_time="2026-11-01T19:00:00Z",
    )
    assert probability.p_yes is None
    assert profile_id is None
    assert scope == "UNAVAILABLE"
    assert blockers == ["RESEARCH_PROBABILITY_DOMAIN_BOUNDARY"]


def test_missing_calibration_still_fails_closed():
    probability, profile_id, scope, blockers = _resolve_probability(
        contract=_contract(),
        evidence=_evidence(),
        persistence=_StubPersistence(),
        station_id=STATION,
        lead_time_bucket="H0",
        decision_time="2026-11-01T19:00:00Z",
    )
    assert probability.p_yes is None
    assert probability.p_no is None
    assert profile_id is None
    assert scope == "UNAVAILABLE"
    assert blockers == ["CALIBRATION_PROFILE_UNAVAILABLE"]


def test_probability_scoring_reports_brier_and_no_skill_baseline():
    predictions = [
        _prediction(0, p_yes=0.9),
        _prediction(1, p_yes=0.1),
        _prediction(2, p_yes=0.8),
        _prediction(3, p_yes=0.2),
    ]
    outcomes = [
        _outcome(0, yes_outcome=True),
        _outcome(1, yes_outcome=False),
        _outcome(2, yes_outcome=True),
        _outcome(3, yes_outcome=False),
    ]
    summary = probability_scoring_summary(predictions, outcomes)
    assert summary["sample_n"] == 4
    assert summary["certified"] is False
    assert summary["probability_publishable"] is False
    assert abs(summary["pooled"]["brier_score"] - 0.025) < 1e-9
    assert abs(summary["pooled"]["no_skill_brier_score"] - 0.25) < 1e-9
    assert abs(summary["pooled"]["brier_skill_score"] - 0.9) < 1e-9
    assert summary["by_lead_bucket"]["H0"]["sample_n"] == 4


def test_probability_scoring_ignores_rows_without_a_numeric_probability():
    predictions = [_prediction(0, p_yes=None), _prediction(1, p_yes=1.0)]
    outcomes = [_outcome(0, yes_outcome=True), _outcome(1, yes_outcome=True)]
    summary = probability_scoring_summary(predictions, outcomes)
    assert summary["sample_n"] == 0
    assert summary["pooled"]["brier_score"] is None
