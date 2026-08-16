"""
gate_engine/universal_agent/model_validation/validation_wrapper.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Model Validation Wrapper for B4 adapter results.

Wraps a WnbaPropsAdapterResult (or any compatible result dict) and
assembles a ValidatedAdapterResult containing:
  - Immutable run manifest entry
  - Calibration scoreboard snapshot
  - Drift summary
  - Health state
  - Promotion gate evaluation
  - Full validation summary dict

All outputs are advisory only. Nothing here alters production probabilities,
changes terminal labels, or triggers any live-market action.

B4 completion status (all three blocking items resolved — 2026-08-16):
  FOLLOWUP_193: Pipeline State Separation — DONE (pipeline_state.py + pipeline_gateway.py wired)
  FOLLOWUP_194: Settlement Worker Reliability — DONE (backoff/heartbeat in settlement_worker.py)
  FOLLOWUP_195: Full-Pipeline Integration Fixtures — DONE (test_b4_full_pipeline_integration.py,
                test_b4_pipeline_state_wired.py, test_pipeline_state.py, test_settlement_reliability.py)

can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

can_execute           = False
PRODUCTION_AUTHORITY  = False
USER_OUTPUT_AUTHORITY = False
CAPITAL_AUTHORITY     = False
EXECUTION_RULE        = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
PATCH_ID              = "WOW-PATCH-2026-08-11-MODELVAL"


@dataclass(frozen=True)
class ValidatedAdapterResult:
    """
    B4 adapter result augmented with model-validation metadata.

    adapter_result:      The original WnbaPropsAdapterResult (or dict representation).
    run_manifest:        Immutable provenance dict for this validation run.
    validation_summary:  Full validation summary (calibration, drift, health, gate).
    promotion_status:    "APPROVED" | "PENDING" | "BLOCKED".
    validated_at:        ISO-8601 timestamp.
    patch_id:            Version identifier.
    """
    adapter_result:    Any    # WnbaPropsAdapterResult or dict
    run_manifest:      dict
    validation_summary: dict
    promotion_status:  str
    validated_at:      str
    patch_id:          str


class ModelValidationWrapper:
    """
    Assembles a ValidatedAdapterResult from a B4 adapter result and
    optional validation component outputs.

    All components are optional — missing ones produce UNKNOWN/null entries
    rather than failing. Can_execute = False throughout.
    """

    def wrap(
        self,
        *,
        adapter_result:       Any,
        run_id:               str,
        model_id:             str,
        model_version:        str         = "v1.0",
        stat_key:             str         = "unknown",
        sport:                str         = "WNBA",
        feature_snapshot_ids: list[str]   | None = None,
        params:               dict        | None = None,
        brier_score:          float | None = None,
        n_settled:            int         = 0,
        drift_status:         str         = "UNKNOWN",
        health_state:         str         = "UNKNOWN",
        calibration_threshold: float      = 0.20,
        min_settled_required:  int        = 50,
        governance_approved:   bool       = False,
        manual_sign_off:       bool       = False,
    ) -> ValidatedAdapterResult:
        """
        Build a ValidatedAdapterResult.

        Parameters
        ----------
        adapter_result        B4 adapter result object or dict.
        run_id                Unique run identifier (propagated to manifest).
        model_id              Logical model name.
        model_version         Semver string.
        stat_key              Stat targeted by this model.
        sport                 Sport this model applies to.
        feature_snapshot_ids  Snapshot IDs used as inputs.
        params                Model parameters for provenance hash.
        brier_score           Current Brier score for calibration check.
        n_settled             Number of settled observations.
        drift_status          DriftMonitor.compute_drift().overall_status.
        health_state          ModelHealthStateMachine.state.
        calibration_threshold Brier score ceiling for threshold check.
        min_settled_required  Minimum settled obs for promotion gate.
        governance_approved   Explicit external approval (never auto-set).
        manual_sign_off       Explicit human sign-off (never auto-set).
        """
        from gate_engine.universal_agent.model_validation.model_manifest import (
            ModelManifest,
        )
        from gate_engine.universal_agent.model_validation.promotion_gate import (
            PromotionGate, PromotionStatus,
        )

        ts = datetime.now(timezone.utc).isoformat()

        # Build run manifest
        manifest = ModelManifest()
        entry = manifest.record(
            run_id=run_id,
            model_id=model_id,
            model_version=model_version,
            params=params or {},
            feature_snapshot_ids=feature_snapshot_ids or [],
            stat_key=stat_key,
            sport=sport,
            created_at=ts,
        )

        # Evaluate promotion gate
        calib_ok = (brier_score is not None and brier_score < calibration_threshold)
        drift_ok = drift_status in ("NOMINAL", "WARNING")  # ALERT blocks promotion
        health_ok = health_state in ("HEALTHY", "DEGRADED")
        settled_ok = n_settled >= min_settled_required

        gate = PromotionGate()
        decision = gate.evaluate(
            model_id=model_id,
            calibration_threshold_met=calib_ok,
            drift_acceptable=drift_ok,
            health_state_ok=health_ok,
            n_settled_sufficient=settled_ok,
            manual_sign_off=manual_sign_off,
            governance_approved=governance_approved,
        )

        run_manifest_dict = {
            "run_id":                entry.run_id,
            "model_id":              entry.model_id,
            "model_version":         entry.model_version,
            "param_hash":            entry.param_hash,
            "feature_snapshot_ids":  list(entry.feature_snapshot_ids),
            "stat_key":              entry.stat_key,
            "sport":                 entry.sport,
            "created_at":            entry.created_at,
        }

        validation_summary = {
            "model_id":              model_id,
            "brier_score":           brier_score,
            "n_settled":             n_settled,
            "drift_status":          drift_status,
            "health_state":          health_state,
            "promotion_status":      decision.status,
            "promotion_checklist":   decision.checklist,
            "blocking_items":        decision.blocking_items,
            "governance_approved":   governance_approved,
            "no_auto_promotion":     True,
            "ceiling":               "MODEL_QUALIFIED_HOLD",
            "can_execute":           False,
            "completed_blockers": [
                "FOLLOWUP_193: Pipeline State Separation + Scoped DATA_CONTRACT_FAIL — RESOLVED 2026-08-16 (pipeline_state.py + pipeline_gateway.py)",
                "FOLLOWUP_194: Settlement Worker Reliability — RESOLVED 2026-08-16 (backoff/heartbeat in settlement_worker.py)",
                "FOLLOWUP_195: Full-Pipeline Integration Fixtures — RESOLVED 2026-08-16 (test_b4_full_pipeline_integration.py, test_b4_pipeline_state_wired.py)",
            ],
        }

        return ValidatedAdapterResult(
            adapter_result=adapter_result,
            run_manifest=run_manifest_dict,
            validation_summary=validation_summary,
            promotion_status=decision.status,
            validated_at=ts,
            patch_id=PATCH_ID,
        )
