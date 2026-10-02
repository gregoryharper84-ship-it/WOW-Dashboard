from __future__ import annotations

from v17.learning_receipts import (
    Applicability,
    ChallengerGate,
    DiscreditedApproach,
    EngineeringLearningPayload,
    LearningReceipt,
    ModelLearningPayload,
    ReceiptSource,
    ReceiptValidity,
    challenger_gate_reasons,
    hydrate_receipts,
    mark_validity,
    validate_receipt,
)


def engineering_receipt(
    receipt_id: str = "LR-ENG-1",
    *,
    subsystem: str = "candidate_reconciliation",
    error_fingerprint: str | None = "PROP_EVENT_IDENTITY_CONFLICT",
    sport_code: str | None = "NFL",
    validity: str = "ACTIVE",
) -> LearningReceipt:
    return LearningReceipt(
        receipt_id=receipt_id,
        receipt_type="ENGINEERING",
        schema_version="1.0",
        created_at="2026-10-02T16:00:00Z",
        source=ReceiptSource(
            ticket_id="#823",
            terminal_state="FIXED_AND_VERIFIED",
            repository="gregoryharper84-ship-it/WOW-Dashboard",
            commit_sha="abc123",
            agent_role="QA_VERIFICATION_AGENT",
        ),
        applicability=Applicability(
            subsystem=subsystem,
            error_fingerprint=error_fingerprint,
            sport_code=sport_code,
            component_paths=("artifacts/wow-engine/v17/example.py",),
            dependency_versions=(("provider_schema", "v2"),),
            target_code_hash="hash-a",
            ast_fingerprint="ast-a",
        ),
        validity=ReceiptValidity(status=validity),
        evidence_refs=("test://identity-conflict",),
        payload=EngineeringLearningPayload(
            observed_failure="canonical event id mismatch",
            expected_behavior="all source rows reconcile exactly once",
            root_cause="display UUID used instead of canonical event identity",
            corrective_change="resolve canonical event identity before scoring",
            reproduction=("submit frozen NFL prop manifest",),
            validation=("typed failure preserved", "no silent row loss"),
            regression_protection=("canonical identity regression test",),
            failure_fingerprints=("PROP_EVENT_IDENTITY_CONFLICT",),
            discredited_approaches=(
                DiscreditedApproach(
                    strategy="normalize to MODEL_UNAVAILABLE",
                    result="REJECTED",
                    reason="destroys typed failure ownership",
                    patch_sha="deadbeef",
                ),
            ),
        ),
    )


def model_receipt() -> LearningReceipt:
    return LearningReceipt(
        receipt_id="LR-MODEL-1",
        receipt_type="MODEL",
        schema_version="1.0",
        created_at="2026-10-02T16:00:00Z",
        source=ReceiptSource(
            ticket_id="EXP-NFL-V2",
            terminal_state="EXPERIMENT_CREATED",
            repository="gregoryharper84-ship-it/WOW-Dashboard",
            commit_sha="def456",
        ),
        applicability=Applicability(
            subsystem="nfl_probability_model",
            sport_code="NFL",
            target_code_hash="model-hash",
        ),
        validity=ReceiptValidity(status="ACTIVE"),
        evidence_refs=("replay://nfl-v2",),
        payload=ModelLearningPayload(
            sport="NFL",
            controlling_specialist="NFL_FITTED_SPECIALIST",
            hypothesis="candidate feature improves discrimination without calibration loss",
            cohort_definition="settled NFL holdout",
            sample_size=240,
            effect_size=0.035,
            uncertainty={"ci": [0.01, 0.06]},
            baseline_metrics={"brier": 0.22},
            observed_metrics={"brier": 0.21},
            calibration_analysis={"status": "DRIFTED"},
            governance_disposition={
                "class": "C",
                "status": "REJECTED",
                "production_behavior_changed": False,
                "can_execute": False,
            },
        ),
    )


def test_engineering_receipt_preserves_negative_knowledge() -> None:
    receipt = engineering_receipt()
    assert validate_receipt(receipt) == []
    approach = receipt.payload.discredited_approaches[0]
    assert approach.result == "REJECTED"
    assert approach.patch_sha == "deadbeef"
    assert receipt.can_execute is False
    assert receipt.terminal_authority == "V17_TERMINAL_REDUCER"


