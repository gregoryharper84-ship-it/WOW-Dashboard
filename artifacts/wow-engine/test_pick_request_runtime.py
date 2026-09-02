from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import api_prod_market as market_api
import pick_request_runtime as runtime
from pick_request_runtime import install_pick_request_routes


class _Mutation:
    def __init__(self, sink, payload):
        self.sink = sink
        self.payload = payload

    def execute(self):
        self.sink.append(self.payload)
        return SimpleNamespace(data=[self.payload])


class _Table:
    def __init__(self, sink):
        self.sink = sink

    def upsert(self, payload, on_conflict=None):
        assert on_conflict == "source_snapshot_id"
        return _Mutation(self.sink, payload)


class _Client:
    def __init__(self, sink):
        self.sink = sink

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _Table(self.sink)


def _evidence(*, l10=True, opponent_context=None):
    now = datetime.now(timezone.utc)
    n = 10 if l10 else 5
    evidence = {
        "captured_at": now.isoformat(),
        "game_log": list(range(1, n + 1)),
        "box_score_log": [{"minutes": 30 + i, "stat": i, "outs": 15 + i} for i in range(n)],
        "role_status": {"status": "ACTIVE", "role": "STARTER"},
        "role_timestamp": now.isoformat(),
        "opportunity_ledger": {"status": "PASS", "minutes_projection": 34},
        "source_timestamps": {
            "official_box_scores": now.isoformat(),
            "official_role_status": now.isoformat(),
        },
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "OFFICIAL_BOX_SCORE_L10_V1",
    }
    if opponent_context is not None:
        evidence["opponent_context"] = opponent_context
    return evidence


def _row(row_key, *, sport="MLB", stat_type="Ks", l10=True, include_evidence=True, opponent_context=None):
    row = {
        "row_key": row_key,
        "event_id": f"{sport}:TEST:{row_key}",
        "event_start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sport": sport,
        "player": "Test Player",
        "stat_type": stat_type,
        "line": 5.5,
        "direction": "MORE",
        "source_type": "NORMALIZED",
    }
    if include_evidence:
        row["evidence"] = _evidence(l10=l10, opponent_context=opponent_context)
    return row


