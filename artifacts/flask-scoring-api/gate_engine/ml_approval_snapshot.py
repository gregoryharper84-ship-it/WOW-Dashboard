"""
gate_engine/ml_approval_snapshot.py
WOW-PATCH-2026-07-13 — P2-10: Store Approval-Time Evidence

Every ML approval must preserve a snapshot of all decision inputs at the
moment of approval.  This snapshot is IMMUTABLE — settlement data is added
separately and must NEVER overwrite approval-time fields.

This allows retro analysis to distinguish:
    - bad projection     (model_prob was wrong from the start)
    - bad price          (breakeven_prob was too high)
    - stale approval     (approval_timestamp was too far before game)
    - adverse lineup change (after snapshot was taken)
    - normal variance    (CLV positive, result negative)
    - platform settlement anomaly (promo/special)

DB table: ml_approval_snapshots

Settlement data is stored in separate columns (closing_*, settlement_*).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Required approval-time fields
# ---------------------------------------------------------------------------

APPROVAL_FIELDS = [
    "approval_timestamp",
    "approved_stake",
    "approved_return",
    "approved_multiplier",
    "approved_breakeven_prob",
    "approved_model_prob",
    "approved_no_vig_prob",
    "approved_edge",
    "final_label",
    # Contextual (optional but logged)
    "starting_pitchers",
    "confirmed_lineups",
    "bullpen_state",
    "market_source",
    "market_timestamp",
]

# Settlement fields — added AFTER approval, never overwrite approval fields
SETTLEMENT_FIELDS = [
    "closing_multiplier",
    "closing_no_vig_prob",
    "closing_line_movement",
    "official_result",
    "platform_result",
    "settlement_timestamp",
    "model_result",
    "platform_settlement_status",
    "calibration_outcome",
    "calibration_eligible",
]

DDL = """
CREATE TABLE IF NOT EXISTS ml_approval_snapshots (
    id                        SERIAL PRIMARY KEY,
    snapshot_id               TEXT UNIQUE NOT NULL,
    created_at                TIMESTAMPTZ DEFAULT NOW(),
    -- Identity
    league                    TEXT,
    event_date                DATE,
    away_team                 TEXT,
    home_team                 TEXT,
    selected_side             TEXT,
    market_type               TEXT,
    -- Approval-time (IMMUTABLE after insert)
    approval_timestamp        TIMESTAMPTZ,
    approved_stake            NUMERIC,
    approved_return           NUMERIC,
    approved_multiplier       NUMERIC,
    approved_breakeven_prob   NUMERIC,
    approved_model_prob       NUMERIC,
    approved_no_vig_prob      NUMERIC,
    approved_edge             NUMERIC,
    final_label               TEXT,
    starting_pitchers         JSONB,
    confirmed_lineups         JSONB,
    bullpen_state             JSONB,
    market_source             TEXT,
    market_timestamp          TIMESTAMPTZ,
    -- Settlement (nullable; populated after game ends)
    closing_multiplier        NUMERIC,
    closing_no_vig_prob       NUMERIC,
    closing_line_movement     NUMERIC,
    official_result           TEXT,
    platform_result           TEXT,
    settlement_timestamp      TIMESTAMPTZ,
    model_result              TEXT,
    platform_settlement_status TEXT,
    calibration_outcome       TEXT,
    calibration_eligible      BOOLEAN,
    settled                   BOOLEAN DEFAULT FALSE
)
"""


# ---------------------------------------------------------------------------
# Snapshot builder (in-memory, no DB required)
# ---------------------------------------------------------------------------

def build_approval_snapshot(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Build an immutable approval-time snapshot dict from a candidate.

    The snapshot captures all decision inputs at approval time.
    Settlement data is explicitly excluded — it must be added separately
    via add_settlement_to_snapshot().

    Returns the snapshot dict (never includes settlement fields).
    """
    now = datetime.now(tz=timezone.utc).isoformat()

    snapshot: dict[str, Any] = {
        # Identity
        "snapshot_id":            candidate.get("snapshot_id") or _generate_id(candidate),
        "created_at":             now,
        "league":                 candidate.get("league"),
        "event_date":             candidate.get("event_date"),
        "away_team":              candidate.get("away_team"),
        "home_team":              candidate.get("home_team"),
        "selected_side":          candidate.get("selected_side"),
        "market_type":            candidate.get("market_type"),
        # Approval-time evidence
        "approval_timestamp":     candidate.get("approval_timestamp") or now,
        "approved_stake":         _to_float(candidate.get("stake") or candidate.get("approved_stake")),
        "approved_return":        _to_float(candidate.get("listed_return") or candidate.get("approved_return")),
        "approved_multiplier":    _to_float(candidate.get("multiplier") or candidate.get("approved_multiplier")
                                   or (_to_float(candidate.get("listed_return") or candidate.get("approved_return"))
                                       / _to_float(candidate.get("stake") or candidate.get("approved_stake"))
                                       if _to_float(candidate.get("stake") or candidate.get("approved_stake"))
                                       else None)),
        "approved_breakeven_prob": _to_float(candidate.get("breakeven_prob") or candidate.get("approved_breakeven_prob")
                                   or (_to_float(candidate.get("stake") or candidate.get("approved_stake"))
                                       / _to_float(candidate.get("listed_return") or candidate.get("approved_return"))
                                       if _to_float(candidate.get("listed_return") or candidate.get("approved_return"))
                                       else None)),
        "approved_model_prob":    _to_float(candidate.get("model_prob") or candidate.get("model_probability") or candidate.get("approved_model_prob")),
        "approved_no_vig_prob":   _to_float(candidate.get("market_no_vig_prob") or candidate.get("no_vig_probability") or candidate.get("approved_no_vig_prob")),
        "approved_edge":          _to_float(candidate.get("verified_edge") or candidate.get("edge") or candidate.get("approved_edge")),
        "final_label":            candidate.get("final_label"),
        # Contextual
        "starting_pitchers":      candidate.get("starting_pitchers"),
        "confirmed_lineups":      candidate.get("confirmed_lineups"),
        "bullpen_state":          candidate.get("bullpen_state"),
        "market_source":          candidate.get("market_source"),
        "market_timestamp":       candidate.get("market_timestamp"),
        # Settlement fields initialized to None — populated separately
        "settled":                     False,
        "closing_multiplier":          None,
        "closing_no_vig_prob":         None,
        "closing_line_movement":       None,
        "official_result":             None,
        "platform_result":             None,
        "settlement_timestamp":        None,
        "model_result":                None,
        "platform_settlement_status":  None,
        "calibration_outcome":         None,
        "calibration_eligible":        None,
        # Safety marker
        "snapshot_immutable_fields":   APPROVAL_FIELDS,
        "execution_rule":              "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
    }
    return snapshot


