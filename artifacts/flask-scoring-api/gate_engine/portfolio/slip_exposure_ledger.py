"""
gate_engine/portfolio/slip_exposure_ledger.py
WOW-PATCH-2026-08-01-CROSS-SLIP-DUPLICATE-GUARD

Session-level thesis exposure persistence and tiered enforcement.

This module extends the existing PgPortfolioGovernor (which guards against
within-session player/stat/direction duplicates) by tracking the full
financial-exposure footprint of a slip at thesis level, including:

  - exact duplicate legs (same player, stat, line, side across slips)
  - shared-distribution legs (same player, stat, side — different threshold)

Exposure source precedence
--------------------------
  session ledger (DB)
  > open/unsettled ledger rows (from wow_session_thesis_exposure)
  > same-slate proposed rows supplied in the current call
  > workbook fallback (caller-supplied portfolio_stake_base)

Exposure tiers
--------------
  TIER_0 — PASS                     (no duplicate or shared-distribution)
  TIER_1 — PASS_WITH_DISCLOSURE     (0% < dup_pct <= 20%, no TIER_3 trigger)
  TIER_2 — HOLD_CONFIRMATION_REQUIRED (shared-distribution >20% OR
                                       missing portfolio denominator)
  TIER_3 — HARD_STOP_CROSS_SLIP_OVEREXPOSURE  (exact duplicate pct > 20%)

TIER_3 cannot be overridden by user confirmation.

can_execute = False unconditional.
"""
from __future__ import annotations

import os
from datetime import date as _date_type
from typing import Any

can_execute = False

# ---------------------------------------------------------------------------
# Tier constants
# ---------------------------------------------------------------------------

TIER_0 = "TIER_0"
TIER_1 = "TIER_1"
TIER_2 = "TIER_2"
TIER_3 = "TIER_3"

TIER_ACTIONS = {
    TIER_0: "PASS",
    TIER_1: "PASS_WITH_DISCLOSURE",
    TIER_2: "HOLD_CONFIRMATION_REQUIRED",
    TIER_3: "HARD_STOP_CROSS_SLIP_OVEREXPOSURE",
}

# Thresholds (inclusive lower bound, exclusive upper bound)
_EXACT_DUP_TIER3_THRESHOLD    = 0.20   # > 20% → TIER_3
_DIST_FAM_TIER2_THRESHOLD     = 0.20   # > 20% shared distribution → TIER_2

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS wow_session_thesis_exposure (
    id                BIGSERIAL   PRIMARY KEY,
    session_id        TEXT        NOT NULL,
    research_run_id   TEXT        NOT NULL DEFAULT '',
    slate_date        DATE        NOT NULL DEFAULT CURRENT_DATE,
    slip_id           TEXT        NOT NULL DEFAULT '',
    row_id            TEXT        NOT NULL DEFAULT '',
    player_id         TEXT        NOT NULL DEFAULT '',
    event_id          TEXT        NOT NULL DEFAULT '',
    market_type       TEXT        NOT NULL DEFAULT '',
    stat_type         TEXT        NOT NULL DEFAULT '',
    line              NUMERIC(8,2),
    side              TEXT        NOT NULL DEFAULT '',
    distribution_family TEXT      NOT NULL DEFAULT '',
    proposed_stake    NUMERIC(12,4) NOT NULL DEFAULT 0,
    submission_status TEXT        NOT NULL DEFAULT 'PROPOSED',
    settlement_status TEXT        NOT NULL DEFAULT 'OPEN',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wow_session_thesis_session_slate
    ON wow_session_thesis_exposure (session_id, slate_date);

CREATE INDEX IF NOT EXISTS idx_wow_session_thesis_dist_family
    ON wow_session_thesis_exposure (session_id, slate_date, distribution_family);
"""


def ensure_session_thesis_table_exists() -> bool:
    """Create the table if absent. Returns True on success, False on failure."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return False
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=10)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(_DDL)
        conn.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------

def _exact_dup_key(row: dict[str, Any]) -> str:
    """Exact duplicate key: player|stat|line|side (all four must match)."""
    player = str(row.get("player") or row.get("player_id") or "").lower().strip()
    stat   = str(row.get("stat_type") or row.get("prop_type") or "").lower().strip()
    line   = str(row.get("line") or "")
    side   = str(row.get("side") or row.get("direction") or "").upper().strip()
    return f"{player}|{stat}|{line}|{side}"


def _distribution_family_key(row: dict[str, Any]) -> str:
    """
    Shared-distribution key: player|stat|side (direction-inclusive, line-agnostic).
    Two alternate thresholds for the same player/stat/direction share one distribution.
    """
    player = str(row.get("player") or row.get("player_id") or "").lower().strip()
    stat   = str(row.get("stat_type") or row.get("prop_type") or "").lower().strip()
    side   = str(row.get("side") or row.get("direction") or "").upper().strip()
    return f"{player}|{stat}|{side}"


