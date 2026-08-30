import api_prod_market
from prop_settlement import LINEUP_PAYOUT_CONTEXT_REQUIRED, SettlementRule
from prop_settlement_registry import SettlementRuleResolution
from test_api_prod_market import AUTH, _install_common, _request_payload, _result, client


def test_score_prop_hydrates_prizepicks_rule_server_side_without_caller_semantics(monkeypatch):
    captured = {}

    def fake_score(**kwargs):
        return _result(market_available=False, market_quality="NO_QUALIFYING_MARKET")

    def fake_resolver(**kwargs):
        captured.update(kwargs)
        rule = SettlementRule(
            settlement_basis="PRIZEPICKS_OFFICIAL_SCORING",
            boundary_operator="GT",
            equality_treatment="PUSH",
            void_treatment="REMOVE_LEG_REPRICE",
            rule_version="PRIZEPICKS_PLAYER_PICKS_2026_08_11",
            source="SERVER_REGISTRY:test-rule:official-source",
            money_semantics="LINEUP_CONTEXT_REQUIRED",
        )
        return SettlementRuleResolution(
            status="PASS",
            blocker=None,
            rule=rule,
            authority="SERVER_REGISTRY_REVIEWED_CERTIFIED",
            provider="PRIZEPICKS",
            rule_id="test-rule",
            rule_version=rule.rule_version,
            source_ref="official-source",
            source_hash="test-hash",
            money_semantics="LINEUP_CONTEXT_REQUIRED",
            observed_rule_status="NOT_SUPPLIED",
            can_execute=False,
        )

    _install_common(monkeypatch, fake_score)
    monkeypatch.setattr(api_prod_market, "resolve_prop_settlement_rule", fake_resolver)

    payload = _request_payload()
    payload["settlement_provider"] = "PrizePicks"
    response = client.post("/score-prop", json=payload, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert captured["provider"] == "PRIZEPICKS"
    assert captured["observed_rule"] is None
    assert body["objective_lanes"]["MODEL"]["status"] == "PASS"
    assert body["objective_lanes"]["SETTLEMENT"]["status"] == "PASS"
    assert body["objective_lanes"]["SETTLEMENT"]["rule_authority"] == "SERVER_REGISTRY_REVIEWED_CERTIFIED"
    assert body["objective_lanes"]["SETTLEMENT"]["provider"] == "PRIZEPICKS"
    assert body["objective_lanes"]["SETTLEMENT"]["observed_rule_status"] == "NOT_SUPPLIED"
    assert body["objective_lanes"]["SETTLEMENT"]["money_context_required"] is True
    assert body["objective_lanes"]["MONEY"]["status"] == "HOLD"
    assert body["objective_lanes"]["MONEY"]["settlement_blocker"] == LINEUP_PAYOUT_CONTEXT_REQUIRED
    assert body["probability_publishable"] is True
    assert body["can_execute"] is False


def test_conflicting_quote_and_request_provider_cannot_select_a_rule(monkeypatch):
    def fake_score(**kwargs):
        return _result(market_available=False, market_quality="NO_QUALIFYING_MARKET")

    called = {"resolver": False}

    def should_not_resolve(**_kwargs):
        called["resolver"] = True
        raise AssertionError("conflicting providers must not reach registry selection")

    _install_common(monkeypatch, fake_score)
    monkeypatch.setattr(api_prod_market, "resolve_prop_settlement_rule", should_not_resolve)

    payload = _request_payload()
    payload["settlement_provider"] = "PrizePicks"
    now = payload["event_start_time"]
    payload["market_side_a"] = {
        "side": "MORE",
        "american_odds": -110,
        "line": payload["line"],
        "settlement_basis": "FULL_GAME_PLAYER_STAT",
        "retrieved_at": now,
        "participant": payload["player"],
        "stat": payload["stat_type"],
        "period": "FULL_GAME",
        "event_id": payload["event_id"],
        "provider": "DIFFERENT_BOOK",
    }

    response = client.post("/score-prop", json=payload, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert called["resolver"] is False
    assert body["objective_lanes"]["SETTLEMENT"]["status"] == "HOLD"
    assert body["objective_lanes"]["SETTLEMENT"]["blocker"] == "WOW_HOLD_SETTLEMENT_RULE_CONFLICT"
    assert body["objective_lanes"]["MODEL"]["status"] == "PASS"
    assert body["can_execute"] is False
