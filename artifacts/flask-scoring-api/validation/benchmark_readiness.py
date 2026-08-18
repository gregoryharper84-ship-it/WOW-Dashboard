"""
validation/benchmark_readiness.py

Benchmark-readiness logic for the 1IP prediction logger.

A benchmark is "ready" when ≥ BENCHMARK_THRESHOLD games have been
settled with verified outcomes in wow_validation_outcome_log.

Rules (from spec):
  - Default threshold: 20 settled eligible games.
  - Configurable via VALIDATION_BENCHMARK_THRESHOLD env var.
  - "Eligible" = row exists in wow_validation_prediction_log AND has an
    entry in wow_validation_outcome_log (settled).
  - DO NOT evaluate holdout early — this module only checks counts;
    it never reads model_probability or computes metrics.
  - Only use this readiness flag externally to decide whether to run the harness.
"""
from __future__ import annotations

import os
from typing import Any, Optional

_DEFAULT_THRESHOLD = 20


def _get_threshold() -> int:
    try:
        return max(1, int(os.environ.get("VALIDATION_BENCHMARK_THRESHOLD", _DEFAULT_THRESHOLD)))
    except (ValueError, TypeError):
        return _DEFAULT_THRESHOLD


def _get_conn():
    try:
        import psycopg2
    except ImportError:
        return None
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return None
    try:
        return psycopg2.connect(db_url, connect_timeout=5)
    except Exception:
        return None


def get_status() -> dict[str, Any]:
    """
    Query the validation tables and return a benchmark readiness status dict.

    Returns
    -------
    dict with:
      ready:                  bool
      threshold:              int
      n_logged:               int   — total predictions logged
      n_settled:              int   — predictions with attached outcomes
      n_verified_settled:     int   — outcomes where outcome_verified=True
      n_hits:                 int   — settled outcomes where hit=True
      n_duplicates_prevented: int   — ON CONFLICT DO NOTHING skips
      n_write_failures:       int   — DB write errors (from wow_validation_prediction_log skip_reason)
      benchmark_sample_count: int   — same as n_settled (alias for clarity)
      db_available:           bool
      error:                  str | None
    """
    threshold = _get_threshold()
    conn = _get_conn()
    if conn is None:
        return {
            "ready":                  False,
            "threshold":              threshold,
            "n_logged":               0,
            "n_settled":              0,
            "n_verified_settled":     0,
            "n_hits":                 0,
            "n_duplicates_prevented": 0,
            "n_write_failures":       0,
            "benchmark_sample_count": 0,
            "db_available":           False,
            "error":                  "DB_UNAVAILABLE",
        }
    try:
        with conn.cursor() as cur:
            # Total predictions logged
            cur.execute("SELECT COUNT(*) FROM wow_validation_prediction_log")
            n_logged = cur.fetchone()[0]

            # Settled (have outcome)
            cur.execute("SELECT COUNT(*) FROM wow_validation_outcome_log")
            n_settled = cur.fetchone()[0]

            # Verified settled
            cur.execute(
                "SELECT COUNT(*) FROM wow_validation_outcome_log WHERE outcome_verified = TRUE"
            )
            n_verified = cur.fetchone()[0]

            # Hits
            cur.execute(
                "SELECT COUNT(*) FROM wow_validation_outcome_log WHERE hit = TRUE"
            )
            n_hits = cur.fetchone()[0]

        return {
            "ready":                  n_settled >= threshold,
            "threshold":              threshold,
            "n_logged":               n_logged,
            "n_settled":              n_settled,
            "n_verified_settled":     n_verified,
            "n_hits":                 n_hits,
            "n_duplicates_prevented": 0,   # in-process only; not persisted to DB
            "n_write_failures":       0,   # in-process only; not persisted to DB
            "benchmark_sample_count": n_settled,
            "db_available":           True,
            "error":                  None,
        }
    except Exception as e:
        return {
            "ready":                  False,
            "threshold":              threshold,
            "n_logged":               0,
            "n_settled":              0,
            "n_verified_settled":     0,
            "n_hits":                 0,
            "n_duplicates_prevented": 0,
            "n_write_failures":       0,
            "benchmark_sample_count": 0,
            "db_available":           False,
            "error":                  f"DB_ERROR:{type(e).__name__}:{str(e)[:80]}",
        }
    finally:
        conn.close()


