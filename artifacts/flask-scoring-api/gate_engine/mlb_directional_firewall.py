"""
mlb_directional_firewall.py  —  PATCH-015 + PATCH-016
WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE

Enforces:
  MLB_K_LESS=WATCH_ONLY         — K LESS cannot exceed MLB_K_LESS_WATCH
  MLB_OUTS_MORE=MODEL_QUALIFIED_HOLD ceiling

Per-row enrichment added:
  directional_lane              str  — K_MORE | K_LESS | OUTS_MORE | OUTS_LESS |
                                       PITCH_COUNT_MORE | PITCH_COUNT_LESS |
                                       BATTERS_FACED_MORE | BATTERS_FACED_LESS |
                                       OTHER_PITCHER | NOT_PITCHER_PROP
  failure_path_score            float (0–1) | None
  short_outing_support_share    float (0–1) | None  (K LESS only)
  required_out_survival_lower_bound  float (0–1) | None  (OUTS MORE only)
  directional_forward_test_status   str

Hard ceiling rules (PATCH-015):
  K LESS lane:
    short_outing_support_share > 0.50 → MLB_K_LESS_WATCH + HIGH_CONFIDENCE_SUSPENDED
    All K LESS rows capped at MLB_K_LESS_WATCH (temporary WATCH_ONLY firewall)

  OUTS MORE lane:
    P(reach_required_outs) lower bound < active floor (default 0.65) → NO_LOW_PROBABILITY blocker
    conditional_as_unconditional=True → MODEL_INVALID blocker
    unresolved workload restriction → MODEL_QUALIFIED_HOLD ceiling

PATCH-016 ledger:
  Every settled MLB pitcher row is written to the mlb_directional_pitcher_ledger table.

can_execute=false is unconditional — this module never grants execution authority.
"""
from __future__ import annotations

import os
from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Sport / stat-type detection helpers
# ---------------------------------------------------------------------------

_MLB_SPORTS = {"MLB", "baseball", "mlb", "Baseball"}

_K_STAT_TYPES = {
    "pitcher strikeouts", "strikeouts", "strikeouts recorded",
    "pitcher ks", "ks", "k", "pitcher k", "pitcher strikeout",
}

_OUTS_STAT_TYPES = {
    "pitching outs", "outs recorded", "pitcher outs", "pitching outs recorded",
    "outs", "outs pitched",
}

_PITCH_COUNT_STAT_TYPES = {
    "pitches thrown", "pitch count", "pitches",
}

_BATTERS_FACED_STAT_TYPES = {
    "batters faced", "batters retired",
}

# Active workload survival floor (PATCH-015, configurable via enrichment)
_DEFAULT_OUTS_MORE_FLOOR = 0.65

can_execute = False


def _is_mlb_row(row: dict[str, Any]) -> bool:
    sport = str(row.get("sport") or row.get("league") or "")
    return sport.strip() in _MLB_SPORTS or sport.strip().lower() == "mlb"


def _normalize_stat(row: dict[str, Any]) -> str:
    return (
        str(row.get("stat_type") or row.get("prop_type") or "")
        .lower()
        .strip()
        .replace("_", " ")
        .replace("-", " ")
    )


def _is_pitcher_prop(row: dict[str, Any]) -> bool:
    """True if the row represents an MLB starting-pitcher prop (not batter)."""
    position = str(row.get("position") or row.get("player_position") or "").upper()
    market   = str(row.get("market_type") or "").lower()
    stat     = _normalize_stat(row)
    return (
        any(k in stat for k in ("strikeout", " out", "pitch", "batter", " k"))
        or position == "SP"
        or "pitcher" in stat
        or "pitcher" in market
    )