# ---------------------------------------------------------------------------
# Group builders
# ---------------------------------------------------------------------------

def build_duplicate_groups(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    """
    Group row indices by exact-duplicate key.
    Returns {exact_dup_key: [row_index, ...]} for groups with ≥ 2 members.
    """
    groups: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        key = _exact_dup_key(row)
        groups.setdefault(key, []).append(i)
    return {k: v for k, v in groups.items() if len(v) >= 2}


def build_shared_distribution_groups(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    """
    Group row indices by shared-distribution key (same player|stat|side, any line).
    Returns only groups where more than one distinct line is present
    (i.e. nested/alternate-threshold exposure, not pure duplicates).
    """
    dist_groups: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        key = _distribution_family_key(row)
        dist_groups.setdefault(key, []).append(i)

    result: dict[str, list[int]] = {}
    for key, indices in dist_groups.items():
        lines = {str(rows[i].get("line") or "") for i in indices}
        if len(lines) > 1:
            result[key] = indices
    return result


# ---------------------------------------------------------------------------
# Exposure percentage calculators
# ---------------------------------------------------------------------------

def calculate_duplicate_leg_exposure_pct(
    group_row_indices: list[int],
    rows: list[dict[str, Any]],
    portfolio_stake_base: float | None,
) -> float | None:
    """
    Sum of proposed_stake for rows in the duplicate group, divided by
    portfolio_stake_base.  Returns None when denominator is unavailable.
    """
    if portfolio_stake_base is None or portfolio_stake_base <= 0:
        return None
    total = sum(
        float(rows[i].get("proposed_stake") or 0)
        for i in group_row_indices
    )
    return total / portfolio_stake_base


def calculate_distribution_family_exposure_pct(
    group_row_indices: list[int],
    rows: list[dict[str, Any]],
    portfolio_stake_base: float | None,
) -> float | None:
    """
    Sum of proposed_stake for rows in the shared-distribution group, divided by
    portfolio_stake_base.  Returns None when denominator is unavailable.
    """
    return calculate_duplicate_leg_exposure_pct(
        group_row_indices, rows, portfolio_stake_base
    )


# ---------------------------------------------------------------------------
# DB exposure reader
# ---------------------------------------------------------------------------

def get_current_slate_exposure(
    session_id: str,
    slate_date: str | _date_type,
) -> dict[str, Any]:
    """
    Query wow_session_thesis_exposure for all open rows on this session/date.

    Returns a dict:
        {
          "rows": [ <row_dicts> ],
          "ok": True/False,
          "error": None / str,
        }

    Falls back to empty when DATABASE_URL is absent or unreachable.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return {"rows": [], "ok": False, "error": "DATABASE_URL not configured"}
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(db_url, connect_timeout=10)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT *
            FROM wow_session_thesis_exposure
            WHERE session_id = %s
              AND slate_date = %s
              AND settlement_status = 'OPEN'
            ORDER BY created_at
            """,
            (session_id, str(slate_date)),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return {"rows": rows, "ok": True, "error": None}
    except Exception as exc:
        return {"rows": [], "ok": False, "error": str(exc)}


def register_thesis_row(
    *,
    session_id: str,
    research_run_id: str = "",
    slate_date: str | _date_type,
    slip_id: str = "",
    row_id: str = "",
    player_id: str = "",
    event_id: str = "",
    market_type: str = "",
    stat_type: str = "",
    line: float | None = None,
    side: str = "",
    distribution_family: str = "",
    proposed_stake: float = 0.0,
    submission_status: str = "PROPOSED",
) -> dict[str, Any]:
    """
    Insert a thesis row into wow_session_thesis_exposure.
    Silently fails (returns ok=False) when DB is unavailable — caller proceeds.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return {"ok": False, "error": "DATABASE_URL not configured"}
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO wow_session_thesis_exposure
              (session_id, research_run_id, slate_date, slip_id, row_id,
               player_id, event_id, market_type, stat_type, line, side,
               distribution_family, proposed_stake, submission_status)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id, research_run_id, str(slate_date), slip_id, row_id,
                player_id, event_id, market_type, stat_type, line, side,
                distribution_family or _distribution_family_key({
                    "player": player_id, "stat_type": stat_type, "side": side
                }),
                proposed_stake, submission_status,
            ),
        )
        conn.commit()
        conn.close()
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Cross-slip exposure ceiling (the main gate)
# ---------------------------------------------------------------------------

def apply_cross_slip_exposure_ceiling(
    rows: list[dict[str, Any]],
    portfolio_stake_base: float | None = None,
    *,
    existing_ledger_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Evaluate cross-slip duplicate and shared-distribution exposure for a batch
    of proposed rows.  Must be called AFTER weakest-leg elimination and card
    fragility audits, but BEFORE final card output.

    Parameters
    ----------
    rows : list of row dicts.  Each must contain at least:
        player / player_id, stat_type / prop_type, line, side / direction,
        proposed_stake (numeric).
    portfolio_stake_base : total portfolio stake denominator.
        When None, the gate sums all proposed_stake values in rows + ledger
        as a workbook fallback.
    existing_ledger_rows : open rows already in the DB ledger for this
        session/slate (from get_current_slate_exposure).

    Returns
    -------
    dict:
        tier : TIER_0 … TIER_3
        action : PASS / PASS_WITH_DISCLOSURE / HOLD_CONFIRMATION_REQUIRED
                 / HARD_STOP_CROSS_SLIP_OVEREXPOSURE
        duplicate_groups : {key: [indices]}
        shared_distribution_groups : {key: [indices]}
        exposure_tiers : list of per-group detail dicts
        highest_exact_dup_pct : float | None
        highest_dist_fam_pct : float | None
        portfolio_stake_base : resolved denominator
        can_execute : False
    """
    combined = list(rows)
    if existing_ledger_rows:
        combined = existing_ledger_rows + list(rows)

    # Resolve denominator (workbook fallback)
    denom = portfolio_stake_base
    if denom is None or denom <= 0:
        denom_sum = sum(float(r.get("proposed_stake") or 0) for r in combined)
        denom = denom_sum if denom_sum > 0 else None

    dup_groups  = build_duplicate_groups(combined)
    dist_groups = build_shared_distribution_groups(combined)

    exposure_tiers: list[dict[str, Any]] = []
    highest_exact_dup_pct: float | None = None
    highest_dist_pct:      float | None = None
    overall_tier = TIER_0

    # ── Exact duplicate groups ────────────────────────────────────────────
    for key, indices in dup_groups.items():
        pct = calculate_duplicate_leg_exposure_pct(indices, combined, denom)
        if pct is None:
            # Unknown denominator: treat as TIER_2 (missing denominator caps at HOLD)
            tier = TIER_2
            action = TIER_ACTIONS[TIER_2]
            note = "denominator_unavailable"
        elif pct > _EXACT_DUP_TIER3_THRESHOLD:
            tier = TIER_3
            action = TIER_ACTIONS[TIER_3]
            note = f"exact_dup_pct={pct:.1%}>20%"
        elif pct > 0:
            tier = TIER_1
            action = TIER_ACTIONS[TIER_1]
            note = f"exact_dup_pct={pct:.1%}"
        else:
            tier = TIER_0
            action = TIER_ACTIONS[TIER_0]
            note = "no_exposure"

        exposure_tiers.append({
            "group_type": "exact_duplicate",
            "key": key,
            "row_indices": indices,
            "exposure_pct": pct,
            "tier": tier,
            "action": action,
            "note": note,
        })

        if pct is not None and (highest_exact_dup_pct is None or pct > highest_exact_dup_pct):
            highest_exact_dup_pct = pct
        overall_tier = _max_tier(overall_tier, tier)

    # ── Shared-distribution groups (nested/alternate-line) ───────────────
    for key, indices in dist_groups.items():
        pct = calculate_distribution_family_exposure_pct(indices, combined, denom)
        if pct is None:
            tier = TIER_2
            action = TIER_ACTIONS[TIER_2]
            note = "denominator_unavailable"
        elif pct > _DIST_FAM_TIER2_THRESHOLD:
            tier = TIER_2
            action = TIER_ACTIONS[TIER_2]
            note = f"dist_fam_pct={pct:.1%}>20%"
        elif pct > 0:
            tier = TIER_1
            action = TIER_ACTIONS[TIER_1]
            note = f"dist_fam_pct={pct:.1%}"
        else:
            tier = TIER_0
            action = TIER_ACTIONS[TIER_0]
            note = "no_exposure"

        exposure_tiers.append({
            "group_type": "shared_distribution",
            "key": key,
            "row_indices": indices,
            "exposure_pct": pct,
            "tier": tier,
            "action": action,
            "note": note,
        })

        if pct is not None and (highest_dist_pct is None or pct > highest_dist_pct):
            highest_dist_pct = pct
        overall_tier = _max_tier(overall_tier, tier)

    return {
        "tier": overall_tier,
        "action": TIER_ACTIONS[overall_tier],
        "duplicate_groups": dup_groups,
        "shared_distribution_groups": dist_groups,
        "exposure_tiers": exposure_tiers,
        "highest_exact_dup_pct": highest_exact_dup_pct,
        "highest_dist_fam_pct": highest_dist_pct,
        "portfolio_stake_base": denom,
        "row_count_evaluated": len(combined),
        "can_execute": False,
    }


_TIER_ORDER = [TIER_0, TIER_1, TIER_2, TIER_3]


def _max_tier(a: str, b: str) -> str:
    """Return the more restrictive of two tier labels."""
    rank_a = _TIER_ORDER.index(a) if a in _TIER_ORDER else 0
    rank_b = _TIER_ORDER.index(b) if b in _TIER_ORDER else 0
    return _TIER_ORDER[max(rank_a, rank_b)]
