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
                        raw_features, notes,
                        raw_l5, raw_l10,
                        games_available, sample_scope,
                        cross_season_used, manual_fallback_used,
                        audit_valid, invalid_reason,
                        projection_status, projection_value, projection_margin,
                        projection_source, final_approval_blocker,
                        used_average_only, data_quality_tag, block_power_flex,
                        live_cushion_margin, retro_result_margin, final_result,
                        board_line, pp_cash_threshold, consensus_line,
                        consensus_price_more, consensus_price_less,
                        no_vig_probability, model_probability, adjusted_edge,
                        edge_math, board_consensus_delta, drift_grade,
                        market_cause, terminal_bucket, threshold_hit_rate,
                        source_conflict, mutex_group_id, preferred_candidate
                    ) VALUES (
                        %(run_date)s, %(sport)s, %(player)s, %(prop)s, %(line)s, %(side)s,
                        %(game_date)s, %(wow_score)s, %(signal)s, %(message)s,
                        %(classification)s, %(environment)s,
                        %(source_odds)s, %(source_rundown)s, %(source_logs)s, %(source_status)s,
                        %(l5_hit_rate)s, %(l10_hit_rate)s, %(l10_median)s, %(l10_avg)s,
                        %(raw_features)s, %(notes)s,
                        %(raw_l5)s, %(raw_l10)s,
                        %(games_available)s, %(sample_scope)s,
                        %(cross_season_used)s, %(manual_fallback_used)s,
                        %(audit_valid)s, %(invalid_reason)s,
                        %(projection_status)s, %(projection_value)s, %(projection_margin)s,
                        %(projection_source)s, %(final_approval_blocker)s,
                        %(used_average_only)s, %(data_quality_tag)s, %(block_power_flex)s,
                        %(live_cushion_margin)s, %(retro_result_margin)s, %(final_result)s,
                        %(board_line)s, %(pp_cash_threshold)s, %(consensus_line)s,
                        %(consensus_price_more)s, %(consensus_price_less)s,
                        %(no_vig_probability)s, %(model_probability)s, %(adjusted_edge)s,
                        %(edge_math)s, %(board_consensus_delta)s, %(drift_grade)s,
                        %(market_cause)s, %(terminal_bucket)s, %(threshold_hit_rate)s,
                        %(source_conflict)s, %(mutex_group_id)s, %(preferred_candidate)s
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
                    "raw_l5":         json.dumps(result.get("raw_l5") or []),
                    "raw_l10":        json.dumps(result.get("raw_l10") or []),
                    "games_available":      result.get("games_available"),
                    "sample_scope":         result.get("sample_scope"),
                    "cross_season_used":    result.get("cross_season_used", False),
                    "manual_fallback_used": result.get("manual_fallback_used", False),
                    "audit_valid":          result.get("audit_valid"),
                    "invalid_reason":       result.get("invalid_reason"),
                    "projection_status":    result.get("projection_status"),
                    "projection_value":     result.get("projection_value"),
                    "projection_margin":    result.get("projection_margin"),
                    "projection_source":    result.get("projection_source"),
                    "final_approval_blocker": result.get("final_approval_blocker"),
                    "used_average_only":    result.get("used_average_only", False),
                    "data_quality_tag":     result.get("data_quality_tag"),
                    "block_power_flex":     result.get("block_power_flex", False),
                    "live_cushion_margin":  result.get("live_cushion_margin"),
                    "retro_result_margin":  result.get("retro_result_margin"),
                    "final_result":         result.get("final_result"),
                    "board_line":             result.get("board_line"),
                    "pp_cash_threshold":      result.get("pp_cash_threshold"),
                    "consensus_line":         result.get("consensus_line"),
                    "consensus_price_more":   result.get("consensus_price_more"),
                    "consensus_price_less":   result.get("consensus_price_less"),
                    "no_vig_probability":     result.get("no_vig_probability"),
                    "model_probability":      result.get("model_probability"),
                    "adjusted_edge":          result.get("adjusted_edge"),
                    "edge_math":              result.get("edge_math"),
                    "board_consensus_delta":  result.get("board_consensus_delta"),
                    "drift_grade":            result.get("drift_grade"),
                    "market_cause":           result.get("market_cause"),
                    "terminal_bucket":        result.get("terminal_bucket"),
                    "threshold_hit_rate":     result.get("threshold_hit_rate"),
                    "source_conflict":        result.get("source_conflict", False),
                    "mutex_group_id":         result.get("mutex_group_id"),
                    "preferred_candidate":    result.get("preferred_candidate"),
                })
        conn.close()
        return True
    except Exception as e:
        print(f"[storage] save_scan_result failed: {e}")
        return False


