"""
gate_engine/universal_agent/lanes/mlb_moneyline/field_map.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3A

Pure deterministic field extraction for MLB moneyline evidence rows.

All functions:
  - Accept the row dict as read-only input (no mutation).
  - Return structured evidence dicts / scalars.
  - Use explicit "MISSING" / "UNKNOWN" sentinels for absent fields.
  - Never fabricate probability estimates or status values.

Field sources mapped from the WOW/LLP MLB moneyline pipeline:
  starter_status / starter_source  ← llp_mlb_winner_preflight Gate 1
  lineup_status  / lineup_source   ← llp_mlb_winner_preflight Gate 1
  event_status   / weather_status  ← llp_mlb_winner_preflight Gate 2
  sportsbook_no_vig_probability    ← moneyline_probability + preflight Gate 3
  kalshi_multiplier / breakeven    ← llp_mlb_winner_preflight Gate 3
  model_probability / cal_lb       ← moneyline_probability scoring output
  preflight_status / blockers      ← llp_mlb_winner_preflight enforcement

can_execute = False
"""
from __future__ import annotations

from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Sentinel strings used in source_coverage
_AVAILABLE = "available"
_MISSING   = "missing"

# All row fields this adapter reads — fixed audit trail
SOURCE_ROW_FIELDS_USED: tuple[str, ...] = (
    "event_id", "sport", "market", "prop_type",
    "team", "opponent", "team_id", "opponent_id",
    "home_team", "away_team", "home_team_id", "away_team_id",
    "slate_date", "event_date",
    "starter_status", "starter_source", "starter_as_of",
    "lineup_status",  "lineup_source",
    "event_status",   "weather_status", "weather_source",
    "kalshi_multiplier", "kalshi_breakeven_probability", "breakeven_gap",
    "sportsbook_no_vig_probability", "consensus_no_vig_probability",
    "model_probability", "calibrated_probability_lower_bound",
    "candidate_odds", "team_odds", "opponent_odds", "odds_source",
    "preflight_checked", "preflight_status", "preflight_blockers",
    "upgrade_allowed",
    "pulled_at", "as_of", "odds_pulled_at",
    "data_stale", "is_stale",
    "source_conflicts", "gates",
)


# ── Identity extraction ───────────────────────────────────────────────────────

def extract_canonical_event_id(row: dict) -> str:
    """Return canonical event id. Caller must have already validated it exists."""
    return str(row["event_id"]).strip()


def extract_event_name(row: dict) -> str | None:
    """Build a human-readable event name from team vs opponent fields."""
    team     = (row.get("team") or row.get("home_team") or "").strip()
    opponent = (row.get("opponent") or row.get("away_team") or "").strip()
    if team and opponent:
        return f"{team} vs {opponent}"
    return team or opponent or None


def extract_event_date(row: dict) -> str | None:
    """Return slate_date / event_date as a string, or None if absent."""
    d = row.get("slate_date") or row.get("event_date")
    return str(d).strip() if d else None


def extract_team_identity(row: dict) -> dict[str, str | None]:
    """Return team/opponent name and ID fields (all optional)."""
    return {
        "team_id":            row.get("team_id") or row.get("home_team_id"),
        "team_name":          row.get("team") or row.get("home_team"),
        "opponent_team_id":   row.get("opponent_id") or row.get("away_team_id"),
        "opponent_team_name": row.get("opponent") or row.get("away_team"),
    }


# ── Source metadata ───────────────────────────────────────────────────────────

def extract_source_timestamps(row: dict) -> dict[str, str]:
    """
    Collect source timestamps from the row.
    Never fabricates timestamps — returns only keys that are present.
    """
    ts: dict[str, str] = {}
    for key in ("pulled_at", "as_of", "odds_pulled_at", "starter_as_of", "lineup_as_of"):
        val = row.get(key)
        if val is not None:
            ts[key] = str(val)
    return ts


