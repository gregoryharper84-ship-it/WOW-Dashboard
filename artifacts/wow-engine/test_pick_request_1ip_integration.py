"""Integration tests for the MLB 1IP dedicated path in pick_request_runtime.py
(WOW-PATCH-2026-09-01-MLB-1IP-FULL-MODEL-GOVERNED).

These mock a hypothetical future certified model artifact
(market_api._prop_route_artifact returning PROP_CERTIFIED_MODEL_ARTIFACT_READY
for the 1IP stat) purely to prove the orchestration wiring end-to-end. The
real wow_prop_certified_model_artifact RPC has no promoted, active row for
(MLB, 1ST_INNING_PITCHES_THROWN) today, so in production this path stays
gated exactly like every other uncertified prop route -- these tests do not
claim otherwise.
"""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import api_prod_market as market_api
from mlb_1ip_specialist import CANONICAL_STAT_TYPE as MLB_1IP_STAT_TYPE
from pick_request_runtime import install_pick_request_routes


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

    def specialist(sport, stat):
        return {"sport": sport, "canonical_prop_type": stat, "controlling_specialist": "wow.mlb-first-inning-pitch-count-expert"}

    def route(sport, stat):
        return {"ok": True, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY", "can_execute": False}

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


def test_lineup_tbd_with_projected_top_four_reaches_the_specialist(monkeypatch):
    client = _build(monkeypatch)
    response = _post(client, [_row("r1")])
    assert response.status_code == 200
    body = response.json()
    row = body["rows"][0]
    assert row["model_evaluated"] is True
    assert row["code"] == "MODEL_QUALIFIED_HOLD"
    assert row["lineup_evidence_state"] == "PROJECTED_OR_RECONSTRUCTED"
    assert row["final_refresh_required"] is True
    assert row["result"]["scout_research_barrier"]["stages"]
    assert body["rows_completed"] == 1
    assert body["reconciliation_pass"] is True
    assert body["can_execute"] is False


def test_lineup_tbd_alone_never_produces_model_unavailable(monkeypatch):
    client = _build(monkeypatch)
    response = _post(client, [_row("r1", lineup_evidence=_lineup_evidence(projected_top_four=None))])
    body = response.json()
    row = body["rows"][0]
    assert row["code"] != "MODEL_UNAVAILABLE"
    assert row["terminal_label"] != "MODEL_UNAVAILABLE"
    assert row["detail"]["terminal_label"] == "REJECT_DATA_QUALITY"
    assert row["model_evaluated"] is False


def test_stale_starter_row_is_purged_without_affecting_other_1ip_rows(monkeypatch):
    client = _build(monkeypatch)
    stale = _lineup_evidence(starter_name_at_capture="Original Starter", starter_name="Replacement Starter")
    response = _post(client, [_row("stale", lineup_evidence=stale), _row("valid")])
    body = response.json()
    assert body["rows_in"] == 2
    assert body["reconciliation_pass"] is True
    by_key = {row["row_key"]: row for row in body["rows"]}
    assert by_key["stale"]["terminal_label"] == "SLATE_PURGE"
    assert by_key["stale"]["detail"]["reason"] == "STARTER_CHANGED"
    assert by_key["valid"]["model_evaluated"] is True
    assert by_key["valid"]["code"] == "MODEL_QUALIFIED_HOLD"


def test_missing_market_evidence_does_not_erase_a_completed_1ip_row(monkeypatch):
    client = _build(monkeypatch)
    response = _post(client, [_row("r1", money_lane_status="PAYOUT_UNRESOLVED")])
    row = response.json()["rows"][0]
    assert row["model_evaluated"] is True
    assert row["code"] == "MODEL_QUALIFIED_HOLD"
    assert "MARKET_DATA_UNAVAILABLE" in row["result"]["blockers"]
    assert row["result"]["P_MORE"] is not None


def test_truly_unreconstructable_inputs_return_data_quality_blocker(monkeypatch):
    client = _build(monkeypatch)
    unreconstructable = _lineup_evidence(starter_status="PROBABLE", projected_top_four=[])
    response = _post(client, [_row("r1", lineup_evidence=unreconstructable)])
    row = response.json()["rows"][0]
    assert row["code"] == "MANDATORY_EVENT_TREE_INPUTS_UNOBTAINABLE_AFTER_APPROVED_ATTEMPTS"
    assert row["terminal_label"] != "MODEL_UNAVAILABLE"
    assert row["model_evaluated"] is False


def test_official_lineup_confirmation_after_provisional_scoring_clears_final_refresh_flag(monkeypatch):
    """Re-scoring with the confirmed lineup (the primitive a future final-
    refresh runner would call once official lineup posts) drops
    final_refresh_required and moves lineup_evidence_state to
    OFFICIAL_CONFIRMED -- proving invalidation/rerun of provisional scoring
    is possible with the same evidence contract."""
    client = _build(monkeypatch)
    provisional = _post(client, [_row("r1")]).json()["rows"][0]
    assert provisional["final_refresh_required"] is True

    confirmed_evidence = _lineup_evidence(official_lineup_status="CONFIRMED", projected_top_four=None)
    refreshed = _post(client, [_row("r1", lineup_evidence=confirmed_evidence)]).json()["rows"][0]
    assert refreshed["lineup_evidence_state"] == "OFFICIAL_CONFIRMED"
    assert refreshed["final_refresh_required"] is False
    assert refreshed["model_evaluated"] is True


def test_row_reconciliation_is_exact_once_across_mixed_1ip_outcomes(monkeypatch):
    client = _build(monkeypatch)
    stale = _lineup_evidence(starter_name_at_capture="A", starter_name="B")
    unreconstructable = _lineup_evidence(starter_status="PROBABLE", projected_top_four=[])
    rows = [
        _row("completed"),
        _row("purged", lineup_evidence=stale),
        _row("rejected_data_quality", lineup_evidence=unreconstructable),
    ]
    body = _post(client, rows).json()
    assert body["rows_in"] == 3
    assert body["rows_completed"] + body["rows_held"] + body["rows_rejected"] == 3
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
