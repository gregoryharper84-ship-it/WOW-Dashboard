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


def test_wolfram_is_arithmetic_audit_not_probability_authority():
    c = _contract()
    shared = c["shared_core"]
    backend = c["backend_contract"]
    assert shared["wolfram_arithmetic_audit_is_not_a_probability_model"] is True
    assert shared["wolfram_failure_blocks_only_affected_arithmetic_claims"] is True
    assert backend["wolfram_arithmetic_audit_flag"] == "WOW_WOLFRAM_ARITHMETIC_AUDIT_ENABLED=1"
    assert backend["wolfram_app_id_server_side_only"] is True


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


def test_render_deployment_pointer_is_governed_by_typed_exact_receipts():
    contract = _contract()
    backend = contract["backend_contract"]
    pointer = backend["deployment_pointer_contract"]

    assert backend["current_render_service"] == "wow-governed-probability-engine"
    assert backend["current_render_service_id"] == "srv-da7sa9gu01pc73brt80g"
    assert "current_deployed_sha" not in backend
    assert "current_render_deploy_id" not in backend
    assert backend["auto_deploy"] is True
    assert backend["auto_deploy_trigger"] == "checksPass"
    assert backend["openapi_introspection_blocks_live_editor_sync"] is False

    assert pointer["authority"] == "LATEST_SUCCESSFUL_GITHUB_DEPLOYMENT_ATTESTATION_FROM_EXACT_RENDER_RECEIPT"
    assert pointer["environment"] == "wow-v17-render-production-attestation"
    assert pointer["task"] == "wow-v17-render-receipt-pointer"
    assert set(pointer["advancing_receipt_statuses"]) == {
        "EXACT_SHA_RENDER_DEPLOY_LIVE",
        "EXACT_SHA_ALREADY_LIVE",
    }
    assert set(pointer["non_advancing_receipt_statuses"]) == {
        "NON_MAIN_NO_DEPLOY",
        "STALE_SHA_NO_DEPLOY",
        "UPSTREAM_NOT_SUCCESS_NO_DEPLOY",
        "DEPLOY_SUPERSEDED_BY_NEW_MAIN",
    }
    assert pointer["green_workflow_alone_advances_pointer"] is False
    assert pointer["direct_protected_main_mutation"] is False
    assert pointer["can_execute"] is False

    bootstrap = pointer["bootstrap_exact_receipt"]
    assert bootstrap["receipt_status"] == "EXACT_SHA_RENDER_DEPLOY_LIVE"
    assert bootstrap["commit_sha"] == "7f13238683774b8fa4e95c743de3497d1e0be26b"
    assert bootstrap["render_deploy_id"] == "dep-daq3gd8u01pc73fllam0"
    assert bootstrap["deploy_authority"] == "GOVERNED_RENDER_API_HANDOFF"
    assert bootstrap["workflow_run_id"] == "35916975478"
    assert bootstrap["workflow_job_id"] == "107370876748"
    assert bootstrap["can_execute"] is False

    assert "RECONCILE_BACKEND_DEPLOYMENT_POINTER_FROM_EXACT_RENDER_RECEIPT" not in contract["remaining_repository_reconciliation"]
    assert "R20_RENDER_DEPLOYMENT_POINTER_GOVERNED_BY_TYPED_EXACT_RECEIPT" in contract["resolved_phase_a_findings"]


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
    assert active["action_schemas"]["WOW_BETTING_ENGINE"] == "v17/openapi.wow-betting-engine.v17.yaml"


def test_v16_is_legacy_compatibility_not_current_generation():
    legacy = _contract()["legacy_v16_compatibility_contracts"]
    assert legacy["generation_status"] == "LEGACY_SUPERSEDED"
    assert legacy["governed_probability"]["compatibility_status"] == "PRESERVED_BY_ACTIVE_V17"
    assert legacy["pick_request"]["compatibility_status"] == "PRESERVED_BY_ACTIVE_V17"
    assert "scoreWowPickRequest" in legacy["pick_request"]["operations"]
    assert "scoreWowTeamEventRequest" in legacy["pick_request"]["operations"]


def test_wow_editor_resync_is_required_and_live_action_acceptance_stays_fail_closed():
    c = _contract()
    attest = c["editor_attestation"]
    wow = attest["WOW_BETTING_ENGINE"]
    llp = attest["LLP_TEAM_BETTING_ENGINE"]

    assert wow["required"] is True
    assert llp["required"] is True
    assert wow["status"] == "RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED"
    assert wow["historical_status"] == "LIVE_EDITOR_SYNC_VERIFIED"
    assert wow["historical_verified_at"] == "2026-09-16"
    assert wow["editor_update_reported_at"] == "2026-09-20T22:14:47Z"
    assert wow["editor_update_evidence"] == "USER_REPORTED_IN_CHAT"
    assert wow["instructions_blob_sha"] is None
    assert wow["repository_instruction_parity"] == "RESYNC_REQUIRED_AFTER_PR766"
    assert wow["live_host_addendum"] == "WOW_V17_CUSTOM_GPT_PRIZEPICKS_SKILL_ADDENDUM.txt"
    assert wow["action_schema"] == "v17/openapi.wow-betting-engine.v17.yaml"
    assert wow["canonical_prop_operation"] == "scoreWowPickRequest"
    assert wow["legacy_prop_operation_alias"] == "scoreWowV17PickRequest"
    assert wow["bearer_auth_changed"] is False
    assert wow["verified_after_reload"] is False
    assert wow["live_action_acceptance_verified"] is False
    assert wow["multipage_prizepicks_acceptance_verified"] is False
    assert wow["live"] is True
    assert wow["historical_health_acceptance_status"] == "PASS"
    assert wow["historical_health_acceptance_runtime"] == "V17_ACTIVE"
    assert wow["health_acceptance_can_execute"] is False
    assert llp["status"] == "LIVE_EDITOR_SYNC_VERIFIED"
    assert c["remaining_external_sync"] == ["WOW_BETTING_ENGINE_EDITOR_RESYNC_AFTER_PR766_AND_LIVE_ACTION_ACCEPTANCE"]
    assert c["activation"]["v17_cutover_allowed"] is True
    assert c["activation"]["can_execute"] is False
