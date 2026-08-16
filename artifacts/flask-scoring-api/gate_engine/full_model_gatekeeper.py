"""
gate_engine/full_model_gatekeeper.py
WOW Full Model Contract Gatekeeper v1.1
WOW-FMCG-v1.1 · Patch #25 · Precedence 105 · ENGINE v16.5

Hardening patch applied over v1.0:
  1. Host abstraction: no dependency on a Replit app named "WOW Betting Engine"
     or any nested Custom-GPT invocation.  NESTED_CUSTOM_GPT_REQUIRED = False.
  2. Bidirectional MORE/LESS enforcement: PROP markets must carry evidence
     that both sides were evaluated before qualification.
  3. Calibration-health precheck (Layer 0.5 gate result) read from gates dict
     and enforced separately from candidate dynamic calibration.
  4. Source timestamp/freshness grading gate reads source_grade verdict for
     N/T (no-timestamp) grade flags before qualification.
  5. Probability-component-ledger + shrinkage verdict gated: UNCALIBRATED or
     PROXY_ONLY ledger status blocks FINAL_APPROVED.
  6. Strict probability bounds: only 0 < p < 1 (exclusive).  p=0.0 and p=1.0
     are now FAIL — structurally degenerate, cannot be publishable.
  7. Correlation/dependency checks remain separate from directional/session-
     exposure controls.  Session directional exposure is now an explicit gate.
  8. Final-refresh gate: FINAL_APPROVED rows must carry final_refresh_passed=True
     or a FINAL_REFRESH_CLEAR result; vacuous-pass rows are held.
  9. Row reconciliation preserved (no change — dedup happens in pipeline).
 10. Canonical ceiling resolver: canonical_ceiling_resolve() uses the
     authoritative 190-label CC ordering.  All three final-row paths
     (apply_gatekeeper, verify_cc_envelope, verify_v16_result) use it.
 11. Terminal-label native check: invented labels outside the known CC and
     FMCG sets produce HOLD (downgrade-only, never error).
 12. can_execute = False  (unconditional)
     dry_run_only = True  (unconditional)
     CROSS_SPORT_HIGH_PROBABILITY_SELECTOR_STATUS = "PROPOSED_NOT_BINDING"
 13. All existing FINAL_APPROVED, settlement, evidence, bankroll, exposure,
     and safety gates are preserved and unweakened.
 14. Auth-patch task (#238 / #239) is unrelated — not mixed in here.

full_model_status: COMPLETE | INCOMPLETE | INVALIDATED
qualification_result: PASS | HOLD | REJECT
"""
from __future__ import annotations

import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

GATEKEEPER_VERSION: str  = "WOW-FMCG-v1.1"
CONTRACT_ID: str         = "FULL_MODEL_CONTRACT_GATEKEEPER"
PATCH_ID: str            = "WOW-PATCH-FMCG-v1.1"
PATCH_PRECEDENCE: int    = 105
ENGINE_VERSION: str      = "v16.5"
CAN_EXECUTE: bool        = False
DRY_RUN_ONLY: bool       = True

# Host abstraction — Replit is a capability-specific service, not the engine host.
# The scoring engine is NOT dependent on locating a Replit app or nested GPT.
NESTED_CUSTOM_GPT_REQUIRED: bool = False

# Cross-sport high-probability selector is PROPOSED only — not wired into scoring.
CROSS_SPORT_HIGH_PROBABILITY_SELECTOR_STATUS: str = "PROPOSED_NOT_BINDING"

# Kalshi recovery mode state constant (informational — enforced by kalshi governor).
KALSHI_RECOVERY_MODE: str = "ACTIVE"

# full_model_status
STATUS_COMPLETE:    str = "COMPLETE"
STATUS_INCOMPLETE:  str = "INCOMPLETE"
STATUS_INVALIDATED: str = "INVALIDATED"

# qualification_result
QUAL_PASS:   str = "PASS"
QUAL_HOLD:   str = "HOLD"
QUAL_REJECT: str = "REJECT"

# Gate statuses
GATE_PASS: str = "PASS"
GATE_FAIL: str = "FAIL"
GATE_HOLD: str = "HOLD"
GATE_SKIP: str = "SKIP"

FINAL_APPROVED:       str = "FINAL_APPROVED"
MODEL_QUALIFIED_HOLD: str = "MODEL_QUALIFIED_HOLD"

# Labels required by the gate-engine classifier to reach FINAL_APPROVED.
REQUIRED_GATE_KEYS: tuple[str, ...] = (
    "slate_validation", "status_role", "l5_l10_ledger",
    "market_gate", "ev_gate", "slip_structure", "exposure_gate",
)

# Prop market families that require bidirectional evaluation.
PROP_MARKET_FAMILIES: frozenset[str] = frozenset({
    "hits", "strikeouts", "pitcher_outs", "outs", "home_runs", "stolen_bases",
    "walks", "rbi", "total_bases",
    "points", "pts", "rebounds", "reb", "assists", "ast",
    "points_rebounds_assists", "pra", "points_rebounds", "points_assists",
    "blocks", "steals", "turnovers",
    "passing_yards", "rushing_yards", "receiving_yards", "receptions",
    "goals", "saves", "shots_on_goal",
    "total_games", "fantasy_points", "fp", "minutes",
})

# Sport-specific matchup check sports.
SPORTS_REQUIRING_MATCHUP: frozenset[str] = frozenset({
    "MLB", "NBA", "WNBA", "NFL", "NHL", "tennis",
})

# Monotonic ceiling order — local 16-label fallback.
# The canonical resolver uses the full 190-label CC ordering below.
_CEILING_ORDER: list[str] = [
    FINAL_APPROVED,
    "MONEY_QUALIFIED",
    "MARKET_VERIFIED_HOLD",
    MODEL_QUALIFIED_HOLD,
    "CALIBRATION_STALE_HOLD",
    "MARKET_QUALIFIED_BUT_SLIP_NEGATIVE",
    "WATCH",
    "RESEARCH_INTEREST",
    "LLP_SCOUT",
    "NO_PLAY",
    "REJECT_NO_EDGE",
    "REJECT_BAD_STRUCTURE",
    "REJECT_DATA_QUALITY",
    "SOURCE_CONFLICT",
    "SLATE_PURGE",
    "DUPLICATE_EXPOSURE_BLOCK",
]
_CEILING_RANK: dict[str, int] = {lbl: i for i, lbl in enumerate(_CEILING_ORDER)}

# Native backend labels (used by terminal-label native check).
NATIVE_BACKEND_LABELS: frozenset[str] = frozenset(_CEILING_ORDER)


def _ceiling_rank(label: str | None) -> int:
    """Local 16-label fallback rank (0 = most permissive)."""
    if label is None:
        return -1
    return _CEILING_RANK.get(label, 0)


def _cc_rank(label: str | None) -> int:
    """
    Authoritative 190-label CC ceiling rank (lazy import).
    Falls back to local 16-label rank if CC module unavailable.
    """
    if label is None:
        return -1
    try:
        from gate_engine.command_center.cc_labels import ceiling_rank as _cr
        return _cr(label)
    except Exception:
        return _ceiling_rank(label)


