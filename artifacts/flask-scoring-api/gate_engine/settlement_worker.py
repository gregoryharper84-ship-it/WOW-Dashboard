"""
gate_engine/settlement_worker.py
Stage 2 — Item 6: Background settlement automation worker

Polls for unsettled records in llp_event_settlements and
kalshi_forecast_ledger. Grades ONLY the selected side using
gate_engine.ml_settlement_truth.reconcile_settlement() for prop picks
and kalshi_engine.settlement_reconciliation.reconcile() for Kalshi.

Worker behaviour:
  - Runs as a daemon thread; started once per gunicorn worker process.
  - Uses pg_try_advisory_lock so only ONE worker across the pool grades
    at any given tick (same pattern as _llp_snapshot_cron_loop).
  - Polls every SETTLEMENT_WORKER_INTERVAL_SEC (default 300 = 5 min).
  - Never places live orders or trades.

IMPORTANT: can_execute is always False.
  The worker records calibration data only.
  It must never submit orders to any exchange or sportsbook.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

# ── Safety constants ──────────────────────────────────────────────────────────
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
CAN_EXECUTE    = False

# ── Worker configuration ──────────────────────────────────────────────────────
SETTLEMENT_WORKER_INTERVAL_SEC = int(
    os.environ.get("SETTLEMENT_WORKER_INTERVAL_SEC", "300")
)
SETTLEMENT_WORKER_ENABLED = (
    os.environ.get("SETTLEMENT_WORKER_DISABLED", "").strip().lower()
    not in ("1", "true", "yes")
)

# pg_try_advisory_lock key — must be unique across all workers in the DB
_ADVISORY_LOCK_KEY = 778597299  # chosen to not collide with LLP cron (778597203)

# Batch size: max records graded per tick
_BATCH_SIZE = int(os.environ.get("SETTLEMENT_WORKER_BATCH_SIZE", "20"))

# ── Worker state ──────────────────────────────────────────────────────────────
_WORKER_STARTED = False
_WORKER_LOCK    = threading.Lock()

_WORKER_STATS: dict[str, Any] = {
    "ticks":              0,
    "props_graded":       0,
    "kalshi_graded":      0,
    "errors":             0,
    "last_tick":          None,
    "last_success_tick":  None,   # updated only when at least one row was graded
    "last_error":         None,
    "enabled":            SETTLEMENT_WORKER_ENABLED,
    "interval_sec":       SETTLEMENT_WORKER_INTERVAL_SEC,
}


# ─────────────────────────────────────────────────────────────────────────────
# DB helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_conn():
    import psycopg2  # type: ignore
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=10)


# ─────────────────────────────────────────────────────────────────────────────
# Prop settlement grading (llp_event_settlements)
# ─────────────────────────────────────────────────────────────────────────────

def _grade_open_prop_settlements(cur, conn) -> int:
    """
    Find OPEN prop settlement records and attempt to grade them.
    Grades ONLY the selected_side — never the opposing side.
    Returns the number of records graded.
    """
    from .ml_settlement_truth import reconcile_settlement
    import math

    try:
        cur.execute(
            """
            SELECT id, event_key, selected_side, model_probability,
                   entry_price, closing_price, raw_row
            FROM llp_event_settlements
            WHERE settlement_status = 'OPEN'
              AND selected_side IS NOT NULL
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (_BATCH_SIZE,),
        )
        rows = cur.fetchall()
    except Exception:
        return 0

    graded = 0
    for row in rows:
        rec_id, event_key, selected_side, model_prob, entry_price, closing_price, raw_row = row
        raw = raw_row or {}

        # Build a reconcilable entry with the SELECTED SIDE only
        entry = {
            "official_event_result":  raw.get("official_event_result"),
            "selected_side":          selected_side,
            "selected_side_is_home":  raw.get("selected_side_is_home"),
            "platform_display_result": raw.get("platform_display_result"),
            "platform_payment":       raw.get("platform_payment"),
            "stake":                  raw.get("stake"),
            "promo_protection_active": raw.get("promo_protection_active", False),
        }

        # Skip if official result is not yet available
        if not entry["official_event_result"] or entry["official_event_result"] == "UNKNOWN":
            continue

        reconciled = reconcile_settlement(entry)
        model_result = reconciled.get("model_result")
        if model_result == "UNKNOWN":
            continue   # still can't grade

        # Compute calibration metrics
        brier: Optional[float] = None
        log_loss: Optional[float] = None
        outcome = 1 if model_result == "WIN" else (0 if model_result == "LOSS" else None)
        if model_prob is not None and outcome is not None:
            p = float(model_prob)
            brier = round((p - outcome) ** 2, 6)
            p_clip = max(1e-7, min(1 - 1e-7, p))
            log_loss = round(
                -(outcome * math.log(p_clip) + (1 - outcome) * math.log(1 - p_clip)), 8
            )

        # Probability bucket
        bucket = _probability_to_bucket(
            float(model_prob) if model_prob is not None else None
        )

        # CLV
        clv = None
        if closing_price is not None and entry_price is not None:
            try:
                clv = round(float(closing_price) - float(entry_price), 4)
            except (TypeError, ValueError):
                pass

        process_pass_fail = "PASS" if model_result in ("WIN", "LOSS", "PUSH") else "FAIL"
        failure_category  = None if process_pass_fail == "PASS" else "UNRESOLVABLE_RESULT"

        try:
            # Idempotency guard (Item 6): AND settlement_status = 'OPEN' ensures
            # that a row already graded by a prior tick is a no-op, not an error.
            cur.execute(
                """
                UPDATE llp_event_settlements
                SET settlement_status    = 'SETTLED',
                    settled_at           = NOW(),
                    selected_side_result = %s,
                    brier_score          = %s,
                    log_loss             = %s,
                    calibration_bucket   = %s,
                    clv                  = %s,
                    process_pass_fail    = %s,
                    failure_category     = %s
                WHERE id = %s
                  AND settlement_status = 'OPEN'
                """,
                (
                    model_result, brier, log_loss, bucket,
                    clv, process_pass_fail, failure_category,
                    rec_id,
                ),
            )
            if cur.rowcount > 0:
                graded += 1
        except Exception:
            pass

    if graded:
        conn.commit()

    return graded


