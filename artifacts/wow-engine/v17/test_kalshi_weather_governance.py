from __future__ import annotations

from v17.host_routing import (
    KALSHI_WEATHER_MARKET_EXPERT,
    PROJECT_CHAT,
    controlling_engine_for,
    expected_full_model_operation_id,
    resolve_host_route,
)
from v17.kalshi_weather_governance import (
    GLOBAL_TERMINAL_AUTHORITY,
    governance_snapshot,
    reduce_kalshi_weather_prediction_v17,
)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.rows = [row for row in self.rows if row.get(key) == value]
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def execute(self):
        return _Result(self.rows)


class _Client:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Query(self.tables.get(name, []))


def _tables():
    decision_time = "2026-09-11T12:00:00+00:00"
    contract = {
        "ticker": "KXTEMPNYCHS-TEST",
        "lane": "HOURLY_TEMPERATURE",
        "rule_snapshot_id": "rule-1",
        "settlement_source": "Synoptic Data",
        "settlement_location_type": "SOURCE_LOCATION_CODE",
        "settlement_location_code": "KALSHI_WEATHER_INDEX:NYC",
        "settlement_station_id": None,
        "timezone": "America/New_York",
        "observation_window": "POINT_IN_TIME:2026-09-11T14:00:00+00:00",
    }
    prediction = {
        "prediction_id": "pred-1",
        "rule_snapshot_id": "rule-1",
        "ticker": contract["ticker"],
        "decision_time": decision_time,
        "model_version": "KALSHI_WEATHER_V2_HOURLY_SHADOW_1",
        "calibration_profile_id": "cal-1",
        "p_yes": 0.64,
        "p_no": 0.36,
        "lower_bound_yes": 0.58,
        "upper_bound_yes": 0.70,
        "central_estimate_f": 77.2,
        "threshold_distance": "NEAR_THRESHOLD",
        "probability_status": "SHADOW_BLOCKED",
        "probability_publishable": False,
        "blockers": [
            "FEE_AND_FRICTION_NOT_EVALUATED_IN_SHADOW_CAPTURE",
            "SHADOW_ONLY_NO_PUBLICATION",
        ],
        "warnings": [],
        "evidence_snapshot_ids": ["src-1", "src-2"],
        "model_payload": {
            "contract": contract,
            "lead_time_bucket": "H1_6",
            "market_price_used_as_model_input": False,
            "can_execute": False,
            "terminal": {
                "payload": {
                    "local_terminal_label_audit_only": True,
                    "local_global_terminal_authority": False,
                    "global_terminal_authority_required": "V17_TERMINAL_REDUCER",
                }
            },
        },
        "can_execute": False,
    }
    return {
        "wow_kalshi_weather_predictions": [prediction],
        "wow_kalshi_weather_contract_rules": [{
            "rule_snapshot_id": "rule-1",
            "ticker": contract["ticker"],
            "settlement_source_name": "Synoptic Data",
            "can_execute": False,
        }],
        "wow_runtime_capabilities": [{
            "capability_key": "KALSHI_WEATHER_PROBABILITY",
            "capability_status": "AVAILABLE",
            "evidence": {"probability_publishable": True, "lifecycle": "CERTIFIED"},
            "can_execute": False,
        }],
        "wow_kalshi_weather_calibration_profiles": [{
            "calibration_profile_id": "cal-1",
            "station_id": "KALSHI_WEATHER_INDEX:NYC",
            "lane": "HOURLY_TEMPERATURE",
            "lead_time_bucket": "H1_6",
            "model_version": prediction["model_version"],
            "certified": True,
            "sample_n": 100,
            "certification_evidence": {"acceptance": "CERTIFIED", "source": "immutable_shadow_ledger"},
            "fitted_as_of": "2026-09-10T00:00:00+00:00",
            "can_execute": False,
        }],
        "wow_kalshi_weather_source_snapshots": [
            {
                "source_snapshot_id": "src-1",
                "rule_snapshot_id": "rule-1",
                "issued_at": "2026-09-11T11:00:00+00:00",
                "evidence_time": "2026-09-11T11:00:00+00:00",
            },
            {
                "source_snapshot_id": "src-2",
                "rule_snapshot_id": "rule-1",
                "issued_at": "2026-09-11T11:30:00+00:00",
                "evidence_time": "2026-09-11T11:30:00+00:00",
            },
        ],
    }


def test_weather_lane_routes_to_weather_specialist_but_not_global_authority():
    route = resolve_host_route(PROJECT_CHAT, "HOURLY_TEMPERATURE")
    assert route.controlling_engine_identity == KALSHI_WEATHER_MARKET_EXPERT
    assert route.global_terminal_authority is False
    assert route.can_execute is False
    assert controlling_engine_for("DAILY_HIGH_TEMPERATURE") == KALSHI_WEATHER_MARKET_EXPERT
    assert expected_full_model_operation_id("HOURLY_TEMPERATURE") == "analyzeKalshiWeatherV17Contract"


