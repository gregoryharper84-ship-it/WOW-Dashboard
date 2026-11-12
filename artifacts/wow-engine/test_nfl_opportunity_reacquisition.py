"""Regression: incomplete NFL opportunities must use verified acquisition, never flags.

The provided raw opportunities are not promoted by changing NOT_READY to PASS.
A separate source-backed snapshot must be obtained, validated and frozen.
"""
from fastapi.testclient import TestClient

from prop_auto_hydration import PropAutoHydrationError
import pick_request_runtime as facade
from test_pick_request_runtime import _build, _evidence, _row


def _manual_nfl_row(row_key="nfl-opp", *, stat_type="REC_YARDS", line=67.5):
    row = _row(row_key, sport="NFL", stat_type=stat_type)
    row["event_id"] = "2026_11_WAS_NYG"
    row["player"] = "Malik Nabers"
    row["opponent"] = "Washington Commanders"
    row["line"] = line
    row["evidence"]["opportunity_ledger"] = {
        "status": "NOT_READY",
        "targets": None,
        "reason": "TARGETS_UNAVAILABLE",
    }
    return row


def test_incomplete_nfl_opportunities_reacquired_from_registered_source(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch)
    row = _manual_nfl_row()
    seen = []

    def verified_hydrator(**kwargs):
        seen.append(kwargs)
        fresh = _evidence()
        fresh["opportunity_ledger"] = {
            "status": "PASS", "stat_type": "RECEIVING_YARDS",
            "source": "NFLVERSE_WEEKLY_PLAYER_STATS", "l10_opportunity_mean": 7.3,
        }
        fresh["rate_provenance"] = "NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1"
        return fresh

    monkeypatch.setattr(facade, "auto_hydrate_prop_evidence", verified_hydrator)
    response = client.post("/score-pick-request", json={"rows": [row]})
    assert response.status_code == 200
    terminal = response.json()["rows"][0]
    assert terminal["acquisition"]["mode"] == "AUTO_HYDRATION"
    assert terminal["acquisition"]["reacquisition_reason"] == "OPPORTUNITY_LEDGER_NOT_READY"
    assert terminal["acquisition"]["provider"] == "NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1"
    assert terminal["acquisition"]["status"] == "PASS"
    assert terminal["can_execute"] is False
    assert len(seen) == 1
    assert seen[0]["canonical_event_id"] == "2026_11_WAS_NYG"
    assert seen[0]["opponent"] == "Washington Commanders"
    assert seen[0]["stat_type"] == "RECEIVING_YARDS"
    assert len(persisted) == 1
    assert persisted[0]["line"] == 67.5
    assert persisted[0]["opportunity_ledger"]["status"] == "PASS"
    assert persisted[0]["opportunity_ledger"]["source"] == "NFLVERSE_WEEKLY_PLAYER_STATS"
    assert persisted[0]["opportunity_ledger"].get("targets") is None
    assert routed == [("NFL", "RECEIVING_YARDS")]
    assert len(scored) == 1


def test_missing_official_nfl_history_fails_closed_and_never_scores(monkeypatch):
    client, persisted, _routed, scored = _build(monkeypatch)
    def source_down(**_kwargs):
        raise PropAutoHydrationError("NFL_PROP_SOURCE_UNAVAILABLE", "source unavailable")

    monkeypatch.setattr(facade, "auto_hydrate_prop_evidence", source_down)
    response = client.post("/score-pick-request", json={"rows": [_manual_nfl_row()]})
    assert response.status_code == 200
    terminal = response.json()["rows"][0]
    assert terminal["code"] == "NFL_PROP_SOURCE_UNAVAILABLE"
    assert terminal["terminal_status"] in {"HELD", "REJECTED"}
    assert terminal["acquisition"]["status"] == "FAILED"
    assert terminal["acquisition"]["reacquisition_reason"] == "OPPORTUNITY_LEDGER_NOT_READY"
    assert terminal["probability_publishable"] is False
    assert terminal["can_execute"] is False
    assert persisted == []
    assert scored == []


def test_valid_nfl_ledger_is_not_overwritten_and_mlb_does_not_reacquire(monkeypatch):
    client, persisted, _routed, _scored = _build(monkeypatch)
    called = []
    monkeypatch.setattr(facade, "auto_hydrate_prop_evidence", lambda **kwargs: called.append(kwargs))
    nfl = _manual_nfl_row("nfl-ready")
    nfl["evidence"]["opportunity_ledger"] = {"status": "PASS", "source": "OFFICIAL_GAME_LOG"}
    mlb = _row("mlb-not-ready")
    mlb["evidence"]["opportunity_ledger"] = {"status": "NOT_READY"}
    response = client.post("/score-pick-request", json={"rows": [nfl, mlb]})
    assert response.status_code == 200
    by_key = {r["row_key"]: r for r in response.json()["rows"]}
    assert by_key["nfl-ready"]["acquisition"]["mode"] == "CALLER_SUPPLIED_RAW_EVIDENCE"
    assert by_key["mlb-not-ready"]["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert by_key["mlb-not-ready"]["detail"]["blocker"] == "OPPORTUNITY_LEDGER_NOT_READY"
    assert called == []
    assert len(persisted) == 1


def test_model_unavailable_prevents_reacquisition_even_with_missing_ledger(monkeypatch):
    client, persisted, routed, scored = _build(monkeypatch, unsupported_sports={"NFL"})
    called = []
    monkeypatch.setattr(facade, "auto_hydrate_prop_evidence", lambda **kwargs: called.append(kwargs))
    response = client.post("/score-pick-request", json={"rows": [_manual_nfl_row()]})
    terminal = response.json()["rows"][0]
    assert terminal["code"] == "MODEL_UNAVAILABLE"
    assert terminal["acquisition"]["mode"] == "NOT_ATTEMPTED_ROUTE_BLOCKED"
    assert terminal["can_execute"] is False
    assert called == []
    assert persisted == routed == scored == []
