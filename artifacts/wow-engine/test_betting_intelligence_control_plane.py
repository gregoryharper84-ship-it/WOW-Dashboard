from __future__ import annotations

import pytest

from v17.betting_intelligence_control_plane import (
    CONTROL_PLANE_SCOPE,
    ECOSYSTEM_COORDINATOR,
    CapabilityRecord,
    HandoffReceipt,
    HandoffStatus,
    ObjectiveContract,
    ProductAcceptance,
    ProductMetricSnapshot,
    ProductState,
    StagnationAction,
    WorkItem,
    WorkState,
    build_control_plane_snapshot,
    evaluate_product_acceptance,
    reconcile_work_items,
    stagnation_action,
)


def test_betting_intelligence_scope_is_product_not_ecosystem_authority() -> None:
    assert CONTROL_PLANE_SCOPE == "PRODUCT_INTELLIGENCE_ORCHESTRATION"
    assert ECOSYSTEM_COORDINATOR == "WOW_ECOSYSTEM_CONDUCTOR"


def _objective() -> ObjectiveContract:
    return ObjectiveContract(
        objective_id="OBJ-LLP-001",
        request_id="REQ-001",
        requested_outcome="Return today's governed NFL moneyline slate",
        scope={"sport": "NFL", "market": "MONEYLINE"},
        required_systems=("SCOUT", "LLP_TEAM_BETTING_ENGINE", "INDEPENDENT_VERIFICATION"),
        acceptance_criteria=(
            "candidate_universe_complete",
            "exact_specialist_routing",
            "governed_probability_packages",
            "reconciliation_balanced",
            "independent_verification",
        ),
        current_owner="WOW_BETTING_INTELLIGENCE",
    )


def _capability(*, product_ready: bool = True) -> CapabilityRecord:
    return CapabilityRecord(
        capability_id="LLP:NFL:MONEYLINE",
        product="LLP_TEAM_BETTING_ENGINE",
        sport_or_domain="NFL",
        market_or_route="MONEYLINE",
        controlling_specialist="NFL_GAME_WIN_V17",
        model_state="READY",
        runtime_state="HEALTHY",
        discovery_state="COMPLETE",
        routing_state="READY",
        verification_state="PASS",
        product_ready=product_ready,
    )


def test_objective_contract_preserves_v17_authority_and_no_execution() -> None:
    objective = _objective()
    objective.validate()
    assert objective.can_execute is False
    assert objective.terminal_authority == "V17_TERMINAL_REDUCER"


def test_objective_requires_acceptance_criteria() -> None:
    objective = ObjectiveContract(
        objective_id="OBJ-1",
        request_id="REQ-1",
        requested_outcome="test",
        scope={},
        required_systems=("WOW_BETTING_ENGINE",),
        acceptance_criteria=(),
    )
    with pytest.raises(ValueError, match="acceptance_criteria"):
        objective.validate()


def test_product_ready_capability_requires_all_readiness_dimensions() -> None:
    record = CapabilityRecord(
        capability_id="LLP:NBA:MONEYLINE",
        product="LLP_TEAM_BETTING_ENGINE",
        sport_or_domain="NBA",
        market_or_route="MONEYLINE",
        controlling_specialist="NBA_GAME_WIN_V17",
        model_state="READY",
        runtime_state="HEALTHY",
        discovery_state="COMPLETE",
        routing_state="BLOCKED",
        verification_state="PASS",
        product_ready=True,
    )
    with pytest.raises(ValueError, match="product_ready cannot be true"):
        record.validate()


def test_product_ready_capability_cannot_hide_blockers() -> None:
    record = _capability(product_ready=True)
    record = CapabilityRecord(**{**record.__dict__, "blockers": ("ACTION_BINDING_MISSING",)})
    with pytest.raises(ValueError, match="blockers"):
        record.validate()


def test_reconciliation_requires_every_item_to_reach_terminal_state() -> None:
    result = reconcile_work_items(
        [
            WorkItem("c1", "LLP", WorkState.SCORED),
            WorkItem("c2", "ENGINEERING", WorkState.IN_PROGRESS),
            WorkItem("c3", "LLP", WorkState.BLOCKED, blocker_codes=("MODEL_UNAVAILABLE",)),
        ]
    )
    assert result.rows_in == 3
    assert result.rows_terminal == 2
    assert result.rows_nonterminal == 1
    assert result.missing_terminal_ids == ("c2",)
    assert result.balanced is False
    assert result.candidate_conservation_rate == pytest.approx(2 / 3)


def test_reconciliation_detects_duplicate_candidate_identity() -> None:
    result = reconcile_work_items(
        [
            WorkItem("c1", "LLP", WorkState.SCORED),
            WorkItem("c1", "LLP", WorkState.BLOCKED, blocker_codes=("DATA_MISSING",)),
        ]
    )
    assert result.duplicate_ids == ("c1",)
    assert result.balanced is False


def test_blocked_work_item_requires_typed_blocker() -> None:
    with pytest.raises(ValueError, match="blocker"):
        reconcile_work_items([WorkItem("c1", "LLP", WorkState.BLOCKED)])


