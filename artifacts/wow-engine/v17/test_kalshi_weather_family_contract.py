from __future__ import annotations

import json
from pathlib import Path

from v17.host_routing import (
    KALSHI_WEATHER_MARKET_EXPERT,
    PROJECT_CHAT,
    WEATHER_FAMILIES,
    controlling_engine_for,
    expected_full_model_operation_id,
    resolve_host_route,
)


def test_every_declared_weather_family_routes_to_the_weather_specialist():
    assert WEATHER_FAMILIES
    for family in WEATHER_FAMILIES:
        route = resolve_host_route(PROJECT_CHAT, family)
        assert controlling_engine_for(family) == KALSHI_WEATHER_MARKET_EXPERT
        assert route.controlling_engine_identity == KALSHI_WEATHER_MARKET_EXPERT
        assert route.global_terminal_authority is False
        assert route.can_execute is False
        assert expected_full_model_operation_id(family) == "analyzeKalshiWeatherV17Contract"


def test_alignment_contract_weather_families_cannot_drift_from_router():
    contract_path = Path(__file__).with_name("custom_engine_alignment_contract.json")
    payload = json.loads(contract_path.read_text())
    declared = set(payload["hosts"][KALSHI_WEATHER_MARKET_EXPERT]["owns"])
    assert declared == set(WEATHER_FAMILIES)
    assert payload["hosts"][KALSHI_WEATHER_MARKET_EXPERT]["global_terminal_authority"] is False
    assert payload["weather_contract"]["global_probability_publication_authority"] == "V17_TERMINAL_REDUCER"
    assert payload["weather_contract"]["unsupported_or_uncertified_lane_behavior"] == "FAIL_CLOSED_NO_PLAY_DATA_INSUFFICIENT"
    assert payload["weather_contract"]["can_execute"] is False
