"""
wnba_composite_gate.py  —  PATCH-017
WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE

Enforces the WNBA Composite Forward-Test Gate:
  WNBA_COMPOSITE_FORWARD_TEST=ACTIVE
  minimum_unique_player_games=20 before ceiling can be reviewed
  duplicate_thresholds_count_once=True
  DNP_or_void_not_a_projection_hit=True
  standard_and_promotional_separated=True

Maximum ceiling until milestone: MODEL_QUALIFIED_HOLD.
Promotional/goblin/demon lines do not upgrade a row when status/role is unresolved.

DB table: wnba_composite_forward_test_ledger
  Tracks unique (player, event_date) graded player-games.
  Alternate thresholds for the same player-game count as one observation.
  DNP/void observations are recorded but not counted toward the hit/miss milestone.

can_execute=False is unconditional.
"""
from __future__ import annotations

import os
from typing import Any

from .labels import PropLabel

can_execute = False

# ---------------------------------------------------------------------------
# WNBA sport / stat-type detection
# ---------------------------------------------------------------------------

_WNBA_SPORTS = {"WNBA", "wnba", "W NBA", "Women's Basketball"}

# Exact normalized stat names (after lower + replace +/-/_ with space)
# "Defensive Rebounds" → "defensive rebounds" must NOT match
_COMPOSITE_STAT_FAMILIES_EXACT = {
    "pra", "points rebounds assists",
    "p r", "points rebounds",       # P+R normalised
    "p a", "points assists",         # P+A normalised
    "r a", "rebounds assists",       # R+A normalised
    "points", "rebounds", "assists",
    "pts", "reb", "ast",
}

_BLOCKERS_THAT_PREVENT_PROMO_UPGRADE = {
    "ROLE_UNRESOLVED",
    "SOURCE_CONFLICT",
    "DATA_UNOBTAINABLE",
    "OUTLIER_CONTAMINATED",
}

# DB milestone target
MILESTONE_UNIQUE_PLAYER_GAMES = 20
MILESTONE_K_LESS_ROWS         = 10  # referenced in tests but owned by mlb_directional_firewall


def _is_wnba_row(row: dict[str, Any]) -> bool:
    sport = str(row.get("sport") or row.get("league") or "").strip()
    return sport in _WNBA_SPORTS or sport.upper() == "WNBA"


def _is_composite_stat(row: dict[str, Any]) -> bool:
    stat = (
        str(row.get("stat_type") or row.get("prop_type") or "")
        .lower()
        .strip()
        .replace("_", " ")
        .replace("+", " ")
        .replace("-", " ")
    )
    # Exact full-string match only — prevents "Defensive Rebounds" from
    # matching via the substring "reb" or "rebounds".
    return stat in _COMPOSITE_STAT_FAMILIES_EXACT


def _is_promo_line(row: dict[str, Any]) -> bool:
    ot = str(row.get("offer_type") or "").lower()
    return any(t in ot for t in ("goblin", "demon", "promo", "discount", "boost", "elevated"))


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


def _apply_ceiling(row: dict[str, Any], ceiling: str) -> None:
    current = row.get("terminal_label")
    if current is None:
        return
    # Never downgrade a REJECT label — those are terminal and must not be softened
    if current and current.startswith("REJECT"):
        return
    if _LABEL_TIER.get(current, 99) > _LABEL_TIER.get(ceiling, 99):
        row["terminal_label"] = ceiling


def _safe_str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_DDL_WNBA_LEDGER = """
CREATE TABLE IF NOT EXISTS wnba_composite_forward_test_ledger (
    id                      BIGSERIAL PRIMARY KEY,
    player_name             TEXT NOT NULL,
    event_date              DATE NOT NULL,
    event_id                TEXT,
    stat_family             TEXT,
    exact_line              NUMERIC,
    direction               TEXT,
    offer_type              TEXT,
    role_status             TEXT,
    primary_teammate_status TEXT,
    multi_path_class        TEXT,
    calibrated_lower_bound  NUMERIC,
    raw_probability         NUMERIC,
    forward_test_status     TEXT,
    is_dnp_or_void          BOOLEAN DEFAULT FALSE,
    is_promo                BOOLEAN DEFAULT FALSE,
    duplicate_group_id      TEXT,
    settled_result          TEXT,
    model_hit               BOOLEAN,
    row_id                  TEXT,
    logged_at               TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_name, event_date, stat_family, exact_line, direction)
)
"""