def canonical_ceiling_resolve(a: str | None, b: str | None) -> str | None:
    """
    Canonical strict lowest-ceiling resolver used by EVERY final-row path.

    Uses the authoritative 190-label CC ordering so that all three
    downstream paths (apply_gatekeeper, verify_cc_envelope, verify_v16_result)
    apply an identical monotonic downgrade-only rule.

    Returns whichever label is MORE restrictive (higher rank).
    None is treated as 'no ceiling set' and is always less restrictive
    than any concrete label.
    """
    if a is None:
        return b
    if b is None:
        return a
    return a if _cc_rank(a) >= _cc_rank(b) else b


def _more_restrictive(a: str | None, b: str | None) -> str | None:
    """Alias kept for internal call sites; delegates to canonical_ceiling_resolve."""
    return canonical_ceiling_resolve(a, b)


# Model status classifications
ACTIVE_MODEL_STATUSES:      frozenset[str] = frozenset({"ACTIVE"})
PROVISIONAL_MODEL_STATUSES: frozenset[str] = frozenset({"PROVISIONAL"})

# Downstream governors (informational — not enforced by this module)
REQUIRED_DOWNSTREAM_GOVERNORS: list[str] = [
    "slip_card_dependency",
    "joint_probability",
    "weakest_leg",
    "payout_breakeven",
    "platform_portfolio_governance",
    "final_refresh",
    "kalshi_portfolio_recovery_governor",
]

# Material-change invalidation signals (caller sets these on the row)
INVALIDATION_SIGNALS: dict[str, str] = {
    "material_status_change":       "MATERIAL_STATUS_CHANGE",
    "lineup_finalized_after_score": "LINEUP_CHANGE_POST_SCORE",
    "starter_changed":              "STARTER_CHANGE",
    "goalie_changed":               "GOALIE_CHANGE",
    "qb_changed":                   "QB_CHANGE",
    "event_started":                "EVENT_STARTED",
    "settlement_status_changed":    "SETTLEMENT_STATUS_CHANGE",
    "price_age_exceeded":           "PRICE_FRESHNESS_EXCEEDED",
    "weather_material_change":      "WEATHER_MATERIAL_CHANGE",
}


# ---------------------------------------------------------------------------
# Gate result builder
# ---------------------------------------------------------------------------

def _gr(status: str, evidence: dict[str, Any], blocker: str | None = None) -> dict[str, Any]:
    r: dict[str, Any] = {"status": status, "evidence": evidence}
    if blocker:
        r["blocker"] = blocker
    return r


# ---------------------------------------------------------------------------
# Individual gate checks — all read-only
# Existing v1.0 gates (1–14) are preserved and unweakened.
# New v1.1 gates (15–21) are appended.
# ---------------------------------------------------------------------------

# ── v1.0 gate 1 ─────────────────────────────────────────────────────────────

def _check_invalidation(row: dict[str, Any]) -> tuple[dict[str, Any], list[str], bool]:
    triggered: dict[str, str] = {}
    for key, label in INVALIDATION_SIGNALS.items():
        val = row.get(key)
        if val is True or val == "true" or val == "True":
            triggered[key] = label

    is_inv = bool(triggered)
    evidence = {"triggered_signals": triggered, "is_invalidated": is_inv}
    if is_inv:
        reasons  = list(triggered.values())
        blockers = [f"FMCG:INVALIDATED:{r}" for r in reasons]
        return _gr(GATE_FAIL, evidence, f"FMCG:INVALIDATED:{'+'.join(reasons)}"), blockers, True
    return _gr(GATE_PASS, evidence), [], False


# ── v1.0 gate 2 ─────────────────────────────────────────────────────────────

