from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import api_prod_market as market_api
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


def _evidence(*, l10=True):
    now = datetime.now(timezone.utc)
    n = 10 if l10 else 5
    return {
        "captured_at": now.isoformat(),
        "game_log": list(range(1, n + 1)),
        "box_score_log": [{"minutes": 30 + i, "stat": i} for i in range(n)],
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


def _row(row_key, *, sport="MLB", stat_type="Ks", l10=True):
    return {
        "row_key": row_key,
        "event_id": f"{sport}:TEST:{row_key}",
        "event_start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sport": sport,
        "player": "Test Player",
        "stat_type": stat_type,
        "line": 5.5,
        "direction": "MORE",
        "evidence": _evidence(l10=l10),
    }


def _build(monkeypatch, *, unsupported_sports=()):
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
            "prediction": {"prediction_id": "00000000-0000-0000-0000-000000000001"},
            "model_evidence": {"calibrated_probability_lower_bound": 0.58},
            "probability_publishable": True,
            "can_execute": False,
        }

    monkeypatch.setattr(market_api.prod.base_api, "_controlling_specialist_provider", specialist)
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
    assert body["rows_in"] == 1
    assert body["rows_completed"] == 1
    assert body["rows_held"] == 0
    assert body["rows_rejected"] == 0
    assert body["reconciliation_pass"] is True
    assert body["rows"][0]["code"] == "MODEL_QUALIFIED"
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


def test_unsupported_row_is_held_without_model_or_snapshot(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch, unsupported_sports={"WNBA"})
    response = client.post("/score-pick-request", json={"rows": [_row("wnba", sport="WNBA", stat_type="REB")]})
    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["terminal_status"] == "HELD"
    assert row["code"] == "MODEL_UNAVAILABLE"
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False
    assert persisted == []
    assert routed == []
    assert scored == []


def test_bad_row_cannot_erase_good_sibling_and_reconciliation_is_exact(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    response = client.post(
        "/score-pick-request",
        json={"rows": [_row("bad", l10=False), _row("good")]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rows_in"] == 2
    assert body["rows_completed"] == 1
    assert body["rows_held"] == 0
    assert body["rows_rejected"] == 1
    assert body["reconciliation_pass"] is True
    by_key = {row["row_key"]: row for row in body["rows"]}
    assert by_key["bad"]["terminal_status"] == "REJECTED"
    assert by_key["bad"]["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert by_key["bad"]["detail"]["blocker"] == "L10_GAME_LOG_INCOMPLETE"
    assert by_key["good"]["terminal_status"] == "COMPLETED"
    assert by_key["good"]["code"] == "MODEL_QUALIFIED"
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