def extract_source_provenance(row: dict) -> dict[str, str]:
    """Collect source provenance references from the row."""
    prov: dict[str, str] = {}
    for key in (
        "starter_source", "lineup_source",
        "weather_source", "odds_source",
        "model_source",   "event_source",
    ):
        val = row.get(key)
        if val is not None:
            prov[key] = str(val)
    return prov


# ── Market snapshot ───────────────────────────────────────────────────────────

def extract_market_snapshot(row: dict) -> dict[str, Any]:
    """
    Extract raw market evidence for the moneyline market.
    All fields read-only from the row; None when absent.
    """
    return {
        "market_type":                   row.get("market") or row.get("prop_type"),
        "sportsbook_no_vig_probability": row.get("sportsbook_no_vig_probability"),
        "kalshi_multiplier":             row.get("kalshi_multiplier"),
        "kalshi_breakeven_probability":  row.get("kalshi_breakeven_probability"),
        "breakeven_gap":                 row.get("breakeven_gap"),
        "candidate_odds":                row.get("candidate_odds") or row.get("team_odds"),
        "opponent_odds":                 row.get("opponent_odds"),
        "consensus_no_vig_probability":  row.get("consensus_no_vig_probability"),
        "preflight_status":              row.get("preflight_status"),
        "preflight_blockers":            list(row.get("preflight_blockers") or []),
    }


# ── Deterministic model inputs ────────────────────────────────────────────────

def extract_deterministic_model_inputs(row: dict) -> dict[str, Any]:
    """
    Extract deterministic model inputs for the SPORT_SPECIALIST role.
    All values are read-only from the row; None when absent.
    """
    return {
        "model_probability":                  row.get("model_probability"),
        "calibrated_probability_lower_bound": row.get("calibrated_probability_lower_bound"),
        "starter_status":                     row.get("starter_status"),
        "lineup_status":                      row.get("lineup_status"),
        "event_status":                       row.get("event_status"),
        "weather_status":                     row.get("weather_status"),
        "upgrade_allowed":                    row.get("upgrade_allowed"),
    }


# ── Failure and conflict evidence ─────────────────────────────────────────────

def extract_source_failures(row: dict) -> list[dict[str, Any]]:
    """
    Build source_failures list from preflight_blockers.
    Hard blockers (Gate 3) are severity=HIGH; watch blockers severity=LOW.
    """
    failures: list[dict[str, Any]] = []
    blockers = row.get("preflight_blockers") or []
    gate     = (row.get("gates") or {}).get("mlb_winner_preflight") or {}
    hard_set = set(gate.get("hard_blockers") or [])

    for blocker in blockers:
        failures.append({
            "source":   "mlb_winner_preflight",
            "reason":   str(blocker),
            "severity": "HIGH" if blocker in hard_set else "LOW",
        })
    return failures


def extract_source_conflicts(row: dict) -> list[dict[str, Any]]:
    """
    Build source_conflicts list from any explicit conflict fields in the row.
    Returns empty list if no conflicts recorded.
    """
    conflicts: list[dict[str, Any]] = []
    for item in (row.get("source_conflicts") or []):
        if isinstance(item, dict):
            conflicts.append(item)
    return conflicts


# ── DATA_SLATE_INTEGRITY derivation helpers ───────────────────────────────────

def build_source_coverage(row: dict) -> dict[str, str]:
    """
    Return a source_coverage dict mapping each evidence key to
    "available" or "missing". Used by DATA_SLATE_INTEGRITY role.
    """
    checks: dict[str, Any] = {
        "starter_status":            row.get("starter_status"),
        "lineup_status":             row.get("lineup_status"),
        "event_status":              row.get("event_status"),
        "weather_status":            row.get("weather_status"),
        "sportsbook_no_vig":         row.get("sportsbook_no_vig_probability"),
        "kalshi_multiplier":         row.get("kalshi_multiplier"),
        "model_probability":         row.get("model_probability"),
        "calibrated_probability_lb": row.get("calibrated_probability_lower_bound"),
    }
    return {k: (_AVAILABLE if v is not None else _MISSING) for k, v in checks.items()}