def _detect_lane(row: dict[str, Any]) -> str:
    stat      = _normalize_stat(row)
    direction = str(row.get("direction") or row.get("side") or "MORE").upper()

    if not _is_mlb_row(row):
        return "NOT_PITCHER_PROP"

    if stat in _K_STAT_TYPES or "strikeout" in stat or stat.startswith("k ") or stat == "k":
        return f"K_{direction}" if direction in ("MORE", "LESS") else "K_MORE"

    if stat in _OUTS_STAT_TYPES or "pitching out" in stat or "outs recorded" in stat:
        return f"OUTS_{direction}" if direction in ("MORE", "LESS") else "OUTS_MORE"

    if stat in _PITCH_COUNT_STAT_TYPES or "pitch count" in stat or "pitches" in stat:
        return f"PITCH_COUNT_{direction}" if direction in ("MORE", "LESS") else "PITCH_COUNT_MORE"

    if stat in _BATTERS_FACED_STAT_TYPES or "batter" in stat:
        return f"BATTERS_FACED_{direction}" if direction in ("MORE", "LESS") else "BATTERS_FACED_MORE"

    if _is_pitcher_prop(row):
        return "OTHER_PITCHER"

    return "NOT_PITCHER_PROP"


# ---------------------------------------------------------------------------
# K LESS firewall (PATCH-015)
# ---------------------------------------------------------------------------

def _apply_k_less_firewall(row: dict[str, Any]) -> None:
    """
    Hard rule: MLB K LESS=WATCH_ONLY (temporary, until 10 unique K LESS rows settled).

    short_outing_support_share > 0.50 → additional HIGH_CONFIDENCE_SUSPENDED blocker.
    All K LESS rows capped at MLB_K_LESS_WATCH regardless.
    """
    blockers = row.setdefault("blockers", [])
    gates    = row.setdefault("gates", {})

    # Pull or compute short_outing_support_share
    sos = row.get("short_outing_support_share")
    if sos is None:
        # Try to derive from enrichment fields provided by the scoring request
        p_less        = _safe_float(row.get("model_probability") or row.get("calibrated_probability"))
        p_early_exit  = _safe_float(row.get("early_exit_probability") or
                                     row.get("short_outing_probability"))
        if p_less and p_early_exit and p_less > 0:
            sos = p_early_exit / p_less
            row["short_outing_support_share"] = round(sos, 4)

    report: dict[str, Any] = {
        "lane":                      "K_LESS",
        "firewall_status":           "MLB_K_LESS_WATCH_ONLY",
        "short_outing_support_share": sos,
        "ceiling_applied":           "MLB_K_LESS_WATCH",
        "can_execute":               False,
    }

    # Rule 1: short_outing_support_share > 0.50 → HIGH confidence prohibited
    if sos is not None and sos > 0.50:
        report["high_confidence_prohibited_reason"] = (
            f"short_outing_support_share={sos:.3f} > 0.50 — "
            "failure-path model may not use early-exit uncertainty as support for LESS"
        )
        if "MLB_K_LESS_SHORT_OUTING_BLOCK" not in blockers:
            blockers.append("MLB_K_LESS_SHORT_OUTING_BLOCK")
        # Stamp HIGH_CONFIDENCE_SUSPENDED if row is attempting approval
        tl = row.get("terminal_label", "")
        if tl in (PropLabel.FINAL_APPROVED.value, PropLabel.MONEY_QUALIFIED.value,
                  PropLabel.MARKET_VERIFIED_HOLD.value):
            row["terminal_label"] = PropLabel.HIGH_CONFIDENCE_SUSPENDED.value
            report["label_applied"] = PropLabel.HIGH_CONFIDENCE_SUSPENDED.value

    # Rule 2: Unconditional WATCH_ONLY ceiling — all K LESS rows
    # MLB_K_LESS_WATCH is a custom label; set directly rather than via tier map.
    tl = row.get("terminal_label")
    if tl and not tl.startswith("REJECT") and tl not in (
        PropLabel.SLATE_PURGE.value,
        PropLabel.DATA_CONTRACT_FAIL.value,
        PropLabel.MLB_WINNER_PREFLIGHT_BLOCK.value,
    ):
        row["terminal_label"] = PropLabel.MLB_K_LESS_WATCH.value
    report["ceiling_enforced"] = True

    gates["mlb_k_less_firewall"] = report
    row["directional_forward_test_status"] = "MLB_K_LESS_WATCH_ONLY_ACTIVE"


# ---------------------------------------------------------------------------
# OUTS MORE workload survival gate (PATCH-015)
# ---------------------------------------------------------------------------