def get_eligible_predictions(
    *,
    limit: int = 200,
    offset: int = 0,
    settled_only: bool = False,
) -> dict[str, Any]:
    """
    Export eligible frozen predictions for offline benchmark runs.

    Parameters
    ----------
    limit         Max rows to return (capped at 500).
    offset        Pagination offset.
    settled_only  If True, only return predictions with attached outcomes.

    Returns
    -------
    dict with: rows (list of dicts), total_available, limit, offset
    """
    limit = min(limit, 500)
    conn = _get_conn()
    if conn is None:
        return {"rows": [], "total_available": 0, "limit": limit,
                "offset": offset, "error": "DB_UNAVAILABLE"}
    try:
        with conn.cursor() as cur:
            if settled_only:
                base_q = """
                    SELECT p.prediction_id, p.log_dedup_key,
                           p.game_date, p.pitcher_name, p.pitcher_mlbam_id,
                           p.opponent, p.line, p.direction,
                           p.model_probability, p.model_uncertainty,
                           p.feature_snapshot_id, p.model_version,
                           p.frozen_at, p.logged_at,
                           o.actual_pitches, o.hit,
                           o.outcome_source, o.outcome_verified
                    FROM   wow_validation_prediction_log p
                    JOIN   wow_validation_outcome_log o
                           ON p.log_dedup_key = o.log_dedup_key
                    ORDER  BY p.game_date ASC, p.logged_at ASC
                    LIMIT  %s OFFSET %s
                """
                count_q = """
                    SELECT COUNT(*) FROM wow_validation_prediction_log p
                    JOIN wow_validation_outcome_log o ON p.log_dedup_key = o.log_dedup_key
                """
            else:
                base_q = """
                    SELECT p.prediction_id, p.log_dedup_key,
                           p.game_date, p.pitcher_name, p.pitcher_mlbam_id,
                           p.opponent, p.line, p.direction,
                           p.model_probability, p.model_uncertainty,
                           p.feature_snapshot_id, p.model_version,
                           p.frozen_at, p.logged_at,
                           o.actual_pitches, o.hit,
                           o.outcome_source, o.outcome_verified
                    FROM   wow_validation_prediction_log p
                    LEFT   JOIN wow_validation_outcome_log o
                           ON p.log_dedup_key = o.log_dedup_key
                    ORDER  BY p.game_date ASC, p.logged_at ASC
                    LIMIT  %s OFFSET %s
                """
                count_q = "SELECT COUNT(*) FROM wow_validation_prediction_log"

            cur.execute(count_q)
            total = cur.fetchone()[0]

            cur.execute(base_q, (limit, offset))
            cols = [
                "prediction_id", "log_dedup_key",
                "game_date", "pitcher_name", "pitcher_mlbam_id",
                "opponent", "line", "direction",
                "model_probability", "model_uncertainty",
                "feature_snapshot_id", "model_version",
                "frozen_at", "logged_at",
                "actual_pitches", "hit",
                "outcome_source", "outcome_verified",
            ]
            rows = []
            for db_row in cur.fetchall():
                d = dict(zip(cols, db_row))
                # Normalize Decimal/datetime for JSON serialization
                for k in ("line", "model_probability", "model_uncertainty"):
                    if d.get(k) is not None:
                        d[k] = float(d[k])
                for k in ("frozen_at", "logged_at"):
                    if d.get(k) is not None:
                        d[k] = str(d[k])
                rows.append(d)

        return {
            "rows":            rows,
            "total_available": total,
            "limit":           limit,
            "offset":          offset,
            "error":           None,
        }
    except Exception as e:
        return {"rows": [], "total_available": 0, "limit": limit,
                "offset": offset, "error": f"DB_ERROR:{str(e)[:80]}"}
    finally:
        conn.close()
