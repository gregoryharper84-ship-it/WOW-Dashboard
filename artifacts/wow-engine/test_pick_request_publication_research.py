"""Canonical acquisition must preserve publication-only research separation."""
from types import SimpleNamespace

import pytest

import api_prod_market as market_api
import calibration_publication_api as publication_api
import prop_evidence_repair as evidence_repair
from prop_fitted_provider import CertifiedInference
from prop_model_adapters import mlb_pitcher_so_failure_path_nb_v1_adapter
from test_mlb_strikeout_min3_candidate_calibration import _artifact
from test_pick_request_runtime import _Client, _build, _row


PUBLICATION_LOCK = {"blockers": ["FORWARD_SHADOW_NOT_COMPLETED"]}
PUBLICATION_PREFLIGHT = {
    "ok": True,
    "specialist_model_capability": "NOT_EVALUATED",
    "governed_probability_capability": "UNAVAILABLE",
    "governed_publication_capability": "UNAVAILABLE",
    "probability_publishable": False,
    "governed_publishable": False,
    "failed_contract_scope": ["CALIBRATION", "PUBLICATION"],
    "blockers": ["FORWARD_SHADOW_NOT_COMPLETED"],
}


class _PreflightClient(_Client):
    def __init__(self, sink, preflight):
        super().__init__(sink)
        self.preflight = preflight

    def rpc(self, name, params):
        assert name == "wow_governed_probability_preflight"
        assert params == {}
        if isinstance(self.preflight, Exception):
            raise self.preflight
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.preflight))


def _publication_lock(monkeypatch, persisted, *, preflight=None, evidence=None):
    if preflight is None:
        preflight = dict(PUBLICATION_PREFLIGHT)
    monkeypatch.setattr(
        market_api.prod,
        "_runtime_capability",
        lambda _key: {
            "capability_status": "UNAVAILABLE",
            "evidence": PUBLICATION_LOCK if evidence is None else evidence,
            "can_execute": False,
        },
    )
    monkeypatch.setattr(market_api.prod, "get_client", lambda: _PreflightClient(persisted, preflight))


def _raw_research_result():
    return {
        "ok": True,
        "research_only": True,
        "probability_publishable": False,
        "governed_publishable": False,
        "probability_claim_status": "SPECIALIST_RAW_RESEARCH_ONLY",
        "blockers": ["FORWARD_SHADOW_NOT_COMPLETED"],
        "research_model_output": {
            "raw_specialist_probability": 0.61,
            "model_family": "TEST_MODEL_FAMILY",
            "specialist_version": "wow.test-specialist@1",
            "calibrated_probability": None,
            "calibrated_probability_lower_bound": None,
            "calibrated_probability_upper_bound": None,
            "calibration_status": "UNKNOWN_OR_BLOCKED",
        },
        "backend_traversal": {"prediction_ledger_write": "NOT_ATTEMPTED_PUBLICATION_LOCK"},
        "can_execute": False,
    }


def test_publication_only_lock_reaches_existing_scorer_but_stays_held(monkeypatch):
    client, persisted, routed, _ = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted)
    requests = []

    def score(_market_api, req, **_kwargs):
        requests.append(req)
        return _raw_research_result()

    monkeypatch.setattr(publication_api, "_raw_specialist_research", score)
    monkeypatch.setattr(market_api.prod, "_reject_llp_prop_identity", lambda _: "WOW_BETTING_ENGINE")
    response = client.post("/score-pick-request", json={"rows": [_row("publication-lock")]})

    assert response.status_code == 200
    body = response.json()
    outcome = body["rows"][0]
    assert len(requests) == 1, outcome
    assert routed == [("MLB", "PITCHER_STRIKEOUTS")]
    assert len(persisted) == 1
    assert requests[0].source_snapshot_id == persisted[0]["source_snapshot_id"]
    assert requests[0].line == 5.5
    assert outcome["terminal_status"] == "HELD"
    assert outcome["code"] == "CALIBRATION_BLOCKED_NO_PUBLISH"
    assert outcome["model_evaluated"] is True
    assert outcome["rank_eligible"] is False
    assert outcome["probability_publishable"] is False
    assert outcome["downstream_money_evaluation_allowed"] is False
    assert outcome["downstream_portfolio_evaluation_allowed"] is False
    assert outcome["specialist_scoring_attempted"] is True
    assert outcome["result"]["research_model_output"]["raw_specialist_probability"] == 0.61
    assert outcome["result"]["research_model_output"]["calibrated_probability_lower_bound"] is None
    assert outcome["result"]["backend_traversal"]["prediction_ledger_write"] == "NOT_ATTEMPTED_PUBLICATION_LOCK"
    assert outcome["specialist_utilization_audit"]["raw_probability_produced"] is True
    assert outcome["specialist_utilization_audit"]["calibrator_invoked"] is False
    assert body["top10_model_reconciliation"]["balanced"] is True
    assert body["top10_model_reconciliation"]["rows_with_typed_blocker"] == 1
    assert body["rows_completed"] == 0
    assert body["can_execute"] is False


