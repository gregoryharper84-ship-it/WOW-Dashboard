"""
pp_pregame_snapshot.py — Immutable Pregame Evidence Snapshot
WOW-PATCH-2026-08-15-PP-PROMOTION-AND-SAME-GAME-FRAGILITY

Written once: after the final refresh check passes, before final-card
publication.  Records are never updated (write-once / append-only).

Failure behaviour:
    A write failure PRESERVES research output (probability rank, analysis)
    but BLOCKS final-card / money qualification by setting
    PREGAME_SNAPSHOT_BLOCK on the affected rows.

    This is fail-closed for the paid-card layer only.
    Research visibility is never silenced by a snapshot failure.

Schema: wow_pp_pregame_snapshots
    snapshot_id             TEXT PRIMARY KEY
    research_run_id         TEXT
    row_id                  TEXT
    snapshot_at             TIMESTAMPTZ
    final_refresh_passed    BOOLEAN
    lineup_fingerprint      TEXT        -- hash of participant/lineup state
    market_fingerprint      TEXT        -- hash of market/line/side state
    price_at_snapshot       TEXT        -- odds at snapshot time (American)
    calibrated_probability  NUMERIC
    lower_bound             NUMERIC
    upper_bound             NUMERIC
    slip_type               TEXT
    sources_version         JSONB       -- {source_name: version/timestamp}
    pipeline_meta           JSONB
    created_at              TIMESTAMPTZ DEFAULT NOW()

Module invariants:
    can_execute              = False   (unconditional)
    PRODUCTION_AUTHORITY     = False
    EXECUTION_RULE           = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Module-level authority constants — unconditional
# ---------------------------------------------------------------------------
can_execute          = False
PRODUCTION_AUTHORITY = False
EXECUTION_RULE       = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Label set on rows when snapshot write fails (imported lazily)
_SNAPSHOT_BLOCK_LABEL = "PREGAME_SNAPSHOT_BLOCK"
_CAP_LABEL            = "MARKET_VERIFIED_HOLD"

# ---------------------------------------------------------------------------
# Fields captured in pipeline_meta for use as final-refresh baselines.
# Must cover the union of all fields read by pp_final_refresh detectors.
# ---------------------------------------------------------------------------
_BASELINE_FIELDS: frozenset[str] = frozenset({
    # LINEUP detector
    "lineup_status", "status", "injury_flag", "is_confirmed", "dnp_flag",
    # PARTICIPANT detector
    "player", "team", "opponent", "game", "game_id", "game_time",
    # MARKET detector
    "prop_type", "market", "stat_key", "line", "side", "direction",
    # PRICE detector
    "odds_more", "odds_less", "price", "price_more", "price_less",
    # SETTLEMENT detector
    "game_settled", "series_settled", "settlement_state", "game_status",
    # WEATHER detector
    "weather_condition", "weather_forecast", "precipitation_probability",
    "wind_speed", "temperature", "weather_risk_flag",
    # SOURCE detector: stored separately via sources_version JSONB column
})

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS wow_pp_pregame_snapshots (
    snapshot_id            TEXT         PRIMARY KEY,
    research_run_id        TEXT,
    row_id                 TEXT,
    snapshot_at            TIMESTAMPTZ  NOT NULL,
    final_refresh_passed   BOOLEAN      NOT NULL,
    lineup_fingerprint     TEXT,
    market_fingerprint     TEXT,
    price_at_snapshot      TEXT,
    calibrated_probability NUMERIC,
    lower_bound            NUMERIC,
    upper_bound            NUMERIC,
    slip_type              TEXT,
    sources_version        JSONB,
    pipeline_meta          JSONB,
    created_at             TIMESTAMPTZ  DEFAULT NOW()
)
"""

_CREATE_IDX_ROW    = "CREATE INDEX IF NOT EXISTS idx_ppsnap_row_id ON wow_pp_pregame_snapshots(row_id)"
_CREATE_IDX_RUN    = "CREATE INDEX IF NOT EXISTS idx_ppsnap_run_id ON wow_pp_pregame_snapshots(research_run_id)"
_CREATE_IDX_DATE   = "CREATE INDEX IF NOT EXISTS idx_ppsnap_date   ON wow_pp_pregame_snapshots(snapshot_at)"


def ensure_table(conn) -> None:
    """Create the snapshot table + indexes if they don't exist. Idempotent."""
    with conn.cursor() as cur:
        cur.execute(_CREATE_TABLE)
        cur.execute(_CREATE_IDX_ROW)
        cur.execute(_CREATE_IDX_RUN)
        cur.execute(_CREATE_IDX_DATE)
    conn.commit()