def test_metadata_gate_rejects_cross_subsystem_and_cross_sport_receipts() -> None:
    good = engineering_receipt("LR-GOOD")
    wrong_subsystem = engineering_receipt("LR-SUB", subsystem="deployment_runtime")
    wrong_sport = engineering_receipt("LR-SPORT", sport_code="MLB")
    stale = engineering_receipt("LR-STALE", validity="STALE_HISTORICAL")

    hydrated = hydrate_receipts(
        {
            "subsystem": "candidate_reconciliation",
            "error_fingerprint": "PROP_EVENT_IDENTITY_CONFLICT",
            "sport_code": "NFL",
            "component_paths": ["artifacts/wow-engine/v17/example.py"],
        },
        [wrong_subsystem, wrong_sport, stale, good],
    )
    assert [row.receipt_id for row in hydrated] == ["LR-GOOD"]


def test_semantic_layer_cannot_reintroduce_ineligible_receipts() -> None:
    exact = engineering_receipt("LR-EXACT")
    generic = engineering_receipt("LR-GENERIC", error_fingerprint=None)
    hydrated = hydrate_receipts(
        {
            "subsystem": "candidate_reconciliation",
            "error_fingerprint": "PROP_EVENT_IDENTITY_CONFLICT",
            "sport_code": "NFL",
        },
        [generic, exact],
    )
    assert [row.receipt_id for row in hydrated] == ["LR-EXACT"]


def test_ast_change_marks_receipt_stale_but_does_not_delete_it() -> None:
    receipt = engineering_receipt()
    validity = mark_validity(
        receipt,
        current_target_code_hash="hash-a",
        current_ast_fingerprint="ast-b",
        current_dependency_versions={"provider_schema": "v2"},
    )
    assert validity.status == "STALE_HISTORICAL"
    assert validity.invalidation_reason == "primary AST fingerprint changed"


def test_code_hash_change_is_suspect_before_ast_invalidation() -> None:
    validity = mark_validity(
        engineering_receipt(),
        current_target_code_hash="hash-b",
        current_ast_fingerprint="ast-a",
        current_dependency_versions={"provider_schema": "v2"},
    )
    assert validity.status == "SUSPECT_VERSION_DRIFT"


def test_dependency_version_change_marks_receipt_stale() -> None:
    validity = mark_validity(
        engineering_receipt(),
        current_target_code_hash="hash-a",
        current_ast_fingerprint="ast-a",
        current_dependency_versions={"provider_schema": "v3"},
    )
    assert validity.status == "STALE_HISTORICAL"
    assert "provider_schema" in (validity.invalidation_reason or "")


def test_model_receipt_cannot_claim_production_behavior_change() -> None:
    receipt = model_receipt()
    assert validate_receipt(receipt) == []

    bad_payload = ModelLearningPayload(
        **{
            **receipt.payload.__dict__,
            "governance_disposition": {
                "class": "C",
                "status": "PROMOTED",
                "production_behavior_changed": True,
                "can_execute": False,
            },
        }
    )
    bad = LearningReceipt(**{**receipt.__dict__, "payload": bad_payload})
    assert "model receipt must set production_behavior_changed=false" in validate_receipt(bad)


def test_challenger_gate_is_policy_supplied_not_universal_p_value() -> None:
    payload = model_receipt().payload
    gate = ChallengerGate(min_sample=200, min_effect_size=0.02)
    assert challenger_gate_reasons(
        payload,
        gate,
        uncertainty_bound_satisfied=True,
        repeatability_satisfied=True,
        multiple_test_adjustment_satisfied=True,
        temporal_stability_satisfied=True,
    ) == []

    strict_gate = ChallengerGate(min_sample=500, min_effect_size=0.05)
    reasons = challenger_gate_reasons(
        payload,
        strict_gate,
        uncertainty_bound_satisfied=False,
        repeatability_satisfied=False,
        multiple_test_adjustment_satisfied=False,
        temporal_stability_satisfied=False,
    )
    assert reasons == [
        "MIN_SAMPLE",
        "MIN_EFFECT_SIZE",
        "UNCERTAINTY_BOUND",
        "REPEATABILITY",
        "MULTIPLE_TEST_ADJUSTMENT",
        "TEMPORAL_STABILITY",
    ]


def test_model_receipt_preserves_v17_governance_boundary() -> None:
    receipt = model_receipt()
    assert receipt.payload.governance_disposition["status"] == "REJECTED"
    assert receipt.payload.governance_disposition["production_behavior_changed"] is False
    assert receipt.can_execute is False
    assert receipt.terminal_authority == "V17_TERMINAL_REDUCER"
