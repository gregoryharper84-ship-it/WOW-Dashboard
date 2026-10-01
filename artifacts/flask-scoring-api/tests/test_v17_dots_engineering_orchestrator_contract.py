from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / ".agents/skills/wow-dots-engineering-orchestrator/SKILL.md"
CONTRACT = ROOT / "artifacts/wow-engine/WOW_DOTS_ENGINEERING_ORCHESTRATOR.yaml"
RECEIPT = ROOT / "artifacts/wow-engine/WOW-DOTS-PILOT-ACCEPTANCE-2026-09-30.md"

EXPECTED_WORKFLOW = [
    "REPORTER_INTAKE",
    "RESEARCH_TRIAGE",
    "ENGINEERING",
    "INDEPENDENT_REVIEW",
    "QA_VERIFICATION",
    "RELEASE_OBSERVABILITY",
    "REPORTER_CLOSURE",
]

EXPECTED_TERMINALS = [
    "FIXED_AND_VERIFIED",
    "PR_CREATED",
    "EXPERIMENT_CREATED",
    "DUPLICATE",
    "NOT_REPRODUCIBLE",
    "BLOCKED_WITH_EXACT_REASON",
    "DEFERRED_WITH_JUSTIFICATION",
]


def _contract() -> dict:
    payload = yaml.safe_load(CONTRACT.read_text())
    assert isinstance(payload, dict)
    return payload


def _skill_text() -> str:
    return SKILL.read_text()


def test_dots_supervisor_artifacts_exist_and_skill_has_valid_frontmatter() -> None:
    assert SKILL.exists()
    assert CONTRACT.exists()
    assert RECEIPT.exists()
    text = _skill_text()
    assert text.startswith("---\n")
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert metadata["name"] == "wow-dots-engineering-orchestrator"
    assert metadata["description"].strip()


