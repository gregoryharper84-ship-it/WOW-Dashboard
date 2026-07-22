"""
category_scan_ledger.py  —  Ledger extension for GET /wow/kalshi/category-scan
WOW v16.5 Category-Router / Singles-Governor Layer

Extends the existing `kalshi_candidate_ledger` table (owned by
kalshi_engine/llp_bridge/kalshi_watch_ledger.py) with the ~20 new columns
required by the category-scan pipeline.  Uses idempotent
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — no data loss, safe to run on
a table that already has pre-existing rows.

Also adds a separate `kalshi_category_scan_log` table that records one row
per full scan invocation (meta-level).

Thread-safety: schema init is guarded by a module-level lock + boolean flag
so it runs at most once per worker process (same pattern as kalshi_watch_ledger).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Optional

_SCHEMA_READY = False
_SCHEMA_LOCK  = threading.Lock()

_NEW_COLUMNS = [
    # Column name                  SQL type
    ("category",                  "TEXT"),
    ("lane",                      "TEXT"),
    ("contract_id",               "TEXT"),
    ("contract_identity_key",     "TEXT"),
    ("city_or_sport",             "TEXT"),
    ("event_id",                  "TEXT"),
    ("model_version",             "TEXT"),
    ("calibrated_lower_bound",    "NUMERIC"),
    ("market_price",              "NUMERIC"),
    ("fee_adjusted_break_even",   "NUMERIC"),
    ("net_edge_lower_bound",      "NUMERIC"),
    ("price_age_minutes",         "NUMERIC"),
    ("settlement_status",         "TEXT"),
    ("result",                    "TEXT"),
    ("profit_loss",               "NUMERIC"),
    ("brier_score",               "NUMERIC"),
    ("calibration_bin",           "TEXT"),
    ("closing_price_if_available","NUMERIC"),
    ("process_pass_fail",         "TEXT"),
    ("failure_category",          "TEXT"),
    ("scan_run_id",               "TEXT"),
]


def ensure_schema(conn) -> None:
    """
    Idempotent schema extension.
    1. Adds new columns to kalshi_candidate_ledger via ALTER TABLE ... ADD COLUMN IF NOT EXISTS
    2. Creates kalshi_category_scan_log if absent.
    Both operations are committed once and then cached.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with conn.cursor() as cur:
            # ── Extend existing ledger table ───────────────────────────────────
            for col_name, col_type in _NEW_COLUMNS:
                cur.execute(f"""
                    ALTER TABLE kalshi_candidate_ledger
                    ADD COLUMN IF NOT EXISTS {col_name} {col_type};
                """)

            # ── Category-scan invocation log ──────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kalshi_category_scan_log (
                    id                     BIGSERIAL PRIMARY KEY,
                    run_id                 TEXT NOT NULL,
                    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    scan_date              DATE NOT NULL DEFAULT CURRENT_DATE,
                    markets_discovered     INTEGER,
                    markets_classified     INTEGER,
                    weather_markets        INTEGER,
                    sports_winner_markets  INTEGER,
                    economics_markets      INTEGER,
                    disabled_markets       INTEGER,
                    combo_rejections       INTEGER,
                    identity_failures      INTEGER,
                    settlement_failures    INTEGER,
                    stale_price_failures   INTEGER,
                    model_failures         INTEGER,
                    edge_failures          INTEGER,
                    portfolio_failures     INTEGER,
                    final_pool_size        INTEGER,
                    request_params         JSONB,
                    duration_ms            INTEGER
                );
                CREATE INDEX IF NOT EXISTS kalshi_category_scan_log_run_id_idx
                    ON kalshi_category_scan_log (run_id);
                CREATE INDEX IF NOT EXISTS kalshi_category_scan_log_date_idx
                    ON kalshi_category_scan_log (scan_date);
            """)

            conn.commit()
        _SCHEMA_READY = True


def log_scan_candidate(
    conn,
    *,
    # existing fields from kalshi_candidate_ledger (subset used here)
    sport:                   Optional[str]   = None,
    home_team:               Optional[str]   = None,
    away_team:               Optional[str]   = None,
    ticker:                  Optional[str]   = None,
    event_ticker:            Optional[str]   = None,
    market_title:            Optional[str]   = None,
    market_type:             str             = "unknown",
    market_status:           Optional[str]   = None,
    trading_active:          Optional[bool]  = None,
    kalshi_orderbook_source: str             = "no_ticker",
    orderbook_age_seconds:   Optional[float] = None,
    staleness_grade:         Optional[str]   = None,
    scan_price:              Optional[float] = None,
    no_vig_probability:      Optional[float] = None,
    model_probability:       Optional[float] = None,
    adjusted_edge:           Optional[float] = None,
    edge_floor:              Optional[float] = None,
    label:                   str             = "KALSHI_REJECT_UNCALIBRATED",
    blocker_tags:            Optional[list]  = None,
    final_lock_checked_at:   Optional[str]   = None,
    final_lock_fresh:        bool            = False,
    notes:                   Optional[str]   = None,
    # new category-scan fields
    category:                Optional[str]   = None,
    lane:                    Optional[str]   = None,
    contract_id:             Optional[str]   = None,
    contract_identity_key:   Optional[str]   = None,
    city_or_sport:           Optional[str]   = None,
    event_id:                Optional[str]   = None,
    model_version:           Optional[str]   = None,
    calibrated_lower_bound:  Optional[float] = None,
    market_price:            Optional[float] = None,
    fee_adjusted_break_even: Optional[float] = None,
    net_edge_lower_bound:    Optional[float] = None,
    price_age_minutes:       Optional[float] = None,
    settlement_status:       Optional[str]   = None,
    result:                  Optional[str]   = None,
    profit_loss:             Optional[float] = None,
    brier_score:             Optional[float] = None,
    calibration_bin:         Optional[str]   = None,
    closing_price_if_available: Optional[float] = None,
    process_pass_fail:       Optional[str]   = None,
    failure_category:        Optional[str]   = None,
    scan_run_id:             Optional[str]   = None,
) -> int | None:
    """
    Insert one candidate row into kalshi_candidate_ledger (with extended
    category-scan columns).  Returns the new row id, or None on failure
    (never raises — logging must never break the primary pipeline).
    """
    fl_ts: Optional[datetime] = None
    if final_lock_checked_at:
        try:
            fl_ts = datetime.fromisoformat(
                final_lock_checked_at.replace("Z", "+00:00")
            )
            if fl_ts.tzinfo is None:
                fl_ts = fl_ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            fl_ts = None

    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kalshi_candidate_ledger (
                    sport, home_team, away_team, ticker, event_ticker,
                    market_title, market_type, market_status, trading_active,
                    kalshi_orderbook_source, orderbook_age_seconds, staleness_grade,
                    scan_price, no_vig_probability, model_probability,
                    adjusted_edge, edge_floor, label, blocker_tags,
                    final_lock_checked_at, final_lock_fresh, notes,
                    category, lane, contract_id, contract_identity_key,
                    city_or_sport, event_id, model_version,
                    calibrated_lower_bound, market_price, fee_adjusted_break_even,
                    net_edge_lower_bound, price_age_minutes, settlement_status,
                    result, profit_loss, brier_score, calibration_bin,
                    closing_price_if_available, process_pass_fail, failure_category,
                    scan_run_id
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s
                ) RETURNING id
            """, (
                sport, home_team, away_team, ticker, event_ticker,
                market_title, market_type, market_status, trading_active,
                kalshi_orderbook_source, orderbook_age_seconds, staleness_grade,
                scan_price, no_vig_probability, model_probability,
                adjusted_edge, edge_floor, label, blocker_tags or [],
                fl_ts, final_lock_fresh, notes,
                category, lane, contract_id, contract_identity_key,
                city_or_sport, event_id, model_version,
                calibrated_lower_bound, market_price, fee_adjusted_break_even,
                net_edge_lower_bound, price_age_minutes, settlement_status,
                result, profit_loss, brier_score, calibration_bin,
                closing_price_if_available, process_pass_fail, failure_category,
                scan_run_id,
            ))
            row_id = cur.fetchone()[0]
            conn.commit()
            return row_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def log_scan_run(
    conn,
    *,
    run_id:                str,
    scan_date:             str,
    markets_discovered:    int = 0,
    markets_classified:    int = 0,
    weather_markets:       int = 0,
    sports_winner_markets: int = 0,
    economics_markets:     int = 0,
    disabled_markets:      int = 0,
    combo_rejections:      int = 0,
    identity_failures:     int = 0,
    settlement_failures:   int = 0,
    stale_price_failures:  int = 0,
    model_failures:        int = 0,
    edge_failures:         int = 0,
    portfolio_failures:    int = 0,
    final_pool_size:       int = 0,
    request_params:        Optional[dict] = None,
    duration_ms:           Optional[int]  = None,
) -> int | None:
    """Insert one row into kalshi_category_scan_log. Returns id or None."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kalshi_category_scan_log (
                    run_id, scan_date,
                    markets_discovered, markets_classified,
                    weather_markets, sports_winner_markets,
                    economics_markets, disabled_markets, combo_rejections,
                    identity_failures, settlement_failures, stale_price_failures,
                    model_failures, edge_failures, portfolio_failures,
                    final_pool_size, request_params, duration_ms
                ) VALUES (
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                ) RETURNING id
            """, (
                run_id, scan_date,
                markets_discovered, markets_classified,
                weather_markets, sports_winner_markets,
                economics_markets, disabled_markets, combo_rejections,
                identity_failures, settlement_failures, stale_price_failures,
                model_failures, edge_failures, portfolio_failures,
                final_pool_size,
                json.dumps(request_params) if request_params else None,
                duration_ms,
            ))
            row_id = cur.fetchone()[0]
            conn.commit()
            return row_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
