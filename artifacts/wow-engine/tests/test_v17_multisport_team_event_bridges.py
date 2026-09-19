from __future__ import annotations

from copy import deepcopy

import pytest

import v17.multisport_team_event_bridges as multisport_bridges
import v17.team_event_bridge_runtime as bridge_runtime
from v17.llp_governed_package_scoring import PASS, validate_governed_scoring_package
from v17.multisport_team_event_governance import FINAL_APPROVED, reduce_multisport_team_event
from v17.multisport_team_event_models import (
    ModelInputsInsufficient,
    score_mma_team_event,
    score_nhl_team_event,
    score_soccer_team_event,
    score_tennis_team_event,
    score_wnba_team_event,
)
from v17.team_event_capability_manifest import CERTIFIED_TEAM_EVENT_SPORTS
from v17.team_event_model_registry_audit import CERTIFIED, certification_state
from v17.team_event_request_runtime import TeamEventRequest


FUTURE_START = "2026-12-31T20:00:00Z"
LATEST_UPDATE = "2026-09-18T12:00:00Z"


def _base_evidence(sport: str) -> dict:
    if sport == "WNBA":
        return {
            "injury_report": {"status": "CURRENT"},
            "expected_starters_rotation": "CONFIRMED",
            "rest_back_to_back": "RESTED",
            "home_win_pct": 0.70,
            "away_win_pct": 0.50,
            "home_elo": 1580,
            "away_elo": 1510,
            "sample_size": 24,
            "status_freshness_hours": 0.5,
        }
    if sport == "NHL":
        return {
            "goalie_status": "CONFIRMED",
            "injury_report": {"status": "CURRENT"},
            "rest_travel": "NORMAL",
            "home_elo": 1575,
            "away_elo": 1510,
            "home_goalie_sv_pct": 0.918,
            "away_goalie_sv_pct": 0.907,
            "home_pp_pct": 0.245,
            "away_pk_pct": 0.795,
            "sample_size": 20,
            "status_freshness_hours": 0.5,
        }
    if sport == "SOCCER":
        return {
            "starting_xi_status": "CONFIRMED",
            "injury_report": {"status": "CURRENT"},
            "competition_rules": "REGULATION_1X2",
            "home_draw_away_outcome_space": "HOME_DRAW_AWAY",
            "home_xg_per_game": 1.85,
            "away_xg_per_game": 1.10,
            "sample_size": 12,
            "status_freshness_hours": 0.5,
        }
    if sport == "TENNIS":
        return {
            "surface": "HARD",
            "retirement_settlement_rules": "BOOK_RULES_CAPTURED",
            "participant_status": "ACTIVE",
            "tournament_round": "R32",
            "surface_adjusted_form": 0.64,
            "sample_size": 15,
            "status_freshness_hours": 0.5,
        }
    if sport == "MMA":
        return {
            "weight_class": "LIGHTWEIGHT",
            "scheduled_rounds": 3,
            "participant_status": "ACTIVE",
            "weigh_in_status": "OFFICIAL",
            "no_contest_draw_outcome_space": "FIGHTER_A_FIGHTER_B_DRAW_NC",
            "fight_history": [
                {"fighter_a": "Alpha", "fighter_b": "X", "winner": "Alpha"},
                {"fighter_a": "Alpha", "fighter_b": "Y", "winner": "Y"},
                {"fighter_a": "Alpha", "fighter_b": "Z", "winner": "Alpha"},
                {"fighter_a": "Beta", "fighter_b": "X", "winner": "Beta"},
                {"fighter_a": "Beta", "fighter_b": "Y", "winner": "Beta"},
                {"fighter_a": "Beta", "fighter_b": "Z", "winner": "Z"},
            ],
            "status_freshness_hours": 0.5,
        }
    raise AssertionError(sport)


def _req(sport: str, *, evidence: dict | None = None, market_prior: dict | None = None) -> TeamEventRequest:
    return TeamEventRequest(
        requester_host_identity="WOW_BETTING_ENGINE",
        research_run_id=f"test-{sport.lower()}",
        requested_slate_date="2026-12-31",
        requested_timezone="UTC",
        scan_stage="PREGAME",
        candidate_family=("MATCH_WINNER" if sport == "TENNIS" else "FIGHT_WINNER" if sport == "MMA" else "OUTRIGHT_WINNER"),
        decision_intent="WINNER",
        event_key=f"{sport}:alpha-beta",
        official_event_id=f"official-{sport.lower()}-1",
        event_start_time_utc=FUTURE_START,
        sport=sport,
        league=("ATP" if sport == "TENNIS" else "UFC" if sport == "MMA" else "MLS" if sport == "SOCCER" else sport),
        market_family="OUTRIGHT_WINNER",
        settlement_basis="OFFICIAL_FINAL_RESULT",
        home_team="Alpha",
        away_team="Beta",
        source_snapshot_id=f"snapshot-{sport.lower()}-1",
        latest_material_update_timestamp=LATEST_UPDATE,
        market_prior=market_prior,
        sport_specific_evidence=deepcopy(evidence or _base_evidence(sport)),
    )


def test_wnba_probability_is_market_independent_and_governed_package_valid():
    request_a = _req("WNBA", market_prior={"home_implied_probability": 0.90})
    request_b = _req("WNBA", market_prior={"home_implied_probability": 0.20})
    result_a = score_wnba_team_event(request_a)
    result_b = score_wnba_team_event(request_b)

    assert result_a["calibrated_probability"] == result_b["calibrated_probability"]
    assert result_a["market_probability_used_as_model"] is False
    assert result_a["generic_reasoning_used_as_model"] is False
    assert result_a["can_execute"] is False
    assert validate_governed_scoring_package(result_a).status == PASS


