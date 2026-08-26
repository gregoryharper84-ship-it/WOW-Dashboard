"""
validation/outcome_logger.py

Post-game outcome attachment for wow_validation_prediction_log.

Reuses validation.schema.outcome_record.OutcomeRecord and enforces all
leakage guards from that schema:
  - outcome_timestamp must be strictly after prediction frozen_at
  - Duplicate settlement on same log_dedup_key → rejected (409-style)
  - Identity mismatch (actual prediction_id ≠ DB record) → rejected
  - Conflicting outcome (already settled with different actual_pitches) → rejected

All writes are fail-closed (raises on violation); callers are expected to
surface errors as HTTP 4xx — this is NOT a fail-open logger like prediction_logger.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional


class OutcomeLogError(Exception):
    """Raised when outcome attachment fails a validation or integrity check."""
    def __init__(self, code: str, detail: str):
        self.code   = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _get_conn():
    try:
        import psycopg2
    except ImportError:
        raise OutcomeLogError("DB_UNAVAILABLE", "psycopg2 not installed")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise OutcomeLogError("DB_UNAVAILABLE", "DATABASE_URL not set")
    try:
        return psycopg2.connect(db_url, connect_timeout=5)
    except Exception as e:
        raise OutcomeLogError("DB_CONNECTION_FAILED", str(e)[:120])


def _get_prediction_by_dedup_key(cur, log_dedup_key: str) -> Optional[dict]:
    cur.execute(
        """
        SELECT prediction_id, frozen_at, game_date, pitcher_name,
               pitcher_mlbam_id, line, direction
        FROM   wow_validation_prediction_log
        WHERE  log_dedup_key = %s
        """,
        (log_dedup_key,),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = ["prediction_id", "frozen_at", "game_date", "pitcher_name",
            "pitcher_mlbam_id", "line", "direction"]
    return dict(zip(cols, row))


def _get_outcome(cur, log_dedup_key: str) -> Optional[dict]:
    cur.execute(
        """
        SELECT actual_pitches, hit, outcome_source, outcome_timestamp
        FROM   wow_validation_outcome_log
        WHERE  log_dedup_key = %s
        """,
        (log_dedup_key,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"actual_pitches": row[0], "hit": row[1],
            "outcome_source": row[2], "outcome_timestamp": str(row[3])}


def attach_outcome(
    *,
    log_dedup_key: str,
    actual_pitches: int,
    outcome_source: str,
    outcome_verified: bool = False,
    notes: str = "",
    outcome_timestamp: Optional[str] = None,
) -> dict:
    """
    Attach a post-game outcome to a frozen prediction identified by log_dedup_key.

    Validation
    ----------
    1. Prediction must exist in wow_validation_prediction_log.
    2. outcome_timestamp must be strictly after prediction.frozen_at (leakage guard).
    3. No duplicate settlement on the same log_dedup_key.
    4. No conflicting settlement (same key, different actual_pitches).

    Returns dict with: outcome_id, prediction_id, hit, actual_pitches, logged_at.
    Raises OutcomeLogError on any violation.
    """
    import psycopg2  # noqa (caught by _get_conn if missing)
    from validation.schema.outcome_record import attach_outcome as _schema_attach

    now_utc   = datetime.now(timezone.utc).isoformat()
    outcome_ts = outcome_timestamp or now_utc

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # 1. Fetch prediction
            pred = _get_prediction_by_dedup_key(cur, log_dedup_key)
            if not pred:
                raise OutcomeLogError(
                    "PREDICTION_NOT_FOUND",
                    f"No prediction with log_dedup_key={log_dedup_key!r}",
                )

            # 2. Check for existing outcome (no duplicates, no conflicts)
            existing = _get_outcome(cur, log_dedup_key)
            if existing:
                if existing["actual_pitches"] == actual_pitches:
                    # Idempotent re-submission of same result
                    conn.close()
                    return {
                        "action":          "ALREADY_SETTLED",
                        "prediction_id":   pred["prediction_id"],
                        "actual_pitches":  existing["actual_pitches"],
                        "hit":             existing["hit"],
                        "outcome_source":  existing["outcome_source"],
                    }
                raise OutcomeLogError(
                    "CONFLICTING_OUTCOME",
                    f"Already settled with actual_pitches={existing['actual_pitches']}; "
                    f"received {actual_pitches}. Conflicting settlements are rejected.",
                )

            # 3. Leakage guard via schema (raises ValueError on violation)
            # Build a minimal mock prediction for schema validation
            class _MockPred:
                prediction_id = pred["prediction_id"]
                frozen_at     = str(pred["frozen_at"])
                line          = float(pred["line"])
                direction     = str(pred["direction"]).upper()

            try:
                outcome_rec = _schema_attach(
                    _MockPred(),
                    actual_pitches   = actual_pitches,
                    outcome_source   = outcome_source,
                    outcome_verified = outcome_verified,
                    notes            = notes,
                    _outcome_timestamp = outcome_ts,
                )
            except ValueError as ve:
                raise OutcomeLogError("LEAKAGE_GUARD_FAILED", str(ve)[:200])

            # 4. Write
            cur.execute(
                """
                INSERT INTO wow_validation_outcome_log
                    (prediction_id, log_dedup_key, outcome_timestamp,
                     actual_pitches, hit, outcome_source, outcome_verified, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING outcome_log_id, logged_at
                """,
                (
                    pred["prediction_id"],
                    log_dedup_key,
                    outcome_ts,
                    actual_pitches,
                    outcome_rec.hit,
                    outcome_source,
                    outcome_verified,
                    notes or None,
                ),
            )
            row = cur.fetchone()
            conn.commit()

        return {
            "action":          "OUTCOME_ATTACHED",
            "outcome_log_id":  row[0],
            "prediction_id":   pred["prediction_id"],
            "log_dedup_key":   log_dedup_key,
            "hit":             outcome_rec.hit,
            "actual_pitches":  actual_pitches,
            "outcome_source":  outcome_source,
            "logged_at":       str(row[1]),
        }
    except OutcomeLogError:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise OutcomeLogError("UNEXPECTED_ERROR", str(e)[:200])
    finally:
        conn.close()