def add_settlement_to_snapshot(
    snapshot: dict[str, Any],
    settlement: dict[str, Any],
) -> dict[str, Any]:
    """
    Add settlement data to an existing snapshot.

    HARD RULE: approval-time fields are NEVER overwritten.
    Only settlement-specific columns are updated.
    Returns a new dict (does not mutate the original snapshot).
    """
    out = dict(snapshot)

    # Only copy settlement fields — approval fields are protected
    for field in SETTLEMENT_FIELDS:
        if field in settlement:
            out[field] = settlement[field]

    # Overwriting approval-time model_prob is explicitly blocked:
    # if settlement dict includes "approved_model_prob", drop it.
    for protected in APPROVAL_FIELDS:
        if protected in settlement and protected != "final_label":
            # Warn but never overwrite
            out.setdefault("settlement_warnings", []).append(
                f"Attempted overwrite of immutable approval field '{protected}' blocked."
            )

    out["settled"] = True
    out["settlement_timestamp"] = settlement.get("settlement_timestamp") or \
        datetime.now(tz=timezone.utc).isoformat()
    return out


def validate_snapshot_integrity(snapshot: dict[str, Any]) -> dict[str, Any]:
    """
    Validate that all required approval-time fields are present in the snapshot.

    Returns {passed, missing_fields, detail}.
    """
    critical = [
        "approved_model_prob", "approved_breakeven_prob",
        "approved_stake", "approved_return", "final_label",
        "approval_timestamp",
    ]
    missing = [f for f in critical if snapshot.get(f) is None]
    return {
        "passed":         not missing,
        "missing_fields": missing,
        "detail":         (
            "Snapshot complete" if not missing
            else f"Missing required approval fields: {missing}"
        ),
    }


# ---------------------------------------------------------------------------
# DB persistence (best-effort)
# ---------------------------------------------------------------------------