def test_nhl_model_is_deterministic_and_binary_probabilities_sum_to_one():
    request = _req("NHL")
    first = score_nhl_team_event(request)
    second = score_nhl_team_event(request)
    assert first["calibrated_probability"] == second["calibrated_probability"]
    assert first["simulation_count"] == 5000
    assert first["calibrated_home_probability"] + first["calibrated_away_probability"] == pytest.approx(1.0)
    assert validate_governed_scoring_package(first).status == PASS


def test_soccer_preserves_three_state_1x2_outcome_space():
    result = score_soccer_team_event(_req("SOCCER"))
    states = result["three_state_1x2"]
    assert states["draw"] > 0.0
    assert states["home"] + states["draw"] + states["away"] == pytest.approx(1.0, abs=1e-6)
    assert result["outcome_space"] == "HOME_DRAW_AWAY_1X2"
    assert validate_governed_scoring_package(result).status == PASS


def test_tennis_surface_specialist_returns_governed_package():
    result = score_tennis_team_event(_req("TENNIS"))
    assert result["model_components"]["surface_adjusted_form"] == pytest.approx(0.64)
    assert result["outcome_space"] == "PLAYER_A_PLAYER_B_MATCH_WINNER"
    assert validate_governed_scoring_package(result).status == PASS


def test_mma_fits_internal_elo_from_fight_history_and_never_uses_market_probability():
    result = score_mma_team_event(_req("MMA", market_prior={"home_implied_probability": 0.99}))
    assert result["mma_elo_fit"]["home_fights_in_ledger"] >= 3
    assert result["mma_elo_fit"]["away_fights_in_ledger"] >= 3
    assert result["market_probability_used_as_model"] is False
    assert result["generic_reasoning_used_as_model"] is False
    assert validate_governed_scoring_package(result).status == PASS


def test_mma_without_history_is_inputs_insufficient_not_model_unavailable():
    evidence = _base_evidence("MMA")
    evidence.pop("fight_history")
    with pytest.raises(ModelInputsInsufficient) as exc:
        score_mma_team_event(_req("MMA", evidence=evidence))
    assert exc.value.code == "MODEL_INPUTS_INSUFFICIENT"
    assert "fight_history" in exc.value.missing_fields


def test_exact_runtime_registration_activates_certification_without_mutating_static_catalog():
    previous_registry = dict(bridge_runtime.TEAM_EVENT_BRIDGES)
    previous_installed = multisport_bridges._INSTALLED
    try:
        multisport_bridges._INSTALLED = False
        for sport in multisport_bridges.BRIDGE_SCORERS:
            bridge_runtime.TEAM_EVENT_BRIDGES.pop(sport, None)

        receipt = multisport_bridges.install_multisport_team_event_bridges()
        assert receipt["status"] == "INSTALLED"
        assert receipt["can_execute"] is False
        assert set(receipt["registered_sports"]) == {"WNBA", "NHL", "SOCCER", "TENNIS", "MMA"}

        health = bridge_runtime.team_event_bridge_health()
        for sport in ("WNBA", "NHL", "SOCCER", "TENNIS", "MMA"):
            status, certification_id = certification_state(sport, registered=True)
            assert status == CERTIFIED
            assert certification_id == multisport_bridges.RUNTIME_CERTIFICATIONS[sport]
            assert health[sport]["registered_capability"] is True
            assert health[sport]["certification_status"] == CERTIFIED
            assert health[sport]["certification_id"] == certification_id
            assert health[sport]["scorer_resolvable"] is True
            assert health[sport]["can_execute"] is False
            # Static catalog remains non-promoting; exact live registration owns
            # the runtime certification proof.
            assert sport not in CERTIFIED_TEAM_EVENT_SPORTS
    finally:
        bridge_runtime.TEAM_EVENT_BRIDGES.clear()
        bridge_runtime.TEAM_EVENT_BRIDGES.update(previous_registry)
        multisport_bridges._INSTALLED = previous_installed


def test_governance_can_final_approve_new_sport_only_after_exact_registration():
    previous_registry = dict(bridge_runtime.TEAM_EVENT_BRIDGES)
    previous_installed = multisport_bridges._INSTALLED
    try:
        multisport_bridges._INSTALLED = False
        for sport in multisport_bridges.BRIDGE_SCORERS:
            bridge_runtime.TEAM_EVENT_BRIDGES.pop(sport, None)

        request = _req("WNBA")
        package = score_wnba_team_event(request)
        before = reduce_multisport_team_event(request, package)
        assert before["status"] == "HOLD"
        assert "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED" in before["blockers"]

        multisport_bridges.install_multisport_team_event_bridges()
        after = reduce_multisport_team_event(request, package)
        assert after["status"] == "PASS"
        assert after["terminal_label"] == FINAL_APPROVED
        assert after["probability_publishable"] is True
        assert after["rank_eligible"] is True
        assert after["can_execute"] is False
    finally:
        bridge_runtime.TEAM_EVENT_BRIDGES.clear()
        bridge_runtime.TEAM_EVENT_BRIDGES.update(previous_registry)
        multisport_bridges._INSTALLED = previous_installed
