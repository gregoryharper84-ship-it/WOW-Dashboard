"""
validation/prediction_logger.py

WOW 1IP Prediction Logger — observational data collection only.

DESIGN INVARIANTS
-----------------
1. Fail-open: any exception inside log_1ip_prediction is caught and stored
   as a gate diagnostic on the row. It NEVER propagates to the caller.
   Scoring availability is never affected by logger failure.
2. Idempotency: the DB-level dedup key is SHA-256[:16] of
   (pitcher_mlbam_id, game_date, line, direction) — game-level identity,
   not frozen_at.  ON CONFLICT (log_dedup_key) DO NOTHING prevents
   duplicate rows from retries or multi-worker fan-out.
3. PredictionRecord schema reused unchanged from validation.schema.
4. No production calculations modified: logger reads fields set earlier
   in the pipeline; writes only to the validation DB table.
5. Post-start guard: game already started (board_date < now UTC) → SKIP.
6. Synthetic/test row guard: known test markers in enrichment → SKIP.

Table created lazily on first call (CREATE TABLE IF NOT EXISTS — safe for
multi-worker deployments: Postgres handles concurrent DDL).

Skip reasons
------------
NOT_MLB, NOT_1IP_STAT, CEILING_NOT_HOLD, MISSING_PROBABILITY,
MISSING_PITCHER_ID, MISSING_LINE_OR_DIRECTION, MISSING_GAME_DATE,
GAME_ALREADY_STARTED, SYNTHETIC_ROW, TEST_ROW_MARKER, DB_UNAVAILABLE
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Module-level counters (in-process only; cross-worker totals read from DB)
# ---------------------------------------------------------------------------

_counters: dict[str, int] = {
    "logged":              0,
    "skipped":             0,
    "write_failures":      0,
    "duplicates_prevented": 0,
}
_counters_lock = threading.Lock()

_table_created = False
_table_lock    = threading.Lock()


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_CREATE_PREDICTION_LOG = """
CREATE TABLE IF NOT EXISTS wow_validation_prediction_log (
    log_id              BIGSERIAL PRIMARY KEY,
    prediction_id       TEXT        NOT NULL,
    log_dedup_key       TEXT        NOT NULL UNIQUE,   -- (pitcher+date+line+dir) hash
    schema_version      TEXT        NOT NULL DEFAULT '1.0.0',
    frozen_at           TIMESTAMPTZ NOT NULL,
    sport               TEXT        NOT NULL DEFAULT 'MLB',
    prop_type           TEXT        NOT NULL DEFAULT '1IP_PITCHES_THROWN',
    game_date           TEXT        NOT NULL,
    pitcher_name        TEXT        NOT NULL,
    pitcher_mlbam_id    INTEGER     NOT NULL,
    opponent            TEXT,
    line                NUMERIC(8,2) NOT NULL,
    direction           TEXT        NOT NULL CHECK (direction IN ('LESS','MORE')),
    model_probability   NUMERIC(8,6),
    model_uncertainty   NUMERIC(8,6),
    feature_snapshot_id TEXT        NOT NULL,
    model_version       TEXT        NOT NULL,
    data_provenance     JSONB,
    run_id              TEXT,
    request_id          TEXT,
    board_date          TEXT,
    start_time          TEXT,
    skip_reason         TEXT,
    logged_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wow_validation_outcome_log (
    outcome_log_id      BIGSERIAL   PRIMARY KEY,
    prediction_id       TEXT        NOT NULL,
    log_dedup_key       TEXT        NOT NULL UNIQUE REFERENCES wow_validation_prediction_log(log_dedup_key),
    schema_version      TEXT        NOT NULL DEFAULT '1.0.0',
    outcome_timestamp   TIMESTAMPTZ NOT NULL,
    actual_pitches      INTEGER     NOT NULL CHECK (actual_pitches >= 0),
    hit                 BOOLEAN     NOT NULL,
    outcome_source      TEXT        NOT NULL,
    outcome_verified    BOOLEAN     NOT NULL DEFAULT FALSE,
    notes               TEXT,
    logged_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _ensure_tables(conn) -> None:
    global _table_created
    with _table_lock:
        if _table_created:
            return
        with conn.cursor() as cur:
            cur.execute(_CREATE_PREDICTION_LOG)
        conn.commit()
        _table_created = True


# ---------------------------------------------------------------------------
# DB connection — own connection; never imports from app.py
# ---------------------------------------------------------------------------

def _get_conn():
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("psycopg2 not available")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=5)


# ---------------------------------------------------------------------------
# Dedup key  (stable across workers; independent of frozen_at)
# ---------------------------------------------------------------------------

def _log_dedup_key(pitcher_mlbam_id: int, game_date: str,
                   line: float, direction: str) -> str:
    payload = {
        "pitcher_mlbam_id": int(pitcher_mlbam_id),
        "game_date":        game_date,
        "line":             float(line),
        "direction":        direction.upper(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "ddk_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Probability extraction from enrichment / row gates
# ---------------------------------------------------------------------------

def _extract_probability(enrichment: dict) -> Optional[float]:
    """
    Pull calibrated probability from the enrichment ledger.

    The 1IP event-tree result flows through pitcher_prob_ledger_adapter →
    model_probability_ledger → prob_ledger.run, ending up as
    enrichment["model_probability_ledger"]["calibrated_probability"].
    Falls back to raw_probability when calibrated is absent.
    """
    ledger = enrichment.get("model_probability_ledger") or {}
    for key in ("calibrated_probability", "raw_probability", "hit_probability"):
        val = ledger.get(key)
        if val is not None:
            try:
                f = float(val)
                if 0.0 <= f <= 1.0:
                    return round(f, 6)
            except (TypeError, ValueError):
                pass
    return None


def _extract_uncertainty(enrichment: dict) -> Optional[float]:
    ledger = enrichment.get("model_probability_ledger") or {}
    for key in ("uncertainty", "model_uncertainty"):
        val = ledger.get(key)
        if val is not None:
            try:
                return round(float(val), 6)
            except (TypeError, ValueError):
                pass
    return None


# ---------------------------------------------------------------------------
# Skip-reason checks
# ---------------------------------------------------------------------------

_TERMINAL_REJECTS = frozenset({
    "REJECT_NO_PLAY", "REJECT_DATA_QUALITY", "DATA_CONTRACT_FAIL",
    "REJECT_COINFLIP", "REJECT_ODDS", "REJECT_MARKET_ADVERSE",
})

_TEST_MARKERS = frozenset({
    "synthetic_fixture", "test_fixture", "SYNTHETIC", "TEST_ONLY",
    "__test__", "pytest",
})


def _skip_reason(
    row: dict,
    enrichment: dict,
    model_prob: Optional[float],
) -> Optional[str]:
    """
    Return a typed skip reason string, or None if the row is eligible.

    Checks are ordered from cheapest to most expensive.
    """
    # Sport / stat key
    sport   = (row.get("sport") or "").upper()
    stat_sk = (row.get("stat_key") or row.get("prop_type") or "").upper()

    if sport != "MLB":
        return "NOT_MLB"
    if stat_sk != "1IP_PITCHES_THROWN":
        return "NOT_1IP_STAT"

    # Ceiling
    terminal = row.get("terminal_label") or ""
    if terminal == "MODEL_QUALIFIED_HOLD":
        pass  # eligible ceiling
    elif terminal in _TERMINAL_REJECTS:
        return "CEILING_NOT_HOLD"
    else:
        return "CEILING_NOT_HOLD"

    # Probability
    if model_prob is None:
        return "MISSING_PROBABILITY"

    # Identity fields
    pitcher_id = row.get("player_id")
    if not pitcher_id:
        return "MISSING_PITCHER_ID"

    line      = row.get("line") or row.get("line_value")
    direction = (row.get("direction") or row.get("side") or "").upper()
    if not line or direction not in {"LESS", "MORE"}:
        return "MISSING_LINE_OR_DIRECTION"

    game_date = row.get("game_date") or row.get("board_date")
    if not game_date or len(str(game_date)) < 10:
        return "MISSING_GAME_DATE"

    # Post-start guard: if game_date is in the past (UTC), skip.
    try:
        gd_str = str(game_date)[:10]
        start_time = row.get("start_time") or ""
        if start_time and len(start_time) >= 16:
            # Combine game_date + start_time if available
            _game_dt = datetime.fromisoformat(f"{gd_str}T{start_time[:5]}:00+00:00")
        else:
            # Default: game date in UTC (midnight); skip if already past
            _game_dt = datetime.fromisoformat(f"{gd_str}T00:00:00+00:00")
        if datetime.now(timezone.utc) >= _game_dt:
            return "GAME_ALREADY_STARTED"
    except (ValueError, TypeError):
        pass  # unparseable → allow (conservative)

    # Synthetic / test row markers
    provenance = enrichment.get("savant_1ip_ledger") or {}
    fetch_method = (provenance.get("fetch_method") or provenance.get("source") or "").lower()
    for marker in _TEST_MARKERS:
        if marker.lower() in fetch_method:
            return "SYNTHETIC_ROW"

    acq_status = (enrichment.get("1ip_acquisition_status") or "").upper()
    if "TEST" in acq_status or "SYNTHETIC" in acq_status:
        return "TEST_ROW_MARKER"

    return None  # eligible


# ---------------------------------------------------------------------------
# Main log function (fail-open)
# ---------------------------------------------------------------------------

def log_1ip_prediction(
    row: dict,
    enrichment: dict,
    *,
    run_id: str = "",
    request_id: str = "",
) -> dict:
    """
    Attempt to persist an immutable PredictionRecord for a qualifying 1IP row.

    Always returns a status dict (never raises).  Scoring path receives this
    dict in row["gates"]["prediction_logger"] for observability.

    Status dict keys
    ----------------
    action:   "LOGGED" | "SKIPPED" | "DUPLICATE_PREVENTED" | "WRITE_FAILURE"
    reason:   skip reason string or error summary
    dedup_key: str (present for LOGGED and DUPLICATE_PREVENTED)
    """
    global _counters
    status: dict[str, Any] = {"action": "WRITE_FAILURE", "reason": "unknown"}

    try:
        model_prob = _extract_probability(enrichment)
        skip = _skip_reason(row, enrichment, model_prob)

        if skip:
            with _counters_lock:
                _counters["skipped"] += 1
            return {"action": "SKIPPED", "reason": skip}

        # --- Build PredictionRecord ---
        from validation.schema.prediction_record import PredictionRecord

        pitcher_id   = int(row.get("player_id", 0))
        pitcher_name = (row.get("player") or row.get("player_name") or "").strip()
        opponent     = (row.get("opponent") or "").strip()
        game_date    = str(row.get("game_date") or row.get("board_date") or "")[:10]
        line         = float(row.get("line") or row.get("line_value") or 0)
        direction    = (row.get("direction") or row.get("side") or "LESS").upper()
        start_time   = str(row.get("start_time") or "")

        uncertainty = _extract_uncertainty(enrichment)

        prov = enrichment.get("savant_1ip_ledger") or {}
        bf   = enrichment.get("first_inning_bf_distribution") or {}
        ppb  = enrichment.get("pitches_per_batter_distribution") or {}
        data_provenance = {
            "source":          prov.get("source"),
            "fetch_method":    prov.get("fetch_method"),
            "bf_n":            bf.get("n"),
            "ppb_n":           ppb.get("n"),
            "board_date":      game_date,
            "pitcher_id":      pitcher_id,
            "acquisition_status": enrichment.get("1ip_acquisition_status"),
            "run_id":          run_id,
            "request_id":      request_id,
        }

        features = {
            "bf_distribution":         bf,
            "pitches_per_batter_dist": ppb,
            "l10_hit_rate":            prov.get("l10_hit_rate"),
            "l5_hit_rate":             prov.get("l5_hit_rate"),
            "data_coverage":           prov.get("data_coverage"),
        }

        pred = PredictionRecord.create(
            game_date          = game_date,
            pitcher_name       = pitcher_name,
            pitcher_mlbam_id   = pitcher_id,
            opponent           = opponent,
            line               = line,
            direction          = direction,
            model_probability  = model_prob,
            model_uncertainty  = uncertainty,
            features           = features,
            model_version      = "1ip_monte_carlo_event_tree_v1",
            data_provenance    = data_provenance,
        )

        dedup_key = _log_dedup_key(pitcher_id, game_date, line, direction)
        status["dedup_key"] = dedup_key

        # --- Write to DB (idempotent) ---
        try:
            conn = _get_conn()
            try:
                _ensure_tables(conn)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO wow_validation_prediction_log
                            (prediction_id, log_dedup_key, frozen_at,
                             game_date, pitcher_name, pitcher_mlbam_id,
                             opponent, line, direction,
                             model_probability, model_uncertainty,
                             feature_snapshot_id, model_version,
                             data_provenance, run_id, request_id,
                             board_date, start_time)
                        VALUES
                            (%s, %s, %s,
                             %s, %s, %s,
                             %s, %s, %s,
                             %s, %s,
                             %s, %s,
                             %s, %s, %s,
                             %s, %s)
                        ON CONFLICT (log_dedup_key) DO NOTHING
                        RETURNING log_id
                        """,
                        (
                            pred.prediction_id,
                            dedup_key,
                            pred.frozen_at,
                            pred.game_date,
                            pred.pitcher_name,
                            pred.pitcher_mlbam_id,
                            pred.opponent,
                            pred.line,
                            pred.direction,
                            pred.model_probability,
                            pred.model_uncertainty,
                            pred.feature_snapshot_id,
                            pred.model_version,
                            json.dumps(pred.data_provenance),
                            run_id or None,
                            request_id or None,
                            game_date,
                            start_time or None,
                        ),
                    )
                    returned = cur.fetchone()
                conn.commit()

                if returned:
                    with _counters_lock:
                        _counters["logged"] += 1
                    status = {"action": "LOGGED", "reason": "ok",
                              "dedup_key": dedup_key,
                              "prediction_id": pred.prediction_id}
                else:
                    with _counters_lock:
                        _counters["duplicates_prevented"] += 1
                    status = {"action": "DUPLICATE_PREVENTED", "reason": "on_conflict_do_nothing",
                              "dedup_key": dedup_key}
            finally:
                conn.close()

        except Exception as db_exc:
            with _counters_lock:
                _counters["write_failures"] += 1
            status = {
                "action":    "WRITE_FAILURE",
                "reason":    f"db:{type(db_exc).__name__}:{str(db_exc)[:80]}",
                "dedup_key": dedup_key,
            }

    except Exception as outer_exc:
        with _counters_lock:
            _counters["write_failures"] += 1
        status = {
            "action": "WRITE_FAILURE",
            "reason": f"outer:{type(outer_exc).__name__}:{str(outer_exc)[:80]}",
        }

    return status


# ---------------------------------------------------------------------------
# Counter / status reads (in-process; cross-worker totals come from DB)
# ---------------------------------------------------------------------------

def get_in_process_counters() -> dict:
    with _counters_lock:
        return dict(_counters)
