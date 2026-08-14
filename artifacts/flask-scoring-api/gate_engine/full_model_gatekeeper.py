"""
gate_engine/full_model_gatekeeper.py
WOW Full Model Contract Gatekeeper v1.0
WOW-FMCG-v1.0 · Patch #25 · Precedence 104 · ENGINE v16.5

Fail-closed candidate-level enforcement contract.
Necessary but NOT sufficient for FINAL_APPROVED.

Consumes and validates already-produced specialist outputs.
Does NOT recompute probability, calibration, no-vig, push probability,
payout economics, or sport-specific projections.

Can only DOWNGRADE terminal labels. Never originates FINAL_APPROVED.

full_model_status: COMPLETE | INCOMPLETE | INVALIDATED
qualification_result: PASS | HOLD | REJECT

Downstream governors that must also pass (this module does not enforce them):
  slip_card_dependency, joint_probability, weakest_leg, payout_breakeven,
  platform_portfolio_governance, final_refresh, kalshi_portfolio_recovery_governor

can_execute = False  (unconditional)
"""
from __future__ import annotations

import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

GATEKEEPER_VERSION: str = "WOW-FMCG-v1.0"
CONTRACT_ID: str        = "FULL_MODEL_CONTRACT_GATEKEEPER"
PATCH_ID: str           = "WOW-PATCH-FMCG-v1.0"
PATCH_PRECEDENCE: int   = 104
ENGINE_VERSION: str     = "v16.5"
CAN_EXECUTE: bool       = False
DRY_RUN_ONLY: bool      = True

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
# Used to detect whether completeness is achievable for this path.
REQUIRED_GATE_KEYS: tuple[str, ...] = (
    "slate_validation", "status_role", "l5_l10_ledger",
    "market_gate", "ev_gate", "slip_structure", "exposure_gate",
)

# Monotonic ceiling order (index 0 = most permissive, higher = more restrictive).
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


def _ceiling_rank(label: str | None) -> int:
    if label is None:
        return -1
    return _CEILING_RANK.get(label, 0)


def _more_restrictive(a: str | None, b: str | None) -> str | None:
    """Return whichever label is more restrictive (higher rank)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _ceiling_rank(a) >= _ceiling_rank(b) else b


# Model status classifications
ACTIVE_MODEL_STATUSES:     frozenset[str] = frozenset({"ACTIVE"})
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
    """Build a gate result dict."""
    r: dict[str, Any] = {"status": status, "evidence": evidence}
    if blocker:
        r["blocker"] = blocker
    return r


# ---------------------------------------------------------------------------
# Individual gate checks — all read-only
# ---------------------------------------------------------------------------

def _check_invalidation(row: dict[str, Any]) -> tuple[dict[str, Any], list[str], bool]:
    """
    Detect material-change signals that invalidate the prior gatekeeper result.
    Returns (gate_result, blockers, is_invalidated).
    """
    triggered: dict[str, str] = {}
    for key, label in INVALIDATION_SIGNALS.items():
        val = row.get(key)
        if val is True or val == "true" or val == "True":
            triggered[key] = label

    is_inv = bool(triggered)
    evidence = {
        "triggered_signals": triggered,
        "is_invalidated":    is_inv,
    }
    if is_inv:
        reasons   = list(triggered.values())
        blockers  = [f"FMCG:INVALIDATED:{r}" for r in reasons]
        return _gr(GATE_FAIL, evidence, f"FMCG:INVALIDATED:{'+'.join(reasons)}"), blockers, True
    return _gr(GATE_PASS, evidence), [], False


def _check_upstream_ceiling(row: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Record the entry terminal_label. Gatekeeper is downgrade-only."""
    entry = row.get("terminal_label")
    return _gr(GATE_PASS, {
        "entry_terminal_label":   entry,
        "gatekeeper_can_upgrade": False,
    }), entry