def _apply_outs_more_gate(row: dict[str, Any]) -> None:
    """
    Hard rules for OUTS MORE:
      P(reach_required_outs) lower bound < floor → NO_LOW_PROBABILITY
      conditional used as unconditional → MODEL_INVALID
      unresolved workload restriction → MODEL_QUALIFIED_HOLD ceiling
    """
    blockers = row.setdefault("blockers", [])
    gates    = row.setdefault("gates", {})

    # Pull survival fields (expected from enrichment)
    p_survival_lb = _safe_float(row.get("required_out_survival_lower_bound") or
                                 row.get("p_reach_required_outs_lower_bound"))
    conditional_as_uncond = bool(row.get("conditional_probability_used_as_unconditional", False))
    workload_unresolved   = bool(row.get("workload_restriction_unresolved", False))

    floor = _safe_float(row.get("outs_more_survival_floor")) or _DEFAULT_OUTS_MORE_FLOOR

    report: dict[str, Any] = {
        "lane":                              "OUTS_MORE",
        "ceiling_applied":                   "MLB_OUTS_MORE_HOLD",
        "p_survival_lower_bound":            p_survival_lb,
        "active_floor":                      floor,
        "conditional_as_unconditional":      conditional_as_uncond,
        "workload_restriction_unresolved":   workload_unresolved,
        "can_execute":                       False,
    }

    # Rule 1: survival lower bound below floor → NO_LOW_PROBABILITY
    if p_survival_lb is not None and p_survival_lb < floor:
        report["blocker"] = f"NO_LOW_PROBABILITY — survival_lb={p_survival_lb:.3f} < floor={floor:.3f}"
        if "MLB_OUTS_MORE_LOW_PROBABILITY" not in blockers:
            blockers.append("MLB_OUTS_MORE_LOW_PROBABILITY")
        _apply_ceiling(row, PropLabel.MODEL_QUALIFIED_HOLD.value)
        row["directional_forward_test_status"] = "MLB_OUTS_MORE_HOLD_CEILING_ACTIVE"
        gates["mlb_outs_more_gate"] = report
        return

    # Rule 2: conditional reported as unconditional → MODEL_INVALID
    if conditional_as_uncond:
        report["blocker"] = (
            "MODEL_INVALID — conditional_probability_given_normal_workload "
            "cannot be reported as the final unconditional probability"
        )
        if "MLB_OUTS_MORE_CONDITIONAL_AS_UNCONDITIONAL" not in blockers:
            blockers.append("MLB_OUTS_MORE_CONDITIONAL_AS_UNCONDITIONAL")
        _apply_ceiling(row, PropLabel.MODEL_QUALIFIED_HOLD.value)
        row["directional_forward_test_status"] = "MLB_OUTS_MORE_HOLD_CEILING_ACTIVE"
        gates["mlb_outs_more_gate"] = report
        return

    # Rule 3: workload restriction unresolved → MODEL_QUALIFIED_HOLD ceiling
    if workload_unresolved:
        report["blocker"] = "MODEL_QUALIFIED_HOLD — material workload restriction unresolved"
        _apply_ceiling(row, PropLabel.MODEL_QUALIFIED_HOLD.value)
        row["directional_forward_test_status"] = "MLB_OUTS_MORE_HOLD_CEILING_ACTIVE"
    else:
        row["directional_forward_test_status"] = "MLB_OUTS_MORE_HOLD_CEILING_ACTIVE"
        # Unconditional HOLD ceiling even when rules above pass (PATCH-015 initial state)
        _apply_ceiling(row, PropLabel.MODEL_QUALIFIED_HOLD.value)

    gates["mlb_outs_more_gate"] = report


# ---------------------------------------------------------------------------
# Ceiling helper
# ---------------------------------------------------------------------------

_LABEL_TIER: dict[str, int] = {
    PropLabel.RESEARCH_INTEREST.value:    0,
    PropLabel.MODEL_QUALIFIED_HOLD.value: 1,
    PropLabel.MARKET_VERIFIED_HOLD.value: 2,
    PropLabel.MONEY_QUALIFIED.value:      3,
    PropLabel.FINAL_APPROVED.value:       4,
}

