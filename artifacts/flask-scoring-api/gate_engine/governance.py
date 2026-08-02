"""
governance.py — WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE

Canonical active-patch registry and governance handshake.

Every prop-scoring run must supply expected_governance_hash.
A mismatch returns RUN_INVALID_GOVERNANCE_MISMATCH (HTTP 409 at route level).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Engine identity
# ---------------------------------------------------------------------------
MASTER_SPEC_VERSION  = "WOW-v16"
ENGINE_CODE_VERSION  = "v16.5"

# ---------------------------------------------------------------------------
# Canonical patch registry
# ---------------------------------------------------------------------------
_PATCH_REGISTRY: list[dict[str, Any]] = [
    {
        "patch_id":    "WOW-CORE-v16",
        "version":     "16.0",
        "effective_at": "2026-06-01",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  0,
        "can_execute": False,
        "description": "WOW v16 Clean Core — base gate engine",
    },
    {
        "patch_id":    "WOW-PATCH-2026-06-27-SHARP-ANCHOR",
        "version":     "1.0",
        "effective_at": "2026-06-27",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  10,
        "can_execute": False,
        "description": "Sharp market anchor + house rules matrix",
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-07-JS-STYLE",
        "version":     "1.0",
        "effective_at": "2026-07-07",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  20,
        "can_execute": False,
        "description": "JS-style conversion + slip governance",
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-10-COMBO-SETTLEMENT",
        "version":     "1.0",
        "effective_at": "2026-07-10",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  30,
        "can_execute": False,
        "description": "Combo & settlement governance (Rules A-G)",
    },
    {
        "patch_id":    "WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0",
        "version":     "1.0",
        "effective_at": "2026-07-14",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  40,
        "can_execute": False,
        "description": "Mandatory data acquisition and reconstruction",
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE",
        "version":     "1.0",
        "effective_at": "2026-07-15",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  50,
        "can_execute": False,
        "description": (
            "Prop calibration, exposure, and slip governance: "
            "settlement-aware market delta; source ceilings; "
            "component/composite mutex; opportunity-state consistency; "
            "duplicate exposure; promo rules; calibration suspension; "
            "Prop Reliability Freeze 2026-07-15 through 2026-07-22"
        ),
        "freeze_start": "2026-07-15",
        "freeze_end":   "2026-07-22",
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-15-LLP-DATA-ACQUISITION-RESILIENCE",
        "version":     "1.0",
        "effective_at": "2026-07-15",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  60,
        "can_execute": False,
        "description": (
            "LLP data acquisition resilience: league-scoped event identity; "
            "provider market aliases (moneyline→h2h); UTC normalization; "
            "league-aware time tolerance; doubleheader detection; "
            "PrizePicks decimal/American disambiguation; "
            "two-book no-vig consensus reconstruction with outlier filtering; "
            "source ceilings by data quality; anti-circular model probability; "
            "contract-stage reporting. "
            "Extends WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-15-PROP-CONFIDENCE-AND-MARKET-LABEL-SEPARATION",
        "version":     "1.0",
        "effective_at": "2026-07-15",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  70,
        "can_execute": False,
        "description": (
            "Prop confidence/market/money/slip decision separation: "
            "analysis_mode (HIT_CONFIDENCE/MARKET_EDGE/SLIP_EV/FULL_APPROVAL); "
            "payout scope — missing payout blocks MONEY_QUALIFIED only, never HIT_CONFIDENCE; "
            "governance degradation — local valid + remote unavailable → DEGRADED "
            "  (research/confidence allowed, money/final_approved blocked); "
            "market evidence labels (MARKET_UNVERIFIED_HOLD/ONE_SIDED_MARKET_SUPPORT/"
            "MARKET_CORROBORATED_HOLD/MARKET_VERIFIED_HOLD); "
            "strict two-sided no-vig; adjacent-line interpolation uncertainty; "
            "confidence labels (FINAL_CONFIDENCE_HIGH/MEDIUM/LOW/UNOBTAINABLE); "
            "probability audit (PROVISIONAL when incomplete); "
            "board-source classification (screenshot → research only); "
            "same-game correlation — narrative alone never blocks individual confidence; "
            "four-decision terminal output: confidence/market/money/slip; "
            "FINAL_CONFIDENCE_HIGH never aliases FINAL_APPROVED."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE",
        "version":     "1.0",
        "effective_at": "2026-07-30",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  85,
        "can_execute": False,
        "description": (
            "WNBA composite forward-test gate (PATCH-017): MODEL_QUALIFIED_HOLD ceiling "
            "until 20 unique player-games settled in wnba_composite_forward_test_ledger; "
            "promo upgrades blocked on unresolved role/status. "
            "MLB directional firewall (PATCH-015/016): K LESS → WATCH_ONLY unconditional; "
            "K LESS with short_outing_support_share > 0.50 → HIGH_CONFIDENCE_SUSPENDED; "
            "OUTS MORE → MODEL_QUALIFIED_HOLD; conditional-used-as-unconditional blocked. "
            "Cross-ticket exposure governor (PATCH-014): exact duplicate rejection, "
            "alternate-threshold deduplication, Power/Flex copy detection, "
            "portfolio fragility scoring (DIVERSIFIED/CONCENTRATED/FRAGILE). "
            "Runtime manifest, skill router, audit block in every response; "
            "fail-closed on missing required specialist. "
            "Skill files: wow-slip-probability-optimizer-SKILL-v3.md, "
            "wow-wnba-composite-prop-expert-SKILL.md, "
            "wow-mlb-pitcher-failure-path-expert-SKILL-v2.md, "
            "wow-cross-ticket-exposure-governor-SKILL.md."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-15-GOVERNANCE-RESILIENCE-AND-ERROR-CONTRACT",
        "version":     "1.0",
        "effective_at": "2026-07-15",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  80,
        "can_execute": False,
        "description": (
            "Governance resilience and structured error contract: "
            "distinct error codes (GOVERNANCE_UNAVAILABLE / "
            "GOVERNANCE_CACHED_DEGRADED_RUN / GOVERNANCE_MISMATCH / "
            "GOVERNANCE_CONTRACT_INVALID / SCAN_UNAVAILABLE_DEGRADED_RUN); "
            "GOVERNANCE_UNAVAILABLE and GOVERNANCE_MISMATCH are never "
            "interchangeable — unavailable=no comparison made, mismatch="
            "comparison failed; "
            "in-process GovernanceSnapshot cache (default 5-min TTL) allows "
            "degraded research run at MODEL_QUALIFIED_HOLD when live endpoint "
            "is transiently unreachable; "
            "RunGovernancePin pins verified governance identity to run_id at "
            "handshake success — mid-run outages cannot erase already-verified "
            "governance; "
            "GET /wow/engine/health (no external I/O, sub-ms) reports process "
            "health, governance load, snapshot metadata, and DB env state; "
            "make_error_contract() returns retryable/retry_after/stage/label_ceiling "
            "on every failure so callers can distinguish transient from deterministic; "
            "degraded ceiling table: UNAVAILABLE→RESEARCH_INTEREST, "
            "CACHED_DEGRADED→MODEL_QUALIFIED_HOLD, MISMATCH→run_invalid. "
            "Extends all active v16 patches."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-WNBA-001-OPPORTUNITY-STABILITY-GATE",
        "version":     "1.0",
        "effective_at": "2026-07-31",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  90,
        "can_execute": False,
        "description": (
            "WNBA Opportunity and Role Engine (Stage 1): "
            "gates WNBA rows on opportunity stability before the probability pipeline. "
            "Computes minutes_stability_score, usage_stability_score, "
            "shot_attempt_stability_score, rotation_volatility_score, "
            "opportunity_stability_score (0-100 composite), role_state, role_confidence, archetype. "
            "Hard reject: OSS < 65 (70 for PRA) → WNBA_REJECT_UNSTABLE_OPPORTUNITY; "
            "rotation_volatility > 80 → WNBA_REJECT_ROTATION_VOLATILITY; "
            "Soft hold: role_confidence < 0.80 → WNBA_HOLD_ROLE_UNCERTAIN (MODEL_QUALIFIED_HOLD ceiling). "
            "Missing game log (< 3 non-DNP games) → soft hold. "
            "Non-WNBA rows untouched. can_execute=False unconditional. "
            "Module: gate_engine/wnba/opportunity_engine.py. "
            "New endpoint: POST /wow/wnba/opportunity-audit. "
            "New skill: wow.wnba-opportunity-scenario-and-exposure-governor:v1."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-PORTFOLIO-001-CROSS-SLIP-EXPOSURE-GOVERNOR",
        "version":     "1.0",
        "effective_at": "2026-07-31",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  91,
        "can_execute": False,
        "description": (
            "Cross-Slip Portfolio Exposure Governor (Stage 1 foundation): "
            "session-level thesis and market-family deduplication, "
            "complementary to PgSessionLedger (player/game/archetype). "
            "Market-family dedup: same player + stat_family (any line/direction) → max 1 per session; "
            "catches alternate-line exposure (PRA 19.5 and PRA 22.5 on same player = same distribution). "
            "Thesis dedup: same player + stat + direction → max 1 per session. "
            "Block labels: REJECT_CROSS_SLIP_CONCENTRATION, REJECT_DUPLICATE_THESIS. "
            "In-memory fallback path preserved for offline/test environments. "
            "Module: gate_engine/portfolio/cross_slip_exposure.py. "
            "New endpoint: POST /wow/session/exposure-audit. "
            "can_execute=False unconditional."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-PORTFOLIO-002-CROSS-SLIP-PERSISTENT-GOVERNANCE",
        "version":     "1.0",
        "effective_at": "2026-07-31",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  92,
        "can_execute": False,
        "description": (
            "Cross-Slip Persistent Governance (Stage 2A): "
            "Promotes the in-memory PortfolioExposureGovernor to a DB-backed "
            "PgPortfolioGovernor that catches duplicate exposure across separate "
            "/gate-engine/run calls, not only within a single request. "
            "Uses SELECT FOR UPDATE to prevent race conditions. "
            "Fail-closed: any DB error → SESSION_LEDGER_UNAVAILABLE blocks the run. "
            "Slate-date expiry: prior-date rows are invisible to dedup check "
            "(a new slate begins clean without a purge). "
            "Tables: wow_portfolio_dedup (dedup sentinel + lock target), "
            "wow_portfolio_exposure_log (full audit log with player, event, "
            "stat_family, direction, market_line, distribution_key, thesis_key, "
            "source_ts, decision_label, blockers). "
            "New labels: REJECT_DUPLICATE_PLAYER_EXPOSURE, RUN_INVALID_SESSION_ID_MISSING. "
            "New endpoint: GET /wow/session/exposure-inspect. "
            "Module: gate_engine/portfolio/pg_portfolio_governor.py. "
            "can_execute=False unconditional."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-2026-08-01-CROSS-SLIP-DUPLICATE-GUARD",
        "version":     "1.0",
        "effective_at": "2026-08-01",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  93,
        "can_execute": False,
        "description": (
            "Cross-Slip Duplicate Guard (2026-08-01 postmortem): "
            "Session-level thesis exposure ledger in wow_session_thesis_exposure. "
            "Tracks exact duplicate legs (same player/stat/line/side) and "
            "shared-distribution groups (same player/stat/side, alternate lines). "
            "Exposure tiers: TIER_0=PASS, TIER_1=PASS_WITH_DISCLOSURE (0-20%), "
            "TIER_2=HOLD_CONFIRMATION_REQUIRED (dist-family>20% or unknown denom), "
            "TIER_3=HARD_STOP_CROSS_SLIP_OVEREXPOSURE (exact dup>20%). "
            "TIER_3 cannot be overridden. "
            "Exposure source precedence: session ledger > open unsettled rows > "
            "same-slate proposed rows > workbook fallback. "
            "Gate runs AFTER weakest-leg elimination and card fragility, "
            "BEFORE final card output. "
            "Module: gate_engine/portfolio/slip_exposure_ledger.py. "
            "Table: wow_session_thesis_exposure. "
            "can_execute=False unconditional."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE",
        "version":     "1.0",
        "effective_at": "2026-08-01",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  94,
        "can_execute": False,
        "description": (
            "1IP Efficiency Gap Enforcement (2026-08-01 postmortem): "
            "Mandatory pre-event-tree efficiency audit for all 1IP pitch-count "
            "LESS candidates. Seven Tier-1 metrics (P/BF, pitches/start, walk rate, "
            "first-pitch strike, zone rate, BB rate, CSW rate) weighted 0.20/0.20/"
            "0.15/0.15/0.10/0.10/0.10. Each scored 0.0/0.5/1.0. "
            "Three Tier-2 modifiers (WHIP, hard-hit, chase rate) add up to 0.10. "
            "Fewer than 4 of 7 Tier-1 metrics → EFFICIENCY_SCORE_INCOMPLETE → "
            "cap LESS at MODEL_QUALIFIED_HOLD. "
            "Bands: STABLE(<0.30), MILD_DETERIORATION(0.30-0.50, haircut -0.02), "
            "MATERIAL_DETERIORATION(0.50-0.70, cap=HOLD), "
            "SEVERE_DETERIORATION(>=0.70, cap=WATCH). "
            "ERA and xERA are contextual only, receive no weight. "
            "Postgame data must not be used in pregame regrade. "
            "Module: gate_engine/mlb/first_inning_efficiency.py. "
            "can_execute=False unconditional."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY",
        "version":     "1.0",
        "effective_at": "2026-08-01",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  95,
        "can_execute": False,
        "description": (
            "Pitch-Count Directional Asymmetry (2026-08-01 postmortem): "
            "Mandatory post-event-tree Directional Fragility Score (DFS) for all "
            "1IP pitch-count LESS candidates. "
            "DFS = 0.35*three_batter_less_dependence + 0.30*extended_inning_loss_rate "
            "+ 0.20*right_tail_mass + 0.15*min(1, uncertainty_gap/0.10). "
            "Bands: LOW(<0.55), MODERATE(0.55-0.70, -0.02 lower bound), "
            "HIGH(0.70-0.80, cap=HOLD), SEVERE(>=0.80, cap=WATCH). "
            "Hard override: three_batter_less_dependence>=0.80 AND "
            "P(MORE|BF>=4)>=0.70 → SEVERE unconditionally. "
            "Lowest-ceiling propagation: event-tree → efficiency → directional → "
            "market/payout → slip → cross-slip exposure → final. "
            "No downstream pass may erase an upstream ceiling. "
            "Module: gate_engine/mlb/first_inning_efficiency.py. "
            "can_execute=False unconditional."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY-DYNAMIC-CALIBRATION-AND-FINAL-REFRESH",
        "version":     "1.0",
        "effective_at": "2026-08-01",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  96,
        "can_execute": False,
        "description": (
            "LLP v16 Upgrade — Slate Integrity, Dynamic Calibration, and Final Refresh (2026-08-01): "
            "Converts LLP from a candidate-ranking assistant into a slate-locked, "
            "market-normalized, dynamically calibrated, failure-aware, automatically refreshed "
            "outright-winner and upset auditing engine under WOW v16 Clean Core. "
            "Mandatory 12-step call order: governance_sync → full_slate_discovery → "
            "slate_integrity_lock → exact_market_and_settlement_lock → "
            "critical_participant_lock → independent_sport_model → market_normalization → "
            "dynamic_calibration → failure_path_model → probability_and_edge_lane_separation → "
            "final_refresh_governor → lowest_ceiling_output. "
            "No step may be skipped; a downstream pass cannot erase an upstream blocker. "
            "Slate integrity hard blocks: WRONG_DATE, WRONG_YEAR, EVENT_NOT_FOUND, "
            "EVENT_ALREADY_STARTED, EVENT_FINISHED, EVENT_POSTPONED, EVENT_CANCELED, "
            "DUPLICATE_TEAM_EVENT, PARTICIPANT_IDENTITY_CONFLICT. "
            "Market normalization: exact two-way and three-way no-vig (sum=1.0000±0.0005); "
            "soccer full-time requires Home/Draw/Away prices; "
            "raw_implied_probability, market_hold, no_vig_probability may never be merged. "
            "Dynamic calibration: fixed universal haircut prohibited as sole method; "
            "candidate-specific uncertainty required (base_calibration_error + sport_volatility + "
            "sample_size + lineup + injury + source_conflict + market_disagreement + freshness); "
            "no calibration evidence → UNCALIBRATED_MODEL → ceiling=WATCH. "
            "Failure-path model: P(win)=Σ P(regime_i)×P(win|regime_i); moneyline paths must "
            "produce outright loss; backdoor-cover is not a moneyline failure path. "
            "Lane separation: probability rank by calibrated_probability_lower_bound (price excluded); "
            "edge rank by lower_bound_edge = calibrated_lower_bound − no_vig − friction_buffer; "
            "point edge may never be labeled lower-bound edge. "
            "Final refresh governor: mandatory recheck ≤5 min before output; price ≤10 min; "
            "status ≤15 min; weather ≤30 min; any failure removes row. "
            "New skills: wow.llp-slate-integrity-expert, wow.llp-market-normalization-expert, "
            "wow.llp-dynamic-calibration-expert, wow.llp-failure-path-expert, "
            "wow.llp-final-refresh-governor. "
            "Updated skill: wow.llp-moneyline-probability-expert. "
            "can_execute=False unconditional."
        ),
    },
]

# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------

def _active_patches() -> list[dict[str, Any]]:
    return [p for p in _PATCH_REGISTRY if p.get("status") == "ACTIVE"]


def compute_governance_hash(patches: list[dict[str, Any]] | None = None) -> str:
    """
    Deterministic SHA-256 hash of (patch_id, version) pairs for all active patches,
    sorted by patch_id, encoded as UTF-8 JSON.
    """
    if patches is None:
        patches = _active_patches()
    fingerprint = sorted(
        [{"patch_id": p["patch_id"], "version": p["version"]} for p in patches],
        key=lambda x: x["patch_id"],
    )
    raw = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_GOVERNANCE_HASH: str = compute_governance_hash()
_ACTIVE_PATCH_IDS: list[str] = [p["patch_id"] for p in _active_patches()]
# _LOADED_AT is a runtime observability field only — NOT part of the hash.
_LOADED_AT: str = datetime.now(timezone.utc).isoformat()

# Derive effective_at / expires_at from the latest active patch (by precedence).
def _latest_active_patch() -> dict[str, Any]:
    active = _active_patches()
    return max(active, key=lambda p: p.get("precedence", 0)) if active else {}

_LATEST_PATCH = _latest_active_patch()


# ---------------------------------------------------------------------------
# Governance status
# ---------------------------------------------------------------------------

def get_governance_status() -> dict[str, Any]:
    """
    Return the full governance status object for the GET endpoint.

    Required fields (per spec):
      master_spec_version, active_patch_ids, governance_hash,
      engine_code_version, effective_at, expires_at, can_execute
    """
    return {
        "master_spec_version":  MASTER_SPEC_VERSION,
        "engine_code_version":  ENGINE_CODE_VERSION,
        "active_patch_ids":     list(_ACTIVE_PATCH_IDS),
        "governance_hash":      _GOVERNANCE_HASH,
        "loaded_at":            _LOADED_AT,
        "effective_at":         _LATEST_PATCH.get("effective_at"),
        "expires_at":           _LATEST_PATCH.get("expires_at"),
        "patch_count":          len(_active_patches()),
        "patches":              _active_patches(),
        "status":               "ACTIVE",
        "can_execute":          False,
    }


# ---------------------------------------------------------------------------
# Handshake validation
# ---------------------------------------------------------------------------

def validate_handshake(
    expected_hash: str | None,
    expected_patch_ids: list[str] | None = None,
    expected_master_spec_version: str | None = None,
) -> dict[str, Any]:
    """
    Validate the caller's governance expectations against the server's state.

    Returns:
        {
          valid:   bool
          code:    "GOVERNANCE_MATCH" | "RUN_INVALID_GOVERNANCE_MISMATCH"
          detail:  str
          server_hash: str
          expected_hash: str | None
        }
    """
    server_hash = _GOVERNANCE_HASH
    mismatches: list[str] = []

    if expected_hash is not None and expected_hash != server_hash:
        mismatches.append(
            f"governance_hash mismatch: expected={expected_hash[:16]}… "
            f"server={server_hash[:16]}…"
        )

    if expected_master_spec_version is not None:
        if expected_master_spec_version != MASTER_SPEC_VERSION:
            mismatches.append(
                f"master_spec_version mismatch: "
                f"expected={expected_master_spec_version} server={MASTER_SPEC_VERSION}"
            )

    if expected_patch_ids is not None:
        expected_set = set(expected_patch_ids)
        server_set   = set(_ACTIVE_PATCH_IDS)
        missing_from_server = expected_set - server_set
        missing_from_caller = server_set - expected_set
        if missing_from_server:
            mismatches.append(f"patch_ids caller expects but server lacks: {missing_from_server}")
        if missing_from_caller:
            mismatches.append(f"patch_ids server has but caller did not list: {missing_from_caller}")

    if mismatches:
        return {
            "valid":         False,
            "code":          "RUN_INVALID_GOVERNANCE_MISMATCH",
            "can_execute":   False,
            "detail":        "; ".join(mismatches),
            "server_hash":   server_hash,
            "expected_hash": expected_hash,
            "mismatches":    mismatches,
        }

    return {
        "valid":         True,
        "code":          "GOVERNANCE_MATCH",
        "can_execute":   False,
        "detail":        "Governance hash and patch registry match.",
        "server_hash":   server_hash,
        "expected_hash": expected_hash,
        "mismatches":    [],
    }


# ---------------------------------------------------------------------------
# Prop Reliability Freeze helpers
# ---------------------------------------------------------------------------

FREEZE_START = "2026-07-15"
FREEZE_END   = "2026-07-22"


def is_in_prop_reliability_freeze(as_of: str | None = None) -> bool:
    """Return True if as_of (YYYY-MM-DD) falls within the freeze window."""
    try:
        d = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return FREEZE_START <= d <= FREEZE_END
    except Exception:
        return False