def test_v17_global_reducer_can_publish_only_immutable_certified_probability():
    result = reduce_kalshi_weather_prediction_v17(client=_Client(_tables()), prediction_id="pred-1")
    assert result["status"] == "WATCH"
    assert result["code"] == "V17_WEATHER_PROBABILITY_PUBLISHED_EDGE_HELD"
    assert result["p_yes"] == 0.64
    assert result["p_no"] == 0.36
    assert result["probability_publishable"] is True
    assert result["edge_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["immutable_pregame_write_verified"] is True
    assert result["row_reconciliation_verified"] is True
    assert result["global_terminal_authority"] == GLOBAL_TERMINAL_AUTHORITY
    assert result["can_execute"] is False


def test_unavailable_capability_masks_probability_fail_closed():
    tables = _tables()
    tables["wow_runtime_capabilities"][0]["capability_status"] = "UNAVAILABLE"
    tables["wow_runtime_capabilities"][0]["evidence"] = {
        "probability_publishable": False,
        "lifecycle": "IMPLEMENTATION_NOT_CERTIFIED",
    }
    result = reduce_kalshi_weather_prediction_v17(client=_Client(tables), prediction_id="pred-1")
    assert result["probability_publishable"] is False
    assert result["p_yes"] is None
    assert "KALSHI_WEATHER_PROBABILITY_CAPABILITY_UNAVAILABLE" in result["blockers"]


def test_market_price_probability_substitution_is_global_blocker():
    tables = _tables()
    tables["wow_kalshi_weather_predictions"][0]["model_payload"]["market_price_used_as_model_input"] = True
    result = reduce_kalshi_weather_prediction_v17(client=_Client(tables), prediction_id="pred-1")
    assert result["probability_publishable"] is False
    assert "MARKET_PRICE_MODEL_INPUT_PROHIBITED" in result["blockers"]


def test_future_calibration_is_rejected_as_hindsight_leakage():
    tables = _tables()
    tables["wow_kalshi_weather_calibration_profiles"][0]["fitted_as_of"] = "2026-09-12T00:00:00+00:00"
    result = reduce_kalshi_weather_prediction_v17(client=_Client(tables), prediction_id="pred-1")
    assert result["probability_publishable"] is False
    assert "CALIBRATION_AS_OF_VIOLATION" in result["blockers"]


def test_future_source_snapshot_is_rejected_as_hindsight_leakage():
    tables = _tables()
    tables["wow_kalshi_weather_source_snapshots"][0]["issued_at"] = "2026-09-11T13:00:00+00:00"
    result = reduce_kalshi_weather_prediction_v17(client=_Client(tables), prediction_id="pred-1")
    assert result["probability_publishable"] is False
    assert any(item.startswith("SOURCE_AS_OF_VIOLATION") for item in result["blockers"])


def test_incoherent_yes_no_probability_is_rejected():
    tables = _tables()
    tables["wow_kalshi_weather_predictions"][0]["p_no"] = 0.40
    result = reduce_kalshi_weather_prediction_v17(client=_Client(tables), prediction_id="pred-1")
    assert result["probability_publishable"] is False
    assert "PROBABILITY_COMPLEMENT_INCOHERENT" in result["blockers"]


def test_point_estimate_must_remain_inside_calibration_bounds():
    tables = _tables()
    tables["wow_kalshi_weather_predictions"][0]["lower_bound_yes"] = 0.70
    result = reduce_kalshi_weather_prediction_v17(client=_Client(tables), prediction_id="pred-1")
    assert result["probability_publishable"] is False
    assert "DYNAMIC_CALIBRATION_BOUNDS_INVALID" in result["blockers"]


def test_station_lane_lead_time_calibration_identity_is_enforced():
    tables = _tables()
    tables["wow_kalshi_weather_calibration_profiles"][0]["lead_time_bucket"] = "H6_24"
    result = reduce_kalshi_weather_prediction_v17(client=_Client(tables), prediction_id="pred-1")
    assert result["probability_publishable"] is False
    assert "CALIBRATION_LEAD_TIME_BUCKET_MISMATCH" in result["blockers"]


def test_certified_boolean_without_certification_evidence_cannot_publish():
    tables = _tables()
    tables["wow_kalshi_weather_calibration_profiles"][0]["certification_evidence"] = {}
    result = reduce_kalshi_weather_prediction_v17(client=_Client(tables), prediction_id="pred-1")
    assert result["probability_publishable"] is False
    assert "CALIBRATION_CERTIFICATION_EVIDENCE_MISSING" in result["blockers"]


def test_governance_snapshot_exposes_single_global_authority_and_no_execution():
    snapshot = governance_snapshot(client=_Client(_tables()))
    assert snapshot["global_terminal_authority"] == GLOBAL_TERMINAL_AUTHORITY
    assert snapshot["local_terminal_label_audit_only"] is True
    assert snapshot["invariants"]["local_specialist_may_publish"] is False
    assert snapshot["can_execute"] is False