def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """
    Persist an approval snapshot to the ml_approval_snapshots table.
    Best-effort — never throws; returns {ok, id, detail}.
    """
    try:
        import psycopg2
        import json as _json
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            return {"ok": False, "detail": "DATABASE_URL not set"}
        conn = psycopg2.connect(db_url)
        cur  = conn.cursor()
        cur.execute(DDL)
        cur.execute(
            """
            INSERT INTO ml_approval_snapshots (
                snapshot_id, league, event_date, away_team, home_team,
                selected_side, market_type,
                approval_timestamp, approved_stake, approved_return,
                approved_multiplier, approved_breakeven_prob,
                approved_model_prob, approved_no_vig_prob, approved_edge,
                final_label, starting_pitchers, confirmed_lineups,
                bullpen_state, market_source, market_timestamp
            ) VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,
                %s,%s,%s,
                %s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,%s
            )
            ON CONFLICT (snapshot_id) DO NOTHING
            RETURNING id
            """,
            (
                snapshot.get("snapshot_id"),
                snapshot.get("league"),
                snapshot.get("event_date"),
                snapshot.get("away_team"),
                snapshot.get("home_team"),
                snapshot.get("selected_side"),
                snapshot.get("market_type"),
                snapshot.get("approval_timestamp"),
                snapshot.get("approved_stake"),
                snapshot.get("approved_return"),
                snapshot.get("approved_multiplier"),
                snapshot.get("approved_breakeven_prob"),
                snapshot.get("approved_model_prob"),
                snapshot.get("approved_no_vig_prob"),
                snapshot.get("approved_edge"),
                snapshot.get("final_label"),
                _json.dumps(snapshot.get("starting_pitchers")) if snapshot.get("starting_pitchers") else None,
                _json.dumps(snapshot.get("confirmed_lineups")) if snapshot.get("confirmed_lineups") else None,
                _json.dumps(snapshot.get("bullpen_state")) if snapshot.get("bullpen_state") else None,
                snapshot.get("market_source"),
                snapshot.get("market_timestamp"),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {
            "ok":     True,
            "id":     row[0] if row else None,
            "detail": f"Snapshot persisted (id={row[0] if row else 'conflict/existing'}).",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


def settle_snapshot_in_db(
    snapshot_id: str,
    settlement:  dict[str, Any],
) -> dict[str, Any]:
    """
    Write settlement columns to an existing snapshot row.
    NEVER touches approval-time columns.
    """
    try:
        import psycopg2
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            return {"ok": False, "detail": "DATABASE_URL not set"}
        conn = psycopg2.connect(db_url)
        cur  = conn.cursor()
        cur.execute(DDL)
        cur.execute(
            """
            UPDATE ml_approval_snapshots SET
                closing_multiplier        = %s,
                closing_no_vig_prob       = %s,
                closing_line_movement     = %s,
                official_result           = %s,
                platform_result           = %s,
                settlement_timestamp      = %s,
                model_result              = %s,
                platform_settlement_status = %s,
                calibration_outcome       = %s,
                calibration_eligible      = %s,
                settled                   = TRUE
            WHERE snapshot_id = %s
              AND settled = FALSE
            """,
            (
                settlement.get("closing_multiplier"),
                settlement.get("closing_no_vig_prob"),
                settlement.get("closing_line_movement"),
                settlement.get("official_result"),
                settlement.get("platform_result"),
                settlement.get("settlement_timestamp"),
                settlement.get("model_result"),
                settlement.get("platform_settlement_status"),
                settlement.get("calibration_outcome"),
                settlement.get("calibration_eligible"),
                snapshot_id,
            ),
        )
        rows_updated = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return {
            "ok":           True,
            "rows_updated": rows_updated,
            "detail":       (
                f"Settlement applied to snapshot {snapshot_id}."
                if rows_updated > 0
                else f"No unsettled snapshot found for id={snapshot_id}."
            ),
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_id(candidate: dict[str, Any]) -> str:
    import hashlib
    import json
    components = (
        str(candidate.get("league") or ""),
        str(candidate.get("event_date") or ""),
        str(candidate.get("away_team") or ""),
        str(candidate.get("home_team") or ""),
        str(candidate.get("selected_side") or ""),
        str(candidate.get("market_type") or ""),
        str(candidate.get("approval_timestamp") or datetime.now(tz=timezone.utc).isoformat()),
    )
    return hashlib.sha256(json.dumps(components).encode()).hexdigest()[:24]


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