# ─────────────────────────────────────────────────────────────────────────────
# Kalshi settlement grading (kalshi_forecast_ledger)
# ─────────────────────────────────────────────────────────────────────────────

def _grade_open_kalshi_settlements(cur, conn) -> int:
    """
    Find OPEN Kalshi ledger records and attempt to settle them by querying
    the Kalshi API for market resolution.

    Grades ONLY the side_yes_no that was originally evaluated.
    Returns the number of records graded.
    """
    try:
        cur.execute(
            """
            SELECT id, market_ticker, side_yes_no, model_probability
            FROM kalshi_forecast_ledger
            WHERE settlement_status = 'OPEN'
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (_BATCH_SIZE,),
        )
        rows = cur.fetchall()
    except Exception:
        return 0

    graded = 0
    for row in rows:
        rec_id, market_ticker, side_yes_no, model_prob = row

        # Fetch settlement from Kalshi API
        resolution = _fetch_kalshi_resolution(market_ticker)
        if resolution is None:
            continue  # market not yet settled or data unavailable

        yes_resolved = resolution.get("yes_resolved")
        closing_price_cents = resolution.get("closing_price_cents")

        if yes_resolved is None:
            continue

        # Grade ONLY the side that was evaluated
        from kalshi_engine.settlement_reconciliation import reconcile, FILL_STATUS_FILLED, SS_SETTLED
        result_dict = reconcile(
            market_ticker=market_ticker,
            side=side_yes_no,
            fill_status=FILL_STATUS_FILLED,
            calibration_eligible=True,
            settlement_status=SS_SETTLED,
            yes_resolved=yes_resolved,
            closing_price_cents=closing_price_cents,
        )

        if not result_dict.get("calibration_include"):
            continue

        final_result = result_dict.get("final_result")
        outcome = 1 if final_result == "WIN" else (0 if final_result == "LOSS" else None)

        brier: Optional[float] = None
        log_loss: Optional[float] = None
        import math
        if model_prob is not None and outcome is not None:
            p = float(model_prob)
            brier = round((p - outcome) ** 2, 6)
            p_clip = max(1e-7, min(1 - 1e-7, p))
            log_loss = round(
                -(outcome * math.log(p_clip) + (1 - outcome) * math.log(1 - p_clip)), 8
            )

        clv_cents   = result_dict.get("clv_cents")
        clv_percent = result_dict.get("clv_percent")

        result_value = "YES" if final_result == "WIN" else (
            "NO" if final_result == "LOSS" else "VOID"
        )

        try:
            # Idempotency guard (Item 6): AND settlement_status = 'OPEN' makes
            # a previously-graded row a no-op across consecutive ticks.
            cur.execute(
                """
                UPDATE kalshi_forecast_ledger
                SET updated_at        = NOW(),
                    settlement_status = 'SETTLED',
                    result            = %s,
                    closing_price     = %s,
                    brier_score       = %s,
                    clv               = %s,
                    net_pnl           = %s
                WHERE id = %s
                  AND settlement_status = 'OPEN'
                """,
                (
                    result_value,
                    (closing_price_cents / 100.0) if closing_price_cents is not None else None,
                    brier,
                    clv_percent,
                    (result_dict.get("net_pnl_after_fees_cents") or 0) / 100.0,
                    rec_id,
                ),
            )
            if cur.rowcount > 0:
                graded += 1
        except Exception:
            pass

    if graded:
        conn.commit()

    return graded


def _fetch_kalshi_resolution(market_ticker: str) -> Optional[dict[str, Any]]:
    """
    Query Kalshi API for market settlement.
    Returns {yes_resolved: bool, closing_price_cents: float} or None.
    Can_execute is always False — this is a READ-ONLY data fetch.
    """
    try:
        import requests  # noqa: PLC0415
        base_url = os.environ.get("KALSHI_BASE_URL", "https://trading-api.kalshi.com")
        api_key  = os.environ.get("KALSHI_API_KEY", "")
        headers  = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        r = requests.get(
            f"{base_url}/trade-api/v2/markets/{market_ticker}",
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data   = r.json().get("market") or {}
        status = data.get("status", "").lower()
        if status not in ("settled", "finalized"):
            return None

        result = data.get("result", "").upper()
        if result not in ("YES", "NO"):
            return None

        yes_resolved = result == "YES"
        # closing price: last traded price for YES side (in cents, 0-100)
        close_price = data.get("close_price")

        return {
            "yes_resolved":       yes_resolved,
            "closing_price_cents": float(close_price) if close_price is not None else None,
        }
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Worker tick
# ─────────────────────────────────────────────────────────────────────────────

def _probability_to_bucket(prob: Optional[float]) -> Optional[str]:
    if prob is None:
        return None
    buckets = [
        (0.52, 0.55, "52-55%"),
        (0.55, 0.60, "55-60%"),
        (0.60, 0.65, "60-65%"),
        (0.65, 0.70, "65-70%"),
        (0.70, 1.01, "70%+"),
    ]
    for lo, hi, label in buckets:
        if lo <= prob < hi:
            return label
    return None


def _settlement_worker_tick() -> None:
    """One tick: try to acquire advisory lock, then grade open settlements."""
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        _WORKER_STATS["last_error"] = "missing DATABASE_URL"
        return

    conn = None
    lock_held = False
    props_graded  = 0
    kalshi_graded = 0

    try:
        conn = _get_conn()
        cur  = conn.cursor()

        cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        lock_held = cur.fetchone()[0]
        if not lock_held:
            # Another worker is grading right now — skip this tick
            cur.close()
            conn.close()
            return

        # Grade prop settlements (llp_event_settlements)
        try:
            props_graded = _grade_open_prop_settlements(cur, conn)
        except Exception as e:
            _WORKER_STATS["last_error"] = f"prop_grade: {e}"

        # Grade Kalshi settlements (kalshi_forecast_ledger)
        try:
            kalshi_graded = _grade_open_kalshi_settlements(cur, conn)
        except Exception as e:
            _WORKER_STATS["last_error"] = f"kalshi_grade: {e}"

    except Exception as e:
        _WORKER_STATS["last_error"] = f"db-connect: {e}"
        _WORKER_STATS["errors"]    += 1
    finally:
        if conn:
            try:
                if lock_held:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
                    conn.commit()
                cur.close()
                conn.close()
            except Exception:
                pass

    _WORKER_STATS["ticks"]         += 1
    _WORKER_STATS["props_graded"]  += props_graded
    _WORKER_STATS["kalshi_graded"] += kalshi_graded
    now_iso = datetime.now(timezone.utc).isoformat()
    _WORKER_STATS["last_tick"] = now_iso
    # Item 7: last_success_tick is only updated when at least one row was graded
    if props_graded + kalshi_graded > 0:
        _WORKER_STATS["last_success_tick"] = now_iso
        _WORKER_STATS["last_error"]        = None


# ─────────────────────────────────────────────────────────────────────────────
# Worker loop and public start function
# ─────────────────────────────────────────────────────────────────────────────

def _settlement_worker_loop() -> None:
    """Daemon loop: tick every SETTLEMENT_WORKER_INTERVAL_SEC seconds."""
    while True:
        try:
            _settlement_worker_tick()
        except Exception as e:
            _WORKER_STATS["last_error"] = f"loop: {e}"
            _WORKER_STATS["errors"]    += 1
        time.sleep(SETTLEMENT_WORKER_INTERVAL_SEC)


def start_settlement_worker() -> None:
    """
    Start the background settlement worker daemon thread.
    Called once at application startup (e.g. from app.py after gunicorn post_fork).

    Safe to call multiple times — only one thread is ever started per process.
    Respects SETTLEMENT_WORKER_DISABLED env var.

    can_execute is unconditionally False — no live orders will ever be placed.
    """
    global _WORKER_STARTED
    if not SETTLEMENT_WORKER_ENABLED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        t = threading.Thread(
            target=_settlement_worker_loop,
            daemon=True,
            name="settlement-worker",
        )
        t.start()
        _WORKER_STARTED = True


def get_worker_status() -> dict[str, Any]:
    """Return current worker status (for health endpoints)."""
    return {
        **_WORKER_STATS,
        "started":       _WORKER_STARTED,
        "can_execute":   CAN_EXECUTE,
        "execution_rule": EXECUTION_RULE,
    }
