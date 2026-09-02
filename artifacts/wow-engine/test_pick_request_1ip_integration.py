"""Integration tests for the governed MLB 1IP pick-request path.

The tests provide a hypothetical certified empirical artifact only to prove
orchestration. Production remains gated until a real independently reviewed
artifact is promoted in the governed registry.
"""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import api_prod_market as market_api
from mlb_1ip_artifact_pipeline import TrainingRow
from mlb_1ip_empirical_pmf import fit_empirical_pmf
from pick_request_runtime import install_pick_request_routes


VALIDATED_LINES = [11.5, 13.5, 15.5, 17.5, 19.5, 21.5]


def _lineup_evidence(**overrides):
    base = {
        "starter_name": "Test Pitcher",
        "starter_name_at_capture": "Test Pitcher",
        "starter_status": "CONFIRMED",
        "official_lineup_status": "TBD",
        "projected_top_four": [
            {"player": f"Batter {i}", "handedness": "R", "p_pa_vs_pitcher_profile": 4.0 + i * 0.1}
            for i in range(4)
        ],
        "pitcher_bf_distribution": {"p_bf_3": 0.35, "p_bf_4": 0.35, "p_bf_gte5": 0.30},
        "baseline_pitches_per_batter": {"mean": 4.2, "std": 1.1},
    }
    base.update(overrides)
    return base


def _certified_artifact():
    rows = []
    rows.extend(TrainingRow(bf=3, pitches=12 + i % 4) for i in range(450))
    rows.extend(TrainingRow(bf=4, pitches=16 + i % 5) for i in range(400))
    rows.extend(TrainingRow(bf=5, pitches=21 + i % 6) for i in range(300))
    payload = fit_empirical_pmf(rows)
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "model_family": payload["model_family"],
        "model_artifact_version": "MLB_1IP_TEST_ARTIFACT_V1",
        "artifact_checksum": payload["artifact_checksum"],
        "certification_id": "PROP-CERT-TEST-MLB-1IP",
        "artifact_payload": payload,
        "supported_line_min": 11.5,
        "supported_line_max": 21.5,
        "feature_schema_version": "PROP_FEATURES_V1",
        "validation_metrics": {"validated_lines": VALIDATED_LINES},
        "probability_publishable": False,
        "can_execute": False,
    }


def _row(row_key, *, lineup_evidence=None, money_lane_status="PAYOUT_UNRESOLVED", no_evidence=False):
    now = datetime.now(timezone.utc)
    row = {
        "row_key": row_key,
        "event_id": f"MLB:TEST:{row_key}",
        "event_start_time": (now + timedelta(days=1)).isoformat(),
        "sport": "MLB",
        "player": "Test Pitcher",
        "stat_type": "1IP",
        "line": 13.5,
        "direction": "MORE",
        "source_type": "NORMALIZED",
        "money_lane_status": money_lane_status,
    }
    if not no_evidence:
        row["evidence"] = {
            "captured_at": now.isoformat(),
            "game_log": [],
            "box_score_log": [],
            "role_status": {"status": "ACTIVE", "role": "STARTING_PITCHER"},
            "role_timestamp": now.isoformat(),
            "opportunity_ledger": {"status": "PASS"},
            "source_timestamps": {"caller": now.isoformat()},
            "evidence_version": "PROP_EVIDENCE_V1",
            "rate_provenance": "CALLER_SUPPLIED_1IP_V1",
            "lineup_evidence": lineup_evidence if lineup_evidence is not None else _lineup_evidence(),
        }
    return row


def _build(monkeypatch):
    app = FastAPI()
    artifact = _certified_artifact()

    def specialist(sport, stat):
        return {"sport": sport, "canonical_prop_type": stat, "controlling_specialist": "wow.mlb-first-inning-pitch-count-expert"}

    def route(sport, stat):
        return dict(artifact)

    monkeypatch.setattr(market_api.prod.base_api, "_controlling_specialist_provider", specialist)
    monkeypatch.setattr(
        market_api.prod, "_runtime_capability",
        lambda _key: {"capability_status": "AVAILABLE", "evidence": {}, "can_execute": False},
    )
    monkeypatch.setattr(market_api, "_prop_route_artifact", route)

    install_pick_request_routes(app, market_api=market_api, auth_dependency=Depends(lambda: None))
    return TestClient(app)


def _post(client, rows):
    return client.post(
        "/score-pick-request",
        headers={"X-WOW-Model-Identity": "WOW_BETTING_ENGINE"},
        json={"rows": rows},
    )


def test_lineup_tbd_with_projected_top_four_reaches_empirical_specialist(monkeypatch):
    row = _post(_build(monkeypatch), [_row("r1")]).json()["rows"][0]
    assert row["model_evaluated"] is True
    assert row["code"] == "MODEL_QUALIFIED_HOLD"
    assert row["lineup_evidence_state"] == "PROJECTED_OR_RECONSTRUCTED"
    assert row["final_refresh_required"] is True
    assert row["result"]["model_family"] == "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1"
    assert row["result"]["calibration_method"] == "MLB_1IP_EMPIRICAL_TEMPORAL_CAL_V1"
    assert row["result"]["calibrated_probability_lower_bound"] <= row["result"]["calibrated_probability"]
    assert row["result"]["probability_publishable"] is False
    assert row["result"]["scout_research_barrier"]["stages"]