def _check_full_model_completeness(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    All required gate dicts must be present, and calibrated_probability must
    be on the row. Absence → INCOMPLETE (pipeline gap, not scoring failure).
    """
    missing = [g for g in REQUIRED_GATE_KEYS if not isinstance(gates.get(g), dict)]
    has_cal = row.get("calibrated_probability") is not None

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


def _check_market_identity(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Exact market identity must be fully resolved."""
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
    if line_val is None: missing.append("line")
    if not mkt_st:       missing.append("market_gate.market_status")

    evidence = {
        "player": player, "sport": sport, "prop_type": prop_type,
        "line": line_val, "market_status": mkt_st, "sportsbook_line": sb_line,
        "missing_fields": missing,
    }
    if missing:
        b = f"FMCG:MARKET_IDENTITY:MISSING:{','.join(missing)}"
        return _gr(GATE_FAIL, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


def _check_role_status(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Role/status must be current. Stale or conflicted role → HOLD or REJECT."""
    sr         = gates.get("status_role") or {}
    role_st    = (sr.get("role_status") or row.get("role_status") or "").upper()
    role_ts    = sr.get("role_timestamp") or row.get("role_timestamp")

    evidence = {"role_status": role_st, "role_timestamp": role_ts}

    if role_st in {"DEPENDENCY_CONFLICT"}:
        b = f"FMCG:ROLE_STATUS:DEPENDENCY_CONFLICT:{role_st}"
        return _gr(GATE_FAIL, evidence, b), [b]
    if role_st in {"DEPENDENCY_UNRESOLVED", "ROLE_STATE_STALE", "STALE", "RECHECK"}:
        b = f"FMCG:ROLE_STATUS:SOFT_CONFLICT:{role_st}"
        return _gr(GATE_HOLD, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


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

    # Exact-line role-match check
    l10_line    = l5l10.get("line") or l5l10.get("ledger_line")
    scored_line = row.get("line") or row.get("line_value") or row.get("threshold")
    line_mismatch = False
    try:
        if l10_line is not None and scored_line is not None:
            line_mismatch = abs(float(l10_line) - float(scored_line)) > 0.01
    except (TypeError, ValueError):
        line_mismatch = True

    evidence = {
        "l5l10_passed":   l5l10_passed,
        "mkt_passed":     mkt_passed,
        "ev_passed":      ev_passed,
        "l10_hit_rate":   l10_hit_rate,
        "l5_hit_rate":    l5_hit_rate,
        "l10_line":       l10_line,
        "scored_line":    scored_line,
        "line_mismatch":  line_mismatch,
        "evidence_only":  "L5/L10 hit rate alone cannot qualify a row",
    }

    if line_mismatch:
        b = f"FMCG:L10_EVIDENCE:ROLE_LINE_MISMATCH:l10={l10_line} scored={scored_line}"
        return _gr(GATE_FAIL, evidence, b), [b]

    if not l5l10_passed:
        b = "FMCG:L10_EVIDENCE:LEDGER_NOT_PASSED"
        return _gr(GATE_FAIL, evidence, b), [b]

    # L10 passed but market/ev did not — hit rate would be the sole qualifier
    if not (mkt_passed and ev_passed):
        b = "FMCG:L10_EVIDENCE:SOLE_QUALIFIER:market_or_ev_not_passed"
        return _gr(GATE_FAIL, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


def _check_calibrated_probability(
    row: dict[str, Any],
) -> tuple[dict[str, Any], list[str], str | None, str | None]:
    """
    Calibrated probability: present, numeric, in [0,1], from ACTIVE model.
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
        "calibrated_probability":   cal_prob,
        "model_status":             model_status,
        "model_id":                 model_id,
        "probability_publishable":  prob_pub,
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

    if not (0.0 <= p <= 1.0):
        b = f"FMCG:CAL_PROB:OUT_OF_RANGE:{p}"
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

    rule = f"ACTIVE_MODEL:{model_id}:calibrated_prob_in_[0,1]"
    return _gr(GATE_PASS, evidence), [], specialist, rule


def _check_calibrated_lower_bound(
    row: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Validate the calibrated lower bound when present.
    Point probability cannot masquerade as calibrated lower bound
    (lower_bound == calibrated_probability is rejected).
    For models that don't produce a lower bound, absence is SKIP.
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
        # point probability masquerading as floor
        b = f"FMCG:LOWER_BOUND:MASQUERADES_AS_POINT_PROB:lb={lb_f} cal={cp_f}"
        return _gr(GATE_FAIL, evidence, b), [b]

    if cp_f is not None and lb_f > cp_f:
        b = f"FMCG:LOWER_BOUND:EXCEEDS_POINT_ESTIMATE:lb={lb_f} cal={cp_f}"
        return _gr(GATE_FAIL, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


def _check_no_vig_exact_line(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Exact-line no-vig probability must be present.
    Adjacent-line no-vig cannot substitute for exact-line.
    """
    mkt = gates.get("market_gate") or {}

    exact_nv  = mkt.get("exact_market_no_vig_prob")
    adj_nv    = mkt.get("no_vig_prob")
    adj_line  = mkt.get("adjacent_market_line")
    sb_line   = mkt.get("sportsbook_line")
    exact_ln  = mkt.get("exact_market_line")

    evidence = {
        "exact_market_no_vig_prob": exact_nv,
        "no_vig_prob_adjacent":     adj_nv,
        "exact_market_line":        exact_ln,
        "adjacent_market_line":     adj_line,
        "sportsbook_line":          sb_line,
    }

    # No market data at all — skip (caught elsewhere)
    if exact_nv is None and adj_nv is None and sb_line is None:
        return _gr(GATE_SKIP, {**evidence, "reason": "no_market_data"}), []

    if exact_nv is not None:
        return _gr(GATE_PASS, evidence), []

    # Only adjacent-line no-vig available
    if adj_nv is not None:
        b = (f"FMCG:NO_VIG:ADJACENT_LINE_ONLY:adj_line={adj_line} "
             f"sb_line={sb_line}:exact_market_no_vig_prob=None")
        return _gr(GATE_HOLD, evidence, b), [b]

    b = "FMCG:NO_VIG:MISSING"
    return _gr(GATE_FAIL, evidence, b), [b]


def _check_push_rules(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    For whole-number lines, push probability must be resolved.
    Unresolved push rules → HOLD (blocks payout qualification downstream).
    Half-point lines: push structurally impossible → SKIP.
    """
    pp = row.get("pp_thresholds") or {}
    whole = pp.get("whole_number_line")
    cash  = pp.get("cash_threshold")
    push  = row.get("push_prob") or row.get("push_probability")

    evidence = {
        "whole_number_line": whole,
        "cash_threshold":    cash,
        "push_prob":         push,
    }

    if whole is False:
        return _gr(GATE_SKIP, {**evidence, "reason": "half_point_no_push"}), []

    if whole is True and push is None:
        b = "FMCG:PUSH_RULES:UNRESOLVED_FOR_WHOLE_NUMBER_LINE"
        return _gr(GATE_HOLD, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


def _check_contradiction_audit(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """No unresolved SOURCE_CONFLICT or MARKET_CONTRADICTION."""
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
        if has_src_conflict:  reasons.append("SOURCE_CONFLICT")
        if has_mkt_contra:    reasons.append("MARKET_CONTRADICTION")
        b = f"FMCG:CONTRADICTION_AUDIT:UNRESOLVED:{'+'.join(reasons)}"
        return _gr(GATE_FAIL, evidence, b), [b]

    return _gr(GATE_PASS, evidence), []


def _check_freshness(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Detect staleness signals already present in gate outputs / blockers."""
    row_blks  = row.get("blockers") or []
    stale_blks = [b for b in row_blks if "STALE" in str(b).upper()
                  or "STALENESS" in str(b).upper()
                  or "STALE_CEILING" in str(b).upper()]

    sr      = gates.get("status_role") or {}
    role_ts = sr.get("role_timestamp") or row.get("role_timestamp")

    evidence = {
        "role_timestamp":     role_ts,
        "staleness_blockers": stale_blks[:3],
    }
    if stale_blks:
        b = f"FMCG:FRESHNESS:STALENESS_DETECTED:{stale_blks[0][:100]}"
        return _gr(GATE_HOLD, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


def _check_source_grade(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Source grade must not be UNOBTAINABLE or RECONSTRUCTED."""
    src   = str(row.get("source_grade") or "").upper()
    enr   = str(row.get("enrichment_source") or "").upper()
    prov  = str((row.get("provenance") or {}).get("source_type") or "").upper()
    conf  = str(row.get("confidence_lane") or "").upper()
    blks  = row.get("blockers") or []

    is_unob = "UNOBTAINABLE" in src
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


def _check_specialist_failure_path(
    row: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Specialist must have produced valid output (no error path)."""
    fp       = row.get("failure_path") or row.get("specialist_failure")
    hp_gate  = gates.get("hit_probability") or {}
    hp_err   = hp_gate.get("error")

    evidence = {
        "failure_path":    fp,
        "hp_gate_error":   hp_err,
    }

    if fp:
        b = f"FMCG:SPECIALIST_FAILURE_PATH:{str(fp)[:100]}"
        return _gr(GATE_FAIL, evidence, b), [b]
    if hp_err:
        b = f"FMCG:SPECIALIST_FAILURE_PATH:HP_ERROR:{str(hp_err)[:80]}"
        return _gr(GATE_FAIL, evidence, b), [b]
    return _gr(GATE_PASS, evidence), []


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

    Returns a fully structured gatekeeper_result dict.
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

    # ── 3. Per-gate checks (only when complete and not invalidated) ─────────
    identity_result = role_result    = l10_result      = None
    cal_prob_result = lb_result      = no_vig_result   = None
    push_result     = contra_result  = fresh_result    = None
    src_result      = spec_result    = None

    controlling_specialist:    str | None = None
    active_qualification_rule: str | None = None
    qualification_rule_source: str | None = None

    if not is_invalidated and not gate_incomplete:
        identity_result, blks = _check_market_identity(row, gates)
        blockers.extend(blks)

        role_result, blks = _check_role_status(row, gates)
        blockers.extend(blks)

        l10_result, blks = _check_l10_evidence(row, gates)
        blockers.extend(blks)

        cal_prob_result, blks, controlling_specialist, active_qualification_rule = \
            _check_calibrated_probability(row)
        blockers.extend(blks)
        if active_qualification_rule:
            qualification_rule_source = "model_registry"

        lb_result, blks = _check_calibrated_lower_bound(row)
        blockers.extend(blks)

        no_vig_result, blks = _check_no_vig_exact_line(row, gates)
        blockers.extend(blks)

        push_result, blks = _check_push_rules(row, gates)
        blockers.extend(blks)

        contra_result, blks = _check_contradiction_audit(row, gates)
        blockers.extend(blks)

        fresh_result, blks = _check_freshness(row, gates)
        blockers.extend(blks)

        src_result, blks = _check_source_grade(row, gates)
        blockers.extend(blks)

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
        identity_result, role_result, l10_result, cal_prob_result,
        lb_result, no_vig_result, push_result, contra_result,
        fresh_result, src_result, spec_result,
    ] if g is not None]

    if is_invalidated or gate_incomplete:
        qualification_result = QUAL_REJECT
    elif any(g["status"] == GATE_FAIL for g in ran):
        qualification_result = QUAL_REJECT
    elif any(g["status"] == GATE_HOLD for g in ran):
        qualification_result = QUAL_HOLD
    else:
        qualification_result = QUAL_PASS

    # ── Determine lowest_ceiling (monotonic — gatekeeper is downgrade-only) ─
    if qualification_result == QUAL_PASS and full_model_status == STATUS_COMPLETE:
        # PASS: entry label stands; do not upgrade if entry was already lower
        proposed = entry_label or FINAL_APPROVED
    else:
        proposed = MODEL_QUALIFIED_HOLD

    lowest_ceiling = _more_restrictive(proposed, entry_label)

    # ── Summaries from upstream outputs (no recomputation) ──────────────────
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
    }
    invalidation_state = {
        "is_invalidated":      is_invalidated,
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
        # Ceiling / label
        "lowest_ceiling":              lowest_ceiling,
        "terminal_label":              lowest_ceiling,
        "entry_terminal_label":        entry_label,
        # Governance invariants
        "can_execute":                 CAN_EXECUTE,
        "dry_run_only":                DRY_RUN_ONLY,
        # Detailed gate results
        "gate_results": {
            "upstream_ceiling":        upstream_result,
            "invalidation":            inv_result,
            "full_model_completeness": comp_result,
            "market_identity":         identity_result,
            "role_status":             role_result,
            "l10_evidence":            l10_result,
            "calibrated_probability":  cal_prob_result,
            "calibrated_lower_bound":  lb_result,
            "no_vig_exact_line":       no_vig_result,
            "push_rules":              push_result,
            "contradiction_audit":     contra_result,
            "freshness":               fresh_result,
            "source_grade":            src_result,
            "specialist_failure_path": spec_result,
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
            "All required_downstream_governors must also pass."
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

    If terminal_label == FINAL_APPROVED and qualification_result != PASS:
        → row["terminal_label"] = MODEL_QUALIFIED_HOLD
        → FMCG:NO_GATEKEEPER_PASS appended to row["blockers"]

    If full_model_status == INVALIDATED:
        → same downgrade regardless of qualification_result

    Monotonic: if terminal_label is already more restrictive than
    MODEL_QUALIFIED_HOLD, the stronger label is preserved.

    Attaches the full result dict to row["gatekeeper"].
    """
    result = evaluate(row, governance_hash=governance_hash)
    row["gatekeeper"] = result

    entry = row.get("terminal_label") or ""
    if entry != FINAL_APPROVED:
        # Not FINAL_APPROVED — gatekeeper attaches result for observability only.
        return

    qual = result["qualification_result"]
    fms  = result["full_model_status"]

    if qual == QUAL_PASS and fms == STATUS_COMPLETE:
        # Clean PASS — FINAL_APPROVED may proceed to downstream governors.
        return

    # Downgrade
    gk_blockers    = result.get("blockers") or []
    first_blocker  = (gk_blockers[0] if gk_blockers else "UNKNOWN")[:100]

    row["terminal_label"] = MODEL_QUALIFIED_HOLD
    row.setdefault("blockers", []).append(
        f"FMCG:NO_GATEKEEPER_PASS:"
        f"full_model_status={fms}:"
        f"qualification_result={qual}:"
        f"controlling_blocker={first_blocker}"
    )


# ---------------------------------------------------------------------------
# Batch apply (pipeline and command-center use this)
# ---------------------------------------------------------------------------

def apply_gatekeeper_batch(
    rows: list[dict[str, Any]],
    governance_hash: str | None = None,
) -> dict[str, Any]:
    """
    Apply gatekeeper to a list of rows in-place.
    Returns a summary dict for observability/output attachment.
    """
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
# ---------------------------------------------------------------------------

def verify_cc_envelope(envelope: dict[str, Any]) -> bool:
    """
    Verify that an envelope whose engine_label == FINAL_APPROVED carries a
    valid gatekeeper PASS from the originating engine.
    Returns True if the label may proceed; False if it must be downgraded.
    Modifies envelope in-place on downgrade.
    """
    engine_label = envelope.get("engine_label") or ""
    if engine_label != FINAL_APPROVED:
        return True  # Not FINAL_APPROVED — nothing to verify.

    engine_result = envelope.get("engine_result") or {}
    gk = engine_result.get("gatekeeper") or engine_result.get("gatekeeper_result")

    if (gk
            and gk.get("qualification_result") == QUAL_PASS
            and gk.get("full_model_status") == STATUS_COMPLETE
            and gk.get("can_execute") is False):
        return True  # Valid gatekeeper pass from originating engine.

    # No valid pass — downgrade
    envelope["engine_label"] = MODEL_QUALIFIED_HOLD
    envelope.setdefault("cc_blockers", []).append(
        "FMCG:CC:NO_GATEKEEPER_PASS_IN_ENGINE_RESULT:FINAL_APPROVED_DOWNGRADED"
    )
    return False


def verify_v16_result(result: dict[str, Any]) -> None:
    """
    Post-process a /wow/v16/run result in-place.
    If final_label == FINAL_APPROVED but no skill carried a valid gatekeeper
    PASS, downgrade to MODEL_QUALIFIED_HOLD.
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
        result["final_label"] = MODEL_QUALIFIED_HOLD
        result.setdefault("blockers", []).append(
            "FMCG:V16:NO_GATEKEEPER_PASS:FINAL_APPROVED_DOWNGRADED"
        )
        result["gatekeeper_enforcement"] = {
            "gatekeeper_version": GATEKEEPER_VERSION,
            "action":             "DOWNGRADE_FINAL_APPROVED",
            "reason":             "no_skill_carried_valid_gatekeeper_pass",
            "can_execute":        CAN_EXECUTE,
        }
