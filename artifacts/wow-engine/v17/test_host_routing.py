import pytest

from v17.host_routing import (
    LLP_TEAM_BETTING_ENGINE,
    PROJECT_CHAT,
    WOW_BETTING_ENGINE,
    controlling_engine_for,
    host_decision_audit_fields,
    resolve_host_route,
)


def test_prop_is_always_controlled_by_wow_engine():
    for requester in (WOW_BETTING_ENGINE, LLP_TEAM_BETTING_ENGINE, PROJECT_CHAT):
        route = resolve_host_route(requester, "PLAYER_PROP")
        assert route.controlling_engine_identity == WOW_BETTING_ENGINE
        assert route.global_terminal_authority is False
        assert route.can_execute is False


def test_team_event_is_always_controlled_by_llp_engine():
    for requester in (WOW_BETTING_ENGINE, LLP_TEAM_BETTING_ENGINE, PROJECT_CHAT):
        route = resolve_host_route(requester, "OUTRIGHT_WINNER")
        assert route.controlling_engine_identity == LLP_TEAM_BETTING_ENGINE
        assert route.global_terminal_authority is False
        assert route.can_execute is False


def test_host_identity_and_controlling_engine_are_separate_concepts():
    route = resolve_host_route(WOW_BETTING_ENGINE, "MONEYLINE")
    assert route.requester_host_identity == WOW_BETTING_ENGINE
    assert route.controlling_engine_identity == LLP_TEAM_BETTING_ENGINE


def test_unknown_candidate_family_fails_closed():
    with pytest.raises(ValueError, match="CANDIDATE_FAMILY_UNSUPPORTED"):
        controlling_engine_for("MYSTERY_MARKET")


def test_unknown_requester_host_fails_closed():
    with pytest.raises(ValueError, match="UNAUTHORIZED_WOW_REQUESTER_HOST"):
        resolve_host_route("RANDOM_GPT", "PLAYER_PROP")


def test_host_local_label_is_audit_only():
    result = host_decision_audit_fields(host_label="PICK_FAVORITE", host_blockers=[])
    assert result["host_terminal_label"] == "PICK_FAVORITE"
    assert result["host_terminal_authority"] is False
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["can_execute"] is False