def test_canonical_research_entrypoint_runs_fitted_model_without_governed_write(monkeypatch):
    client, persisted, _, governed_calls = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted)
    monkeypatch.setattr(market_api.prod, "_reject_llp_prop_identity", lambda _: "WOW_BETTING_ENGINE")
    monkeypatch.setattr(market_api.prod, "_prop_evidence", lambda req: {
        **next(snapshot for snapshot in persisted if snapshot["source_snapshot_id"] == req.source_snapshot_id),
        "ok": True, "code": "PROP_EVIDENCE_READY",
    })

    def infer(_db, *, request, line, features):
        artifact = _artifact()
        return CertifiedInference(
            artifact=artifact,
            distribution=mlb_pitcher_so_failure_path_nb_v1_adapter(artifact, request, features),
        )

    monkeypatch.setattr(publication_api, "infer_certified_distribution", infer)
    response = client.post("/score-pick-request", json={"rows": [_row("real-research")]})
    assert response.status_code == 200
    outcome = response.json()["rows"][0]
    assert outcome["code"] == "CALIBRATION_BLOCKED_NO_PUBLISH"
    assert outcome["terminal_status"] == "HELD"
    assert outcome["probability_publishable"] is False
    assert outcome["rank_eligible"] is False
    result = outcome["result"]
    assert result["research_only"] is True
    assert 0 < result["research_model_output"]["raw_specialist_probability"] < 1
    assert result["research_model_output"]["calibrated_probability"] is None
    assert result["research_model_output"]["calibrated_probability_lower_bound"] is None
    assert result["backend_traversal"]["prediction_ledger_write"] == "NOT_ATTEMPTED_PUBLICATION_LOCK"
    assert "prediction" not in result
    assert len(persisted) == 1
    assert governed_calls == []