def get_scan_results(
    run_date=None,
    classification=None,
    sport=None,
    limit=200,
    offset=0,
):
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
            OFFSET %s
        """
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params + [limit, offset])
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


def get_compact_scan_rows(run_date, category=None, limit=80):
    """
    Fetch compact scan rows for summary display.
    Includes audit and projection fields. Sorted by wow_score DESC.
    """
    try:
        conn = get_db_conn()
        conds = ["run_date = %s"]
        params = [run_date]
        if category:
            conds.append("classification = %s")
            params.append(category)
        where = "WHERE " + " AND ".join(conds)
        sql = f"""
            SELECT player, sport, prop, side, line,
                   wow_score, signal, message, classification,
                   l5_hit_rate, l10_hit_rate, l10_median,
                   source_odds, source_rundown, source_logs, source_status,
                   games_available, sample_scope,
                   cross_season_used, manual_fallback_used,
                   audit_valid, invalid_reason,
                   projection_status, projection_value, projection_margin,
                   projection_source, final_approval_blocker,
                   used_average_only, data_quality_tag, block_power_flex,
                   live_cushion_margin, retro_result_margin, final_result,
                   board_line, pp_cash_threshold, consensus_line,
                   consensus_price_more, consensus_price_less,
                   no_vig_probability, model_probability, adjusted_edge,
                   edge_math, board_consensus_delta, drift_grade,
                   market_cause, terminal_bucket, threshold_hit_rate,
                   source_conflict, mutex_group_id, preferred_candidate
            FROM scan_results {where}
            ORDER BY wow_score DESC NULLS LAST
            LIMIT %s
        """
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params + [limit])
                rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[storage] get_compact_scan_rows failed: {e}")
        return []


def get_scan_source_flags(run_date):
    """
    Aggregate source availability flags across all rows for a run_date.
    Returns counts used to build execution_report and source_access_status.
    """
    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE source_odds    LIKE '%%AVAILABLE%%') AS odds_avail,
                        COUNT(*) FILTER (WHERE source_odds    NOT IN ('NOT_CALLED','')) AS odds_called,
                        COUNT(*) FILTER (WHERE source_logs    LIKE '%%AVAILABLE%%') AS logs_avail,
                        COUNT(*) FILTER (WHERE source_logs    NOT IN ('NOT_CALLED','')) AS logs_called,
                        COUNT(*) FILTER (WHERE source_status  LIKE '%%AVAILABLE%%') AS status_avail,
                        COUNT(*) FILTER (WHERE source_status  NOT IN ('NOT_CALLED','')) AS status_called,
                        COUNT(*) FILTER (WHERE source_rundown LIKE '%%AVAILABLE%%') AS rundown_avail,
                        COUNT(*) FILTER (WHERE audit_valid = TRUE)  AS audit_valid_count,
                        COUNT(*) FILTER (WHERE audit_valid = FALSE) AS audit_invalid_count,
                        COUNT(*) FILTER (WHERE projection_status = 'INTERNAL')  AS internal_proj_count,
                        COUNT(*) FILTER (WHERE projection_status = 'EXTERNAL')  AS external_proj_count,
                        COUNT(*) FILTER (WHERE projection_status = 'MISSING')   AS missing_proj_count,
                        ARRAY_AGG(DISTINCT sport) AS sports
                    FROM scan_results WHERE run_date = %s
                """, [run_date])
                row = cur.fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception as e:
        print(f"[storage] get_scan_source_flags failed: {e}")
        return {}
