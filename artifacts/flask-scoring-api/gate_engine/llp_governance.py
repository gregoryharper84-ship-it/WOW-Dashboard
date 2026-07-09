"""
llp_governance.py
LLP-PATCH-2026-06-27 Execution Governance v16.1

Prevents model signals from becoming bets without:
  - verified price edge
  - timing validity
  - contradiction clearance
  - calibration
  - exposure control

This module classifies, validates, and logs. It does NOT approve bets.
Final betting decisions remain with LLP/WOW.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 1. Labels
# ---------------------------------------------------------------------------
class LLPLabel(str, Enum):
    APPROVED  = "LLP_APPROVED"
    PLAYABLE  = "LLP_PLAYABLE"
    WATCH     = "LLP_WATCH"
    SCOUT     = "LLP_SCOUT"
    REJECT    = "LLP_REJECT"
    CUT       = "LLP_CUT"


BANNED_AS_FINAL = {
    "LEAN", "CONDITIONAL", "FLIP_CANDIDATE",
    "SOURCE_CONFLICT", "DATA_UNOBTAINABLE", "NO_BET", "STALE_LINE",
}

LABEL_ORDER = [
    LLPLabel.CUT, LLPLabel.REJECT, LLPLabel.SCOUT,
    LLPLabel.WATCH, LLPLabel.PLAYABLE, LLPLabel.APPROVED,
]


def _label_rank(label: str) -> int:
    for i, l in enumerate(LABEL_ORDER):
        if l.value == label:
            return i
    return -1


def cap_label(current: str, ceiling: str) -> str:
    """Return the more restrictive of current and ceiling."""
    if _label_rank(current) > _label_rank(ceiling):
        return ceiling
    return current


# ---------------------------------------------------------------------------
# 2. Edge thresholds by market type
# ---------------------------------------------------------------------------
class MarketType(str, Enum):
    LIQUID_MAIN     = "LIQUID_MAIN"
    WNBA_LOW_LIQ    = "WNBA_LOW_LIQ"
    DERIVATIVES     = "DERIVATIVES"
    ALT_NICHE       = "ALT_NICHE"


EDGE_THRESHOLD = {
    MarketType.LIQUID_MAIN:  0.015,
    MarketType.WNBA_LOW_LIQ: 0.020,
    MarketType.DERIVATIVES:  0.025,
    MarketType.ALT_NICHE:    0.030,
}

MARKET_TYPE_KEYWORDS: list[tuple[list[str], MarketType]] = [
    (["wnba"],                                     MarketType.WNBA_LOW_LIQ),
    (["f5", "1h", "first 5", "team total", "derivative"], MarketType.DERIVATIVES),
    (["alt", "niche", "prop"],                     MarketType.ALT_NICHE),
]


def _detect_market_type(market: str | None) -> MarketType:
    if not market:
        return MarketType.LIQUID_MAIN
    m = market.lower()
    for keywords, mtype in MARKET_TYPE_KEYWORDS:
        if any(k in m for k in keywords):
            return mtype
    return MarketType.LIQUID_MAIN


# ---------------------------------------------------------------------------
# 3. Probability cap tiers
# ---------------------------------------------------------------------------
def _prob_ceiling(prob: float) -> str:
    """Return max allowed LLP label for a given model probability."""
    if prob < 0.52:
        return LLPLabel.REJECT.value
    if prob < 0.55:
        return LLPLabel.WATCH.value
    if prob < 0.58:
        return LLPLabel.PLAYABLE.value
    if prob <= 0.60:
        return LLPLabel.APPROVED.value
    return LLPLabel.APPROVED.value


# ---------------------------------------------------------------------------
# 4. Freshness windows (minutes before game start)
# ---------------------------------------------------------------------------
FRESHNESS_WINDOWS = [
    (timedelta(hours=6),  timedelta(hours=999), 30),   # >6h before: check within 30m
    (timedelta(hours=2),  timedelta(hours=6),   15),   # 2–6h: 15m
    (timedelta(minutes=30), timedelta(hours=2),  5),   # <2h: 5m
    (timedelta(minutes=0),  timedelta(minutes=30), 2), # <30m: 2m (final lock)
]

FINAL_LOCK_WINDOW = timedelta(minutes=30)
FINAL_LOCK_MAX_AGE = timedelta(minutes=2)


def _required_freshness_minutes(time_to_start: timedelta) -> int:
    """Return max allowed line age (minutes) given time_to_start."""
    for low, high, minutes in FRESHNESS_WINDOWS:
        if low <= time_to_start < high:
            return minutes
    return 30


# ---------------------------------------------------------------------------
# 5. Steam thresholds
# ---------------------------------------------------------------------------
STEAM_RERUN_THRESHOLD     = 0.015   # 1.5% consensus move → rerun
STEAM_DOWNGRADE_THRESHOLD = 0.025   # 2.5% → downgrade to WATCH


# ---------------------------------------------------------------------------
# 6. Session exposure defaults
# ---------------------------------------------------------------------------
DEFAULT_EXPOSURE = {
    "max_bets_per_day":       3,
    "normal_daily_cap_units": 1.5,
    "hard_daily_cap_units":   2.0,
    "max_per_game_units":     1.0,
    "max_same_script_units":  1.25,
}

PRICE_EDGE_REQUIRED_FIELDS = [
    "book", "odds", "line", "side", "market",
    "timestamp", "model_probability", "no_vig_probability",
    "edge", "source",
]

# All 20 fields required for calibration grading.
# `opener` is intentionally excluded — missing opener = OPENER_UNAVAILABLE
# (caps confidence, limits market-movement analysis, but does NOT block CLV grading).
# CLV grading requires `close` + `odds`; missing `close` = NO_CLOSE_AVAILABLE (blocks CLV).
CALIBRATION_LEDGER_FIELDS = [
    "date", "sport", "league", "market", "side", "odds", "line",
    "book", "close", "model_probability", "no_vig_probability",
    "edge", "stake", "final_label", "failure_tags", "clv", "result",
    "roi", "brier_bucket", "postmortem_note",
]

# CLV grading requires entry price + closing line. Opener is separate.
CALIBRATION_CLV_REQUIRED   = {"odds", "close"}
CALIBRATION_OPENER_FIELD   = "opener"

# ---------------------------------------------------------------------------
# TU1 — Calibration graduation tiers
# FULL_KELLY renamed to avoid aggressive language; bankroll + PATCH-L
# Reliability Freeze + calibration gates still override all stake sizing.
# ---------------------------------------------------------------------------
FULL_FRACTIONAL_KELLY_ELIGIBLE = (
    "FULL_FRACTIONAL_KELLY_ELIGIBLE — eligible for the highest allowed "
    "fractional Kelly tier only if bankroll, PATCH-L, and calibration gates all permit."
)

CALIBRATION_GRADUATION_TIERS = {
    "<25 candidates":   "MICRO_STAKE_ONLY (0.25u cap)",
    "25–49 candidates": "HALF_UNIT_CAP (0.50u max)",
    "50–99 candidates": "RELIABILITY_FREEZE (quarter-Kelly max)",
    "100+ candidates":  FULL_FRACTIONAL_KELLY_ELIGIBLE,
}

# ---------------------------------------------------------------------------
# TU3 — LLP_PLAYABLE hard stake caps
# LLP_PLAYABLE cannot become a backdoor full-stake bet.
# ---------------------------------------------------------------------------
PLAYABLE_STAKE_CAPS = {
    "pre_25_candidates_max_units":  0.25,   # before 25 logged candidates
    "pre_100_candidates_max_units": 0.50,   # before 100 logged candidates
    "reliability_freeze_max_units": 0.25,   # during Reliability Freeze (quarter-Kelly)
}

CALLEDGER_PATH = os.environ.get(
    "LLP_CALIBRATION_LEDGER_PATH", "/tmp/llp_calibration_ledger.jsonl"
)


# ---------------------------------------------------------------------------
# Validator result helper
# ---------------------------------------------------------------------------
def _ok(code: str = "OK", detail: str = "",
        ceiling: str | None = None) -> dict[str, Any]:
    return {"passed": True,  "code": code, "detail": detail, "ceiling": ceiling}


def _fail(code: str, detail: str = "",
          ceiling: str | None = None) -> dict[str, Any]:
    return {"passed": False, "code": code, "detail": detail, "ceiling": ceiling}


# ---------------------------------------------------------------------------
# Validator 1: label validity
# ---------------------------------------------------------------------------
def validate_llp_label(label: str | None) -> dict[str, Any]:
    """Only the 6 allowed final labels are valid. Banned labels rejected."""
    if not label:
        return _fail("NO_LABEL", "label is required")

    upper = label.strip().upper()
    allowed = {l.value for l in LLPLabel}
    if upper in allowed:
        return _ok("LABEL_VALID", f"label={upper}")

    if upper in BANNED_AS_FINAL:
        return _fail("BANNED_FINAL_LABEL",
                     f"{upper} may only appear as a status/failure tag, "
                     "not as a final action label")

    return _fail("UNKNOWN_LABEL", f"{upper} is not a recognized LLP label")


# ---------------------------------------------------------------------------
# Validator 2: price edge fields
# ---------------------------------------------------------------------------
def validate_price_edge_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    """All required price edge fields must be present for PLAYABLE/APPROVED."""
    label = (candidate.get("final_label") or "").upper()
    if label in (LLPLabel.WATCH.value, LLPLabel.SCOUT.value,
                 LLPLabel.REJECT.value, LLPLabel.CUT.value):
        return _ok("PRICE_FIELDS_NOT_REQUIRED", f"label={label} does not require full price packet")

    missing = [f for f in PRICE_EDGE_REQUIRED_FIELDS if not candidate.get(f)]
    if missing:
        return _fail("MISSING_PRICE_FIELDS",
                     f"Required for {label}: {', '.join(missing)}",
                     ceiling=LLPLabel.SCOUT.value)

    return _ok("PRICE_FIELDS_COMPLETE")


# ---------------------------------------------------------------------------
# Validator 3: edge threshold
# ---------------------------------------------------------------------------
def validate_edge_threshold(candidate: dict[str, Any]) -> dict[str, Any]:
    """edge = model_prob - no_vig_prob must clear threshold for market type."""
    label = (candidate.get("final_label") or "").upper()
    if label in (LLPLabel.WATCH.value, LLPLabel.SCOUT.value,
                 LLPLabel.REJECT.value, LLPLabel.CUT.value):
        return _ok("EDGE_CHECK_SKIPPED", f"label={label}")

    edge = candidate.get("edge")
    model_prob  = candidate.get("model_probability")
    no_vig_prob = candidate.get("no_vig_probability")
    market      = candidate.get("market")

    if edge is None and model_prob is not None and no_vig_prob is not None:
        try:
            edge = float(model_prob) - float(no_vig_prob)
        except (TypeError, ValueError):
            edge = None

    if edge is None:
        return _fail("NO_EDGE_VALUE",
                     "Cannot compute edge — no_vig_probability or model_probability missing",
                     ceiling=LLPLabel.SCOUT.value)

    try:
        edge = float(edge)
    except (TypeError, ValueError):
        return _fail("EDGE_UNPARSEABLE", f"edge={edge}", ceiling=LLPLabel.SCOUT.value)

    mtype = _detect_market_type(market)
    threshold = EDGE_THRESHOLD[mtype]

    if edge < threshold:
        return _fail("EDGE_BELOW_THRESHOLD",
                     f"edge={edge:.4f} < {threshold:.4f} ({mtype.value})",
                     ceiling=LLPLabel.SCOUT.value)

    return _ok("EDGE_CLEARS", f"edge={edge:.4f} >= {threshold:.4f} ({mtype.value})")


# ---------------------------------------------------------------------------
# Validator 4: probability cap
# ---------------------------------------------------------------------------
def validate_probability_cap(candidate: dict[str, Any]) -> dict[str, Any]:
    """Map model_probability to maximum allowed label."""
    prob_raw = candidate.get("model_probability")
    label    = (candidate.get("final_label") or "").upper()

    if prob_raw is None:
        return _fail("NO_MODEL_PROBABILITY", "model_probability required",
                     ceiling=LLPLabel.SCOUT.value)

    try:
        prob = float(prob_raw)
    except (TypeError, ValueError):
        return _fail("PROB_UNPARSEABLE", f"model_probability={prob_raw}",
                     ceiling=LLPLabel.SCOUT.value)

    ceiling = _prob_ceiling(prob)

    if label and _label_rank(label) > _label_rank(ceiling):
        return _fail("PROB_CEILING_BREACH",
                     f"model_probability={prob:.4f} limits label to {ceiling}; "
                     f"requested={label}",
                     ceiling=ceiling)

    return _ok("PROB_CAP_OK", f"prob={prob:.4f} ceiling={ceiling}", ceiling=ceiling)


# ---------------------------------------------------------------------------
# Validator 5: timing freshness
# ---------------------------------------------------------------------------
def validate_timing_freshness(candidate: dict[str, Any]) -> dict[str, Any]:
    """Line age must be within the freshness window for time_to_start."""
    ts_raw      = candidate.get("timestamp")
    start_raw   = candidate.get("game_start_time")
    has_final_lock = bool(candidate.get("final_lock_confirmed"))

    if not ts_raw:
        return _fail("NO_TIMESTAMP", "timestamp required",
                     ceiling=LLPLabel.WATCH.value)

    try:
        ts = _parse_ts(ts_raw)
    except ValueError:
        return _fail("UNPARSEABLE_TIMESTAMP", f"timestamp={ts_raw}",
                     ceiling=LLPLabel.WATCH.value)

    now = datetime.now(timezone.utc)
    line_age = now - ts

    if start_raw:
        try:
            start = _parse_ts(start_raw)
            time_to_start = start - now
            if time_to_start < timedelta(0):
                time_to_start = timedelta(0)
        except ValueError:
            time_to_start = timedelta(hours=3)
    else:
        time_to_start = timedelta(hours=3)

    max_age_m = _required_freshness_minutes(time_to_start)
    max_age   = timedelta(minutes=max_age_m)

    if line_age > max_age:
        return _fail("LINE_STALE",
                     f"Line age {_fmt_delta(line_age)} exceeds {max_age_m}m window "
                     f"(time_to_start={_fmt_delta(time_to_start)})",
                     ceiling=LLPLabel.WATCH.value)

    if time_to_start < FINAL_LOCK_WINDOW and not has_final_lock:
        return _fail("NO_FINAL_LOCK",
                     f"<30m to start requires final_lock_confirmed=True",
                     ceiling=LLPLabel.WATCH.value)

    return _ok("TIMING_FRESH",
               f"line_age={_fmt_delta(line_age)} within {max_age_m}m window")


# ---------------------------------------------------------------------------
# Validator 6: steam protocol
# ---------------------------------------------------------------------------
def validate_steam_protocol(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    consensus_drift = consensus_implied_prob_now - consensus_implied_prob_at_approval
    (positive value = market moved against thesis)
    """
    drift_raw = candidate.get("consensus_implied_drift")
    label     = (candidate.get("final_label") or "").upper()

    if drift_raw is None:
        return _ok("STEAM_CHECK_SKIPPED", "consensus_implied_drift not provided")

    try:
        drift = float(drift_raw)
    except (TypeError, ValueError):
        return _fail("STEAM_DRIFT_UNPARSEABLE", f"consensus_implied_drift={drift_raw}")

    if drift >= STEAM_DOWNGRADE_THRESHOLD:
        return _fail("STEAM_DOWNGRADE",
                     f"Consensus moved {drift:.3f} against thesis (>={STEAM_DOWNGRADE_THRESHOLD}) — "
                     "downgrade to LLP_WATCH unless edge still clears",
                     ceiling=LLPLabel.WATCH.value)

    if drift >= STEAM_RERUN_THRESHOLD:
        return _fail("STEAM_RERUN_REQUIRED",
                     f"Consensus moved {drift:.3f} against thesis (>={STEAM_RERUN_THRESHOLD}) — rerun required")

    return _ok("NO_ADVERSE_STEAM", f"consensus_drift={drift:.4f}")