def test_research_fallback_cannot_change_frozen_snapshot_before_inference(monkeypatch):
    client, persisted, _, governed_calls = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted)
    monkeypatch.setattr(market_api.prod, "_reject_llp_prop_identity", lambda _: "WOW_BETTING_ENGINE")
    fallback_id = "11111111-1111-4111-8111-111111111111"
    model_calls = []

    def fetch_evidence(req):
        if str(req.source_snapshot_id) != fallback_id:
            return {"ok": False, "code": "PROP_EVIDENCE_STALE", "source_snapshot_id": req.source_snapshot_id}
        return {**persisted[0], "ok": True, "code": "PROP_EVIDENCE_READY", "source_snapshot_id": fallback_id}

    def infer(_db, *, request, line, features):
        model_calls.append(request.evidence_snapshot_id)
        artifact = _artifact()
        return CertifiedInference(
            artifact=artifact,
            distribution=mlb_pitcher_so_failure_path_nb_v1_adapter(artifact, request, features),
        )

    monkeypatch.setattr(market_api.prod, "_prop_evidence", fetch_evidence)
    monkeypatch.setattr(evidence_repair, "_fallback_snapshot_ids", lambda _db, _req: [fallback_id])
    monkeypatch.setattr(publication_api, "infer_certified_distribution", infer)
    response = client.post("/score-pick-request", json={"rows": [_row("fallback-evidence")]})
    assert response.status_code == 200
    outcome = response.json()["rows"][0]
    assert outcome["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert outcome["detail"]["blocker_code"] == "PROP_RESEARCH_FROZEN_SNAPSHOT_MISMATCH"
    assert outcome["detail"]["requested_source_snapshot_id"] == persisted[0]["source_snapshot_id"]
    assert outcome["detail"]["effective_source_snapshot_id"] == fallback_id
    assert outcome["source_snapshot_id"] == persisted[0]["source_snapshot_id"]
    assert outcome["probability_publishable"] is False
    assert model_calls == []
    assert governed_calls == []


@pytest.mark.parametrize("preflight", [
    {"blockers": ["FORWARD_SHADOW_NOT_COMPLETED", "SOME_UNKNOWN_FAILURE"]},
    {"blockers": ["SPECIALIST_MODEL_UNAVAILABLE"]},
    {"blockers": ["MANDATORY_MODEL_INPUTS_INCOMPLETE"]},
    {"blockers": ["GOVERNED_PROBABILITY_UNAVAILABLE"]},
    RuntimeError("preflight unavailable"),
    ["malformed preflight"],
])
def test_publication_lock_never_hides_unknown_model_input_or_global_preflight(monkeypatch, preflight):
    client, persisted, routed, scored = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted, preflight=preflight)
    response = client.post("/score-pick-request", json={"rows": [_row("blocked", include_evidence=False)]})
    outcome = response.json()["rows"][0]
    assert outcome["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert outcome["specialist_scoring_attempted"] is False
    assert outcome["acquisition"]["mode"] == "NOT_ATTEMPTED_ROUTE_BLOCKED"
    assert outcome["probability_publishable"] is False
    assert routed == persisted == scored == []


@pytest.mark.parametrize("preflight", [
    {},
    {"ok": False, "code": "UNKNOWN_GLOBAL_FAILURE"},
    {"ok": True, "blockers": ["FORWARD_SHADOW_NOT_COMPLETED"]},
    {**PUBLICATION_PREFLIGHT, "code": "UNKNOWN_GLOBAL_FAILURE"},
    {**PUBLICATION_PREFLIGHT, "failed_contract_scope": False},
    {**PUBLICATION_PREFLIGHT, "failed_contract_scope": None},
    {**PUBLICATION_PREFLIGHT, "failed_contract_scope": []},
    {**PUBLICATION_PREFLIGHT, "failed_contract_scope": "PUBLICATION"},
    {key: value for key, value in PUBLICATION_PREFLIGHT.items() if key != "failed_contract_scope"},
    {**PUBLICATION_PREFLIGHT, "governed_probability_capability": "UNKNOWN"},
    {**PUBLICATION_PREFLIGHT, "governed_probability_capability": {}},
    {**PUBLICATION_PREFLIGHT, "governed_publication_capability": "UNKNOWN"},
    {**PUBLICATION_PREFLIGHT, "specialist_model_capability": "UNKNOWN"},
    {**PUBLICATION_PREFLIGHT, "specialist_model_capability": []},
    {**PUBLICATION_PREFLIGHT, "specialist_model_capability": "UNAVAILABLE"},
    {key: value for key, value in PUBLICATION_PREFLIGHT.items() if key != "probability_publishable"},
    {key: value for key, value in PUBLICATION_PREFLIGHT.items() if key != "governed_publishable"},
])
def test_aggregate_publication_reason_cannot_supply_missing_or_invalid_preflight_proof(monkeypatch, preflight):
    client, persisted, routed, scored = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted, preflight=preflight)
    response = client.post("/score-pick-request", json={"rows": [_row("invalid-preflight")]})
    outcome = response.json()["rows"][0]
    assert outcome["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert outcome["specialist_scoring_attempted"] is False
    assert outcome["acquisition"]["mode"] == "NOT_ATTEMPTED_ROUTE_BLOCKED"
    assert routed == persisted == scored == []


@pytest.mark.parametrize("scope", [["GLOBAL"], ["CONFIDENCE"], ["UNKNOWN"], False, None, "PUBLICATION"])
@pytest.mark.parametrize("source", ["aggregate", "aggregate_evidence", "nested_aggregate_evidence", "nested_preflight"])
def test_every_explicit_failed_scope_source_must_remain_publication_only(monkeypatch, scope, source):
    client, persisted, routed, scored = _build(monkeypatch)
    preflight = dict(PUBLICATION_PREFLIGHT)
    evidence = dict(PUBLICATION_LOCK)
    if source == "aggregate_evidence":
        evidence["failed_contract_scope"] = scope
    elif source == "nested_aggregate_evidence":
        evidence["diagnostic"] = {"failed_contract_scope": scope}
    elif source == "nested_preflight":
        preflight["capability_evidence"] = {"failed_contract_scope": scope}
    _publication_lock(monkeypatch, persisted, preflight=preflight, evidence=evidence)
    if source == "aggregate":
        monkeypatch.setattr(market_api.prod, "_runtime_capability", lambda _: {
            "capability_status": "UNAVAILABLE", "evidence": evidence, "failed_contract_scope": scope,
        })
    response = client.post("/score-pick-request", json={"rows": [_row("other-scope-source")]})
    assert response.json()["rows"][0]["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert routed == persisted == scored == []


def test_publication_preflight_cannot_override_unknown_aggregate_blocker(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted, evidence={"reason": "UNKNOWN_GLOBAL_HOLD"})
    response = client.post("/score-pick-request", json={"rows": [_row("blocked")]})
    assert response.json()["rows"][0]["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert routed == persisted == scored == []


def test_publication_reason_cannot_authorize_unknown_capability_state(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted)
    monkeypatch.setattr(market_api.prod, "_runtime_capability", lambda _: {
        "capability_status": "UNKNOWN", "evidence": PUBLICATION_LOCK,
    })
    response = client.post("/score-pick-request", json={"rows": [_row("unknown-state")]})
    assert response.json()["rows"][0]["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert routed == persisted == scored == []


def test_publication_lock_still_requires_exact_certified_artifact(monkeypatch):
    client, persisted, _, scored = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted)
    monkeypatch.setattr(market_api, "_prop_route_artifact", lambda *_: {
        "ok": False, "code": "PROP_MODEL_REGISTRY_UNAVAILABLE", "can_execute": False,
    })
    response = client.post("/score-pick-request", json={"rows": [_row("no-artifact")]})
    outcome = response.json()["rows"][0]
    assert outcome["code"] == "PROP_MODEL_REGISTRY_UNAVAILABLE"
    assert outcome["specialist_scoring_attempted"] is False
    assert persisted == scored == []


def test_publication_continuation_cannot_accept_governed_output(monkeypatch):
    client, persisted, _, _ = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted)
    monkeypatch.setattr(publication_api, "_raw_specialist_research", lambda *_args, **_kwargs: {
        "ok": True, "research_only": False, "probability_publishable": True, "governed_publishable": True,
    })
    monkeypatch.setattr(market_api.prod, "_reject_llp_prop_identity", lambda _: "WOW_BETTING_ENGINE")
    response = client.post("/score-pick-request", json={"rows": [_row("invalid-governed-result")]})
    outcome = response.json()["rows"][0]
    assert outcome["code"] == "MODEL_OUTPUT_INVALID"
    assert outcome["terminal_status"] == "HELD"
    assert outcome["specialist_scoring_attempted"] is True
    assert outcome["probability_publishable"] is False
    assert "result" not in outcome


@pytest.mark.parametrize("scope", [["GLOBAL"], ["CONFIDENCE"], ["UNKNOWN"]])
def test_publication_reason_cannot_override_other_explicit_failed_scope(monkeypatch, scope):
    client, persisted, routed, scored = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted, preflight={
        **PUBLICATION_PREFLIGHT,
        "failed_contract_scope": scope,
    })
    response = client.post("/score-pick-request", json={"rows": [_row("other-scope")]})
    assert response.json()["rows"][0]["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert routed == persisted == scored == []


def test_publication_continuation_does_not_bypass_dedicated_first_inning_gate(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted)
    response = client.post("/score-pick-request", json={"rows": [_row("1ip", stat_type="1IP")]})
    assert response.json()["rows"][0]["code"] == "PROP_PROBABILITY_UNAVAILABLE"
    assert routed == persisted == scored == []


def test_publication_recovery_cannot_upgrade_continued_request_to_governed_write(monkeypatch):
    client, persisted, _, governed_calls = _build(monkeypatch)
    _publication_lock(monkeypatch, persisted)
    prediction_writes = []
    original_governed_scorer = market_api.score_prop

    # These are the exact two branches of the production publication wrapper.
    # Health has recovered by the time its second preflight would run.
    def recovering_publication_wrapper(req, x_wow_model_identity=None):
        preflight = {**PUBLICATION_PREFLIGHT, "probability_publishable": True, "governed_publishable": True}
        if preflight.get("governed_publishable") is True or preflight.get("probability_publishable") is True:
            prediction_writes.append(req.source_snapshot_id)
            return original_governed_scorer(req, x_wow_model_identity)
        raise AssertionError("the recovered wrapper would use the governed branch")

    monkeypatch.setattr(market_api, "score_prop", recovering_publication_wrapper)
    monkeypatch.setattr(publication_api, "_raw_specialist_research", lambda *_args, **_kwargs: _raw_research_result())
    monkeypatch.setattr(market_api.prod, "_reject_llp_prop_identity", lambda _: "WOW_BETTING_ENGINE")
    response = client.post("/score-pick-request", json={"rows": [_row("recovering-publication")]})
    assert prediction_writes == []
    assert governed_calls == []
    outcome = response.json()["rows"][0]
    assert outcome["code"] == "CALIBRATION_BLOCKED_NO_PUBLISH"
    assert outcome["probability_publishable"] is False
    assert outcome["rank_eligible"] is False


@pytest.mark.parametrize("count", [3, 5, 9])
def test_reviewed_strikeout_history_reaches_scorer_without_padding(monkeypatch, count):
    client, persisted, _, scored = _build(monkeypatch)
    row = _row("short-history")
    row["evidence"]["game_log"] = row["evidence"]["game_log"][:count]
    row["evidence"]["box_score_log"] = row["evidence"]["box_score_log"][:count]
    response = client.post("/score-pick-request", json={"rows": [row]})
    assert response.json()["rows"][0]["terminal_status"] == "COMPLETED"
    assert len(scored) == len(persisted) == 1
    assert len(persisted[0]["game_log"]) == len(persisted[0]["box_score_log"]) == count


@pytest.mark.parametrize("count", [0, 1, 2])
def test_strikeout_history_below_reviewed_floor_remains_typed_input_hold(monkeypatch, count):
    client, persisted, _, scored = _build(monkeypatch)
    row = _row("insufficient")
    row["evidence"]["game_log"] = row["evidence"]["game_log"][:count]
    row["evidence"]["box_score_log"] = row["evidence"]["box_score_log"][:count]
    response = client.post("/score-pick-request", json={"rows": [row]})
    outcome = response.json()["rows"][0]
    assert outcome["terminal_label"] == "MODEL_INPUTS_INSUFFICIENT"
    assert outcome["detail"]["blocker"] == "MLB_RECENT_STARTS_INSUFFICIENT"
    assert persisted == scored == []


@pytest.mark.parametrize("sport,stat", [
    ("MLB", "PITCHING_OUTS"), ("MLB", "STRIKES_THROWN"), ("MLB", "BALLS_THROWN"),
    ("NBA", "POINTS"), ("NFL", "PASSING_YARDS"), ("WNBA", "REBOUNDS"),
    ("NBA", "PITCHER_STRIKEOUTS"),
])
def test_strikeout_floor_does_not_relax_adjacent_route_history(monkeypatch, sport, stat):
    client, persisted, _, scored = _build(monkeypatch)
    row = _row("adjacent", sport=sport, stat_type=stat)
    row["evidence"]["game_log"] = row["evidence"]["game_log"][:9]
    row["evidence"]["box_score_log"] = row["evidence"]["box_score_log"][:9]
    response = client.post("/score-pick-request", json={"rows": [row]})
    outcome = response.json()["rows"][0]
    assert outcome["terminal_label"] == "MODEL_INPUTS_INSUFFICIENT"
    assert outcome["detail"]["blocker"] == "L10_GAME_LOG_INCOMPLETE"
    assert persisted == scored == []
