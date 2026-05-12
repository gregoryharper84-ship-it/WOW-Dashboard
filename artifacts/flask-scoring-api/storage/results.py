"""
Persist and retrieve scan results from PostgreSQL.
"""
import os
import json
from datetime import date

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False


def get_db_conn():
    if not _PSYCOPG2_AVAILABLE:
        raise RuntimeError("psycopg2 is not installed")
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url)


def save_scan_result(result: dict) -> bool:
    """Insert one scan result row. Returns True on success."""
    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO scan_results (
                        run_date, sport, player, prop, line, side,
                        game_date, wow_score, signal, message,
                        classification, environment,
                        source_odds, source_rundown, source_logs, source_status,
                        l5_hit_rate, l10_hit_rate, l10_median, l10_avg,
                        raw_features, notes
                    ) VALUES (
                        %(run_date)s, %(sport)s, %(player)s, %(prop)s, %(line)s, %(side)s,
                        %(game_date)s, %(wow_score)s, %(signal)s, %(message)s,
                        %(classification)s, %(environment)s,
                        %(source_odds)s, %(source_rundown)s, %(source_logs)s, %(source_status)s,
                        %(l5_hit_rate)s, %(l10_hit_rate)s, %(l10_median)s, %(l10_avg)s,
                        %(raw_features)s, %(notes)s
                    )
                """, {
                    "run_date":       result.get("run_date", date.today().isoformat()),
                    "sport":          result.get("sport"),
                    "player":         result.get("player"),
                    "prop":           result.get("prop"),
                    "line":           result.get("line"),
                    "side":           result.get("side"),
                    "game_date":      result.get("game_date"),
                    "wow_score":      result.get("wow_score"),
                    "signal":         result.get("signal"),
                    "message":        result.get("message"),
                    "classification": result.get("classification"),
                    "environment":    result.get("environment", "live"),
                    "source_odds":    result.get("source_odds",    "NOT_CALLED"),
                    "source_rundown": result.get("source_rundown", "NOT_CALLED"),
                    "source_logs":    result.get("source_logs",    "NOT_CALLED"),
                    "source_status":  result.get("source_status",  "NOT_CALLED"),
                    "l5_hit_rate":    result.get("l5_hit_rate"),
                    "l10_hit_rate":   result.get("l10_hit_rate"),
                    "l10_median":     result.get("l10_median"),
                    "l10_avg":        result.get("l10_avg"),
                    "raw_features":   json.dumps(result.get("raw_features") or {}),
                    "notes":          result.get("notes"),
                })
        conn.close()
        return True
    except Exception as e:
        print(f"[storage] save_scan_result failed: {e}")
        return False


def get_scan_results(run_date=None, classification=None, sport=None, limit=200):
    """Fetch scan results filtered by date, classification, sport."""
    try:
        conn = get_db_conn()
        conditions, params = [], []
        if run_date:
            conditions.append("run_date = %s"); params.append(run_date)
        if classification:
            conditions.append("classification = %s"); params.append(classification)
        if sport:
            conditions.append("sport ILIKE %s"); params.append(sport)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT * FROM scan_results
            {where}
            ORDER BY run_at DESC
            LIMIT %s
        """
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params + [limit])
                rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[storage] get_scan_results failed: {e}")
        return []


def get_scan_summary(run_date=None):
    """Return count-by-classification for a given date."""
    try:
        conn = get_db_conn()
        params = []
        where = ""
        if run_date:
            where = "WHERE run_date = %s"; params.append(run_date)
        sql = f"""
            SELECT classification, COUNT(*) AS cnt
            FROM scan_results
            {where}
            GROUP BY classification
            ORDER BY cnt DESC
        """
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        conn.close()
        return {r["classification"]: int(r["cnt"]) for r in rows}
    except Exception as e:
        print(f"[storage] get_scan_summary failed: {e}")
        return {}
