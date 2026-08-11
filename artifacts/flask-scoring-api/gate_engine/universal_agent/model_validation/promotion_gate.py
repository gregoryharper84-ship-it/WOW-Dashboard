"""
gate_engine/universal_agent/model_validation/promotion_gate.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Promotion / Rollback Gate.

Checklist-based gate controlling whether a challenger model may be promoted
to champion status.

ALL five checklist items must be True AND governance_approved must be an
explicit external True (not inferred, not computed) before APPROVED is returned.

Checklist items
---------------
  calibration_threshold_met   Brier score below the family's threshold.
  drift_acceptable            No ALERT-level feature drift detected.
  health_state_ok             Model health is HEALTHY or DEGRADED (operational).
  n_settled_sufficient        Minimum number of settled observations reached.
  manual_sign_off             Human reviewer has explicitly signed off.

governance_approved
  Must be explicitly set to True by the caller (external action).
  This module NEVER generates, infers, or auto-sets governance_approved.
  If False or absent → PENDING or BLOCKED depending on checklist state.

Status
------
  APPROVED   ALL 5 checklist items True AND governance_approved=True.
  PENDING    All or most checklist items True but governance_approved=False/missing.
  BLOCKED    One or more checklist items False.

Rollback gate (rollback_allowed)
  Returns True only when:
    - The challenger being rolled back has a manifest entry (provenance exists).
    - The current champion exists (there is a model to roll back to).
    - governance_approved=True for the rollback action.

can_execute = False — NO_AUTO_PROMOTION is unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

can_execute       = False
NO_AUTO_PROMOTION = True
EXECUTION_RULE    = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

MIN_SETTLED_DEFAULT = 50


class PromotionStatus:
    APPROVED = "APPROVED"
    PENDING  = "PENDING"
    BLOCKED  = "BLOCKED"


@dataclass(frozen=True)
class PromotionDecision:
    """
    Result of a promotion gate evaluation.

    status:              APPROVED | PENDING | BLOCKED.
    checklist:           {item_name: bool} for all 5 items.
    governance_approved: bool — always supplied by caller, never inferred.
    all_checklist_pass:  True when all 5 items are True.
    blocking_items:      List of checklist items that are False.
    evaluated_at:        ISO-8601.
    model_id:            Challenger being evaluated.
    """
    status:             str
    checklist:          dict[str, bool]
    governance_approved: bool
    all_checklist_pass: bool
    blocking_items:     list[str]
    evaluated_at:       str
    model_id:           str


class PromotionGate:
    """
    Advisory-only promotion gate.
    can_execute = False. NO_AUTO_PROMOTION = True.
    """

    def evaluate(
        self,
        *,
        model_id:                 str,
        calibration_threshold_met: bool,
        drift_acceptable:          bool,
        health_state_ok:           bool,
        n_settled_sufficient:      bool,
        manual_sign_off:           bool,
        governance_approved:       bool,
    ) -> PromotionDecision:
        """
        Evaluate the promotion checklist.

        governance_approved MUST be provided explicitly by the caller.
        This gate never generates, infers, or auto-sets it.

        Returns PromotionDecision.
        """
        checklist = {
            "calibration_threshold_met": calibration_threshold_met,
            "drift_acceptable":          drift_acceptable,
            "health_state_ok":           health_state_ok,
            "n_settled_sufficient":      n_settled_sufficient,
            "manual_sign_off":           manual_sign_off,
        }
        blocking = [k for k, v in checklist.items() if not v]
        all_pass = not blocking

        if not all_pass:
            status = PromotionStatus.BLOCKED
        elif not governance_approved:
            status = PromotionStatus.PENDING
        else:
            status = PromotionStatus.APPROVED

        return PromotionDecision(
            status=status,
            checklist=checklist,
            governance_approved=governance_approved,
            all_checklist_pass=all_pass,
            blocking_items=blocking,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            model_id=model_id,
        )

    def evaluate_rollback(
        self,
        *,
        model_id:            str,
        challenger_has_manifest: bool,
        champion_exists:     bool,
        governance_approved: bool,
    ) -> dict[str, Any]:
        """
        Evaluate whether a rollback is allowed.

        Requires governance_approved=True (external) + manifest + champion.
        Never auto-approves.
        """
        rollback_allowed = (
            challenger_has_manifest
            and champion_exists
            and governance_approved
        )
        return {
            "model_id":             model_id,
            "rollback_allowed":     rollback_allowed,
            "challenger_has_manifest": challenger_has_manifest,
            "champion_exists":      champion_exists,
            "governance_approved":  governance_approved,
            "no_auto_rollback":     True,
            "can_execute":          False,
            "evaluated_at":         datetime.now(timezone.utc).isoformat(),
        }