_DDL_WNBA_IDX = """
CREATE INDEX IF NOT EXISTS wnba_ft_player_idx   ON wnba_composite_forward_test_ledger(player_name);
CREATE INDEX IF NOT EXISTS wnba_ft_event_idx    ON wnba_composite_forward_test_ledger(event_date DESC);
CREATE INDEX IF NOT EXISTS wnba_ft_dup_idx      ON wnba_composite_forward_test_ledger(duplicate_group_id);
"""

_wnba_ledger_ready = False


def _ensure_wnba_ledger() -> None:
    global _wnba_ledger_ready
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor()
        cur.execute(_DDL_WNBA_LEDGER)
        cur.execute(_DDL_WNBA_IDX)
        conn.commit()
        cur.close()
        conn.close()
        _wnba_ledger_ready = True
    except Exception:
        pass


def get_unique_player_game_count() -> int:
    """Count unique (player, event_date) graded observations (excl. DNP/void)."""
    _ensure_wnba_ledger()
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor()
        # Count distinct (player_name, event_date) — alternate thresholds deduplicated
        cur.execute(
            "SELECT COUNT(DISTINCT (player_name, event_date)) "
            "FROM wnba_composite_forward_test_ledger "
            "WHERE is_dnp_or_void = FALSE"
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def log_wnba_row(row: dict[str, Any]) -> bool:
    """
    Upsert a WNBA composite row into the forward-test ledger.
    ON CONFLICT (player, event_date, stat_family, line, direction) → update settlement fields.
    Alternate thresholds for the same player-game naturally map to the same
    (player_name, event_date) pair and are deduplicated at the count level.
    """
    _ensure_wnba_ledger()
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor()

        event_date = row.get("event_date") or row.get("game_date")
        if isinstance(event_date, str) and event_date:
            try:
                from dateutil import parser as _dp  # type: ignore
                event_date = _dp.parse(event_date).date()
            except Exception:
                event_date = None

        is_dnp  = bool(row.get("is_dnp_or_void", False) or
                       str(row.get("settled_result") or "").upper() in ("DNP", "VOID", "DNP_OR_VOID"))
        is_promo = _is_promo_line(row)

        cur.execute(
            """
            INSERT INTO wnba_composite_forward_test_ledger (
                player_name, event_date, event_id, stat_family, exact_line,
                direction, offer_type, role_status, primary_teammate_status,
                multi_path_class, calibrated_lower_bound, raw_probability,
                forward_test_status, is_dnp_or_void, is_promo,
                duplicate_group_id, settled_result, model_hit, row_id
            ) VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,%s
            )
            ON CONFLICT (player_name, event_date, stat_family, exact_line, direction)
            DO UPDATE SET
                settled_result      = COALESCE(EXCLUDED.settled_result, wnba_composite_forward_test_ledger.settled_result),
                model_hit           = COALESCE(EXCLUDED.model_hit, wnba_composite_forward_test_ledger.model_hit),
                is_dnp_or_void      = EXCLUDED.is_dnp_or_void,
                forward_test_status = EXCLUDED.forward_test_status,
                logged_at           = NOW()
            """,
            (
                _safe_str(row.get("player_name") or row.get("player")),
                event_date,
                row.get("event_id"),
                _safe_str(row.get("stat_type") or row.get("prop_type") or row.get("stat_family")),
                _safe_float(row.get("line") or row.get("line_score") or row.get("exact_line")),
                row.get("direction") or row.get("side"),
                row.get("offer_type"),
                row.get("role_status"),
                row.get("primary_teammate_status"),
                row.get("multi_path_class"),
                _safe_float(row.get("calibrated_lower_bound") or
                            row.get("calibrated_probability_lower_bound")),
                _safe_float(row.get("raw_probability") or row.get("model_probability")),
                row.get("forward_test_status", "ACTIVE"),
                is_dnp,
                is_promo,
                row.get("duplicate_group_id"),
                row.get("settled_result") or row.get("model_result"),
                _parse_model_hit(row),
                row.get("row_id"),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


def _parse_model_hit(row: dict[str, Any]) -> bool | None:
    res = str(row.get("settled_result") or row.get("model_result") or "").upper()
    if res in ("HIT", "WIN", "YES"):
        return True
    if res in ("MISS", "LOSS", "NO"):
        return False
    return None


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


# ---------------------------------------------------------------------------
# Forward-test milestone check
# ---------------------------------------------------------------------------

def get_forward_test_status() -> dict[str, Any]:
    """Return current milestone progress."""
    unique_pg = get_unique_player_game_count()
    return {
        "milestone_target":           MILESTONE_UNIQUE_PLAYER_GAMES,
        "unique_player_games":        unique_pg,
        "milestone_met":              unique_pg >= MILESTONE_UNIQUE_PLAYER_GAMES,
        "current_ceiling":            (
            PropLabel.MODEL_QUALIFIED_HOLD.value
            if unique_pg < MILESTONE_UNIQUE_PLAYER_GAMES
            else "CEILING_REVIEW_ELIGIBLE"
        ),
        "WNBA_COMPOSITE_FORWARD_TEST": "ACTIVE",
        "duplicate_thresholds_count_once": True,
        "DNP_not_a_projection_hit":        True,
        "standard_and_promotional_separated": True,
        "can_execute": False,
    }


# ---------------------------------------------------------------------------
# Promo upgrade prohibition check
# ---------------------------------------------------------------------------

def _promo_blocked_by_status(row: dict[str, Any]) -> bool:
    """Return True if a promotional line is prohibited from upgrading this row."""
    blockers = row.get("blockers") or []
    for blocker_str in blockers:
        for prohibited in _BLOCKERS_THAT_PREVENT_PROMO_UPGRADE:
            if prohibited in str(blocker_str):
                return True
    # Also check terminal label
    tl = str(row.get("terminal_label") or "")
    return any(p in tl for p in ("ROLE_UNRESOLVED", "SOURCE_CONFLICT", "DATA_UNOBTAINABLE"))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(row: dict[str, Any]) -> None:
    """
    Per-row entry point. Called for every WNBA composite row before classifier.classify().

    Actions:
    1. Detect if row is WNBA + composite stat family.
    2. Apply MODEL_QUALIFIED_HOLD ceiling (forward-test active).
    3. Block promo upgrade if blockers present.
    4. Stamp forward_test_status, multi_path_class, etc.
    5. Optionally log to forward-test ledger (for settled rows).
    """
    if not _is_wnba_row(row):
        return
    if not _is_composite_stat(row):
        return

    row["can_execute"] = False
    row.setdefault("gates", {})

    # Get current milestone status
    unique_pg = get_unique_player_game_count()
    milestone_met = unique_pg >= MILESTONE_UNIQUE_PLAYER_GAMES

    status_label = (
        "MILESTONE_MET_CEILING_REVIEW_ELIGIBLE"
        if milestone_met
        else f"ACTIVE_UNIQUE_PG={unique_pg}_OF_{MILESTONE_UNIQUE_PLAYER_GAMES}"
    )
    row["forward_test_status"] = f"WNBA_COMPOSITE_FORWARD_TEST_{status_label}"

    # Promotional line check — cannot upgrade blocked rows
    if _is_promo_line(row) and _promo_blocked_by_status(row):
        row.setdefault("blockers", []).append("PROMO_UPGRADE_BLOCKED_BY_STATUS")

    # Unconditional ceiling until milestone: MODEL_QUALIFIED_HOLD
    if not milestone_met:
        _apply_ceiling(row, PropLabel.MODEL_QUALIFIED_HOLD.value)

    # Build gate report
    report: dict[str, Any] = {
        "forward_test_status":      row["forward_test_status"],
        "unique_player_games":      unique_pg,
        "milestone_met":            milestone_met,
        "ceiling_applied":          PropLabel.MODEL_QUALIFIED_HOLD.value,
        "multi_path_class":         row.get("multi_path_class"),
        "role_status":              row.get("role_status"),
        "primary_teammate_status":  row.get("primary_teammate_status"),
        "component_covariance_status": row.get("component_covariance_status"),
        "calibrated_lower_bound":   row.get("calibrated_lower_bound") or
                                    row.get("calibrated_probability_lower_bound"),
        "is_promo":                 _is_promo_line(row),
        "can_execute":              False,
    }
    row["gates"]["wnba_composite_gate"] = report

    # Log settled rows to forward-test ledger
    settled = row.get("settled_result") or row.get("model_result")
    if settled and str(settled).upper() not in ("PENDING", "UNRESOLVED", ""):
        log_wnba_row(row)