def build_data_gaps(row: dict) -> list[str]:
    """
    Return sorted list of "MISSING:{field_key}" strings for absent coverage fields.
    Empty list when all coverage fields are present.
    """
    coverage = build_source_coverage(row)
    return sorted(
        f"MISSING:{key}"
        for key, status in coverage.items()
        if status == _MISSING
    )


def derive_data_freshness(row: dict) -> str:
    """
    Derive FRESHNESS_STATE for DATA_SLATE_INTEGRITY.

    MISSING  — both sportsbook_no_vig_probability AND model_probability absent
               (cannot assess market or model quality at all).
    STALE    — row explicitly marked stale.
    FRESH    — source timestamps present and no stale flag.
    UNKNOWN  — some fields present but cannot confirm freshness.

    Never fabricates — degrades to UNKNOWN on ambiguous state.
    """
    no_vig_missing = row.get("sportsbook_no_vig_probability") is None
    model_missing  = row.get("model_probability") is None

    if no_vig_missing and model_missing:
        return "MISSING"

    stale_flag = row.get("data_stale") or row.get("is_stale")
    if stale_flag:
        return "STALE"

    if row.get("pulled_at") or row.get("as_of"):
        return "FRESH"

    return "UNKNOWN"


def derive_slate_consistency(row: dict) -> str:
    """
    Derive CONSISTENCY_STATE from preflight outcome.

    CONSISTENT   — preflight PASS or WATCH (evidence internally consistent,
                   possibly with soft gaps).
    INCONSISTENT — preflight FAIL or FAIL_POSTPONEMENT (hard gate failures,
                   or game cancelled/postponed).
    UNKNOWN      — preflight not checked or status not recognised.
    """
    status = row.get("preflight_status")
    if status in ("PASS", "WATCH"):
        return "CONSISTENT"
    if status in ("FAIL", "FAIL_POSTPONEMENT"):
        return "INCONSISTENT"
    return "UNKNOWN"


# ── NEWS_STATUS derivation helpers ────────────────────────────────────────────

# starter_status → NEWS_STATUS player_status enum mapping
# Sources: llp_mlb_winner_preflight Gate 1 documented values
_STARTER_TO_PLAYER_STATUS: dict[str, str] = {
    "CONFIRMED":       "ACTIVE",
    "PROBABLE_STRONG": "ACTIVE",
    "PROBABLE_ONLY":   "QUESTIONABLE",
    "DOUBTFUL":        "DOUBTFUL",
    "SCRATCHED":       "OUT",
    "OUT":             "OUT",
}


def map_starter_to_player_status(starter_status: str | None) -> str:
    """
    Map a preflight starter_status value to the NEWS_STATUS player_status enum.

    None or unrecognised → "UNKNOWN" (explicit, not fabricated).
    Mapping is based on starter confirmation values from
    llp_mlb_winner_preflight._STARTER_PASS / watch thresholds.
    """
    if starter_status is None:
        return "UNKNOWN"
    return _STARTER_TO_PLAYER_STATUS.get(starter_status, "UNKNOWN")


# ── MARKET_EXACT_LINE derivation helpers ──────────────────────────────────────

def derive_market_status(row: dict) -> str:
    """
    Map event_status to MARKET_STATUS_STATES for MARKET_EXACT_LINE role.

    OPEN      — event is SCHEDULED or ACTIVE_PREGAME_VALID.
    SUSPENDED — event is SUSPENDED.
    CLOSED    — event is POSTPONED or CANCELLED (pick is dead).
    UNKNOWN   — event_status absent or not recognised.
    """
    event = row.get("event_status")
    if event in ("POSTPONED", "CANCELLED"):
        return "CLOSED"
    if event == "SUSPENDED":
        return "SUSPENDED"
    if event in ("SCHEDULED", "ACTIVE_PREGAME_VALID"):
        return "OPEN"
    return "UNKNOWN"


# ── SPORT_SPECIALIST derivation helpers ───────────────────────────────────────