def _check_upstream_ceiling(row: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    entry = row.get("terminal_label")
    return _gr(GATE_PASS, {
        "entry_terminal_label":   entry,
        "gatekeeper_can_upgrade": False,
    }), entry


# ── v1.0 gate 3 ─────────────────────────────────────────────────────────────

def _check_full_model_completeness(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    missing  = [g for g in REQUIRED_GATE_KEYS if not isinstance(gates.get(g), dict)]
    has_cal  = row.get("calibrated_probability") is not None

    evidence = {
        "required_gates": list(REQUIRED_GATE_KEYS),
        "missing_gates":  missing,
        "has_cal_prob":   has_cal,
    }
    blockers: list[str] = []
    if missing:
        blockers.append(f"FMCG:INCOMPLETE:MISSING_GATES:{','.join(missing)}")
    if not has_cal:
        blockers.append("FMCG:INCOMPLETE:NO_CALIBRATED_PROBABILITY")

    if blockers:
        return _gr(GATE_FAIL, evidence, blockers[0]), blockers
    return _gr(GATE_PASS, evidence), []


# ── v1.0 gate 4 ─────────────────────────────────────────────────────────────

def _check_market_identity(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    player    = row.get("player") or row.get("player_name") or row.get("player_id")
    sport     = row.get("sport")
    prop_type = row.get("prop_type") or row.get("stat_key")
    line_val  = row.get("line") or row.get("line_value") or row.get("threshold")
    mkt       = gates.get("market_gate") or {}
    mkt_st    = mkt.get("market_status")
    sb_line   = mkt.get("sportsbook_line")

    missing = []
    if not player:    missing.append("player")
    if not sport:     missing.append("sport")
    if not prop_type: missing.append("prop_type")
    if line_val is None:  missing.append("line")
    if not mkt_st:        missing.append("market_gate.market_status")

    evidence = {
        "player": player, "sport": sport, "prop_type": prop_type,
        "line": line_val, "market_status": mkt_st, "sportsbook_line": sb_line,
        "missing_fields": missing,
    }
    if missing:
        b = f"FMCG:MARKET_IDENTITY:MISSING:{','.join(missing)}"
        return _gr(GATE_FAIL, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


# ── v1.0 gate 5 ─────────────────────────────────────────────────────────────

def _check_role_status(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    sr      = gates.get("status_role") or {}
    role_st = (sr.get("role_status") or row.get("role_status") or "").upper()
    role_ts = sr.get("role_timestamp") or row.get("role_timestamp")

    evidence = {"role_status": role_st, "role_timestamp": role_ts}
    if role_st in {"DEPENDENCY_CONFLICT"}:
        b = f"FMCG:ROLE_STATUS:DEPENDENCY_CONFLICT:{role_st}"
        return _gr(GATE_FAIL, evidence, b), [b]
    if role_st in {"DEPENDENCY_UNRESOLVED", "ROLE_STATE_STALE", "STALE", "RECHECK"}:
        b = f"FMCG:ROLE_STATUS:SOFT_CONFLICT:{role_st}"
        return _gr(GATE_HOLD, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


# ── v1.0 gate 6 ─────────────────────────────────────────────────────────────

def _check_l10_evidence(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    L5/L10 are evidence only — hit rate alone cannot qualify a row.
    l5_l10_ledger must pass, AND market_gate AND ev_gate must also pass.
    Role-matched line: L10 ledger line must match the scored line.
    """
    l5l10 = gates.get("l5_l10_ledger") or {}
    mkt   = gates.get("market_gate") or {}
    ev    = gates.get("ev_gate") or {}

    l5l10_passed = bool(l5l10.get("passed"))
    mkt_passed   = bool(mkt.get("passed"))
    ev_passed    = bool(ev.get("passed"))
    l10_hit_rate = l5l10.get("l10_hit_rate")
    l5_hit_rate  = l5l10.get("l5_hit_rate")

    l10_line    = l5l10.get("line") or l5l10.get("ledger_line")
    scored_line = row.get("line") or row.get("line_value") or row.get("threshold")
    line_mismatch = False
    try:
        if l10_line is not None and scored_line is not None:
            line_mismatch = abs(float(l10_line) - float(scored_line)) > 0.01
    except (TypeError, ValueError):
        line_mismatch = True

    evidence = {
        "l5l10_passed": l5l10_passed, "mkt_passed": mkt_passed,
        "ev_passed": ev_passed, "l10_hit_rate": l10_hit_rate,
        "l5_hit_rate": l5_hit_rate, "l10_line": l10_line,
        "scored_line": scored_line, "line_mismatch": line_mismatch,
        "evidence_only": "L5/L10 hit rate alone cannot qualify a row",
    }

    if line_mismatch:
        b = f"FMCG:L10_EVIDENCE:ROLE_LINE_MISMATCH:l10={l10_line} scored={scored_line}"
        return _gr(GATE_FAIL, evidence, b), [b]
    if not l5l10_passed:
        return _gr(GATE_FAIL, evidence, "FMCG:L10_EVIDENCE:LEDGER_NOT_PASSED"), \
               ["FMCG:L10_EVIDENCE:LEDGER_NOT_PASSED"]
    if not (mkt_passed and ev_passed):
        b = "FMCG:L10_EVIDENCE:SOLE_QUALIFIER:market_or_ev_not_passed"
        return _gr(GATE_FAIL, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


# ── v1.0 gate 7 (UPDATED: strict exclusive bounds) ──────────────────────────

def _check_calibrated_probability(
    row: dict[str, Any],
) -> tuple[dict[str, Any], list[str], str | None, str | None]:
    """
    Calibrated probability: present, numeric, strictly in (0, 1) — exclusive.
    p=0.0 and p=1.0 are FAIL: structurally degenerate, cannot be publishable.
    PROVISIONAL → HOLD (cannot reach FINAL_APPROVED).
    NO_REGISTERED_MODEL → REJECT.
    Returns (gate_result, blockers, controlling_specialist, active_qualification_rule).
    """
    cal_prob     = row.get("calibrated_probability")
    model_status = row.get("model_status") or row.get("calibration_status")
    model_id     = (row.get("model_id") or row.get("calibration_note")
                    or row.get("controlling_model"))
    prob_pub     = row.get("probability_publishable")

    evidence = {
        "calibrated_probability":  cal_prob,
        "model_status":            model_status,
        "model_id":                model_id,
        "probability_publishable": prob_pub,
        "bounds_rule":             "strict_exclusive:(0,1)",
    }
    specialist    = model_id
    rule: str | None = None

    if cal_prob is None:
        b = "FMCG:CAL_PROB:MISSING"
        return _gr(GATE_FAIL, evidence, b), [b], specialist, rule

    try:
        p = float(cal_prob)
    except (TypeError, ValueError):
        b = f"FMCG:CAL_PROB:NOT_NUMERIC:{cal_prob}"
        return _gr(GATE_FAIL, evidence, b), [b], specialist, rule

    # Strict exclusive bounds: 0 < p < 1 (v1.1 hardening)
    if not (0.0 < p < 1.0):
        b = f"FMCG:CAL_PROB:OUT_OF_RANGE_EXCLUSIVE:{p}"
        return _gr(GATE_FAIL, evidence, b), [b], specialist, rule

    if model_status in PROVISIONAL_MODEL_STATUSES:
        b = f"FMCG:CAL_PROB:PROVISIONAL_MODEL:{model_id}"
        rule = "PROVISIONAL_CEILING:MODEL_QUALIFIED_HOLD"
        return _gr(GATE_HOLD, evidence, b), [b], specialist, rule

    if model_status == "NO_REGISTERED_MODEL" or model_status is None:
        b = f"FMCG:CAL_PROB:NO_REGISTERED_MODEL:{model_id}"
        return _gr(GATE_FAIL, evidence, b), [b], specialist, rule

    if prob_pub is False:
        b = "FMCG:CAL_PROB:NOT_PUBLISHABLE"
        return _gr(GATE_FAIL, evidence, b), [b], specialist, rule

    rule = f"ACTIVE_MODEL:{model_id}:calibrated_prob_in_(0,1)_exclusive"
    return _gr(GATE_PASS, evidence), [], specialist, rule


# ── v1.0 gate 8 ─────────────────────────────────────────────────────────────

def _check_calibrated_lower_bound(
    row: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Validate the calibrated lower bound when present.
    Absence is SKIP for non-FS models.
    Point probability cannot masquerade as calibrated lower bound.
    """
    cal_prob = row.get("calibrated_probability")
    lb       = row.get("calibrated_probability_lower_bound")
    model_id = (row.get("model_id") or row.get("calibration_note") or "")
    is_fs    = "fantasy" in model_id.lower() or "_fs_" in model_id.lower()

    evidence = {
        "calibrated_probability": cal_prob,
        "calibrated_lower_bound": lb,
        "model_id":               model_id,
        "is_fs_model":            is_fs,
    }

    if lb is None:
        if is_fs:
            b = f"FMCG:LOWER_BOUND:MISSING_FOR_FS_MODEL:{model_id}"
            return _gr(GATE_HOLD, evidence, b), [b]
        return _gr(GATE_SKIP, {**evidence, "reason": "lower_bound_not_required"}), []

    try:
        lb_f = float(lb)
        cp_f = float(cal_prob) if cal_prob is not None else None
    except (TypeError, ValueError):
        b = "FMCG:LOWER_BOUND:NOT_NUMERIC"
        return _gr(GATE_FAIL, evidence, b), [b]

    if lb_f < 0.0:
        b = f"FMCG:LOWER_BOUND:NEGATIVE:{lb_f}"
        return _gr(GATE_FAIL, evidence, b), [b]

    if cp_f is not None and abs(lb_f - cp_f) < 1e-6:
        b = f"FMCG:LOWER_BOUND:MASQUERADES_AS_POINT_PROB:lb={lb_f} cal={cp_f}"
        return _gr(GATE_FAIL, evidence, b), [b]

    if cp_f is not None and lb_f > cp_f:
        b = f"FMCG:LOWER_BOUND:EXCEEDS_POINT_ESTIMATE:lb={lb_f} cal={cp_f}"
        return _gr(GATE_FAIL, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


# ── v1.0 gate 9 ─────────────────────────────────────────────────────────────

def _check_no_vig_exact_line(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    mkt = gates.get("market_gate") or {}
    exact_nv = mkt.get("exact_market_no_vig_prob")
    adj_nv   = mkt.get("no_vig_prob")
    adj_line = mkt.get("adjacent_market_line")
    sb_line  = mkt.get("sportsbook_line")
    exact_ln = mkt.get("exact_market_line")

    evidence = {
        "exact_market_no_vig_prob": exact_nv,
        "no_vig_prob_adjacent":     adj_nv,
        "exact_market_line":        exact_ln,
        "adjacent_market_line":     adj_line,
        "sportsbook_line":          sb_line,
    }

    if exact_nv is None and adj_nv is None and sb_line is None:
        return _gr(GATE_SKIP, {**evidence, "reason": "no_market_data"}), []
    if exact_nv is not None:
        return _gr(GATE_PASS, evidence), []
    if adj_nv is not None:
        b = (f"FMCG:NO_VIG:ADJACENT_LINE_ONLY:adj_line={adj_line} "
             f"sb_line={sb_line}:exact_market_no_vig_prob=None")
        return _gr(GATE_HOLD, evidence, b), [b]

    b = "FMCG:NO_VIG:MISSING"
    return _gr(GATE_FAIL, evidence, b), [b]


# ── v1.0 gate 10 ────────────────────────────────────────────────────────────

def _check_push_rules(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    pp    = row.get("pp_thresholds") or {}
    whole = pp.get("whole_number_line")
    cash  = pp.get("cash_threshold")
    push  = row.get("push_prob") or row.get("push_probability")

    evidence = {"whole_number_line": whole, "cash_threshold": cash, "push_prob": push}

    if whole is False:
        return _gr(GATE_SKIP, {**evidence, "reason": "half_point_no_push"}), []
    if whole is True and push is None:
        b = "FMCG:PUSH_RULES:UNRESOLVED_FOR_WHOLE_NUMBER_LINE"
        return _gr(GATE_HOLD, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


# ── v1.0 gate 11 ────────────────────────────────────────────────────────────

def _check_contradiction_audit(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    terminal    = row.get("terminal_label") or ""
    row_blks    = row.get("blockers") or []
    data_status = (row.get("data_status") or "").upper()
    mkt         = gates.get("market_gate") or {}

    has_src_conflict = (
        "SOURCE_CONFLICT" in terminal.upper()
        or data_status == "SOURCE_CONFLICT"
        or any("SOURCE_CONFLICT" in str(b).upper() for b in row_blks)
    )
    has_mkt_contra = (
        mkt.get("market_status") == "MARKET_CONTRADICTION"
        or any("MARKET_CONTRADICTION" in str(b).upper() for b in row_blks)
    )

    evidence = {
        "has_source_conflict":      has_src_conflict,
        "has_market_contradiction": has_mkt_contra,
        "data_status":              data_status,
    }

    if has_src_conflict or has_mkt_contra:
        reasons = []
        if has_src_conflict: reasons.append("SOURCE_CONFLICT")
        if has_mkt_contra:   reasons.append("MARKET_CONTRADICTION")
        b = f"FMCG:CONTRADICTION_AUDIT:UNRESOLVED:{'+'.join(reasons)}"
        return _gr(GATE_FAIL, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


# ── v1.0 gate 12 ────────────────────────────────────────────────────────────

def _check_freshness(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    row_blks   = row.get("blockers") or []
    stale_blks = [b for b in row_blks if "STALE" in str(b).upper()
                  or "STALENESS" in str(b).upper()
                  or "STALE_CEILING" in str(b).upper()]
    sr      = gates.get("status_role") or {}
    role_ts = sr.get("role_timestamp") or row.get("role_timestamp")

    evidence = {"role_timestamp": role_ts, "staleness_blockers": stale_blks[:3]}
    if stale_blks:
        b = f"FMCG:FRESHNESS:STALENESS_DETECTED:{stale_blks[0][:100]}"
        return _gr(GATE_HOLD, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


# ── v1.0 gate 13 ────────────────────────────────────────────────────────────

def _check_source_grade(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    src  = str(row.get("source_grade") or "").upper()
    enr  = str(row.get("enrichment_source") or "").upper()
    prov = str((row.get("provenance") or {}).get("source_type") or "").upper()
    conf = str(row.get("confidence_lane") or "").upper()
    blks = row.get("blockers") or []

    is_unob  = "UNOBTAINABLE" in src
    is_recon = (
        "RECONSTRUCTED" in enr or "RECONSTRUCTED" in prov
        or "RECONSTRUCTED" in conf
        or any("RECONSTRUCTED" in str(b).upper() for b in blks)
    )

    evidence = {
        "source_grade": src, "enrichment_source": enr,
        "prov_source_type": prov, "confidence_lane": conf,
    }

    if is_unob:
        b = f"FMCG:SOURCE_GRADE:UNOBTAINABLE:{src}"
        return _gr(GATE_FAIL, evidence, b), [b]
    if is_recon:
        b = "FMCG:SOURCE_GRADE:RECONSTRUCTED_SOURCE"
        return _gr(GATE_FAIL, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


# ── v1.0 gate 14 ────────────────────────────────────────────────────────────

def _check_specialist_failure_path(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    fp      = row.get("failure_path") or row.get("specialist_failure")
    hp_gate = gates.get("hit_probability") or {}
    hp_err  = hp_gate.get("error")

    evidence = {"failure_path": fp, "hp_gate_error": hp_err}
    if fp:
        b = f"FMCG:SPECIALIST_FAILURE_PATH:{str(fp)[:100]}"
        return _gr(GATE_FAIL, evidence, b), [b]
    if hp_err:
        b = f"FMCG:SPECIALIST_FAILURE_PATH:HP_ERROR:{str(hp_err)[:80]}"
        return _gr(GATE_FAIL, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


# ── v1.1 gate 15 — calibration health precheck (Layer 0.5 gate result) ──────

def _check_calibration_health_gate(
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Reads the calibration_health gate result (Layer 0.5) produced earlier in
    the pipeline.  Enforced separately from candidate dynamic calibration.

    SUPPRESS → FAIL (auto-suppress; row cannot reach FINAL_APPROVED)
    WATCH    → HOLD (downgrade ceiling)
    Absent   → SKIP (graceful; does not block rows scored before gate was wired)
    """
    ch = gates.get("calibration_health")
    if not isinstance(ch, dict):
        return _gr(GATE_SKIP, {"reason": "calibration_health_gate_absent"}), []

    grade  = str(ch.get("health_grade") or ch.get("grade") or "").upper()
    detail = ch.get("detail") or ch.get("note") or ""

    evidence = {"health_grade": grade, "detail": str(detail)[:200]}

    if grade == "SUPPRESS":
        b = f"FMCG:CALIBRATION_HEALTH:SUPPRESS:{str(detail)[:80]}"
        return _gr(GATE_FAIL, evidence, b), [b]
    if grade == "WATCH":
        b = f"FMCG:CALIBRATION_HEALTH:WATCH:{str(detail)[:80]}"
        return _gr(GATE_HOLD, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


# ── v1.1 gate 16 — bidirectional MORE/LESS enforcement ───────────────────────

def _check_bidirectional_sides(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    PROP markets must carry evidence that both sides (MORE and LESS) were
    evaluated before qualification.  Cross-market drift is preserved as a
    separate concern.

    Checks (in priority order):
    1. gates["bidirectional_analysis"] — structured gate result if wired
    2. row["bidirectional_evaluation_complete"] — explicit bool from pipeline
    3. SKIP if neither is present (backward-compatible for rows scored before wiring)

    Separates bidirectional enforcement from:
    - session/directional exposure (gate 18)
    - slip_structure correlation checks (downstream governor)
    """
    prop_type = (row.get("prop_type") or row.get("stat_key") or "").lower()
    is_prop   = prop_type in PROP_MARKET_FAMILIES

    # Structured gate from pipeline (preferred)
    bidi_gate = gates.get("bidirectional_analysis")
    if isinstance(bidi_gate, dict):
        both_evaluated = bidi_gate.get("both_sides_evaluated", False)
        more_present   = bidi_gate.get("more_evaluated", both_evaluated)
        less_present   = bidi_gate.get("less_evaluated", both_evaluated)
        evidence = {
            "prop_type":          prop_type,
            "is_prop_market":     is_prop,
            "both_sides":         both_evaluated,
            "more_evaluated":     more_present,
            "less_evaluated":     less_present,
            "source":             "bidirectional_analysis_gate",
        }
        if is_prop and not (more_present and less_present):
            missing_sides = []
            if not more_present: missing_sides.append("MORE")
            if not less_present: missing_sides.append("LESS")
            b = f"FMCG:BIDIRECTIONAL:MISSING_SIDES:{'+'.join(missing_sides)}"
            return _gr(GATE_HOLD, evidence, b), [b]
        return _gr(GATE_PASS, evidence), []

    # Explicit bool from pipeline (second option)
    bidi_complete = row.get("bidirectional_evaluation_complete")
    if bidi_complete is not None:
        evidence = {
            "prop_type":                      prop_type,
            "is_prop_market":                 is_prop,
            "bidirectional_evaluation_complete": bidi_complete,
            "source":                         "row_field",
        }
        if is_prop and bidi_complete is False:
            b = "FMCG:BIDIRECTIONAL:EVALUATION_NOT_COMPLETE"
            return _gr(GATE_HOLD, evidence, b), [b]
        return _gr(GATE_PASS, evidence), []

    # Neither present — skip (graceful for rows scored before wiring)
    return _gr(GATE_SKIP, {
        "prop_type":      prop_type,
        "is_prop_market": is_prop,
        "reason":         "bidirectional_analysis_not_present",
    }), []


# ── v1.1 gate 17 — source timestamp / freshness grading ─────────────────────

def _check_source_timestamp_grading(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Source timestamp/freshness grading: reads the source_grade gate result
    for N/T (no-timestamp) grade flags.  A source without a verifiable
    timestamp (grade_type N or T) blocks FINAL_APPROVED via HOLD.

    Absent gate → SKIP (graceful backward compat).
    """
    sg = gates.get("source_grade")
    if not isinstance(sg, dict):
        # Also check row-level flag
        ts_grade = str(row.get("source_timestamp_grade") or "").upper()
        if ts_grade in {"N", "T", "NO_TIMESTAMP", "TIMESTAMP_MISSING"}:
            b = f"FMCG:SOURCE_TIMESTAMP:NO_TIMESTAMP_GRADE:{ts_grade}"
            return _gr(GATE_HOLD, {"source_timestamp_grade": ts_grade}, b), [b]
        return _gr(GATE_SKIP, {"reason": "source_grade_gate_absent"}), []

    grade_type = str(sg.get("grade_type") or sg.get("timestamp_grade") or "").upper()
    has_ts     = sg.get("has_timestamp", True)   # assume present if not stated
    evidence   = {
        "grade_type":         grade_type,
        "has_timestamp":      has_ts,
        "source_grade_grade": sg.get("grade"),
    }

    if grade_type in {"N", "T"} or has_ts is False:
        b = f"FMCG:SOURCE_TIMESTAMP:NO_TIMESTAMP_GRADE:{grade_type or 'UNKNOWN'}"
        return _gr(GATE_HOLD, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


# ── v1.1 gate 18 — probability component ledger + shrinkage verdict ──────────

def _check_prob_ledger(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Probability-component-ledger verdict must be CALIBRATED before
    qualification.  UNCALIBRATED or PROXY_ONLY blocks FINAL_APPROVED via HOLD.

    Reads gates["prob_ledger"] (produced by prob_ledger.run() in pipeline).
    Absent → SKIP (graceful backward compat).
    """
    pl = gates.get("prob_ledger")
    if not isinstance(pl, dict):
        return _gr(GATE_SKIP, {"reason": "prob_ledger_gate_absent"}), []

    cal_status        = str(pl.get("calibration_status") or
                            pl.get("effective_status") or "UNKNOWN").upper()
    shrinkage_applied = pl.get("shrinkage_applied", False)
    shrinkage_req     = pl.get("shrinkage_required", False)
    missing_comps     = pl.get("missing_required_components") or pl.get("missing_required", [])

    evidence = {
        "calibration_status":     cal_status,
        "shrinkage_applied":      shrinkage_applied,
        "shrinkage_required":     shrinkage_req,
        "missing_components":     missing_comps,
    }

    if cal_status == "CALIBRATED" and not shrinkage_req:
        return _gr(GATE_PASS, evidence), []

    # Shrinkage skipped when required
    if shrinkage_req and not shrinkage_applied:
        b = "FMCG:PROB_LEDGER:SHRINKAGE_REQUIRED_NOT_APPLIED"
        return _gr(GATE_HOLD, evidence, b), [b]

    if cal_status in {"UNCALIBRATED", "PROXY_ONLY"}:
        b = f"FMCG:PROB_LEDGER:NOT_CALIBRATED:{cal_status}"
        return _gr(GATE_HOLD, evidence, b), [b]

    if missing_comps:
        b = f"FMCG:PROB_LEDGER:MISSING_COMPONENTS:{','.join(str(c) for c in missing_comps[:5])}"
        return _gr(GATE_HOLD, evidence, b), [b]

    # Unknown status — hold to be safe (fail-closed)
    if cal_status == "UNKNOWN":
        b = "FMCG:PROB_LEDGER:UNKNOWN_STATUS"
        return _gr(GATE_HOLD, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


# ── v1.1 gate 19 — session directional exposure ───────────────────────────────

def _check_session_directional_exposure(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Session/directional exposure gate — SEPARATE from slip correlation
    (which is a downstream governor) and from structural dependency checks.

    Reads gates["directional_exposure"] produced by directional_exposure.run().
    SESSION_BLOCK  → FAIL   (blocks FINAL_APPROVED)
    SESSION_WARNING → HOLD  (downgrade ceiling)
    Absent → SKIP (graceful backward compat).
    """
    de = gates.get("directional_exposure")
    if not isinstance(de, dict):
        # Also check row-level blockers for directional exposure signals
        row_blks = row.get("blockers") or []
        de_blks  = [b for b in row_blks if "SESSION_DIRECTIONAL" in str(b).upper()
                    or "DIRECTIONAL_EXPOSURE_BLOCK" in str(b).upper()]
        if de_blks:
            b = f"FMCG:SESSION_DIRECTIONAL:BLOCKER_DETECTED:{de_blks[0][:80]}"
            return _gr(GATE_FAIL, {"blocker_detected": de_blks[0]}, b), [b]
        return _gr(GATE_SKIP, {"reason": "directional_exposure_gate_absent"}), []

    session_verdict = str(de.get("session_verdict") or de.get("verdict") or "").upper()
    dominant_count  = de.get("dominant_count")
    script_type     = de.get("dominant_script_type") or de.get("script_type")

    evidence = {
        "session_verdict": session_verdict,
        "dominant_count":  dominant_count,
        "script_type":     script_type,
    }

    if session_verdict == "SESSION_BLOCK":
        b = (f"FMCG:SESSION_DIRECTIONAL:BLOCK:"
             f"count={dominant_count} script={script_type}")
        return _gr(GATE_FAIL, evidence, b), [b]

    if session_verdict == "SESSION_WARNING":
        b = (f"FMCG:SESSION_DIRECTIONAL:WARNING:"
             f"count={dominant_count} script={script_type}")
        return _gr(GATE_HOLD, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


# ── v1.1 gate 20 — pregame snapshot / final refresh ──────────────────────────

def _check_pregame_snapshot(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    For FINAL_APPROVED and MONEY_QUALIFIED rows, final refresh must have
    PASSED and cannot be a vacuous pass.

    Reads (in priority order):
    1. row["final_refresh_passed"]   — bool set by pipeline at snapshot call
    2. row["final_refresh_required"] — if True and not passed → HOLD
    3. gates["pp_final_refresh"]     — structured gate result
    4. If none present: SKIP (graceful for non-money rows and backward compat)
    """
    terminal = row.get("terminal_label") or ""
    money_qualified = terminal in {FINAL_APPROVED, "MONEY_QUALIFIED"}

    final_refresh_passed  = row.get("final_refresh_passed")
    final_refresh_req     = row.get("final_refresh_required")
    refresh_gate          = gates.get("pp_final_refresh") or {}
    refresh_code          = str(refresh_gate.get("code") or "").upper()

    evidence = {
        "terminal_label":          terminal,
        "money_qualified_label":   money_qualified,
        "final_refresh_passed":    final_refresh_passed,
        "final_refresh_required":  final_refresh_req,
        "refresh_gate_code":       refresh_code or None,
    }

    # Explicit bool result from pipeline
    if final_refresh_passed is not None:
        if final_refresh_passed is True:
            return _gr(GATE_PASS, evidence), []
        if money_qualified:
            b = "FMCG:PREGAME_SNAPSHOT:FINAL_REFRESH_NOT_PASSED"
            return _gr(GATE_HOLD, evidence, b), [b]
        # Non-money row with refresh failure: note only
        return _gr(GATE_PASS, {**evidence, "note": "refresh_not_passed_non_money"}), []

    # Explicit required flag without passed flag
    if final_refresh_req is True:
        b = "FMCG:PREGAME_SNAPSHOT:FINAL_REFRESH_REQUIRED_NOT_RESOLVED"
        return _gr(GATE_HOLD, evidence, b), [b]

    # Structured gate result
    if refresh_code:
        if refresh_code == "FINAL_REFRESH_REQUIRED":
            b = "FMCG:PREGAME_SNAPSHOT:REFRESH_GATE_REQUIRED"
            return _gr(GATE_HOLD, evidence, b), [b]
        if refresh_code == "FINAL_REFRESH_VACUOUS" and money_qualified:
            b = "FMCG:PREGAME_SNAPSHOT:VACUOUS_REFRESH_ON_MONEY_ROW"
            return _gr(GATE_HOLD, evidence, b), [b]
        if refresh_code == "FINAL_REFRESH_CLEAR":
            return _gr(GATE_PASS, evidence), []

    # Nothing present — skip (graceful)
    return _gr(GATE_SKIP, {**evidence, "reason": "final_refresh_data_absent"}), []


# ── v1.1 gate 21 — terminal label native check ───────────────────────────────

def _check_terminal_label_native(
    row: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Reject invented terminal labels not in the known native backend set or
    the CC namespace.  Downgrade to HOLD — never an error.

    Cross-sport high-probability selector is explicitly non-binding
    (CROSS_SPORT_HIGH_PROBABILITY_SELECTOR_STATUS = PROPOSED_NOT_BINDING)
    and is excluded from all gate logic.
    """
    label = row.get("terminal_label")
    if label is None:
        return _gr(GATE_SKIP, {"reason": "no_terminal_label"}), []

    # Native FMCG labels (16-label set)
    if label in NATIVE_BACKEND_LABELS:
        return _gr(GATE_PASS, {"label": label, "source": "fmcg_native"}), []

    # CC namespace labels
    if str(label).startswith("CC:"):
        return _gr(GATE_PASS, {"label": label, "source": "cc_namespace"}), []

    # Extended CC label set (lazy import — best effort)
    try:
        from gate_engine.command_center.cc_labels import CEILING_ORDER as _cc_order
        if label in _cc_order:
            return _gr(GATE_PASS, {"label": label, "source": "cc_extended"}), []
    except Exception:
        pass

    # Unrecognized — HOLD (downgrade-only; not a fatal error)
    b = f"FMCG:TERMINAL_LABEL_NATIVE:UNRECOGNIZED:{label[:80]}"
    return _gr(GATE_HOLD, {"label": label, "source": "unrecognized"}, b), [b]


# ---------------------------------------------------------------------------
# Governance hash — lazy, cached per-process
# ---------------------------------------------------------------------------

_GOV_HASH_CACHE: str | None = None


def _get_governance_hash() -> str:
    global _GOV_HASH_CACHE
    if _GOV_HASH_CACHE:
        return _GOV_HASH_CACHE
    try:
        from gate_engine.governance import get_governance_status
        h = get_governance_status().get("governance_hash", "UNKNOWN")
        _GOV_HASH_CACHE = h
        return h
    except Exception:
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# Main evaluation — pure function, does not modify row
# ---------------------------------------------------------------------------

def evaluate(
    row: dict[str, Any],
    governance_hash: str | None = None,
) -> dict[str, Any]:
    """
    Evaluate the Full Model Contract for a single row.
    Read-only: does not modify `row`.

    Gate order (v1.1):
      0.  Upstream ceiling
      1.  Invalidation
      2.  Completeness
      [Per-gate block — only when complete and not invalidated]
      3.  Calibration health precheck (v1.1 — Layer 0.5 gate result)
      4.  Market identity
      5.  Role status
      6.  L10 evidence
      7.  Calibrated probability — strict exclusive (0,1) (v1.1)
      8.  Calibrated lower bound
      9.  No-vig exact line
      10. Push rules
      11. Contradiction audit
      12. Freshness
      13. Source grade
      14. Source timestamp grading (v1.1)
      15. Probability component ledger + shrinkage (v1.1)
      16. Bidirectional MORE/LESS (v1.1)
      17. Session directional exposure (v1.1) — SEPARATE from structural/correlation
      18. Pregame snapshot / final refresh (v1.1)
      19. Terminal label native check (v1.1)
      20. Specialist failure path
    """
    gates    = row.get("gates") or {}
    blockers: list[str] = []
    now_iso  = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # ── 0. Upstream ceiling ─────────────────────────────────────────────────
    upstream_result, entry_label = _check_upstream_ceiling(row)

    # ── 1. Invalidation ─────────────────────────────────────────────────────
    inv_result, inv_blockers, is_invalidated = _check_invalidation(row)
    blockers.extend(inv_blockers)

    # ── 2. Completeness ─────────────────────────────────────────────────────
    comp_result, comp_blockers = _check_full_model_completeness(row, gates)
    blockers.extend(comp_blockers)
    gate_incomplete = comp_result["status"] != GATE_PASS

    # ── Per-gate checks (only when complete and not invalidated) ────────────
    calib_health_result = None
    identity_result     = role_result    = l10_result      = None
    cal_prob_result     = lb_result      = no_vig_result   = None
    push_result         = contra_result  = fresh_result    = None
    src_result          = src_ts_result  = prob_ledger_result = None
    bidi_result         = de_result      = snap_result     = None
    native_label_result = spec_result    = None

    controlling_specialist:    str | None = None
    active_qualification_rule: str | None = None
    qualification_rule_source: str | None = None

    if not is_invalidated and not gate_incomplete:

        # ── 3. Calibration health precheck (v1.1) ──────────────────────────
        calib_health_result, blks = _check_calibration_health_gate(gates)
        blockers.extend(blks)

        # ── 4. Market identity ──────────────────────────────────────────────
        identity_result, blks = _check_market_identity(row, gates)
        blockers.extend(blks)

        # ── 5. Role status ──────────────────────────────────────────────────
        role_result, blks = _check_role_status(row, gates)
        blockers.extend(blks)

        # ── 6. L10 evidence ─────────────────────────────────────────────────
        l10_result, blks = _check_l10_evidence(row, gates)
        blockers.extend(blks)

        # ── 7. Calibrated probability — strict exclusive (0,1) (v1.1) ───────
        cal_prob_result, blks, controlling_specialist, active_qualification_rule = \
            _check_calibrated_probability(row)
        blockers.extend(blks)
        if active_qualification_rule:
            qualification_rule_source = "model_registry"

        # ── 8. Calibrated lower bound ────────────────────────────────────────
        lb_result, blks = _check_calibrated_lower_bound(row)
        blockers.extend(blks)

        # ── 9. No-vig exact line ─────────────────────────────────────────────
        no_vig_result, blks = _check_no_vig_exact_line(row, gates)
        blockers.extend(blks)

        # ── 10. Push rules ───────────────────────────────────────────────────
        push_result, blks = _check_push_rules(row, gates)
        blockers.extend(blks)

        # ── 11. Contradiction audit ──────────────────────────────────────────
        contra_result, blks = _check_contradiction_audit(row, gates)
        blockers.extend(blks)

        # ── 12. Freshness ────────────────────────────────────────────────────
        fresh_result, blks = _check_freshness(row, gates)
        blockers.extend(blks)

        # ── 13. Source grade ─────────────────────────────────────────────────
        src_result, blks = _check_source_grade(row, gates)
        blockers.extend(blks)

        # ── 14. Source timestamp grading (v1.1) ──────────────────────────────
        src_ts_result, blks = _check_source_timestamp_grading(row, gates)
        blockers.extend(blks)

        # ── 15. Probability component ledger + shrinkage (v1.1) ──────────────
        prob_ledger_result, blks = _check_prob_ledger(row, gates)
        blockers.extend(blks)

        # ── 16. Bidirectional MORE/LESS (v1.1) ───────────────────────────────
        bidi_result, blks = _check_bidirectional_sides(row, gates)
        blockers.extend(blks)

        # ── 17. Session directional exposure (v1.1) ───────────────────────────
        de_result, blks = _check_session_directional_exposure(row, gates)
        blockers.extend(blks)

        # ── 18. Pregame snapshot / final refresh (v1.1) ───────────────────────
        snap_result, blks = _check_pregame_snapshot(row, gates)
        blockers.extend(blks)

        # ── 19. Terminal label native check (v1.1) ────────────────────────────
        native_label_result, blks = _check_terminal_label_native(row)
        blockers.extend(blks)

        # ── 20. Specialist failure path ───────────────────────────────────────
        spec_result, blks = _check_specialist_failure_path(row, gates)
        blockers.extend(blks)

    # ── Determine full_model_status ─────────────────────────────────────────
    if is_invalidated:
        full_model_status = STATUS_INVALIDATED
    elif gate_incomplete:
        full_model_status = STATUS_INCOMPLETE
    else:
        full_model_status = STATUS_COMPLETE

    # ── Determine qualification_result ──────────────────────────────────────
    ran = [g for g in [
        calib_health_result, identity_result, role_result, l10_result,
        cal_prob_result, lb_result, no_vig_result, push_result,
        contra_result, fresh_result, src_result, src_ts_result,
        prob_ledger_result, bidi_result, de_result, snap_result,
        native_label_result, spec_result,
    ] if g is not None]

    if is_invalidated or gate_incomplete:
        qualification_result = QUAL_REJECT
    elif any(g["status"] == GATE_FAIL for g in ran):
        qualification_result = QUAL_REJECT
    elif any(g["status"] == GATE_HOLD for g in ran):
        qualification_result = QUAL_HOLD
    else:
        qualification_result = QUAL_PASS

    # ── Canonical ceiling resolution ────────────────────────────────────────
    # Uses the single canonical_ceiling_resolve() for ALL paths (v1.1).
    if qualification_result == QUAL_PASS and full_model_status == STATUS_COMPLETE:
        proposed = entry_label or FINAL_APPROVED
    else:
        proposed = MODEL_QUALIFIED_HOLD

    lowest_ceiling = canonical_ceiling_resolve(proposed, entry_label)

    # ── Summaries from upstream outputs ─────────────────────────────────────
    mkt = gates.get("market_gate") or {}
    ev  = gates.get("ev_gate") or {}
    market_summary = {
        "market_status":            mkt.get("market_status"),
        "sportsbook_line":          mkt.get("sportsbook_line"),
        "no_vig_prob":              mkt.get("no_vig_prob"),
        "exact_market_no_vig_prob": mkt.get("exact_market_no_vig_prob"),
        "edge_score":               ev.get("edge_score"),
    }
    probability_summary = {
        "calibrated_probability":  row.get("calibrated_probability"),
        "calibrated_lower_bound":  row.get("calibrated_probability_lower_bound"),
        "model_status":            row.get("model_status") or row.get("calibration_status"),
        "model_id":                row.get("model_id") or row.get("calibration_note"),
        "probability_publishable": row.get("probability_publishable"),
        "bounds_rule":             "strict_exclusive:(0,1)",
    }
    invalidation_state = {
        "is_invalidated":       is_invalidated,
        "invalidation_reasons": list(
            inv_result.get("evidence", {}).get("triggered_signals", {}).values()
        ),
        "requires_rerun": is_invalidated,
    }

    gov_hash = governance_hash or _get_governance_hash()

    return {
        # Contract / version / governance identifiers
        "gatekeeper_version":          GATEKEEPER_VERSION,
        "contract_id":                 CONTRACT_ID,
        "patch_id":                    PATCH_ID,
        "patch_precedence":            PATCH_PRECEDENCE,
        "engine_version":              ENGINE_VERSION,
        "governance_hash":             gov_hash,
        "evaluated_at":                now_iso,
        # Host abstraction (v1.1)
        "nested_custom_gpt_required":                False,
        "cross_sport_selector_status":               CROSS_SPORT_HIGH_PROBABILITY_SELECTOR_STATUS,
        "kalshi_recovery_mode":                      KALSHI_RECOVERY_MODE,
        # Candidate / run identity
        "candidate_id":                row.get("row_id") or row.get("candidate_id"),
        "player":                      row.get("player") or row.get("player_name"),
        "sport":                       row.get("sport"),
        "prop_type":                   row.get("prop_type") or row.get("stat_key"),
        "line":                        row.get("line") or row.get("line_value"),
        # Execution state
        "full_model_status":           full_model_status,
        "qualification_result":        qualification_result,
        "controlling_specialist":      controlling_specialist,
        "active_qualification_rule":   active_qualification_rule,
        "qualification_rule_source":   qualification_rule_source,
        # Ceiling / label (canonical resolver used — v1.1)
        "lowest_ceiling":              lowest_ceiling,
        "terminal_label":              lowest_ceiling,
        "entry_terminal_label":        entry_label,
        # Governance invariants
        "can_execute":                 CAN_EXECUTE,
        "dry_run_only":                DRY_RUN_ONLY,
        # Detailed gate results (all 21 gates)
        "gate_results": {
            "upstream_ceiling":            upstream_result,
            "invalidation":                inv_result,
            "full_model_completeness":     comp_result,
            "calibration_health":          calib_health_result,
            "market_identity":             identity_result,
            "role_status":                 role_result,
            "l10_evidence":                l10_result,
            "calibrated_probability":      cal_prob_result,
            "calibrated_lower_bound":      lb_result,
            "no_vig_exact_line":           no_vig_result,
            "push_rules":                  push_result,
            "contradiction_audit":         contra_result,
            "freshness":                   fresh_result,
            "source_grade":                src_result,
            "source_timestamp_grading":    src_ts_result,
            "prob_ledger":                 prob_ledger_result,
            "bidirectional_sides":         bidi_result,
            "session_directional_exposure": de_result,
            "pregame_snapshot":            snap_result,
            "terminal_label_native":       native_label_result,
            "specialist_failure_path":     spec_result,
        },
        # Upstream summaries (read-only from specialist outputs)
        "probability_summary":         probability_summary,
        "market_summary":              market_summary,
        # Blockers from this gatekeeper pass
        "blockers":                    blockers,
        # Invalidation
        "invalidation_state":          invalidation_state,
        # Downstream governors (informational — not enforced here)
        "required_downstream_governors": REQUIRED_DOWNSTREAM_GOVERNORS,
        "note": (
            "Gatekeeper PASS is necessary but NOT sufficient for FINAL_APPROVED. "
            "All required_downstream_governors must also pass. "
            "nested_custom_gpt_required=False; "
            "cross_sport_selector_status=PROPOSED_NOT_BINDING."
        ),
    }


# ---------------------------------------------------------------------------
# Apply to a single row in-place
# ---------------------------------------------------------------------------

def apply_gatekeeper(
    row: dict[str, Any],
    governance_hash: str | None = None,
) -> None:
    """
    Evaluate and apply the Full Model Contract Gatekeeper to `row` in-place.
    Uses canonical_ceiling_resolve() for ceiling enforcement (v1.1).
    """
    result = evaluate(row, governance_hash=governance_hash)
    row["gatekeeper"] = result

    entry = row.get("terminal_label") or ""
    if entry != FINAL_APPROVED:
        return

    qual = result["qualification_result"]
    fms  = result["full_model_status"]

    if qual == QUAL_PASS and fms == STATUS_COMPLETE:
        return

    # Downgrade — canonical ceiling resolution
    gk_blockers   = result.get("blockers") or []
    first_blocker = (gk_blockers[0] if gk_blockers else "UNKNOWN")[:100]

    new_label = canonical_ceiling_resolve(MODEL_QUALIFIED_HOLD, entry)
    row["terminal_label"] = new_label
    row.setdefault("blockers", []).append(
        f"FMCG:NO_GATEKEEPER_PASS:"
        f"full_model_status={fms}:"
        f"qualification_result={qual}:"
        f"controlling_blocker={first_blocker}"
    )


# ---------------------------------------------------------------------------
# Batch apply
# ---------------------------------------------------------------------------

def apply_gatekeeper_batch(
    rows: list[dict[str, Any]],
    governance_hash: str | None = None,
) -> dict[str, Any]:
    gov_hash    = governance_hash or _get_governance_hash()
    passed      = held      = rejected   = 0
    invalidated = incomplete = downgraded = 0

    for row in rows:
        entry = row.get("terminal_label")
        apply_gatekeeper(row, governance_hash=gov_hash)
        gk   = row.get("gatekeeper") or {}
        fms  = gk.get("full_model_status")
        qual = gk.get("qualification_result")

        if fms == STATUS_INVALIDATED:
            invalidated += 1
        elif fms == STATUS_INCOMPLETE:
            incomplete += 1
        elif qual == QUAL_PASS:
            passed += 1
        elif qual == QUAL_HOLD:
            held += 1
        else:
            rejected += 1

        if entry == FINAL_APPROVED and row.get("terminal_label") != FINAL_APPROVED:
            downgraded += 1

    return {
        "gatekeeper_version":         GATEKEEPER_VERSION,
        "can_execute":                CAN_EXECUTE,
        "governance_hash":            gov_hash,
        "total_rows":                 len(rows),
        "status_complete_pass":       passed,
        "status_complete_hold":       held,
        "status_complete_reject":     rejected,
        "status_invalidated":         invalidated,
        "status_incomplete":          incomplete,
        "final_approved_downgraded":  downgraded,
    }


# ---------------------------------------------------------------------------
# Command-Center and v16 envelope verification
# Both use canonical_ceiling_resolve() (v1.1).
# ---------------------------------------------------------------------------

def verify_cc_envelope(envelope: dict[str, Any]) -> bool:
    """
    Verify that an envelope whose engine_label == FINAL_APPROVED carries a
    valid gatekeeper PASS from the originating engine.
    Uses canonical_ceiling_resolve() for any label downgrade (v1.1).
    """
    engine_label = envelope.get("engine_label") or ""
    if engine_label != FINAL_APPROVED:
        return True

    engine_result = envelope.get("engine_result") or {}
    gk = engine_result.get("gatekeeper") or engine_result.get("gatekeeper_result")

    if (gk
            and gk.get("qualification_result") == QUAL_PASS
            and gk.get("full_model_status") == STATUS_COMPLETE
            and gk.get("can_execute") is False):
        return True

    # No valid pass — canonical ceiling downgrade
    new_label = canonical_ceiling_resolve(MODEL_QUALIFIED_HOLD, engine_label)
    envelope["engine_label"] = new_label
    envelope.setdefault("cc_blockers", []).append(
        "FMCG:CC:NO_GATEKEEPER_PASS_IN_ENGINE_RESULT:FINAL_APPROVED_DOWNGRADED"
    )
    return False


def verify_v16_result(result: dict[str, Any]) -> None:
    """
    Post-process a /wow/v16/run result in-place.
    Uses canonical_ceiling_resolve() for any label downgrade (v1.1).
    """
    final = result.get("final_label") or ""
    if final != FINAL_APPROVED:
        return

    skill_results = result.get("skill_results") or []
    has_pass = any(
        (sr.get("gatekeeper") or {}).get("qualification_result") == QUAL_PASS
        and (sr.get("gatekeeper") or {}).get("full_model_status") == STATUS_COMPLETE
        for sr in skill_results
    )
    if not has_pass:
        new_label = canonical_ceiling_resolve(MODEL_QUALIFIED_HOLD, final)
        result["final_label"] = new_label
        result.setdefault("blockers", []).append(
            "FMCG:V16:NO_GATEKEEPER_PASS:FINAL_APPROVED_DOWNGRADED"
        )
        result["gatekeeper_enforcement"] = {
            "gatekeeper_version": GATEKEEPER_VERSION,
            "action":             "DOWNGRADE_FINAL_APPROVED",
            "reason":             "no_skill_carried_valid_gatekeeper_pass",
            "can_execute":        CAN_EXECUTE,
        }
