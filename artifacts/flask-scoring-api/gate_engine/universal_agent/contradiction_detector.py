"""
gate_engine/universal_agent/contradiction_detector.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B2

Deterministic cross-role contradiction detection.

Four detection rules are applied over the accepted role results produced by the
orchestrator. Each rule is a pure function; detect_contradictions() is the public
entry point and returns a deterministically-sorted tuple of ContradictionRecord.

Rules
-----
RULE-1  PLAYER-OUT-POSITIVE-ASSESSMENT
        NEWS_STATUS.player_status == "OUT" but SPORT_SPECIALIST reports a
        statistical_assessment that is not None/UNKNOWN/MISSING/NEGATIVE_OUTLOOK.

RULE-2  STALE-DATA-LINE-CONFIRMED
        DATA_SLATE_INTEGRITY.data_freshness_status == "STALE" but
        MARKET_EXACT_LINE.line_confirmed == True.

RULE-3  FAILURE-HIGH-SEVERITY-REPORTED
        FAILURE_CONTRADICTION reports contradiction_detected == True with
        contradiction_severity == "HIGH".  Surfaced as a record so the bundle
        assembler reflects it in bundle_status.

RULE-4  FINAL-REFRESH-COMPLETE-WITH-MISSING-ROLES
        FINAL_REFRESH.all_roles_completed == True but the orchestrator detected
        one or more missing/failed roles.

Design
------
- Pure function: no I/O, no randomness, no side effects.
- Deterministic: returns a tuple sorted by rule_id so repeated calls on the
  same inputs always produce the same sequence.
- Only ACCEPTED (or SKIPPED_RESUMED) roles contribute to rule evaluation.
- No app.py import, no live API call, no Weather code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


# ── Contradiction record ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContradictionRecord:
    """
    One detected cross-role contradiction.

    rule_id        — machine-readable rule identifier (used for sorting).
    description    — human-readable explanation.
    roles_involved — sorted tuple of role_ids involved.
    severity       — "HIGH" | "MEDIUM" | "LOW".
    """
    rule_id:        str
    description:    str
    roles_involved: tuple
    severity:       str

    def to_dict(self) -> dict:
        return {
            "rule_id":        self.rule_id,
            "description":    self.description,
            "roles_involved": list(self.roles_involved),
            "severity":       self.severity,
        }


# ── Internal helpers ───────────────────────────────────────────────────────────

def _get_findings(results_by_role: dict, role_id: str) -> Optional[dict]:
    """
    Return advisory_findings dict for an effectively-accepted role, or None.
    Only ACCEPTED roles (not SKIPPED_RESUMED — they have no in-memory findings)
    contribute to contradiction detection.
    """
    from gate_engine.universal_agent.role_runner import RoleRunnerStatus
    r = results_by_role.get(role_id)
    if r is None:
        return None
    if r.status != RoleRunnerStatus.ACCEPTED:
        return None
    return r.advisory_findings or {}


# ── Rule implementations ───────────────────────────────────────────────────────

def _rule_player_out_positive_assessment(
    results_by_role: dict,
) -> Optional[ContradictionRecord]:
    """
    NEWS_STATUS reports player_status == "OUT" but SPORT_SPECIALIST has a
    statistical_assessment value that is not UNKNOWN / MISSING /
    NEGATIVE_OUTLOOK / None.

    A dict-typed statistical_assessment (the normal case) always triggers this
    rule when player_status == "OUT", because a dict is never equal to the
    exclusion strings.
    """
    ns = _get_findings(results_by_role, "NEWS_STATUS")
    ss = _get_findings(results_by_role, "SPORT_SPECIALIST")
    if ns is None or ss is None:
        return None
    player_status = ns.get("player_status", "UNKNOWN")
    assessment    = ss.get("statistical_assessment")
    if player_status == "OUT" and assessment not in (
        None, "UNKNOWN", "MISSING", "NEGATIVE_OUTLOOK",
    ):
        return ContradictionRecord(
            rule_id="RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT",
            description=(
                "NEWS_STATUS reports player_status=OUT but SPORT_SPECIALIST "
                f"provides statistical_assessment={assessment!r}. "
                "A player marked OUT should not have a positive/present assessment."
            ),
            roles_involved=("NEWS_STATUS", "SPORT_SPECIALIST"),
            severity="HIGH",
        )
    return None


def _rule_stale_data_line_confirmed(
    results_by_role: dict,
) -> Optional[ContradictionRecord]:
    """
    DATA_SLATE_INTEGRITY reports data_freshness_status == "STALE" but
    MARKET_EXACT_LINE reports line_confirmed == True.
    A confirmed line cannot be trusted when the underlying data is stale.
    """
    dsi = _get_findings(results_by_role, "DATA_SLATE_INTEGRITY")
    mel = _get_findings(results_by_role, "MARKET_EXACT_LINE")
    if dsi is None or mel is None:
        return None
    freshness      = dsi.get("data_freshness_status", "UNKNOWN")
    line_confirmed = mel.get("line_confirmed")
    if freshness == "STALE" and line_confirmed is True:
        return ContradictionRecord(
            rule_id="RULE-2-STALE-DATA-LINE-CONFIRMED",
            description=(
                "DATA_SLATE_INTEGRITY reports data_freshness_status=STALE but "
                "MARKET_EXACT_LINE reports line_confirmed=True. "
                "A confirmed line cannot be trusted when underlying data is stale."
            ),
            roles_involved=("DATA_SLATE_INTEGRITY", "MARKET_EXACT_LINE"),
            severity="MEDIUM",
        )
    return None


def _rule_failure_high_severity(
    results_by_role: dict,
) -> Optional[ContradictionRecord]:
    """
    FAILURE_CONTRADICTION reports contradiction_detected == True with
    contradiction_severity == "HIGH".
    Surfaced as a ContradictionRecord so the bundle assembler can reflect
    HIGH severity in bundle_status.
    """
    fc = _get_findings(results_by_role, "FAILURE_CONTRADICTION")
    if fc is None:
        return None
    contradiction_detected = fc.get("contradiction_detected")
    severity               = fc.get("contradiction_severity", "NONE")
    if contradiction_detected is True and severity == "HIGH":
        return ContradictionRecord(
            rule_id="RULE-3-FAILURE-HIGH-SEVERITY",
            description=(
                "FAILURE_CONTRADICTION role detected a contradiction with "
                "severity=HIGH. Resolution is required before this evidence "
                "bundle can be acted upon."
            ),
            roles_involved=("FAILURE_CONTRADICTION",),
            severity="HIGH",
        )
    return None


def _rule_final_refresh_complete_with_missing(
    results_by_role: dict,
    missing_role_ids: Sequence[str],
) -> Optional[ContradictionRecord]:
    """
    FINAL_REFRESH claims all_roles_completed == True but the orchestrator
    detected one or more missing or failed roles.
    """
    fr = _get_findings(results_by_role, "FINAL_REFRESH")
    if fr is None:
        return None
    all_completed = fr.get("all_roles_completed")
    if all_completed is True and len(missing_role_ids) > 0:
        missing_sorted = sorted(missing_role_ids)
        return ContradictionRecord(
            rule_id="RULE-4-FINAL-REFRESH-COMPLETE-WITH-MISSING-ROLES",
            description=(
                f"FINAL_REFRESH claims all_roles_completed=True but the "
                f"orchestrator detected missing/failed roles: {missing_sorted}. "
                f"FINAL_REFRESH was called with incomplete evidence."
            ),
            roles_involved=("FINAL_REFRESH",),
            severity="MEDIUM",
        )
    return None


# ── Public entry point ─────────────────────────────────────────────────────────

def detect_contradictions(
    results_by_role: dict,
    missing_role_ids: Sequence[str] = (),
) -> tuple:
    """
    Run all four contradiction detection rules over accepted role results.

    Parameters
    ----------
    results_by_role
        dict[role_id str → RoleResult].  Only ACCEPTED entries contribute.
    missing_role_ids
        Sequence of role_id strings with no result (never called) or
        non-ACCEPTED status — passed to Rule 4.

    Returns
    -------
    tuple of ContradictionRecord, sorted by rule_id for determinism.
    Same inputs always produce the same output in the same order.

    Pure function: no I/O, no side effects, no randomness.
    """
    candidates = [
        _rule_player_out_positive_assessment(results_by_role),
        _rule_stale_data_line_confirmed(results_by_role),
        _rule_failure_high_severity(results_by_role),
        _rule_final_refresh_complete_with_missing(results_by_role, missing_role_ids),
    ]
    found = [c for c in candidates if c is not None]
    return tuple(sorted(found, key=lambda c: c.rule_id))