def derive_assessment_confidence(row: dict) -> str:
    """
    Derive CONFIDENCE_STATE for SPORT_SPECIALIST assessment.

    HIGH   — all three core model fields present AND preflight PASS.
    MEDIUM — ≥2 core model fields present AND preflight PASS or WATCH.
    LOW    — ≥1 core model field present.
    UNKNOWN — no model fields obtainable.
    """
    model_ok  = row.get("model_probability") is not None
    cal_ok    = row.get("calibrated_probability_lower_bound") is not None
    no_vig_ok = row.get("sportsbook_no_vig_probability") is not None
    preflight = row.get("preflight_status")

    present = sum([model_ok, cal_ok, no_vig_ok])
    if present == 3 and preflight == "PASS":
        return "HIGH"
    if present >= 2 and preflight in ("PASS", "WATCH"):
        return "MEDIUM"
    if present >= 1:
        return "LOW"
    return "UNKNOWN"


# ── FAILURE_CONTRADICTION derivation helpers ──────────────────────────────────

def derive_contradiction_severity(row: dict) -> str:
    """
    Derive SEVERITY_STATE from preflight gate hard/watch blocker lists.

    HIGH    — any hard blockers present (Gate 3 failure or price data missing).
    LOW     — only watch blockers present (Gate 1/2 soft gaps).
    NONE    — preflight PASS with no blockers.
    UNKNOWN — preflight not checked.
    """
    gate  = (row.get("gates") or {}).get("mlb_winner_preflight") or {}
    hard  = gate.get("hard_blockers") or []
    watch = gate.get("watch_blockers") or []

    if hard:
        return "HIGH"
    if watch:
        return "LOW"
    if row.get("preflight_status") == "PASS":
        return "NONE"
    return "UNKNOWN"


def derive_resolution_recommendation(row: dict) -> str:
    """
    Map preflight_status to FAILURE_CONTRADICTION resolution_recommendation.

    PROCEED — preflight PASS (all gates cleared).
    HOLD    — preflight WATCH (soft gaps; may resolve closer to game time).
    ABORT   — preflight FAIL or FAIL_POSTPONEMENT (hard gates failed or postponed).
    UNKNOWN — preflight not checked.
    """
    mapping = {
        "PASS":              "PROCEED",
        "WATCH":             "HOLD",
        "FAIL":              "ABORT",
        "FAIL_POSTPONEMENT": "ABORT",
    }
    return mapping.get(row.get("preflight_status"), "UNKNOWN")


def derive_failure_detected(row: dict) -> bool:
    """
    Return True if any preflight blocker (hard or watch) is present,
    OR if critical model fields are entirely absent.
    """
    blockers = row.get("preflight_blockers") or []
    if blockers:
        return True
    # Critical fields absent without preflight running
    model_missing = row.get("model_probability") is None
    no_vig_missing = row.get("sportsbook_no_vig_probability") is None
    return model_missing and no_vig_missing


def derive_contradiction_detected(row: dict) -> bool:
    """
    Return True if hard blockers are present.
    Hard blockers indicate a pricing/model conflict (e.g. no-vig below breakeven,
    model lower-bound below breakeven). These are evidence-level contradictions.
    """
    gate = (row.get("gates") or {}).get("mlb_winner_preflight") or {}
    hard = gate.get("hard_blockers") or []
    return bool(hard)


# ── FINAL_REFRESH derivation helpers ─────────────────────────────────────────

def derive_refresh_status(data_gaps: list[str]) -> str:
    """
    Return REFRESH_STATUS_STATE based on data availability.

    COMPLETE — no evidence gaps identified.
    PARTIAL  — some evidence fields are missing.
    """
    return "COMPLETE" if not data_gaps else "PARTIAL"


def derive_evidence_snapshot_valid(row: dict) -> bool:
    """
    Return True unless the preflight gate explicitly failed (hard reject or
    postponement). PASS / WATCH / unchecked all return True.
    """
    return row.get("preflight_status") not in ("FAIL", "FAIL_POSTPONEMENT")