def _build(monkeypatch, *, unsupported_sports=(), capability="AVAILABLE"):
    app = FastAPI()
    persisted = []
    routed = []
    scored = []

    def specialist(sport, stat):
        if sport in unsupported_sports:
            return {"sport": sport, "canonical_prop_type": stat, "controlling_specialist": "MODEL_UNAVAILABLE"}
        return {"sport": sport, "canonical_prop_type": stat, "controlling_specialist": "wow.test-specialist"}

    def route(sport, stat):
        routed.append((sport, stat))
        return {"ok": True, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY", "can_execute": False}

    def score(req, x_wow_model_identity=None):
        scored.append((req, x_wow_model_identity))
        return {
            "ok": True,
            "prediction": {
                "prediction_id": "00000000-0000-0000-0000-000000000001",
                "calibrated_probability": 0.63,
                "calibrated_probability_lower_bound": 0.56,
                "calibration_status": "PRECALIBRATION_SHRINKAGE",
            },
            "model_evidence": {
                "calibrated_probability": 0.63,
                "calibrated_probability_lower_bound": 0.56,
            },
            "probability_qualification": {
                "terminal_label": "MODEL_QUALIFIED_HOLD",
                "confidence_tier": "STANDARD",
                "rank_eligible": True,
                "model_supported": True,
                "downstream_money_evaluation_allowed": False,
                "blockers": ["MARKET_DATA_UNAVAILABLE", "PAYOUT_UNRESOLVED"],
            },
            "probability_publishable": True,
            "can_execute": False,
        }


    monkeypatch.setattr(market_api.prod.base_api, "_controlling_specialist_provider", specialist)
    monkeypatch.setattr(
        market_api.prod,
        "_runtime_capability",
        lambda _key: {"capability_status": capability, "evidence": {}, "can_execute": False},
    )
    monkeypatch.setattr(market_api, "_prop_route_artifact", route)
    monkeypatch.setattr(market_api.prod, "get_client", lambda: _Client(persisted))
    monkeypatch.setattr(market_api, "score_prop", score)

    install_pick_request_routes(
        app,
        market_api=market_api,
        auth_dependency=Depends(lambda: None),
    )
    return TestClient(app), persisted, routed, scored


def test_k_alias_freezes_snapshot_and_reaches_certified_pitcher_route(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    response = client.post(
        "/score-pick-request",
        headers={"X-WOW-Model-Identity": "WOW_BETTING_ENGINE"},
        json={"rows": [_row("alias")]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_controller_status"] == "COMPLETE"
    assert body["rows_in"] == 1
    assert body["rows_completed"] == 1
    assert body["rows_held"] == 0
    assert body["rows_rejected"] == 0
    assert body["reconciliation_pass"] is True
    assert body["rows"][0]["code"] == "MODEL_QUALIFIED_HOLD"
    assert body["rows"][0]["acquisition"]["mode"] == "CALLER_SUPPLIED_RAW_EVIDENCE"
    assert len(body["rows"][0]["evidence_fingerprint"]) == 64
    assert body["can_execute"] is False
    assert routed == [("MLB", "PITCHER_STRIKEOUTS")]
    assert len(persisted) == 1
    assert persisted[0]["stat_type"] == "PITCHER_STRIKEOUTS"
    assert persisted[0]["line"] == 5.5
    assert persisted[0]["hydration_status"] == "PASS"
    assert persisted[0]["blockers"] == []
    assert persisted[0]["source_snapshot_id"] == body["rows"][0]["source_snapshot_id"]
    assert persisted[0]["can_execute"] is False
    assert scored[0][0].stat_type == "PITCHER_STRIKEOUTS"


def test_opponent_context_absent_leaves_persisted_snapshot_payload_unchanged(monkeypatch):
    """Postmortem patch WOW-PATCH-2026-09-02: opponent_context is an opt-in
    field. Until migrations/20260902_prop_evidence_opponent_context.sql is
    applied live, an upsert payload carrying an unknown column would fail
    closed for every caller -- so a caller that never supplies it must get a
    byte-identical persisted payload to before this field existed."""
    client, persisted, _routed, _scored = _build(monkeypatch)
    response = client.post(
        "/score-pick-request",
        headers={"X-WOW-Model-Identity": "WOW_BETTING_ENGINE"},
        json={"rows": [_row("no-opponent-context")]},
    )
    assert response.status_code == 200
    assert "opponent_context" not in persisted[0]


def test_opponent_context_supplied_is_persisted_when_caller_opts_in(monkeypatch):
    client, persisted, _routed, _scored = _build(monkeypatch)
    response = client.post(
        "/score-pick-request",
        headers={"X-WOW-Model-Identity": "WOW_BETTING_ENGINE"},
        json={"rows": [_row("with-opponent-context", opponent_context={"k_rate_per_pa": 0.15})]},
    )
    assert response.status_code == 200
    assert persisted[0]["opponent_context"] == {"k_rate_per_pa": 0.15}


def test_unsupported_row_is_held_without_route_hydration_model_or_snapshot(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch, unsupported_sports={"WNBA"})
    hydration_called = {"value": False}

    def should_not_hydrate(**_kwargs):
        hydration_called["value"] = True
        raise AssertionError("unsupported route must terminate before acquisition")

    monkeypatch.setattr(runtime, "auto_hydrate_prop_evidence", should_not_hydrate)
    row = _row("wnba", sport="WNBA", stat_type="REB", include_evidence=False)
    response = client.post("/score-pick-request", json={"rows": [row]})
    assert response.status_code == 200
    body = response.json()
    terminal = body["rows"][0]
    assert body["run_controller_status"] == "BLOCKED"
    assert terminal["terminal_status"] == "HELD"
    assert terminal["code"] == "MODEL_UNAVAILABLE"
    assert terminal["acquisition"]["mode"] == "NOT_ATTEMPTED_ROUTE_BLOCKED"
    assert terminal["probability_publishable"] is False
    assert terminal["can_execute"] is False
    assert hydration_called["value"] is False
    assert persisted == []
    assert routed == []
    assert scored == []


def test_unavailable_aggregate_capability_blocks_before_route_and_auto_hydration(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch, capability="UNAVAILABLE")
    hydration_called = {"value": False}

    def should_not_hydrate(**_kwargs):
        hydration_called["value"] = True
        raise AssertionError("unavailable capability must terminate before acquisition")

    monkeypatch.setattr(runtime, "auto_hydrate_prop_evidence", should_not_hydrate)
    response = client.post(
        "/score-pick-request",
        json={"rows": [_row("capability", include_evidence=False)]},
    )
    assert response.status_code == 200
    body = response.json()
    terminal = body["rows"][0]
    assert body["run_controller_status"] == "BLOCKED"
    assert terminal["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert terminal["acquisition"]["mode"] == "NOT_ATTEMPTED_ROUTE_BLOCKED"
    assert hydration_called["value"] is False
    assert routed == []
    assert persisted == []
    assert scored == []


def test_bad_row_cannot_erase_good_sibling_and_reconciliation_is_exact(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    response = client.post(
        "/score-pick-request",
        json={"rows": [_row("bad", l10=False), _row("good")]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_controller_status"] == "DEGRADED"
    assert body["rows_in"] == 2
    assert body["rows_completed"] == 1
    assert body["rows_held"] == 1
    assert body["rows_rejected"] == 0
    assert body["reconciliation_pass"] is True
    by_key = {row["row_key"]: row for row in body["rows"]}
    assert by_key["bad"]["terminal_status"] == "HELD"
    assert by_key["bad"]["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert by_key["bad"]["detail"]["blocker"] == "L10_GAME_LOG_INCOMPLETE"
    assert by_key["good"]["terminal_status"] == "COMPLETED"
    assert by_key["good"]["code"] == "MODEL_QUALIFIED_HOLD"
    assert len(persisted) == 1
    assert len(scored) == 1

    assert by_key["bad"]["pick_rejected"] is False
    assert by_key["bad"]["verdict_class"] == "ACQUISITION_BLOCKED"
    assert body["pick_rejected_count"] == 0
    assert body["infrastructure_blocked_count"] >= 1
    # Distinct event_ids (the default) are not a common hinge.
    assert by_key["good"]["portfolio_governance"]["duplicate_thesis_count"] == 1


def test_duplicate_thesis_is_detected_and_blocks_portfolio_qualification_without_altering_probability(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    row_a = _row("a")
    row_b = _row("b")
    row_b["event_id"] = row_a["event_id"]  # same event/player/stat/direction -> same thesis
    response = client.post(
        "/score-pick-request",
        json={"rows": [row_a, row_b]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rows_completed"] == 2
    by_key = {row["row_key"]: row for row in body["rows"]}

    gov_a = by_key["a"]["portfolio_governance"]
    gov_b = by_key["b"]["portfolio_governance"]
    assert gov_a["thesis_identity"] == gov_b["thesis_identity"]
    assert gov_a["duplicate_thesis_count"] == 2
    assert gov_b["duplicate_thesis_count"] == 2
    assert gov_a["can_execute"] is False
    # The second occurrence of an identical thesis is machine-flagged and
    # blocks downstream portfolio/slip qualification for that row.
    assert gov_b["duplicate_thesis_flagged"] is True
    assert "DUPLICATE_THESIS_COMMON_HINGE" in gov_b["blockers"]
    assert by_key["b"]["downstream_portfolio_evaluation_allowed"] is False

    # Portfolio/slip qualification is a separate objective lane: it must
    # never change a row's own terminal outcome or sporting probability.
    for key in ("a", "b"):
        assert by_key[key]["terminal_status"] == "COMPLETED"
        assert by_key[key]["probability_publishable"] is True
        assert by_key[key]["result"]["prediction"]["calibrated_probability"] == 0.63


def test_same_event_correlated_legs_are_not_treated_as_independent(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    row_a = _row("a")
    row_b = _row("b", stat_type="BBs")  # different prop on the same event -> not duplicate thesis
    row_b["event_id"] = row_a["event_id"]
    row_b["direction"] = "LESS"  # also not a directional-exposure case

    response = client.post("/score-pick-request", json={"rows": [row_a, row_b]})
    assert response.status_code == 200
    body = response.json()
    by_key = {row["row_key"]: row for row in body["rows"]}

    gov_a = by_key["a"]["portfolio_governance"]
    gov_b = by_key["b"]["portfolio_governance"]
    assert gov_a["same_event_dependent"] is True
    assert gov_b["same_event_dependent"] is True
    assert gov_a["duplicate_thesis_flagged"] is False
    assert gov_a["directional_exposure"] is False
    assert "DEPENDENCE_UNQUANTIFIED_SAME_EVENT" in gov_a["blockers"]
    assert by_key["a"]["downstream_portfolio_evaluation_allowed"] is False
    assert by_key["b"]["downstream_portfolio_evaluation_allowed"] is False
    # No joint probability was fabricated -- each row's own model output is untouched.
    assert by_key["a"]["result"]["prediction"]["calibrated_probability"] == 0.63
    assert by_key["b"]["result"]["prediction"]["calibrated_probability"] == 0.63


def test_directional_and_session_exposure_are_enforced_separately_from_dependency(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    row_a = _row("a")
    row_b = _row("b", stat_type="BBs")  # different prop, same event, same direction
    row_b["event_id"] = row_a["event_id"]

    response = client.post("/score-pick-request", json={"rows": [row_a, row_b]})
    body = response.json()
    by_key = {row["row_key"]: row for row in body["rows"]}
    gov_a = by_key["a"]["portfolio_governance"]

    assert gov_a["directional_exposure"] is True
    assert gov_a["session_event_leg_count"] == 2
    assert "SESSION_DIRECTIONAL_EXPOSURE" in gov_a["blockers"]
    # Both stages fire independently for the same pair, but as distinct blockers.
    assert "DEPENDENCE_UNQUANTIFIED_SAME_EVENT" in gov_a["blockers"]


def test_unrelated_single_row_batch_is_unaffected_by_portfolio_governance(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    response = client.post("/score-pick-request", json={"rows": [_row("solo")]})
    body = response.json()
    row = body["rows"][0]
    assert row["downstream_portfolio_evaluation_allowed"] is True
    assert row["portfolio_governance"]["blockers"] == []
    assert row["portfolio_governance"]["same_event_dependent"] is False
    assert row["portfolio_governance"]["directional_exposure"] is False


def test_exposure_blocker_survives_to_the_final_response_and_can_execute_stays_false(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    row_a = _row("a")
    row_b = _row("b")
    row_b["event_id"] = row_a["event_id"]
    response = client.post("/score-pick-request", json={"rows": [row_a, row_b]})
    body = response.json()
    assert body["can_execute"] is False
    by_key = {row["row_key"]: row for row in body["rows"]}
    for key in ("a", "b"):
        # Nothing downstream of portfolio governance re-runs or overwrites it
        # in this endpoint -- the blocker present here is what a caller sees.
        assert "portfolio_governance" in by_key[key]
        assert by_key[key]["can_execute"] is False
        assert by_key[key]["portfolio_governance"]["can_execute"] is False


def test_missing_evidence_auto_hydrates_freezes_and_scores(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    calls = []

    def hydrate(**kwargs):
        calls.append(kwargs)
        return _evidence()

    monkeypatch.setattr(runtime, "auto_hydrate_prop_evidence", hydrate)
    row = _row("auto", include_evidence=False)
    row["source_type"] = "SCREENSHOT"
    row["platform"] = "PrizePicks"
    response = client.post("/score-pick-request", json={"rows": [row]})

    assert response.status_code == 200
    body = response.json()
    terminal = body["rows"][0]
    assert body["run_controller_status"] == "COMPLETE"
    assert body["rows_completed"] == 1
    assert terminal["terminal_status"] == "COMPLETED"
    assert terminal["code"] == "MODEL_QUALIFIED_HOLD"
    assert terminal["acquisition"]["mode"] == "AUTO_HYDRATION"
    assert terminal["acquisition"]["status"] == "PASS"
    assert terminal["acquisition"]["snapshot_status"] == "FROZEN"
    assert body["telemetry"]["auto_hydration_attempted"] == 1
    assert body["telemetry"]["auto_hydration_succeeded"] == 1
    assert body["telemetry"]["false_global_failure_count"] == 0
    assert len(calls) == 1
    assert calls[0]["sport"] == "MLB"
    assert calls[0]["stat_type"] == "PITCHER_STRIKEOUTS"
    assert calls[0]["source_label"] == "SCREENSHOT:PrizePicks"
    assert len(persisted) == 1
    assert len(scored) == 1


def test_auto_hydration_failure_is_row_local_and_valid_sibling_completes(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)

    def hydrate(**kwargs):
        if kwargs["player"] == "Broken Pitcher":
            raise runtime.PropAutoHydrationError(
                "MLB_STARTER_STATUS_UNRESOLVED",
                "starter not confirmed",
                detail={"player": kwargs["player"]},
            )
        return _evidence()

    monkeypatch.setattr(runtime, "auto_hydrate_prop_evidence", hydrate)
    broken = _row("broken", include_evidence=False)
    broken["player"] = "Broken Pitcher"
    good = _row("good-auto", include_evidence=False)
    good["player"] = "Good Pitcher"

    response = client.post("/score-pick-request", json={"rows": [broken, good]})
    assert response.status_code == 200
    body = response.json()
    assert body["run_controller_status"] == "DEGRADED"
    assert body["rows_in"] == 2
    assert body["rows_completed"] == 1
    assert body["rows_held"] == 1
    assert body["rows_rejected"] == 0
    assert body["reconciliation_pass"] is True
    by_key = {row["row_key"]: row for row in body["rows"]}
    assert by_key["broken"]["code"] == "MLB_STARTER_STATUS_UNRESOLVED"
    assert by_key["broken"]["terminal_status"] == "HELD"
    assert by_key["good-auto"]["terminal_status"] == "COMPLETED"
    assert by_key["good-auto"]["code"] == "MODEL_QUALIFIED_HOLD"
    assert body["telemetry"]["auto_hydration_attempted"] == 2
    assert body["telemetry"]["auto_hydration_succeeded"] == 1
    assert body["telemetry"]["acquisition_failures"] == 1
    assert body["telemetry"]["false_global_failure_count"] == 0
    assert len(persisted) == 1
    assert len(scored) == 1


def test_postgame_evidence_rejects_before_snapshot_write(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    row = _row("postgame")
    row["event_start_time"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    response = client.post("/score-pick-request", json={"rows": [row]})
    assert response.status_code == 200
    terminal = response.json()["rows"][0]
    assert terminal["terminal_status"] == "REJECTED"
    assert terminal["detail"]["blocker"] == "EVENT_NOT_PREGAME"
    assert persisted == []
    assert scored == []