_SELECT_LATEST_SNAPSHOT = """
SELECT pipeline_meta, sources_version
FROM   wow_pp_pregame_snapshots
WHERE  row_id = %s
ORDER  BY snapshot_at DESC
LIMIT  1
"""


def fetch_latest_snapshot(conn, row_id: str) -> dict | None:
    """
    Return the most recent pregame snapshot for *row_id* as a baseline dict
    compatible with pp_final_refresh detectors, or None if no record exists.

    The returned dict merges pipeline_meta (raw field values captured at
    write time) with a reconstructed ``"sources"`` key from the stored
    sources_version JSONB, matching the shape _detect_source_change expects.

    Never raises — any DB or deserialization error returns None so the
    caller falls back to a vacuous pass in the final-refresh gate.

    Authority: read-only; carries no execution or approval authority.
    can_execute = False unconditional.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(_SELECT_LATEST_SNAPSHOT, (row_id,))
            record = cur.fetchone()
        if record is None:
            return None
        pipeline_meta, sources_version = record
        baseline: dict = dict(pipeline_meta) if isinstance(pipeline_meta, dict) else {}
        # Reconstruct the "sources" key the source-change detector needs
        baseline["sources"] = sources_version if isinstance(sources_version, dict) else {}
        return baseline
    except Exception:
        return None  # best-effort: DB unavailable → vacuous pass


def ensure_table_standalone() -> None:
    """
    No-arg wrapper for startup-warmup contexts.
    Creates its own short-lived connection from DATABASE_URL and closes it
    immediately — safe to call from daemon threads or gunicorn post_fork hooks.
    Errors are silenced (non-fatal): the table will be created on first write.
    """
    import os as _os
    try:
        import psycopg2 as _pg  # type: ignore
        db_url = _os.environ.get("DATABASE_URL")
        if not db_url:
            return
        conn = _pg.connect(db_url, connect_timeout=10)
        try:
            ensure_table(conn)
        finally:
            conn.close()
    except Exception:
        pass  # non-fatal; first write call will also ensure the table


# ---------------------------------------------------------------------------
# Fingerprinting helpers
# ---------------------------------------------------------------------------

def _fingerprint(data: Any) -> str:
    """Return a stable 16-char hex fingerprint of the given data."""
    try:
        canonical = json.dumps(data, sort_keys=True, default=str)
    except Exception:
        canonical = str(data)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _lineup_fingerprint(row: dict[str, Any]) -> str:
    """Hash key lineup-affecting fields."""
    fields = {
        "player":          row.get("player"),
        "team":            row.get("team"),
        "opponent":        row.get("opponent"),
        "game":            row.get("game") or row.get("game_id"),
        "game_time":       row.get("game_time"),
        "lineup_status":   row.get("lineup_status") or row.get("status"),
        "is_confirmed":    row.get("is_confirmed"),
        "injury_flag":     row.get("injury_flag"),
    }
    return _fingerprint(fields)


def _market_fingerprint(row: dict[str, Any]) -> str:
    """Hash key market-affecting fields."""
    fields = {
        "prop_type":       row.get("prop_type") or row.get("market"),
        "stat_key":        row.get("stat_key"),
        "line":            row.get("line"),
        "side":            row.get("side") or row.get("direction"),
        "displayed_line":  (row.get("pp_thresholds") or {}).get("displayed_line"),
        "cash_threshold":  (row.get("pp_thresholds") or {}).get("cash_threshold"),
    }
    return _fingerprint(fields)


# ---------------------------------------------------------------------------
# Build snapshot payload from a scored row
# ---------------------------------------------------------------------------

def build_snapshot(
    row: dict[str, Any],
    final_refresh_passed: bool,
    pipeline_meta: dict | None = None,
) -> dict[str, Any]:
    """
    Build the snapshot payload dict from a scored row.
    Does not write to DB — call write_snapshot() for that.
    """
    gates   = row.get("gates") or {}
    wnba_g  = gates.get("wnba_generative", {}) or {}
    tennis_g = gates.get("tennis_total_games", {}) or {}

    def _sf(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    cal_prob = (
        _sf(wnba_g.get("cal_selected"))
        or _sf(tennis_g.get("cal_selected"))
        or _sf(row.get("calibrated_probability"))
    )
    lower_bound = (
        _sf(wnba_g.get("cal_lower_bound"))
        or _sf(tennis_g.get("cal_lower_bound"))
        or _sf(row.get("calibrated_probability_lower_bound"))
        or _sf(row.get("lower_bound"))
    )
    upper_bound = (
        _sf(wnba_g.get("cal_upper_bound"))
        or _sf(row.get("calibrated_probability_upper_bound"))
        or _sf(row.get("upper_bound"))
    )

    sources_version: dict[str, Any] = {}
    for src_name, src_data in (row.get("sources") or {}).items():
        if isinstance(src_data, dict):
            sources_version[src_name] = (
                src_data.get("version")
                or src_data.get("timestamp")
                or src_data.get("pulled_at")
                or "unknown"
            )

    return {
        "snapshot_id":             str(uuid.uuid4()),
        "research_run_id":         row.get("research_run_id"),
        "row_id":                  row.get("row_id"),
        "snapshot_at":             datetime.now(timezone.utc).isoformat(),
        "final_refresh_passed":    final_refresh_passed,
        "lineup_fingerprint":      _lineup_fingerprint(row),
        "market_fingerprint":      _market_fingerprint(row),
        "price_at_snapshot":       str(row.get("price") or row.get("odds_more") or ""),
        "calibrated_probability":  cal_prob,
        "lower_bound":             lower_bound,
        "upper_bound":             upper_bound,
        "slip_type":               (row.get("slip_type") or "NONE").upper(),
        "sources_version":         sources_version,
        # Populate pipeline_meta with the fields the final-refresh detectors
        # need so a subsequent scoring run can load this snapshot as a
        # baseline and detect lineup/market/price/source changes.
        # Caller-supplied pipeline_meta overrides the row-extracted defaults.
        "pipeline_meta":           pipeline_meta if pipeline_meta is not None
                                   else {k: row.get(k) for k in _BASELINE_FIELDS},
    }


# ---------------------------------------------------------------------------
# Write snapshot (fail-closed for paid-card layer)
# ---------------------------------------------------------------------------

_INSERT_SQL = """
INSERT INTO wow_pp_pregame_snapshots (
    snapshot_id, research_run_id, row_id,
    snapshot_at, final_refresh_passed,
    lineup_fingerprint, market_fingerprint, price_at_snapshot,
    calibrated_probability, lower_bound, upper_bound,
    slip_type, sources_version, pipeline_meta
) VALUES (
    %s, %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s
)
ON CONFLICT (snapshot_id) DO NOTHING
"""


def write_snapshot(
    conn,
    snap: dict[str, Any],
) -> tuple[bool, str | None]:
    """
    Write a pregame snapshot to the database.

    Returns (success: bool, error_message: str | None).
    Never raises — caller must handle the False case by blocking the row.
    """
    try:
        ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                _INSERT_SQL,
                (
                    snap["snapshot_id"],
                    snap.get("research_run_id"),
                    snap.get("row_id"),
                    snap["snapshot_at"],
                    snap["final_refresh_passed"],
                    snap.get("lineup_fingerprint"),
                    snap.get("market_fingerprint"),
                    snap.get("price_at_snapshot"),
                    snap.get("calibrated_probability"),
                    snap.get("lower_bound"),
                    snap.get("upper_bound"),
                    snap.get("slip_type"),
                    json.dumps(snap.get("sources_version") or {}),
                    json.dumps(snap.get("pipeline_meta") or {}),
                ),
            )
        conn.commit()
        return True, None
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, str(exc)


def snapshot_and_enforce(
    conn,
    row: dict[str, Any],
    final_refresh_passed: bool,
    pipeline_meta: dict | None = None,
) -> dict[str, Any]:
    """
    Build + write a snapshot for a single row.

    On write failure:
      - Preserves research output (probability, analysis) — never silenced
      - Appends PREGAME_SNAPSHOT_BLOCK blocker
      - Caps terminal_label at MARKET_VERIFIED_HOLD if it was a paid-card label

    Returns a status dict.
    """
    snap    = build_snapshot(row, final_refresh_passed, pipeline_meta)
    ok, err = write_snapshot(conn, snap)

    row.setdefault("gates", {})["pp_pregame_snapshot"] = {
        "can_execute":          False,
        "snapshot_id":          snap["snapshot_id"],
        "written":              ok,
        "write_error":          err,
        "final_refresh_passed": final_refresh_passed,
        "lineup_fingerprint":   snap.get("lineup_fingerprint"),
        "market_fingerprint":   snap.get("market_fingerprint"),
    }

    if not ok:
        # Preserve research output; block paid-card progression only
        blocker = f"PREGAME_SNAPSHOT_WRITE_FAIL:{err or 'unknown'}"
        if blocker not in (row.get("blockers") or []):
            row.setdefault("blockers", []).append(blocker)
        row["pregame_snapshot_available"] = False

        paid_labels = {"MONEY_QUALIFIED", "FINAL_APPROVED"}
        if row.get("terminal_label") in paid_labels:
            row["terminal_label"] = _CAP_LABEL
    else:
        row["pregame_snapshot_available"] = True

    return {
        "can_execute":              False,
        "snapshot_id":              snap["snapshot_id"],
        "written":                  ok,
        "write_error":              err,
        "pregame_snapshot_available": ok,
    }
