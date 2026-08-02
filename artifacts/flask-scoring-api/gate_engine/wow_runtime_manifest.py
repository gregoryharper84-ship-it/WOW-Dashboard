"""
wow_runtime_manifest.py
WOW v16 Clean Core — Runtime skill manifest, router, and audit block builder.

Implements the fail-closed routing model described in:
WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE

Design principle: Markdown defines the behavior; Replit code must enforce it.
The manifest answers: which specialist skills are REQUIRED for this input,
did they all run, what is the lowest ceiling, and can we produce a valid audit block?

can_execute=False is unconditional.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Canonical skill definitions
# ---------------------------------------------------------------------------

WOW_RUNTIME_MANIFEST: dict[str, Any] = {
    "wow_version": "WOW_v16_CLEAN_CORE",
    "active_patch_ids": [
        "WOW-CORE-v16",
        "WOW-PATCH-2026-06-27-SHARP-ANCHOR",
        "WOW-PATCH-2026-07-07-JS-STYLE",
        "WOW-PATCH-2026-07-10-COMBO-SETTLEMENT",
        "WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0",
        "WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE",
        "WOW-PATCH-2026-07-15-LLP-DATA-ACQUISITION-RESILIENCE",
        "WOW-PATCH-2026-07-15-PROP-CONFIDENCE-AND-MARKET-LABEL-SEPARATION",
        "WOW-PATCH-2026-07-15-GOVERNANCE-RESILIENCE-AND-ERROR-CONTRACT",
        "WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE",
        # Stage 1: WNBA opportunity gate + cross-slip portfolio governor (in-memory)
        "WOW-PATCH-WNBA-001-OPPORTUNITY-STABILITY-GATE",
        "WOW-PATCH-PORTFOLIO-001-CROSS-SLIP-EXPOSURE-GOVERNOR",
        # Stage 2A: DB-backed cross-request portfolio persistence
        "WOW-PATCH-PORTFOLIO-002-CROSS-SLIP-PERSISTENT-GOVERNANCE",
        # 2026-08-01 postmortem patches
        "WOW-PATCH-2026-08-01-CROSS-SLIP-DUPLICATE-GUARD",
        "WOW-PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE",
        "WOW-PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY",
        # Multi-Window Prop Persistence & Distribution Audit
        "WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT",
        # Linemakers Presentation & Self-Audit Patch
        "WOW-PATCH-2026-08-01-LINEMAKERS-PRESENTATION-AND-SELF-AUDIT",
        # 2026-08-02 analytical integrity patches
        "WOW-PATCH-2026-08-02-LLP-MATCHUP-EV-INTEGRITY",
        "WOW-PATCH-2026-08-02-LLP-SLIP-CONSTRUCTION-INTEGRITY",
        # LLP v16 upgrade
        "WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY-DYNAMIC-CALIBRATION-AND-FINAL-REFRESH",
    ],
    "skills": {
        "slip_optimizer": {
            "name": "wow.slip-probability-optimizer",
            "version": "v3",
            "required": True,
            "required_when": "any_slip_submission",
            "skill_file": "skills/wow-slip-probability-optimizer-SKILL-v3.md",
        },
        "wnba_composite": {
            "name": "wow.wnba-composite-prop-expert",
            "version": "v1",
            "required": False,
            "required_when": "sport=WNBA and stat_family in P_R_A_COMPONENT_OR_COMPOSITE",
            "skill_file": "skills/wow-wnba-composite-prop-expert-SKILL.md",
        },
        "wnba_opportunity_governor": {
            "name": "wow.wnba-opportunity-scenario-and-exposure-governor",
            "version": "v1",
            "required": False,
            "required_when": "sport=WNBA",
            "skill_file": "skills/wow-wnba-opportunity-scenario-and-exposure-governor-SKILL.md",
        },
        "mlb_pitcher_failure_path": {
            "name": "wow.mlb-pitcher-failure-path-expert",
            "version": "v2",
            "required": False,
            "required_when": "sport=MLB and participant_type=PITCHER",
            "skill_file": "skills/wow-mlb-pitcher-failure-path-expert-SKILL-v2.md",
        },
        "mlb_1ip_pitch_count": {
            "name": "wow.mlb-first-inning-pitch-count-expert",
            "version": "v3",
            "required": False,
            "required_when": "sport=MLB and market_type=1IP_PITCHES_THROWN",
            "skill_file": "skills/wow-mlb-first-inning-pitch-count-expert-SKILL-v3.md",
        },
        "cross_ticket_governor": {
            "name": "wow.cross-ticket-exposure-governor",
            "version": "v1",
            "required": False,
            "required_when": "card_count>1 or duplicate_thesis_detected=true",
            "skill_file": "skills/wow-cross-ticket-exposure-governor-SKILL.md",
        },
        # LLP v16 upgrade skills
        "llp_moneyline_probability": {
            "name": "wow.llp-moneyline-probability-expert",
            "version": "v16-2026-08-01",
            "required": False,
            "required_when": "lane=LLP and market_type=MONEYLINE_WINNER",
            "skill_file": "skills/wow-llp-moneyline-probability-expert-SKILL.md",
        },
        "llp_slate_integrity": {
            "name": "wow.llp-slate-integrity-expert",
            "version": "v1",
            "required": False,
            "required_when": "lane=LLP",
            "skill_file": "skills/wow-llp-slate-integrity-expert-SKILL.md",
        },
        "llp_market_normalization": {
            "name": "wow.llp-market-normalization-expert",
            "version": "v1",
            "required": False,
            "required_when": "lane=LLP",
            "skill_file": "skills/wow-llp-market-normalization-expert-SKILL.md",
        },
        "llp_dynamic_calibration": {
            "name": "wow.llp-dynamic-calibration-expert",
            "version": "v1",
            "required": False,
            "required_when": "lane=LLP",
            "skill_file": "skills/wow-llp-dynamic-calibration-expert-SKILL.md",
        },
        "llp_failure_path": {
            "name": "wow.llp-failure-path-expert",
            "version": "v1",
            "required": False,
            "required_when": "lane=LLP",
            "skill_file": "skills/wow-llp-failure-path-expert-SKILL.md",
        },
        "llp_final_refresh_governor": {
            "name": "wow.llp-final-refresh-governor",
            "version": "v1",
            "required": False,
            "required_when": "lane=LLP",
            "skill_file": "skills/wow-llp-final-refresh-governor-SKILL.md",
        },
    },
    "hard_flags": {
        "can_execute": False,
        "dry_run_only": True,
        "mlb_k_less_ceiling": "WATCH_ONLY",
        "mlb_outs_more_ceiling": "MODEL_QUALIFIED_HOLD",
        "wnba_composite_forward_test": True,
        "cross_ticket_deduplication": True,
        "wnba_opportunity_gate": True,                    # PATCH-WNBA-001
        "cross_slip_portfolio_governor": True,            # PATCH-PORTFOLIO-001
        "cross_slip_persistent_governance_db": True,      # PATCH-PORTFOLIO-002
        # 2026-08-01 postmortem
        "cross_slip_duplicate_guard": True,               # PATCH-2026-08-01-CROSS-SLIP-DUPLICATE-GUARD
        "mlb_1ip_efficiency_gap_enforce": True,           # PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE
        "pitch_count_directional_asymmetry": True,        # PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY
        # Multi-Window Prop Persistence & Distribution Audit
        "prop_persistence_score_enabled":     True,       # PATCH-2026-08-01-MULTI-WINDOW-*
        "window_agreement_classification":    True,
        "threshold_cushion_metrics":          True,
        "recent_form_divergence_detection":   True,
        "hit_rate_inflation_audit":           True,
        "same_player_opportunity_mutex":      True,
        "same_game_correlated_leg_detection": True,
        "next_day_preview_separation":        True,
        "law_of_averages_support_blocked":    True,
        "hot_streak_as_probability_blocked":  True,
        "one_game_sample_blocked":            True,
        "mlb_binomial_hit_model_v1":          True,
        "wnba_points_normal_model_v1":        True,
        "wnba_assists_poisson_model_v1":      True,
        "wnba_threes_binomial_model_v1":      True,
        # Linemakers Presentation & Self-Audit
        "linemakers_candidate_audit_table": True,         # PATCH-2026-08-01-LINEMAKERS-*
        "linemakers_evidence_manifest": True,             # ticker identity warning (not block)
        "linemakers_second_pass_audit": True,             # 7-check consistency audit
        "linemakers_reconciliation_equation": True,       # rows_scanned = Σ(buckets) + qualified
        "linemakers_unified_calibration_ledger": True,    # wow_unified_calibration_ledger
        "sports_gate_event_state_mutex": True,            # Gate 0: LIVE_MARKET_DISABLED
        "orderbook_terminology_sanitation": True,         # no midpoint labeled no-vig
        "lb_edge_vs_point_edge_separation": True,         # explicit separation in output
        "candidate_funnel_summary": True,                 # compact end-of-run funnel report
        # LLP v16 upgrade
        "llp_slate_integrity_lock": True,                 # PATCH-2026-08-01-LLP-SLATE-INTEGRITY-*
        "llp_market_normalization": True,                 # two-way and three-way exact no-vig
        "llp_dynamic_calibration": True,                  # candidate-specific uncertainty; fixed haircut prohibited
        "llp_failure_path_model": True,                   # exact-market regime decomposition
        "llp_final_refresh_governor": True,               # mandatory ≤5-min pre-output recheck
        "llp_probability_edge_lane_separation": True,     # separate probability and edge leaderboards
        # 2026-08-02 — LLP Matchup/EV/Pipeline Integrity
        "matchup_pa_floor_25_enforced": True,             # PATCH-2026-08-02-LLP-MATCHUP-EV-INTEGRITY
        "absence_of_data_neutrality": True,               # zero matchup = DATA_UNAVAILABLE, not negative signal
        "ev_claim_four_field_audit_gate": True,           # model_prob+fair_odds+book+timestamp required
        "variance_vs_safety_separation": True,            # VARIANCE_INCREASE label on downgrade pitched as safer
        "upstream_dependency_lock": True,                 # PIPELINE_INTEGRITY_FAILURE → drop candidate
        # 2026-08-02 — LLP Slip Construction Integrity
        "cross_book_parlay_detection": True,              # PATCH-2026-08-02-LLP-SLIP-CONSTRUCTION-INTEGRITY
        "same_game_correlated_stack_detection": True,     # ML + prop same game dependency detection
        "selective_recency_consistency_check": True,      # recency override requires rule citation
    },
}

# ---------------------------------------------------------------------------
# Manifest governance hash
# Computed from the list of active_patch_ids, deterministic.
# ---------------------------------------------------------------------------

def _compute_manifest_hash() -> str:
    raw = json.dumps(
        sorted(WOW_RUNTIME_MANIFEST["active_patch_ids"]),
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


MANIFEST_GOVERNANCE_HASH: str = _compute_manifest_hash()

# ---------------------------------------------------------------------------
# Skill ID constants (versioned)
# ---------------------------------------------------------------------------

SKILL_SLIP_OPTIMIZER       = "wow.slip-probability-optimizer:v3"
SKILL_WNBA_COMPOSITE       = "wow.wnba-composite-prop-expert:v1"
SKILL_MLB_PITCHER          = "wow.mlb-pitcher-failure-path-expert:v2"
SKILL_K_LESS_FIREWALL      = "MLB_K_LESS_DIRECTIONAL_FIREWALL"
SKILL_OUTS_MORE_GATE       = "MLB_OUTS_MORE_SURVIVAL_GATE"
SKILL_CROSS_TICKET         = "wow.cross-ticket-exposure-governor:v1"

# ---------------------------------------------------------------------------
# Composite stat families for WNBA routing
# ---------------------------------------------------------------------------

_WNBA_COMPOSITE_STAT_FAMILIES = {
    "pra", "points rebounds assists",
    "p r", "points rebounds",    # P+R normalised
    "p a", "points assists",     # P+A normalised
    "r a", "rebounds assists",   # R+A normalised
    "points", "rebounds", "assists",
    "pts", "reb", "ast",
    # raw strings that may come in from GPT
    "p+r", "p+a", "r+a",
    "points+rebounds", "points+assists", "rebounds+assists",
}

# Pitcher prop stat names (normalised lower-case)
_PITCHER_STATS = {
    "pitcher strikeouts", "strikeouts",
    "pitching outs", "outs recorded",
    "pitches thrown", "batters faced",
    "innings pitched", "1st inning pitches thrown",
}


def _norm_stat(s: str) -> str:
    return (s or "").lower().strip().replace("+", " ").replace("-", " ").replace("_", " ")


def _is_wnba_composite(row: dict) -> bool:
    stat = _norm_stat(row.get("stat_type") or row.get("stat_family") or
                      row.get("prop") or row.get("prop_type") or "")
    sport = (row.get("sport") or "").upper()
    return sport == "WNBA" and stat in _WNBA_COMPOSITE_STAT_FAMILIES


def _is_mlb_pitcher(row: dict) -> bool:
    sport = (row.get("sport") or "").upper()
    if sport != "MLB":
        return False
    stat = _norm_stat(row.get("stat_type") or row.get("stat_family") or
                      row.get("prop") or row.get("prop_type") or "")
    participant = (row.get("participant_type") or "").upper()
    return participant == "PITCHER" or stat in _PITCHER_STATS


def _is_k_less(row: dict) -> bool:
    sport = (row.get("sport") or "").upper()
    stat = _norm_stat(row.get("stat_type") or row.get("stat_family") or
                      row.get("prop") or "")
    direction = (row.get("direction") or row.get("side") or "").upper()
    return sport == "MLB" and "strikeout" in stat and direction == "LESS"


def _is_outs_more(row: dict) -> bool:
    sport = (row.get("sport") or "").upper()
    stat = _norm_stat(row.get("stat_type") or row.get("stat_family") or
                      row.get("prop") or "")
    direction = (row.get("direction") or row.get("side") or "").upper()
    return (sport == "MLB" and
            ("pitching out" in stat or "outs recorded" in stat) and
            direction == "MORE")


# ---------------------------------------------------------------------------
# Core router
# ---------------------------------------------------------------------------

def determine_required_skills(
    rows: list[dict],
    cards: list[dict] | None = None,
) -> set[str]:
    """
    Determine which specialist skills are required for this input.

    Args:
        rows:  normalised leg/row objects from the request
        cards: optional list of card/slip objects (for cross-ticket detection)

    Returns:
        Set of skill IDs (versioned strings) that MUST run.
    """
    required: set[str] = {SKILL_SLIP_OPTIMIZER}  # always required

    has_wnba_composite = False
    has_mlb_pitcher    = False
    has_k_less         = False
    has_outs_more      = False

    for row in rows:
        if _is_wnba_composite(row):
            has_wnba_composite = True
        if _is_mlb_pitcher(row):
            has_mlb_pitcher = True
        if _is_k_less(row):
            has_k_less = True
        if _is_outs_more(row):
            has_outs_more = True

    if has_wnba_composite:
        required.add(SKILL_WNBA_COMPOSITE)

    if has_mlb_pitcher:
        required.add(SKILL_MLB_PITCHER)

    if has_k_less:
        required.add(SKILL_K_LESS_FIREWALL)

    if has_outs_more:
        required.add(SKILL_OUTS_MORE_GATE)

    # Cross-ticket governor: needed if > 1 card, or if the same player appears
    # on multiple rows (potential duplicate thesis).
    need_cross_ticket = False
    if cards and len(cards) > 1:
        need_cross_ticket = True
    elif not cards and len(rows) > 1:
        player_keys: set[str] = set()
        for row in rows:
            p = (row.get("player_name") or row.get("player") or "").lower().strip()
            e = (row.get("event_id") or row.get("event") or "").lower().strip()
            s = _norm_stat(row.get("stat_type") or row.get("prop") or "")
            key = f"{p}|{e}|{s}"
            if key in player_keys:
                need_cross_ticket = True
                break
            player_keys.add(key)

    if need_cross_ticket:
        required.add(SKILL_CROSS_TICKET)

    return required


def verify_skills_completed(
    required: set[str],
    completed: set[str],
) -> set[str]:
    """
    Return the set of required skills that did NOT complete.

    If any are missing the caller MUST return REQUIRED_SKILL_NOT_EXECUTED (422).
    """
    return required - completed


# ---------------------------------------------------------------------------
# Lowest-ceiling resolver
# ---------------------------------------------------------------------------

CEILING_RANK: dict[str, int] = {
    "NO_DATA_QUALITY":       0,
    "SLATE_PURGE":           0,
    "REJECT":                0,
    "PORTFOLIO_REJECTED":    0,
    "REJECTED":              0,
    "SCOUT":                 1,
    "RESEARCH_INTEREST":     1,
    "WATCH":                 2,
    "WATCH_ONLY":            2,
    "MLB_K_LESS_WATCH_ONLY": 2,
    "MODEL_QUALIFIED_HOLD":  3,
    "MARKET_VERIFIED_HOLD":  4,
    "MONEY_QUALIFIED":       5,
    "FINAL_APPROVED":        6,
    "YES_MODEL_QUALIFIED":   3,  # slip optimizer alias
    "NO_BAD_STRUCTURE":      0,  # slip optimizer alias
}


def resolve_lowest_ceiling(ceilings: list[str]) -> str:
    """
    Return the lowest (most restrictive) ceiling from a list.
    Unknown ceiling strings sort to rank 99 (treated as permissive).
    """
    if not ceilings:
        return "NO_DATA_QUALITY"
    return min(ceilings, key=lambda c: CEILING_RANK.get(c, 99))


# ---------------------------------------------------------------------------
# Audit block builder
# ---------------------------------------------------------------------------

def build_audit_block(
    research_run_id: str | None,
    skills_required: set[str],
    skills_invoked: list[dict],   # [{"skill": ..., "version": ..., "status": ...}]
    ceilings_applied: list[str],
    lowest_ceiling: str,
    patches_applied: list[str] | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    """
    Build the standard invocation-evidence block that every analysis response
    must include.  ChatGPT treats a response as incomplete when this block
    is absent.

    Args:
        research_run_id:   caller-supplied or auto-generated run ID
        skills_required:   set of skill IDs that were required
        skills_invoked:    list of {"skill": str, "version": str, "status": str}
        ceilings_applied:  list of ceiling label strings that were enforced
        lowest_ceiling:    single lowest-ceiling resolution
        patches_applied:   list of active patch IDs (defaults to manifest list)
        extra:             any additional k/v to merge in

    Returns:
        dict suitable for JSON serialisation in every response body.
    """
    run_id = research_run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    return {
        "research_run_id":  run_id,
        "wow_version":      WOW_RUNTIME_MANIFEST["wow_version"],
        "governance_hash":  MANIFEST_GOVERNANCE_HASH,
        "patches_applied":  patches_applied or WOW_RUNTIME_MANIFEST["active_patch_ids"],
        "skills_required":  sorted(skills_required),
        "skills_invoked":   skills_invoked,
        "ceilings_applied": ceilings_applied,
        "lowest_ceiling":   lowest_ceiling,
        "can_execute":      False,
        **(extra or {}),
    }


# ---------------------------------------------------------------------------
# Skill file validation (used by pre_start and health endpoint)
# ---------------------------------------------------------------------------

_REQUIRED_SKILL_FILES = {
    "wow-slip-probability-optimizer-SKILL-v3.md",
    "wow-wnba-composite-prop-expert-SKILL.md",
    "wow-mlb-pitcher-failure-path-expert-SKILL-v2.md",
    "wow-cross-ticket-exposure-governor-SKILL.md",
    "WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE.md",
}


def validate_skill_files(base_dir: str | None = None) -> dict[str, Any]:
    """
    Verify all required skill markdown files are present in the skills/ directory.

    Returns:
        {
          "status":  "HEALTHY" | "DEGRADED",
          "signal":  "SKILL_FILES_OK" | "REQUIRED_SKILL_MISSING",
          "present": [...],
          "missing": [...],
        }
    """
    if base_dir is None:
        # Locate relative to this file's directory
        base_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
    base_dir = os.path.abspath(base_dir)

    present = []
    missing = []
    for fname in sorted(_REQUIRED_SKILL_FILES):
        path = os.path.join(base_dir, fname)
        if os.path.isfile(path):
            present.append(fname)
        else:
            missing.append(fname)

    return {
        "status":  "HEALTHY" if not missing else "DEGRADED",
        "signal":  "SKILL_FILES_OK" if not missing else "REQUIRED_SKILL_MISSING",
        "present": present,
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# Loaded-skills map (used by /wow/patch-flags)
# ---------------------------------------------------------------------------

def loaded_skills_map() -> dict[str, str]:
    """Return {skill_name: version} for all skills in the manifest."""
    return {
        meta["name"]: meta["version"]
        for meta in WOW_RUNTIME_MANIFEST["skills"].values()
    }


# ---------------------------------------------------------------------------
# Slip calibration helpers (used by /wow/settle-slip)
# ---------------------------------------------------------------------------

def _make_player_event_stat_key(leg: dict) -> str:
    """Build the unique-thesis key for a leg (player + event + stat + direction)."""
    player    = (leg.get("player") or leg.get("player_name") or "").lower().strip()
    event     = (leg.get("event_id") or leg.get("event") or leg.get("game") or "").lower().strip()
    stat      = _norm_stat(leg.get("prop") or leg.get("stat_type") or "")
    direction = (leg.get("side") or leg.get("direction") or "").upper()
    return f"{player}|{event}|{stat}|{direction}"


def compute_settlement_calibration(legs: list[dict]) -> dict[str, Any]:
    """
    Separate financial exposure rows from unique underlying thesis rows.

    Three Morrow PRA thresholds (17.5, 18.5, 19.0) on the same event count as:
    - financial_exposure_rows = 3   (3 legs on the slip)
    - unique_underlying_thesis_rows = 1  (one player-event-stat-direction thesis)

    A DNP/void does not count as a thesis observation.
    """
    financial_rows = len(legs)
    thesis_seen: set[str] = set()
    duplicate_groups: dict[str, list[int]] = {}  # key → [indices]

    for i, leg in enumerate(legs):
        key = _make_player_event_stat_key(leg)
        if key:
            thesis_seen.add(key)
            if key not in duplicate_groups:
                duplicate_groups[key] = []
            duplicate_groups[key].append(i)

    # Alternate-threshold groups: same thesis key but different lines
    alt_threshold_groups = [
        {
            "duplicate_group_id": key,
            "leg_indices":        indices,
            "financial_rows":     len(indices),
            "unique_theses":      1,
        }
        for key, indices in duplicate_groups.items()
        if len(indices) > 1
    ]

    unique_thesis_rows = len(thesis_seen)

    return {
        "financial_exposure_rows":      financial_rows,
        "unique_underlying_thesis_rows": unique_thesis_rows,
        "alternate_threshold_groups":   alt_threshold_groups,
        "has_duplicates": any(len(v) > 1 for v in duplicate_groups.values()),
    }
