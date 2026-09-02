import json
from pathlib import Path


CONTRACT = Path(__file__).with_name("custom_engine_alignment_contract.json")


def _contract() -> dict:
    return json.loads(CONTRACT.read_text())


def test_execution_remains_permanently_disabled_after_v17_backend_cutover():
    c = _contract()
    assert c["status"] == "V17_PRODUCTION_ACTIVE_BACKEND"
    assert c["activation"]["can_execute"] is False
    assert c["activation"]["dry_run_only_no_live_trading_no_market_orders"] is True
    assert c["activation"]["v17_active"] is True
    assert c["activation"]["v17_cutover_allowed"] is True
    assert c["activation"]["owner_cutover_authorized"] is True


def test_both_custom_engines_are_first_class_hosts():
    c = _contract()
    hosts = c["hosts"]
    assert hosts["WOW_BETTING_ENGINE"]["type"] == "CUSTOM_GPT"
    assert hosts["LLP_TEAM_BETTING_ENGINE"]["type"] == "CUSTOM_GPT"
    assert hosts["WOW_BETTING_ENGINE"]["global_terminal_authority"] is False
    assert hosts["LLP_TEAM_BETTING_ENGINE"]["global_terminal_authority"] is False


def test_prop_and_team_event_lane_ownership_is_unambiguous():
    c = _contract()
    wow = set(c["hosts"]["WOW_BETTING_ENGINE"]["owns"])
    llp = set(c["hosts"]["LLP_TEAM_BETTING_ENGINE"]["owns"])
    assert "PLAYER_PROP" in wow
    assert "OUTRIGHT_WINNER" in llp
    assert "MONEYLINE" in llp
    assert "UPSET" in llp
    assert wow.isdisjoint(llp)


def test_only_shared_reducer_has_global_terminal_authority():
    c = _contract()
    assert c["shared_core"]["single_global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert c["team_event_contract"]["may_emit_global_final_approved"] is False
    assert c["team_event_contract"]["may_override_shared_blocker"] is False


def test_team_event_lane_requires_event_mutex_and_specialist():
    c = _contract()["team_event_contract"]
    assert c["host"] == "LLP_TEAM_BETTING_ENGINE"
    assert c["requires_full_mutually_exclusive_outcome_space"] is True
    assert c["requires_sport_specific_controlling_model"] is True
    assert c["requires_probability_claim_audit"] is True
    assert c["requires_event_decision_governor"] is True
    assert c["event_decision"] == "ONE_SIDE_OR_NO_PICK"


def test_prop_lane_requires_bidirectional_specialist_modeling():
    c = _contract()["prop_contract"]
    assert c["host"] == "WOW_BETTING_ENGINE"
    assert c["requires_sport_stat_controlling_model"] is True
    assert c["requires_bidirectional_audit"] is True
    assert c["opposite_side_must_be_rerun"] is True
    assert c["generic_reasoning_substitution_allowed"] is False
    assert c["market_probability_substitution_allowed"] is False


def test_legacy_replit_cannot_be_primary_v17_route():
    c = _contract()["backend_contract"]
    assert c["canonical_runtime"] == "RENDER_SUPABASE_GOVERNED_CORE"
    assert c["legacy_replit_primary_routing_allowed"] is False
    assert c["direct_vendor_actions_terminal_authority"] is False
    assert c["production_entrypoint"] == "api_ncaaf_acceptance:app"
    assert c["v17_activation_flag"] == "WOW_V17_ACTIVE=1"
    assert c["backward_compatible_v16_routes_preserved"] is True


def test_v17_active_backend_uses_existing_governed_team_event_adapter_without_fake_models():
    active = _contract()["v17_active_implementation"]
    assert active["production_entrypoint_changed"] is True
    assert active["production_activation_mode"] == "ADDITIVE_ROUTES_ON_ACCEPTED_ENTRYPOINT"
    assert active["team_event_generic_contract"] == "PRODUCTION_ACTIVE_WHEN_FLAG_ENABLED"
    assert active["team_event_mlb_adapter"] == "REUSES_EXISTING_GOVERNED_MLB_EVENT_PATH"
    assert active["unsupported_team_event_sports"] == "FAIL_CLOSED_MODEL_UNAVAILABLE"
    assert active["host_local_terminal_labels"] == "AUDIT_ONLY"
    assert active["canonical_host_identity_enforcement"] == "ACTIVE"
    assert active["recommendation_ledger_routes"] == "PRODUCTION_ACTIVE"


def test_v16_is_legacy_compatibility_not_current_generation():
    legacy = _contract()["legacy_v16_compatibility_contracts"]
    assert legacy["generation_status"] == "LEGACY_SUPERSEDED"
    assert legacy["governed_probability"]["compatibility_status"] == "PRESERVED_BY_ACTIVE_V17"
    assert legacy["pick_request"]["compatibility_status"] == "PRESERVED_BY_ACTIVE_V17"


def test_live_editor_sync_is_verified_without_changing_execution_authority():
    c = _contract()
    attest = c["editor_attestation"]
    wow = attest["WOW_BETTING_ENGINE"]
    llp = attest["LLP_TEAM_BETTING_ENGINE"]

    assert wow["required"] is True
    assert llp["required"] is True
    assert wow["status"] == "LIVE_EDITOR_SYNC_VERIFIED"
    assert llp["status"] == "LIVE_EDITOR_SYNC_VERIFIED"
    assert wow["instructions_blob_sha"] == "202157522b96921d973e7a9dbc1d373f95249eb7"
    assert wow["action_schema"] == "v17/openapi.wow-betting-engine.v17.yaml"
    assert wow["action_schema_changed"] is False
    assert wow["bearer_auth_changed"] is False
    assert wow["verified_after_reload"] is True
    assert wow["live"] is True
    assert c["remaining_external_sync"] == []
    assert c["activation"]["v17_cutover_allowed"] is True
    assert c["activation"]["can_execute"] is False