def test_dots_supervisor_preserves_v17_authority_and_execution_guards() -> None:
    contract = _contract()
    assert contract["runtime_generation"] == "V17_ACTIVE"
    assert contract["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert contract["custom_gpt_identity"] == "WOW_BETTING_ENGINE"
    assert contract["can_execute"] is False
    assert contract["dry_run_only_no_live_trading_no_market_orders"] is True

    probability = contract["probability_governance"]
    assert probability["exactly_one_controlling_fitted_specialist"] is True
    for key in (
        "orchestrator_may_originate_probability",
        "orchestrator_may_substitute_probability",
        "orchestrator_may_blend_probability",
        "orchestrator_may_override_probability",
        "orchestrator_may_publish_probability",
    ):
        assert probability[key] is False
    assert probability["preserve_typed_failures_exactly"] is True
    assert probability["model_unavailable_is_catch_all"] is False


def test_dots_delegates_to_existing_role_separated_engineering_team() -> None:
    contract = _contract()
    delegation = contract["delegation"]
    assert delegation["skill"] == "wow-autonomous-product-qa-engineering-recovery"
    assert delegation["canonical_workflow"] == EXPECTED_WORKFLOW
    assert delegation["self_approval_allowed"] is False
    assert delegation["production_fix_requires_qa"] is True
    assert delegation["production_fix_requires_production_verification"] is True


def test_dots_platform_mode_cannot_falsely_claim_native_access() -> None:
    contract = _contract()
    platform = contract["platform"]
    assert platform["default_mode"] == "WORK_SURROGATE"
    assert set(platform["supported_modes"]) == {"WORK_SURROGATE", "DOTS_NATIVE"}
    assert platform["dots_native_requires"] == ["account_workspace_access_proven"]
    assert platform["unproven_native_access_behavior"] == "WORK_SURROGATE"
    assert platform["native_mode_may_change_probability_governance"] is False

    text = _skill_text()
    assert "Never claim `DOTS_NATIVE` unless access is actually proven" in text
    assert "Native-Dots unavailability blocks only the platform-mode switch" in text


def test_dots_terminal_dispositions_are_exact_and_complete() -> None:
    contract = _contract()
    assert contract["terminal_dispositions"] == EXPECTED_TERMINALS
    text = _skill_text()
    for terminal in EXPECTED_TERMINALS:
        assert terminal in text
    assert "Never silently drop discovered work" in text


def test_dots_blocked_item_cannot_stall_independent_queue() -> None:
    contract = _contract()
    assert contract["priority"]["blocked_item_stalls_independent_queue"] is False
    assert contract["hard_boundaries"]["continue_independent_queue"] is True
    assert contract["hard_boundaries"]["exact_terminal"] == "BLOCKED_WITH_EXACT_REASON"
    assert contract["hard_boundaries"]["require_smallest_remaining_action"] is True

    text = _skill_text()
    assert "A blocked item must not idle unrelated work" in text
    assert "continue to the next eligible independent item in the same cycle" in text


def test_dots_priority_keeps_restoration_ahead_of_new_features() -> None:
    contract = _contract()
    priority = contract["priority"]
    assert priority["severity_order"] == ["P0", "P1", "P2", "P3"]
    assert priority["class_order_while_degraded"][0] == "governed_scoring_safety_reconciliation_publication"
    assert priority["class_order_while_degraded"][-1] == "new_features"
    assert priority["resume_existing_before_duplicate_creation"] is True
    assert priority["deduplicate_before_create"] is True


def test_dots_change_class_c_remains_governed_challenger_only() -> None:
    contract = _contract()
    change_classes = contract["change_classes"]
    assert change_classes["dots_orchestration_class"] == "B"
    assert change_classes["class_c_requires"] == [
        "challenger",
        "historical_replay",
        "counterexample_review",
        "holdout_or_forward_validation",
        "regression",
        "governed_review",
    ]


def test_dots_receipt_contract_preserves_required_operating_evidence() -> None:
    contract = _contract()
    required = {
        "PLATFORM_MODE",
        "OPEN_AT_START",
        "NEW_INCIDENTS",
        "ACTIVE_ITEM",
        "FIXED_AND_VERIFIED",
        "PRS_CREATED",
        "PRS_MERGED",
        "DEPLOYS_VERIFIED",
        "HARD_BLOCKED",
        "UNFINISHED",
        "REGRESSIONS",
        "EXPERIMENTS",
        "NEXT_ACTIONS",
        "CAN_EXECUTE",
        "TERMINAL_AUTHORITY",
    }
    assert set(contract["receipt_fields"]) == required

    receipt = RECEIPT.read_text()
    assert "PILOT_ACCEPTED_FOR_REPOSITORY_SUPERVISOR_BUILD" in receipt
    assert "CAN_EXECUTE=false" in receipt
    assert "TERMINAL_AUTHORITY=V17_TERMINAL_REDUCER" in receipt
    assert "DOTS_PROBABILITY_AUTHORITY=NONE" in receipt
    assert "DOTS_WAGER_EXECUTION_AUTHORITY=NONE" in receipt


def test_dots_ticket_queue_uses_polling_ttl_wakes_without_probability_authority() -> None:
    contract = _contract()
    queue = contract["ticket_queue"]
    assert queue["change_class"] == "B"
    assert queue["table"] == "public.wow_engineering_backlog"
    assert queue["states"] == ["ACTIONABLE", "IN_PROGRESS", "PARKED", "COMPLETED", "FAILED"]
    assert queue["claim_rpc"] == "wow_claim_engineering_tickets"
    assert queue["wake_up_mode"] == "ORCHESTRATOR_POLL_QUERY"
    assert queue["intended_poll_interval_seconds"] == 3600
    assert queue["external_cron_required"] is False
    assert queue["atomic_claim_required"] is True
    assert queue["row_locking"] == "FOR_UPDATE_SKIP_LOCKED"
    assert queue["blocker_recheck_before_execution"] is True
    assert queue["cleared_blocker_retains_in_progress_claim"] is True
    assert queue["max_wakes"] == 6
    assert queue["ttl_seconds"] == {
        "api_rate_limit": 900,
        "upstream_dependency": 3600,
        "awaiting_pr_review": 14400,
    }
    assert queue["max_wake_terminal"] == "BLOCKED_WITH_EXACT_REASON"
    assert queue["probability_authority"] == "NONE"
    assert queue["can_execute"] is False