def test_product_acceptance_requires_reconciliation_and_independent_verification() -> None:
    base = ProductAcceptance(
        objective_id="OBJ-1",
        criteria={"discovery": True, "routing": True, "model": True},
        independent_verification=False,
        reconciliation_balanced=True,
    )
    result = evaluate_product_acceptance(base)
    assert result["product_state"] == ProductState.WORKING_NOT_COMPLETE.value
    assert result["product_ready"] is False

    verified = ProductAcceptance(
        objective_id="OBJ-1",
        criteria={"discovery": True, "routing": True, "model": True},
        independent_verification=True,
        reconciliation_balanced=True,
    )
    result = evaluate_product_acceptance(verified)
    assert result["product_state"] == ProductState.COMPLETE.value
    assert result["product_ready"] is True


def test_product_acceptance_reports_blocked_instead_of_healthy() -> None:
    result = evaluate_product_acceptance(
        ProductAcceptance(
            objective_id="OBJ-1",
            criteria={"runtime": True, "user_path": False},
            blockers=("LIVE_GPT_EDITOR_SYNC_REQUIRED",),
            independent_verification=False,
            reconciliation_balanced=True,
        )
    )
    assert result["product_state"] == ProductState.BLOCKED.value
    assert result["missing_acceptance_criteria"] == ["user_path"]
    assert result["product_ready"] is False


def test_safe_hold_has_priority_over_other_product_states() -> None:
    result = evaluate_product_acceptance(
        ProductAcceptance(
            objective_id="OBJ-1",
            criteria={"runtime": True},
            safe_hold=True,
            unsupported=True,
            blockers=("SYSTEMIC_FAILURE",),
        )
    )
    assert result["product_state"] == ProductState.SAFE_HOLD.value


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, StagnationAction.RETRY_ALLOWED),
        (1, StagnationAction.RETRY_ALLOWED),
        (2, StagnationAction.DIAGNOSIS_REQUIRED),
        (3, StagnationAction.ESCALATE_P0),
        (4, StagnationAction.REDESIGN_OR_CAPABILITY_DECISION),
        (9, StagnationAction.REDESIGN_OR_CAPABILITY_DECISION),
    ],
)
def test_product_level_stagnation_escalation(count: int, expected: StagnationAction) -> None:
    assert stagnation_action(count) == expected


def test_handoff_receipt_must_change_owner() -> None:
    receipt = HandoffReceipt(
        work_item_id="INC-1",
        from_owner="ENGINEERING",
        to_owner="ENGINEERING",
        reason_code="ROOT_CAUSE",
        status=HandoffStatus.ACCEPTED,
        acceptance_target="production verification",
    )
    with pytest.raises(ValueError, match="change owner"):
        receipt.validate()


def test_metrics_measure_outcomes_not_activity() -> None:
    metrics = ProductMetricSnapshot(
        supported_requests=10,
        completed_requests=8,
        admitted_candidates=40,
        terminal_candidates=38,
        first_pass_requests=6,
        reliable_decisions=15,
        desired_decisions=20,
    )
    assert metrics.product_outcome_completion_rate == pytest.approx(0.8)
    assert metrics.candidate_conservation_rate == pytest.approx(0.95)
    assert metrics.first_pass_completion_rate == pytest.approx(0.6)
    assert metrics.reliable_decision_availability == pytest.approx(0.75)


def test_full_snapshot_is_complete_only_after_terminal_reconciliation_and_verification() -> None:
    work_items = [
        WorkItem("game-1", "LLP_TEAM_BETTING_ENGINE", WorkState.SCORED),
        WorkItem(
            "game-2",
            "LLP_TEAM_BETTING_ENGINE",
            WorkState.BLOCKED,
            blocker_codes=("RESEARCH_EVIDENCE_MISSING",),
        ),
    ]
    handoffs = [
        HandoffReceipt(
            work_item_id="game-2",
            from_owner="WOW_BETTING_INTELLIGENCE",
            to_owner="LLP_TEAM_BETTING_ENGINE",
            reason_code="TEAM_EVENT_PROBABILITY_REQUIRED",
            status=HandoffStatus.ACCEPTED,
            acceptance_target="governed terminal disposition",
            evidence_refs=("snapshot-1",),
        )
    ]
    snapshot = build_control_plane_snapshot(
        objective=_objective(),
        capabilities=[_capability()],
        work_items=work_items,
        handoffs=handoffs,
        acceptance=ProductAcceptance(
            objective_id="OBJ-LLP-001",
            criteria={
                "candidate_universe_complete": True,
                "exact_specialist_routing": True,
                "governed_probability_packages": True,
                "reconciliation_balanced": True,
                "independent_verification": True,
            },
            independent_verification=True,
        ),
    )
    assert snapshot["reconciliation"]["balanced"] is True
    assert snapshot["acceptance"]["product_state"] == ProductState.COMPLETE.value
    assert snapshot["can_execute"] is False
    assert snapshot["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_full_snapshot_cannot_self_certify_when_verification_missing() -> None:
    snapshot = build_control_plane_snapshot(
        objective=_objective(),
        capabilities=[_capability()],
        work_items=[WorkItem("game-1", "LLP_TEAM_BETTING_ENGINE", WorkState.SCORED)],
        handoffs=[],
        acceptance=ProductAcceptance(
            objective_id="OBJ-LLP-001",
            criteria={"candidate_universe_complete": True},
            independent_verification=False,
        ),
    )
    assert snapshot["acceptance"]["product_state"] == ProductState.WORKING_NOT_COMPLETE.value
    assert snapshot["acceptance"]["product_ready"] is False