_LABEL_TIER_CUSTOM: dict[str, int] = {
    "MLB_K_LESS_WATCH":  0,
    "MLB_OUTS_MORE_HOLD": 1,
}


def _apply_ceiling(row: dict[str, Any], ceiling: str) -> None:
    """Downgrade terminal_label to ceiling if current label is above it."""
    current = row.get("terminal_label")
    if current is None:
        return
    # Never downgrade a REJECT label — those are terminal and must not be softened
    if current and current.startswith("REJECT"):
        return
    ceiling_tier = _LABEL_TIER.get(ceiling, 99)
    current_tier = _LABEL_TIER.get(current, 99)
    if current_tier > ceiling_tier:
        row["terminal_label"] = ceiling


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# DB ledger (PATCH-016)
# ---------------------------------------------------------------------------

_DDL_PITCHER_LEDGER = """
CREATE TABLE IF NOT EXISTS mlb_directional_pitcher_ledger (
    id                              BIGSERIAL PRIMARY KEY,
    pitcher                         TEXT,
    event_id                        TEXT,
    event_date                      DATE,
    market_type                     TEXT,
    directional_lane                TEXT,
    line                            NUMERIC,
    offer_type                      TEXT,
    starter_confirmation            TEXT,
    lineup_confirmation             TEXT,
    health_regime                   TEXT,
    predicted_innings               NUMERIC,
    predicted_batters_faced         NUMERIC,
    predicted_pitch_count           NUMERIC,
    predicted_strikeouts            NUMERIC,
    failure_path_score              NUMERIC,
    short_outing_support_share      NUMERIC,
    conditional_probability_given_normal_workload NUMERIC,
    unconditional_probability       NUMERIC,
    calibrated_lower_bound          NUMERIC,
    actual_innings                  NUMERIC,
    actual_batters_faced            NUMERIC,
    actual_pitch_count              NUMERIC,
    actual_strikeouts               NUMERIC,
    settled_result                  TEXT,
    observed_failure_category       TEXT,
    process_pass_or_fail            TEXT,
    duplicate_group_id              TEXT,
    row_id                          TEXT,
    logged_at                       TIMESTAMPTZ DEFAULT NOW()
)
"""

_DDL_PITCHER_LEDGER_IDX = """
CREATE INDEX IF NOT EXISTS mlb_dl_pitcher_idx     ON mlb_directional_pitcher_ledger(pitcher);
CREATE INDEX IF NOT EXISTS mlb_dl_lane_idx        ON mlb_directional_pitcher_ledger(directional_lane);
CREATE INDEX IF NOT EXISTS mlb_dl_event_date_idx  ON mlb_directional_pitcher_ledger(event_date DESC);
CREATE INDEX IF NOT EXISTS mlb_dl_duplicate_idx   ON mlb_directional_pitcher_ledger(duplicate_group_id);
"""


def _ensure_pitcher_ledger() -> None:
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor()
        cur.execute(_DDL_PITCHER_LEDGER)
        cur.execute(_DDL_PITCHER_LEDGER_IDX)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


_pitcher_ledger_ready = False