def test_lineup_tbd_alone_never_produces_model_unavailable(monkeypatch):
    row = _post(_build(monkeypatch), [_row("r1", lineup_evidence=_lineup_evidence(projected_top_four=None))]).json()["rows"][0]
    assert row["code"] != "MODEL_UNAVAILABLE"
    assert row["terminal_label"] != "MODEL_UNAVAILABLE"
    assert row["detail"]["terminal_label"] == "REJECT_DATA_QUALITY"
    assert row["terminal_status"] == "REJECTED"
    assert row["model_evaluated"] is False


def test_stale_starter_row_is_purged_without_affecting_other_1ip_rows(monkeypatch):
    client = _build(monkeypatch)
    stale = _lineup_evidence(starter_name_at_capture="Original Starter", starter_name="Replacement Starter")
    body = _post(client, [_row("stale", lineup_evidence=stale), _row("valid")]).json()
    assert body["reconciliation_pass"] is True
    by_key = {row["row_key"]: row for row in body["rows"]}
    assert by_key["stale"]["terminal_label"] == "SLATE_PURGE"
    assert by_key["stale"]["terminal_status"] == "REJECTED"
    assert by_key["valid"]["model_evaluated"] is True
    assert by_key["valid"]["result"]["model_family"] == "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1"


def test_missing_market_evidence_does_not_erase_a_completed_1ip_row(monkeypatch):
    row = _post(_build(monkeypatch), [_row("r1", money_lane_status="PAYOUT_UNRESOLVED")]).json()["rows"][0]
    assert row["model_evaluated"] is True
    assert row["code"] == "MODEL_QUALIFIED_HOLD"
    assert "MARKET_DATA_UNAVAILABLE" in row["result"]["blockers"]
    assert row["result"]["P_MORE"] is not None


def test_truly_unreconstructable_inputs_return_data_quality_blocker(monkeypatch):
    unreconstructable = _lineup_evidence(starter_status="PROBABLE", projected_top_four=[])
    row = _post(_build(monkeypatch), [_row("r1", lineup_evidence=unreconstructable)]).json()["rows"][0]
    assert row["code"] == "MANDATORY_EVENT_TREE_INPUTS_UNOBTAINABLE_AFTER_APPROVED_ATTEMPTS"
    assert row["terminal_label"] != "MODEL_UNAVAILABLE"
    assert row["terminal_status"] == "REJECTED"
    assert row["model_evaluated"] is False


def test_official_lineup_confirmation_clears_final_refresh_flag(monkeypatch):
    client = _build(monkeypatch)
    provisional = _post(client, [_row("r1")]).json()["rows"][0]
    assert provisional["final_refresh_required"] is True
    confirmed = _lineup_evidence(official_lineup_status="CONFIRMED", projected_top_four=None)
    refreshed = _post(client, [_row("r1", lineup_evidence=confirmed)]).json()["rows"][0]
    assert refreshed["lineup_evidence_state"] == "OFFICIAL_CONFIRMED"
    assert refreshed["final_refresh_required"] is False
    assert refreshed["model_evaluated"] is True
    assert refreshed["result"]["model_artifact_version"] == "MLB_1IP_TEST_ARTIFACT_V1"


def test_three_of_four_projection_is_hold_only_and_never_publishable(monkeypatch):
    three = _lineup_evidence(projected_top_four=_lineup_evidence()["projected_top_four"][:3])
    row = _post(_build(monkeypatch), [_row("r1", lineup_evidence=three)]).json()["rows"][0]
    assert row["terminal_status"] == "COMPLETED"
    assert row["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert row["final_refresh_required"] is True
    assert row["probability_publishable"] is False
    assert row["result"]["lineup_evidence_completeness"] == "PARTIAL_SUFFICIENT"
    assert row["result"]["calibration_method"] == "MLB_1IP_EMPIRICAL_TEMPORAL_CAL_V1"


def test_row_reconciliation_is_exact_once_across_mixed_1ip_outcomes(monkeypatch):
    client = _build(monkeypatch)
    stale = _lineup_evidence(starter_name_at_capture="A", starter_name="B")
    unreconstructable = _lineup_evidence(starter_status="PROBABLE", projected_top_four=[])
    body = _post(client, [
        _row("completed"),
        _row("purged", lineup_evidence=stale),
        _row("rejected_data_quality", lineup_evidence=unreconstructable),
    ]).json()
    assert body["rows_in"] == 3
    assert body["rows_completed"] == 1
    assert body["rows_held"] == 0
    assert body["rows_rejected"] == 2
    assert body["reconciliation_pass"] is True


def test_can_execute_false_is_invariant_across_1ip_http_responses(monkeypatch):
    client = _build(monkeypatch)
    for rows in (
        [_row("r1")],
        [_row("r1", lineup_evidence=_lineup_evidence(projected_top_four=None))],
        [_row("r1", lineup_evidence=_lineup_evidence(starter_name_at_capture="A", starter_name="B"))],
    ):
        body = _post(client, rows).json()
        assert body["can_execute"] is False
        for row in body["rows"]:
            assert row["can_execute"] is False
            assert row["probability_publishable"] is False