# ---------------------------------------------------------------------------
# Validator 7: contradiction hard kills
# ---------------------------------------------------------------------------
HARD_KILL_FIELDS = [
    "market_move_against_thesis",
    "key_player_contradiction",
    "lineup_contradiction",
    "stale_price",
    "missing_timestamp",
    "source_conflict",
    "weather_park_contradiction",
    "wrong_slate",
    "unavailable_price",
]


def validate_contradiction_kills(candidate: dict[str, Any]) -> dict[str, Any]:
    """Any hard kill condition = LLP_REJECT or LLP_CUT."""
    kills = [f for f in HARD_KILL_FIELDS if candidate.get(f)]

    if kills:
        return _fail("HARD_KILL",
                     f"Hard kill condition(s): {kills} — must be LLP_REJECT or LLP_CUT")

    return _ok("NO_CONTRADICTIONS")


# ---------------------------------------------------------------------------
# Validator 8: session exposure
# ---------------------------------------------------------------------------
def validate_session_exposure(candidate: dict[str, Any],
                               session: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Enforce daily caps, per-game/same-script exposure, and LLP_PLAYABLE stake caps.

    LLP_PLAYABLE hard stake caps (TU3):
      Never above 0.25u before 25 logged candidates.
      Never above 0.50u before 100 logged candidates.
      During Reliability Freeze: quarter-Kelly max (0.25u cap).
      LLP_PLAYABLE cannot become a backdoor full-stake bet.
    """
    session = session or {}
    label = (candidate.get("final_label") or "").upper()

    if label in (LLPLabel.WATCH.value, LLPLabel.SCOUT.value,
                 LLPLabel.REJECT.value, LLPLabel.CUT.value):
        return _ok("EXPOSURE_CHECK_SKIPPED", f"label={label} requires no stake")

    bets_today         = int(session.get("bets_today", 0))
    units_today        = float(session.get("units_today", 0.0))
    game_units         = float(session.get("units_this_game", 0.0))
    script_units       = float(session.get("units_same_script", 0.0))
    stake              = float(candidate.get("stake") or 0.0)
    calibrated         = bool(session.get("calibration_verified", False))
    same_side_dup      = bool(candidate.get("duplicate_same_side", False))
    candidates_logged  = int(session.get("candidates_logged", 0))
    reliability_freeze = bool(session.get("reliability_freeze", False))

    hard_cap = DEFAULT_EXPOSURE["hard_daily_cap_units"] if calibrated \
               else DEFAULT_EXPOSURE["normal_daily_cap_units"]
    blockers: list[str] = []

    if bets_today >= DEFAULT_EXPOSURE["max_bets_per_day"]:
        blockers.append(f"MAX_BETS_REACHED:{bets_today}>={DEFAULT_EXPOSURE['max_bets_per_day']}")

    if units_today + stake > hard_cap:
        blockers.append(f"DAILY_CAP_BREACH:{units_today + stake:.2f}u > {hard_cap:.2f}u")

    if game_units + stake > DEFAULT_EXPOSURE["max_per_game_units"]:
        blockers.append(f"GAME_CAP_BREACH:{game_units + stake:.2f}u > {DEFAULT_EXPOSURE['max_per_game_units']:.2f}u")

    if script_units + stake > DEFAULT_EXPOSURE["max_same_script_units"]:
        blockers.append(f"SAME_SCRIPT_BREACH:{script_units + stake:.2f}u > {DEFAULT_EXPOSURE['max_same_script_units']:.2f}u")

    if same_side_dup:
        blockers.append("DUPLICATE_SAME_SIDE_EXPOSURE")

    # TU3 — LLP_PLAYABLE hard stake caps (cannot be a backdoor full-stake bet)
    if label == LLPLabel.PLAYABLE.value:
        if reliability_freeze:
            playable_cap = PLAYABLE_STAKE_CAPS["reliability_freeze_max_units"]
            if stake > playable_cap:
                blockers.append(
                    f"PLAYABLE_RELIABILITY_FREEZE_CAP:stake={stake:.2f}u > "
                    f"{playable_cap:.2f}u (quarter-Kelly max during freeze)"
                )
        elif candidates_logged < 25:
            playable_cap = PLAYABLE_STAKE_CAPS["pre_25_candidates_max_units"]
            if stake > playable_cap:
                blockers.append(
                    f"PLAYABLE_STAKE_CAP:stake={stake:.2f}u > {playable_cap:.2f}u "
                    f"(pre-25 candidates; logged={candidates_logged})"
                )
        elif candidates_logged < 100:
            playable_cap = PLAYABLE_STAKE_CAPS["pre_100_candidates_max_units"]
            if stake > playable_cap:
                blockers.append(
                    f"PLAYABLE_STAKE_CAP:stake={stake:.2f}u > {playable_cap:.2f}u "
                    f"(pre-100 candidates; logged={candidates_logged})"
                )

    if blockers:
        return _fail("EXPOSURE_LIMIT", f"Blockers: {blockers}",
                     ceiling=LLPLabel.REJECT.value)

    return _ok("EXPOSURE_OK",
               f"bets={bets_today} units={units_today:.2f}u stake={stake:.2f}u")


# ---------------------------------------------------------------------------
# Validator 9: re-approval rules
# ---------------------------------------------------------------------------
def validate_reapproval(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    LLP_WATCH → LLP_APPROVED only via full rerun.
    Invalidated submissions must be logged.
    No chase/hedge unless standalone positive EV.
    """
    prior_label      = (candidate.get("prior_label") or "").upper()
    final_label      = (candidate.get("final_label") or "").upper()
    full_rerun_done  = bool(candidate.get("full_rerun_completed"))
    material_change  = bool(candidate.get("material_change_flagged"))
    invalidated      = bool(candidate.get("invalidated_after_submission"))
    is_chase         = bool(candidate.get("is_chase_or_hedge"))
    standalone_ev    = bool(candidate.get("standalone_positive_ev"))

    blockers: list[str] = []

    if (prior_label == LLPLabel.WATCH.value
            and final_label == LLPLabel.APPROVED.value
            and not full_rerun_done):
        blockers.append("WATCH_TO_APPROVED_WITHOUT_RERUN")

    if (prior_label == LLPLabel.APPROVED.value
            and material_change
            and final_label == LLPLabel.APPROVED.value):
        blockers.append("APPROVED_MATERIAL_CHANGE_NOT_REJECTED")

    if is_chase and not standalone_ev:
        blockers.append("CHASE_HEDGE_WITHOUT_STANDALONE_EV")

    if blockers:
        return _fail("REAPPROVAL_VIOLATION", f"Violations: {blockers}")

    note = ""
    if invalidated:
        note = "INVALIDATED_AFTER_SUBMISSION logged"

    return _ok("REAPPROVAL_OK", note or "No reapproval violations")


# ---------------------------------------------------------------------------
# Validator 10: calibration ledger
# ---------------------------------------------------------------------------
def validate_calibration_ledger(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    All 20 ledger fields must be present before unit scaling is allowed.

    Opener (RC2):
      opener field is separate — its absence = OPENER_UNAVAILABLE.
      This caps confidence and blocks opener/market-movement analysis,
      but does NOT prevent CLV grading when entry (odds) and closing (close) exist.

    CLV grading blockers:
      close missing  → NO_CLOSE_AVAILABLE  (blocks CLV grading)
      odds  missing  → entry price missing  (blocks CLV grading)
      opener missing → OPENER_UNAVAILABLE   (caps confidence only)
    """
    ledger = candidate.get("calibration_ledger") or {}

    # Check the 20 required core fields (opener excluded)
    missing = [f for f in CALIBRATION_LEDGER_FIELDS if f not in ledger]

    # Opener tracked separately
    opener_status = None
    if CALIBRATION_OPENER_FIELD not in ledger:
        opener_status = "OPENER_UNAVAILABLE"

    # CLV grading status
    clv_blocked_by: list[str] = []
    for f in CALIBRATION_CLV_REQUIRED:
        if f not in ledger:
            clv_blocked_by.append(f)

    notes: list[str] = []
    if opener_status:
        notes.append(opener_status)
    if clv_blocked_by:
        notes.append(f"NO_CLV_GRADING:{','.join(clv_blocked_by)}_missing")

    if missing:
        return _fail("CALIBRATION_LEDGER_INCOMPLETE",
                     f"Missing required fields: {', '.join(missing)} — no unit scaling allowed"
                     + (f" | {'; '.join(notes)}" if notes else ""))

    if notes:
        return _ok("CALIBRATION_LEDGER_COMPLETE_WITH_NOTES", " | ".join(notes))

    return _ok("CALIBRATION_LEDGER_COMPLETE")


def log_calibration_entry(entry: dict[str, Any]) -> None:
    """Append one calibration ledger entry to the JSONL log."""
    record = {f: entry.get(f) for f in CALIBRATION_LEDGER_FIELDS}
    record["logged_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(CALLEDGER_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def get_calibration_ledger(limit: int = 200) -> list[dict]:
    """Read the calibration ledger (most recent `limit` entries)."""
    try:
        with open(CALLEDGER_PATH) as f:
            lines = f.readlines()
        return [json.loads(l) for l in lines[-limit:] if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def get_calibration_label_stats() -> dict[str, Any]:
    """
    Return per-label performance counts from the calibration ledger (TU2).

    Includes LLP_CUT so high CUT frequency is visible — it often signals
    tooling/data-acquisition problems (missing odds, missing timestamps,
    no close available) rather than betting signal.

    Returns:
      {
        "label_stats": {
          "LLP_APPROVED": {"total": int, "wins": int, ...},
          "LLP_CUT":      {...},
          ...
        },
        "opener_unavailable_count": int,
        "no_close_available_count": int,
        "calibration_graduation_tiers": {...},
        "can_approve_bets": False,
      }
    """
    entries = get_calibration_ledger(limit=10000)
    label_stats: dict[str, dict[str, int]] = {
        lbl.value: {"total": 0, "wins": 0, "losses": 0, "pushes": 0}
        for lbl in LLPLabel
    }
    opener_unavailable = 0
    no_close_available = 0

    for entry in entries:
        label = entry.get("final_label") or "UNKNOWN"
        result = entry.get("result")
        if label not in label_stats:
            label_stats[label] = {"total": 0, "wins": 0, "losses": 0, "pushes": 0}
        label_stats[label]["total"] += 1
        if result == "WIN":
            label_stats[label]["wins"] += 1
        elif result == "LOSS":
            label_stats[label]["losses"] += 1
        elif result == "PUSH":
            label_stats[label]["pushes"] += 1

        if entry.get("opener") is None:
            opener_unavailable += 1
        if entry.get("close") is None:
            no_close_available += 1

    # Compute hit rates
    for stats in label_stats.values():
        total = stats["total"]
        stats["hit_rate"] = round(stats["wins"] / total, 3) if total else None

    return {
        "label_stats":                label_stats,
        "opener_unavailable_count":   opener_unavailable,
        "no_close_available_count":   no_close_available,
        "calibration_graduation_tiers": CALIBRATION_GRADUATION_TIERS,
        "can_approve_bets":           False,
    }


# ---------------------------------------------------------------------------
# Full governance validator
# ---------------------------------------------------------------------------
ALL_GOVERNANCE_VALIDATORS = [
    ("llp_label",            lambda c, s: validate_llp_label(c.get("final_label"))),
    ("price_edge_fields",    lambda c, s: validate_price_edge_fields(c)),
    ("edge_threshold",       lambda c, s: validate_edge_threshold(c)),
    ("probability_cap",      lambda c, s: validate_probability_cap(c)),
    ("timing_freshness",     lambda c, s: validate_timing_freshness(c)),
    ("steam_protocol",       lambda c, s: validate_steam_protocol(c)),
    ("contradiction_kills",  lambda c, s: validate_contradiction_kills(c)),
    ("session_exposure",     lambda c, s: validate_session_exposure(c, s)),
    ("reapproval",           lambda c, s: validate_reapproval(c)),
    ("calibration_ledger",   lambda c, s: validate_calibration_ledger(c)),
]


def run_llp_governance(candidate: dict[str, Any],
                       session: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Run all LLP governance validators against a candidate.

    Returns:
      {
        passed         bool
        results        dict  — per-validator
        blockers       list[str]
        label_ceiling  str | None
        final_label    str   — most restrictive of requested and all ceilings
        rerun_required bool
        can_approve_bets bool  — always False
      }
    """
    results:  dict[str, Any] = {}
    blockers: list[str]      = []
    ceilings: list[str]      = []
    rerun_required = False

    for name, fn in ALL_GOVERNANCE_VALIDATORS:
        try:
            r = fn(candidate, session)
        except Exception as exc:
            r = _fail(f"VALIDATOR_ERROR:{name}", str(exc))

        results[name] = r
        if not r["passed"]:
            blockers.append(f"{name.upper()}:{r['code']}")
        if r.get("ceiling"):
            ceilings.append(r["ceiling"])
        if r.get("code") in ("STEAM_RERUN_REQUIRED",):
            rerun_required = True

    ceiling = _most_restrictive_label(ceilings)
    requested = candidate.get("final_label") or LLPLabel.SCOUT.value
    effective = cap_label(requested, ceiling) if ceiling else requested

    return {
        "passed":          len(blockers) == 0,
        "results":         results,
        "blockers":        blockers,
        "label_ceiling":   ceiling,
        "effective_label": effective,
        "rerun_required":  rerun_required,
        "can_approve_bets": False,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _most_restrictive_label(labels: list[str]) -> str | None:
    if not labels:
        return None
    return min(labels, key=lambda l: _label_rank(l))


def _parse_ts(raw: str) -> datetime:
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _fmt_delta(d: timedelta) -> str:
    total = int(d.total_seconds())
    if total < 0:
        return "0m"
    h, rem = divmod(total, 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


# ---------------------------------------------------------------------------
# Stake-confidence sizing helper  (v16.1A patch)
# ---------------------------------------------------------------------------

_STAKE_ZERO_LABELS = {
    LLPLabel.CUT.value, LLPLabel.REJECT.value,
    LLPLabel.SCOUT.value, LLPLabel.WATCH.value,
}

# MEDIUM gate thresholds
_MEDIUM_PROB_MIN   = 0.58
_MEDIUM_EDGE_MIN   = 0.025
_MEDIUM_LEDGER_MIN = 25

# HIGH gate thresholds
_HIGH_PROB_MIN   = 0.61
_HIGH_EDGE_MIN   = 0.050
_HIGH_LEDGER_MIN = 100


def compute_stake_confidence(
    final_label:       str,
    model_probability: float | None,
    edge:              float | None,
    blocker_tags:      list[str] | None = None,
    context_flags:     dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compute stake sizing and confidence classification for an LLP candidate.
    (v16.1A patch — see llp-stake-confidence-patch-v16.md)

    Parameters
    ----------
    final_label       : one of the 6 LLP labels (never introduces new labels)
    model_probability : float 0–1 or None
    edge              : post-friction edge float or None if unavailable
    blocker_tags      : list of blocker/warning codes already on the candidate
    context_flags     : optional sizing context dict with boolean/int keys:
        final_lock              bool  — fresh final-lock recheck ran?
        exposure_ok             bool  — session exposure within limits?
        ledger_candidate_count  int   — settled candidates in the ledger
        clv_graduation_ok       bool  — CLV graduation passed?
        timestamp_present       bool  — line timestamp present on candidate?

    Returns 8 fields:
        confidence_tier    str   — INSUFFICIENT | LOW | MODERATE | HIGH | VERY_HIGH
        stake_tier         str   — PASS | SMALL | MEDIUM | HIGH
        recommended_stake  float — suggested unit size
        max_allowed_stake  float — hard ceiling in units
        stake_cap_reason   str   — machine-readable cap code
        confidence_reason  str   — human-readable explanation
        big_stake_status   str   — NOT_ELIGIBLE | CAPPED_BY_LABEL | BLOCKED | APPROVED
        big_stake_blockers list  — failing gate codes (empty when APPROVED)

    IMPORTANT: this function NEVER introduces new LLP label values.
    `stake_tier` is orthogonal to `final_label`.
    """
    if blocker_tags is None:
        blocker_tags = []
    if context_flags is None:
        context_flags = {}

    label = (final_label or "").strip().upper()
    prob  = float(model_probability) if model_probability is not None else None
    edg   = float(edge) if edge is not None else None

    # ── 1. Non-actionable labels → PASS immediately ──────────────────────────
    if label in _STAKE_ZERO_LABELS:
        conf_tier = "LOW" if (prob is None or prob < 0.55) else "MODERATE"
        return {
            "confidence_tier":    conf_tier,
            "stake_tier":         "PASS",
            "recommended_stake":  0.0,
            "max_allowed_stake":  0.0,
            "stake_cap_reason":   f"LABEL_CEILING:{label}",
            "confidence_reason":  f"label={label} is not actionable",
            "big_stake_status":   "NOT_ELIGIBLE",
            "big_stake_blockers": [f"LABEL_CEILING:{label}"],
        }

    if label not in (LLPLabel.PLAYABLE.value, LLPLabel.APPROVED.value):
        return {
            "confidence_tier":    "INSUFFICIENT",
            "stake_tier":         "PASS",
            "recommended_stake":  0.0,
            "max_allowed_stake":  0.0,
            "stake_cap_reason":   "UNKNOWN_LABEL",
            "confidence_reason":  f"label={label!r} not recognized",
            "big_stake_status":   "NOT_ELIGIBLE",
            "big_stake_blockers": ["UNKNOWN_LABEL"],
        }

    # ── 2. Confidence tier ────────────────────────────────────────────────────
    if prob is None:
        conf_tier = "INSUFFICIENT"
    elif prob < 0.55:
        conf_tier = "LOW"
    elif prob < 0.58:
        conf_tier = "MODERATE"
    elif prob < 0.61:
        conf_tier = "HIGH"
    else:
        conf_tier = "VERY_HIGH"

    prob_str = f"model_probability={prob:.4f}" if prob is not None else "model_probability=None"
    edge_str = f"edge={edg:.4f}" if edg is not None else "edge=None"
    confidence_reason = f"{prob_str}, {edge_str}"

    # ── 3. LLP_PLAYABLE hard cap — SMALL only, no MEDIUM/HIGH ────────────────
    if label == LLPLabel.PLAYABLE.value:
        ledger_count = int(context_flags.get("ledger_candidate_count", 0))
        if ledger_count < _MEDIUM_LEDGER_MIN:
            rec = max_u = PLAYABLE_STAKE_CAPS["pre_25_candidates_max_units"]
            cap_reason  = "PLAYABLE_PRE_25_CAP"
        else:
            rec = max_u = PLAYABLE_STAKE_CAPS["pre_100_candidates_max_units"]
            cap_reason  = "PLAYABLE_LABEL_CEILING"
        return {
            "confidence_tier":    conf_tier,
            "stake_tier":         "SMALL",
            "recommended_stake":  rec,
            "max_allowed_stake":  max_u,
            "stake_cap_reason":   cap_reason,
            "confidence_reason":  confidence_reason,
            "big_stake_status":   "CAPPED_BY_LABEL",
            "big_stake_blockers": ["PLAYABLE_CANNOT_EXCEED_SMALL"],
        }

    # ── 4. LLP_APPROVED — gate evaluation for MEDIUM and HIGH ────────────────
    final_lock_ok     = bool(context_flags.get("final_lock", False))
    exposure_ok       = bool(context_flags.get("exposure_ok", True))
    ledger_count      = int(context_flags.get("ledger_candidate_count", 0))
    clv_ok            = bool(context_flags.get("clv_graduation_ok", False))
    timestamp_present = bool(context_flags.get("timestamp_present", True))

    medium_blockers: list[str] = []
    high_blockers:   list[str] = []

    # Edge availability & floor
    if edg is None:
        medium_blockers.append("NO_EDGE_VALUE")
        high_blockers.append("NO_EDGE_VALUE")
    else:
        if edg < _MEDIUM_EDGE_MIN:
            medium_blockers.append(f"EDGE_BELOW_MEDIUM_FLOOR:{edg:.4f}<{_MEDIUM_EDGE_MIN}")
            high_blockers.append(f"EDGE_BELOW_HIGH_FLOOR:{edg:.4f}<{_HIGH_EDGE_MIN}")
        elif edg < _HIGH_EDGE_MIN:
            high_blockers.append(f"EDGE_BELOW_HIGH_FLOOR:{edg:.4f}<{_HIGH_EDGE_MIN}")

    # Probability floors
    if prob is None:
        medium_blockers.append("NO_MODEL_PROBABILITY")
        high_blockers.append("NO_MODEL_PROBABILITY")
    else:
        if prob < _MEDIUM_PROB_MIN:
            medium_blockers.append(f"PROB_BELOW_MEDIUM_MIN:{prob:.4f}<{_MEDIUM_PROB_MIN}")
            high_blockers.append(f"PROB_BELOW_HIGH_MIN:{prob:.4f}<{_HIGH_PROB_MIN}")
        elif prob < _HIGH_PROB_MIN:
            high_blockers.append(f"PROB_BELOW_HIGH_MIN:{prob:.4f}<{_HIGH_PROB_MIN}")

    # Timestamp
    if not timestamp_present:
        medium_blockers.append("NO_TIMESTAMP")
        high_blockers.append("NO_TIMESTAMP")

    # Final-lock
    if not final_lock_ok:
        medium_blockers.append("FINAL_LOCK_NOT_CONFIRMED")
        high_blockers.append("FINAL_LOCK_NOT_CONFIRMED")

    # Exposure
    if not exposure_ok:
        medium_blockers.append("EXPOSURE_BREACH")
        high_blockers.append("EXPOSURE_BREACH")

    # Ledger maturity
    if ledger_count < _MEDIUM_LEDGER_MIN:
        medium_blockers.append(f"LEDGER_IMMATURE:{ledger_count}<{_MEDIUM_LEDGER_MIN}")
        high_blockers.append(f"LEDGER_IMMATURE:{ledger_count}<{_HIGH_LEDGER_MIN}")
    elif ledger_count < _HIGH_LEDGER_MIN:
        high_blockers.append(f"LEDGER_IMMATURE:{ledger_count}<{_HIGH_LEDGER_MIN}")

    # CLV graduation (HIGH only)
    if not clv_ok:
        high_blockers.append("CLV_GRADUATION_REQUIRED")

    # ── 5. Determine tier ────────────────────────────────────────────────────
    can_high   = len(high_blockers)   == 0
    can_medium = len(medium_blockers) == 0

    if can_high:
        return {
            "confidence_tier":    conf_tier,
            "stake_tier":         "HIGH",
            "recommended_stake":  1.25,
            "max_allowed_stake":  1.50,
            "stake_cap_reason":   "APPROVED_HIGH_TIER",
            "confidence_reason":  confidence_reason,
            "big_stake_status":   "APPROVED",
            "big_stake_blockers": [],
        }
    if can_medium:
        return {
            "confidence_tier":    conf_tier,
            "stake_tier":         "MEDIUM",
            "recommended_stake":  0.75,
            "max_allowed_stake":  1.00,
            "stake_cap_reason":   "APPROVED_MEDIUM_TIER",
            "confidence_reason":  confidence_reason,
            "big_stake_status":   "APPROVED",
            "big_stake_blockers": high_blockers,
        }

    # APPROVED but MEDIUM/HIGH blocked → SMALL
    if ledger_count < _MEDIUM_LEDGER_MIN:
        rec = max_u = PLAYABLE_STAKE_CAPS["pre_25_candidates_max_units"]
    else:
        rec = max_u = PLAYABLE_STAKE_CAPS["pre_100_candidates_max_units"]
    return {
        "confidence_tier":    conf_tier,
        "stake_tier":         "SMALL",
        "recommended_stake":  rec,
        "max_allowed_stake":  max_u,
        "stake_cap_reason":   "APPROVED_MEDIUM_BLOCKED",
        "confidence_reason":  confidence_reason,
        "big_stake_status":   "BLOCKED",
        "big_stake_blockers": medium_blockers,
    }