def log_pitcher_row(row: dict[str, Any]) -> bool:
    """Write a settled MLB pitcher row to the directional ledger (PATCH-016)."""
    global _pitcher_ledger_ready
    if not _pitcher_ledger_ready:
        _ensure_pitcher_ledger()
        _pitcher_ledger_ready = True
    try:
        import psycopg2  # type: ignore
        from datetime import date as _date
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor()

        event_date = row.get("event_date") or row.get("game_date")
        if isinstance(event_date, str) and event_date:
            try:
                from dateutil import parser as _dp  # type: ignore
                event_date = _dp.parse(event_date).date()
            except Exception:
                event_date = None

        cur.execute(
            """
            INSERT INTO mlb_directional_pitcher_ledger (
                pitcher, event_id, event_date, market_type, directional_lane,
                line, offer_type, starter_confirmation, lineup_confirmation,
                health_regime, predicted_innings, predicted_batters_faced,
                predicted_pitch_count, predicted_strikeouts, failure_path_score,
                short_outing_support_share, conditional_probability_given_normal_workload,
                unconditional_probability, calibrated_lower_bound,
                actual_innings, actual_batters_faced, actual_pitch_count,
                actual_strikeouts, settled_result, observed_failure_category,
                process_pass_or_fail, duplicate_group_id, row_id
            ) VALUES (
                %s,%s,%s,%s,%s, %s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,%s
            )
            """,
            (
                row.get("player_name") or row.get("pitcher"),
                row.get("event_id"),
                event_date,
                row.get("market_type") or row.get("stat_type"),
                row.get("directional_lane"),
                _safe_float(row.get("line") or row.get("line_score")),
                row.get("offer_type"),
                row.get("starter_confirmation"),
                row.get("lineup_confirmation"),
                row.get("health_regime"),
                _safe_float(row.get("predicted_innings")),
                _safe_float(row.get("predicted_batters_faced")),
                _safe_float(row.get("predicted_pitch_count")),
                _safe_float(row.get("predicted_strikeouts")),
                _safe_float(row.get("failure_path_score")),
                _safe_float(row.get("short_outing_support_share")),
                _safe_float(row.get("conditional_probability_given_normal_workload")),
                _safe_float(row.get("unconditional_probability") or
                            row.get("model_probability") or row.get("calibrated_probability")),
                _safe_float(row.get("calibrated_lower_bound") or
                            row.get("calibrated_probability_lower_bound")),
                _safe_float(row.get("actual_innings")),
                _safe_float(row.get("actual_batters_faced")),
                _safe_float(row.get("actual_pitch_count")),
                _safe_float(row.get("actual_strikeouts")),
                row.get("settled_result") or row.get("model_result"),
                row.get("observed_failure_category"),
                row.get("process_pass_or_fail"),
                row.get("duplicate_group_id"),
                row.get("row_id"),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


def query_directional_ledger(lane: str | None = None, limit: int = 100) -> list[dict]:
    """Return summary counts per directional lane."""
    try:
        import psycopg2.extras  # type: ignore
        import psycopg2  # type: ignore
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if lane:
            cur.execute(
                "SELECT * FROM mlb_directional_pitcher_ledger "
                "WHERE directional_lane=%s ORDER BY logged_at DESC LIMIT %s",
                (lane, limit),
            )
        else:
            cur.execute(
                "SELECT directional_lane, COUNT(*) AS total, "
                "SUM(CASE WHEN settled_result='HIT' THEN 1 ELSE 0 END) AS hits, "
                "SUM(CASE WHEN settled_result='MISS' THEN 1 ELSE 0 END) AS misses, "
                "COUNT(DISTINCT duplicate_group_id) AS unique_theses "
                "FROM mlb_directional_pitcher_ledger "
                "GROUP BY directional_lane ORDER BY total DESC"
            )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(row: dict[str, Any]) -> None:
    """
    Per-row entry point. Called for every row before classifier.classify().
    Stamps directional_lane, applies lane-specific ceilings, and enforces
    the PATCH-015 hard rules.

    can_execute=False is unconditional — this module never modifies that.
    """
    if not _is_mlb_row(row):
        row.setdefault("directional_lane", "NOT_PITCHER_PROP")
        return

    lane = _detect_lane(row)
    row["directional_lane"] = lane

    # Pitcher rows always get the can_execute flag
    if lane != "NOT_PITCHER_PROP":
        row["can_execute"] = False

    if lane == "K_LESS":
        _apply_k_less_firewall(row)

    elif lane == "OUTS_MORE":
        _apply_outs_more_gate(row)

    elif lane in ("K_MORE", "OUTS_LESS", "PITCH_COUNT_MORE", "PITCH_COUNT_LESS",
                  "BATTERS_FACED_MORE", "BATTERS_FACED_LESS", "OTHER_PITCHER"):
        # Other pitcher lanes: record lane, no ceiling change
        row["directional_forward_test_status"] = f"LANE_{lane}_TRACKING"
        row.setdefault("gates", {})["mlb_directional_firewall"] = {
            "lane": lane,
            "ceiling_applied": None,
            "can_execute": False,
        }
