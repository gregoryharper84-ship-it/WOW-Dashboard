import os
import re
import json
import random
import math
import threading
import time
import statistics
import traceback
from collections import deque
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

app = Flask(__name__)
_APP_START_TIME = time.time()
CORS(app, origins="*", allow_headers=["Content-Type", "Authorization", "X-API-Key"])


@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    app.logger.exception("Unhandled server error")
    return jsonify({
        "ok": False,
        "error": {
            "type": type(e).__name__,
            "message": str(e),
            "trace": traceback.format_exc()[-2000:],
        },
        "source_access_status": {
            "market_odds": "Failed",
            "board_source": "Not Retrieved",
            "l5_l10_logs": "Not Retrieved",
            "status_lineups": "Not Retrieved",
        },
        "market_verified": [],
        "model_qualified": [],
        "conditional": [],
        "watch": [],
        "reject": [],
        "data_insufficient": [{
            "route": request.path if request else "unknown",
            "reason": "Unhandled backend exception",
            "status": "FAILED",
        }],
        "execution_notes": ["Server error occurred before full scan completed."],
    }), 500

DISCLAIMER = (
    "SUPPORT LAYER ONLY — This score is a statistical signal for informational "
    "analysis purposes. It cannot and does not approve, authorize, or recommend "
    "any bet or wager. All decisions remain solely with the user."
)

VALID_WINDOWS    = {"L5": 5, "L10": 10}
VALID_ENVIRONMENTS = {"test", "live"}

SIDE_MAP = {
    "over":  "MORE",
    "more":  "MORE",
    "MORE":  "MORE",
    "OVER":  "MORE",
    "under": "LESS",
    "less":  "LESS",
    "LESS":  "LESS",
    "UNDER": "LESS",
}
# All records are normalized on write; use exact equality in SQL
SIDE_SQL = {
    "MORE": ("side = 'MORE'", []),
    "LESS": ("side = 'LESS'", []),
}
# SQL expression to normalize any legacy value at query time (defensive)
_SIDE_NORM_EXPR = (
    "CASE WHEN UPPER(side) IN ('OVER','MORE')  THEN 'MORE' "
    "     WHEN UPPER(side) IN ('UNDER','LESS') THEN 'LESS' "
    "     ELSE side END"
)

_fallback_log: deque = deque(maxlen=50)
_log_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_public_url() -> str:
    domains = os.environ.get("REPLIT_DOMAINS", "")
    first = domains.split(",")[0].strip() if domains else ""
    return f"https://{first}" if first else "http://localhost:8000"


def get_db_conn():
    if not _PSYCOPG2_AVAILABLE:
        raise RuntimeError("psycopg2 is not installed")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(database_url)


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # CORS preflight — let Flask-CORS respond without auth check
        if request.method == "OPTIONS":
            return f(*args, **kwargs)
        expected_key = os.environ.get("SCORING_API_KEY", "")
        if not expected_key:
            return jsonify({"error": "Server misconfiguration: SCORING_API_KEY is not set"}), 500
        # Try X-API-Key header first, then fall back to Authorization: Bearer <key>
        provided_key = request.headers.get("X-API-Key", "").strip()
        auth_source = "X-API-Key"
        if not provided_key:
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                provided_key = auth[7:].strip()
                auth_source = "Authorization: Bearer"
        # Safe auth debug logging — never logs the key itself
        _key_present = bool(provided_key)
        _key_len     = len(provided_key) if provided_key else 0
        _key_tail    = provided_key[-4:] if _key_len >= 4 else "----"
        _exp_len     = len(expected_key)
        _exp_tail    = expected_key[-4:] if _exp_len >= 4 else "----"
        app.logger.info(
            "[AUTH] path=%s method=%s header_source=%s "
            "key_present=%s provided_len=%d provided_tail=...%s "
            "expected_len=%d expected_tail=...%s",
            request.path, request.method, auth_source,
            _key_present, _key_len, _key_tail,
            _exp_len, _exp_tail,
        )
        if not provided_key:
            return jsonify({
                "error": "Missing API key",
                "hint": "Include your key in the X-API-Key request header",
                "auth_debug": {
                    "header_checked": "X-API-Key",
                    "fallback_checked": "Authorization: Bearer",
                    "key_present": False,
                },
            }), 401
        if not secrets_equal(provided_key, expected_key):
            return jsonify({
                "error": "Invalid API key",
                "auth_debug": {
                    "key_present": True,
                    "provided_len": _key_len,
                    "provided_tail": f"...{_key_tail}",
                    "expected_len": _exp_len,
                    "expected_tail": f"...{_exp_tail}",
                    "lengths_match": _key_len == _exp_len,
                },
            }), 401
        return f(*args, **kwargs)
    return decorated


def secrets_equal(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def build_filter_clause(player=None, sport=None, prop=None, side=None,
                        since=None, environment=None):
    """
    Returns (conditions, params) for a WHERE clause.
    Apply order: since → player/sport/prop → side → environment.
    `side` must already be normalized to 'MORE' or 'LESS'.
    `environment` must already be normalized to 'test' or 'live'.
    """
    conditions, params = [], []
    if since:
        params.append(since)
        conditions.append("timestamp >= %s")
    if player:
        params.append(f"%{player}%")
        conditions.append("player ILIKE %s")
    if sport:
        params.append(sport)
        conditions.append("sport ILIKE %s")
    if prop:
        params.append(f"%{prop}%")
        conditions.append("prop ILIKE %s")
    if side:
        side_fragment, _ = SIDE_SQL[side]
        conditions.append(side_fragment)
    if environment:
        params.append(environment)
        conditions.append("environment = %s")
    return conditions, params


def build_query_source(conditions, params, window_n=None):
    """
    Returns (cte_sql, cte_params, source_name, aggregate_where).

    When window_n is set: wraps the filtered rows in a CTE limited to N rows,
    so all subsequent aggregates operate on that window.
    When window_n is None: queries scoring_requests directly with the WHERE clause.
    """
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    if window_n is not None:
        cte_sql = f"""
            WITH working_set AS (
                SELECT * FROM scoring_requests
                {where}
                ORDER BY timestamp DESC
                LIMIT %s
            )
        """
        return cte_sql, params + [window_n], "working_set", ""
    else:
        return "", params, "scoring_requests", where


def serialize_row(row):
    ts = row["timestamp"]
    return {
        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "player": row["player"],
        "sport": row["sport"],
        "prop": row["prop"],
        "side": row["side"],
        "line": float(row["line"]),
        "score": float(row["score"]),
        "label": row.get("label", "Support Layer Only"),
        "environment": row.get("environment", "test"),
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _safe_float(val, default=None):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _normalize_01(val, scale=10.0):
    """Normalize a value that might be 0-1 or 0-scale to 0-1."""
    v = _safe_float(val)
    if v is None:
        return None
    return v / scale if v > 1.0 else v


def compute_wow_score(features: dict, player: str, prop: str, side: str, line: float):
    """
    WOW scoring spec implementation.
    Returns (score 0-100, signal string, message string).

    Scoring philosophy: edge > volume, penalize missing/weak inputs,
    heavily penalize conflicts. GPT handles final tier classification.
    """
    score = 50.0
    positives = []
    negatives = []

    # 1. Recent hit rate — up to +15 / down to -5
    hr = _safe_float(features.get("l5_hit_rate") or features.get("l10_hit_rate"))
    if hr is not None:
        if hr >= 0.80:   score += 15; positives.append("strong recent hit rate")
        elif hr >= 0.70: score += 10; positives.append("solid hit rate")
        elif hr >= 0.60: score += 6;  positives.append("moderate hit rate")
        elif hr >= 0.50: score += 2
        else:            score -= 5;  negatives.append("low recent hit rate")
    else:
        score -= 6; negatives.append("no hit rate data")

    # 2. Median / recent average edge vs line — up to +15 / down to -20
    recent_avg = _safe_float(features.get("recent_avg"))
    median_edge = _safe_float(features.get("median_edge"))

    if recent_avg is not None and line > 0:
        if side == "MORE":
            edge_pct = (recent_avg - line) / line
        else:
            edge_pct = (line - recent_avg) / line

        if edge_pct >= 0.30:    score += 15; positives.append("strong median edge vs line")
        elif edge_pct >= 0.15:  score += 10; positives.append("solid median edge")
        elif edge_pct >= 0.05:  score += 5;  positives.append("slight median edge")
        elif edge_pct >= -0.05: score += 1
        elif edge_pct >= -0.15: score -= 10; negatives.append("recent average below line")
        else:                   score -= 20; negatives.append("recent average well below line")
    elif median_edge is not None:
        me = median_edge
        if me >= 0.50:    score += 12; positives.append("strong median edge")
        elif me >= 0.20:  score += 6;  positives.append("positive median edge")
        elif me >= 0.0:   score += 1
        else:             score -= 12; negatives.append("negative median edge")
    else:
        score -= 8; negatives.append("no recent average data")

    # 3. Market support / market gap — up to +15 / down to -20
    mg = _safe_float(features.get("market_gap") or features.get("market_support"))
    if mg is not None:
        mg_n = mg / 10.0 if mg > 1.0 else mg
        if mg_n >= 0.75:    score += 15; positives.append("strong market support")
        elif mg_n >= 0.60:  score += 10; positives.append("market support present")
        elif mg_n >= 0.50:  score += 4
        elif mg_n >= 0.40:  score -= 8;  negatives.append("weak market support")
        else:               score -= 20; negatives.append("market disagreement")
    else:
        score -= 4; negatives.append("no market data")

    # 4. Role / usage stability — up to +10 / down to -5
    rs = _normalize_01(features.get("role_score") or features.get("role_stability"))
    if rs is not None:
        if rs >= 0.80:   score += 10; positives.append("stable role")
        elif rs >= 0.60: score += 6
        elif rs >= 0.40: score += 2
        else:            score -= 5;  negatives.append("role uncertainty")
    else:
        score -= 3

    # 5. Matchup — up to +7.5 / down to -5
    mu = _normalize_01(features.get("matchup_score") or features.get("matchup_rating"))
    if mu is not None:
        if mu >= 0.75:   score += 7.5; positives.append("favorable matchup")
        elif mu >= 0.55: score += 4
        elif mu >= 0.40: score += 1
        else:            score -= 5; negatives.append("tough matchup")
    else:
        score -= 3

    # 6. Line movement — up to +5 / down to -3
    lm = _normalize_01(features.get("line_movement_score") or features.get("line_movement"))
    if lm is not None:
        if lm >= 0.70:   score += 5; positives.append("positive line movement")
        elif lm >= 0.50: score += 2
        else:            score -= 3; negatives.append("negative line movement")

    # 7. Pace / usage — up to +5
    pace = _normalize_01(features.get("pace_factor") or features.get("pace"))
    if pace is not None:
        if pace >= 0.70:   score += 5; positives.append("pace/usage supports prop")
        elif pace >= 0.50: score += 2

    # 8. Injury / status — heavy penalty
    inj = _safe_float(features.get("injury_flag"))
    if inj is not None:
        if inj >= 2:   score -= 20; negatives.append("player likely out")
        elif inj >= 1: score -= 12; negatives.append("injury/status concern")

    # 9. GPT confidence alignment — soft adjustment
    conf = _safe_float(features.get("confidence"))
    if conf is not None:
        c = conf / 100.0 if conf > 1.0 else conf
        if c >= 0.75:   score += 4
        elif c >= 0.60: score += 2
        elif c < 0.40:  score -= 3

    score = round(max(0.0, min(100.0, score)), 2)

    # Signal label
    if score >= 90:   signal = "Elite Signal — strong statistical edge"
    elif score >= 80: signal = "Strong Signal — model support"
    elif score >= 70: signal = "Playable Signal — positive edge"
    elif score >= 60: signal = "Conditional Signal — needs validation"
    elif score >= 50: signal = "Watchlist Signal — thin edge"
    elif score >= 40: signal = "Weak Signal — no clear edge"
    elif score >= 30: signal = "Negative Signal — poor support"
    else:             signal = "Reject Signal — major red flags"

    # Message
    if positives and negatives:
        msg = f"{signal} — {positives[0]}; penalized for {negatives[0]}"
    elif positives:
        msg = f"{signal} — {positives[0]} align with submitted side"
    elif negatives:
        msg = f"{signal} — {negatives[0]} does not support this line"
    else:
        msg = f"{signal} — limited input data provided"

    return score, signal, msg


def compute_rf_score(features: dict, player: str, prop: str, side: str, line: float) -> float:
    score, _, _ = compute_wow_score(features, player, prop, side, line)
    return score


def persist_request(player, sport, prop, side, line, score, label,
                    game_date=None, environment="test"):
    from datetime import date as _date
    if game_date is None:
        game_date = _date.today().isoformat()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "player": player, "sport": sport, "prop": prop,
        "side": side, "line": line, "score": score, "label": label,
        "game_date": game_date, "environment": environment,
    }
    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scoring_requests "
                    "(timestamp, player, sport, prop, side, line, score, label, game_date, environment) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (entry["timestamp"], player, sport, prop, side, line, score, label,
                     game_date, environment)
                )
        conn.close()
        return True
    except Exception:
        with _log_lock:
            _fallback_log.appendleft(entry)
        return False


# ---------------------------------------------------------------------------
# DB query functions
# ---------------------------------------------------------------------------

def fetch_log(player=None, sport=None, prop=None, side=None,
              since=None, window_n=None, limit=50, environment=None):
    """
    Query recent scoring records.
    Window (L5/L10) overrides the limit when set.
    """
    conn = get_db_conn()
    conditions, params = build_filter_clause(player, sport, prop, side, since, environment)

    if window_n is not None:
        effective_limit = window_n
    else:
        effective_limit = min(limit, 200)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT timestamp, player, sport, prop, side, line, score, label
        FROM scoring_requests
        {where}
        ORDER BY timestamp DESC
        LIMIT %s
    """
    with conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params + [effective_limit])
            rows = cur.fetchall()
    conn.close()
    return [serialize_row(r) for r in rows]


def _append_where(agg_where, extra):
    """Safely append an extra AND condition to an existing WHERE clause (or start one)."""
    return f"{agg_where} AND {extra}" if agg_where else f"WHERE {extra}"


def _top_props_from(rows):
    return [
        {
            "player": r["player"], "sport": r["sport"], "prop": r["prop"],
            "side": r["side"], "line": float(r["line"]),
            "avg_score": float(r["avg_score"]), "times_scored": int(r["times_scored"])
        }
        for r in rows
    ]


def fetch_stats(player=None, sport=None, prop=None, side=None,
                since=None, window_n=None, top_limit=10, environment=None):
    """
    Aggregate stats. When window_n is set, all aggregates operate on the
    latest N filtered records via a CTE.
    `side` must be 'MORE', 'LESS', or None (already normalized).
    """
    conn = get_db_conn()
    conditions, params = build_filter_clause(player, sport, prop, side, since, environment)
    cte_sql, cte_params, source, agg_where = build_query_source(
        conditions, params, window_n
    )
    top_n = max(1, min(int(top_limit), 100))

    over_where  = _append_where(agg_where, "side = 'MORE'")
    under_where = _append_where(agg_where, "side = 'LESS'")

    with conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # Overview: count, avg, max, min + over/under split
            cur.execute(
                f"{cte_sql} "
                f"SELECT COUNT(*) AS total, "
                f"ROUND(AVG(score)::numeric,2) AS avg_score, "
                f"ROUND(MAX(score)::numeric,2) AS max_score, "
                f"ROUND(MIN(score)::numeric,2) AS min_score, "
                f"COUNT(*) FILTER (WHERE side = 'MORE') AS over_count, "
                f"COUNT(*) FILTER (WHERE side = 'LESS') AS under_count, "
                f"ROUND(AVG(score) FILTER (WHERE side = 'MORE')::numeric, 2) AS over_avg, "
                f"ROUND(AVG(score) FILTER (WHERE side = 'LESS')::numeric, 2) AS under_avg "
                f"FROM {source} {agg_where}",
                cte_params
            )
            overview = cur.fetchone()

            # Avg by sport
            cur.execute(
                f"{cte_sql} "
                f"SELECT sport, COUNT(*) AS requests, "
                f"ROUND(AVG(score)::numeric,2) AS avg_score "
                f"FROM {source} {agg_where} "
                f"GROUP BY sport ORDER BY requests DESC",
                cte_params
            )
            by_sport = cur.fetchall()

            # Top scored props — all
            cur.execute(
                f"{cte_sql} "
                f"SELECT player, sport, prop, side, line, "
                f"ROUND(AVG(score)::numeric,2) AS avg_score, COUNT(*) AS times_scored "
                f"FROM {source} {agg_where} "
                f"GROUP BY player, sport, prop, side, line "
                f"ORDER BY avg_score DESC LIMIT %s",
                cte_params + [top_n]
            )
            top_props = cur.fetchall()

            # Top scored props — over side
            cur.execute(
                f"{cte_sql} "
                f"SELECT player, sport, prop, side, line, "
                f"ROUND(AVG(score)::numeric,2) AS avg_score, COUNT(*) AS times_scored "
                f"FROM {source} {over_where} "
                f"GROUP BY player, sport, prop, side, line "
                f"ORDER BY avg_score DESC LIMIT %s",
                cte_params + [top_n]
            )
            over_top = cur.fetchall()

            # Top scored props — under side
            cur.execute(
                f"{cte_sql} "
                f"SELECT player, sport, prop, side, line, "
                f"ROUND(AVG(score)::numeric,2) AS avg_score, COUNT(*) AS times_scored "
                f"FROM {source} {under_where} "
                f"GROUP BY player, sport, prop, side, line "
                f"ORDER BY avg_score DESC LIMIT %s",
                cte_params + [top_n]
            )
            under_top = cur.fetchall()

            # Most recent
            cur.execute(
                f"{cte_sql} "
                f"SELECT timestamp, player, sport, prop, side, line, score "
                f"FROM {source} {agg_where} "
                f"ORDER BY timestamp DESC LIMIT %s",
                cte_params + [top_n]
            )
            recent = cur.fetchall()

    conn.close()

    total = int(overview["total"])
    return {
        "record_count": total,
        "total_request_count": total,
        "average_score": float(overview["avg_score"]) if overview["avg_score"] is not None else None,
        "average_score_overall": float(overview["avg_score"]) if overview["avg_score"] is not None else None,
        "max_score": float(overview["max_score"]) if overview["max_score"] is not None else None,
        "min_score": float(overview["min_score"]) if overview["min_score"] is not None else None,
        "over_count": int(overview["over_count"]),
        "under_count": int(overview["under_count"]),
        "over_average_score": float(overview["over_avg"]) if overview["over_avg"] is not None else None,
        "under_average_score": float(overview["under_avg"]) if overview["under_avg"] is not None else None,
        "average_score_by_sport": [
            {"sport": r["sport"], "requests": int(r["requests"]), "avg_score": float(r["avg_score"])}
            for r in by_sport
        ],
        "top_scored_props":   _top_props_from(top_props),
        "over_top_props":     _top_props_from(over_top),
        "under_top_props":    _top_props_from(under_top),
        "most_recent_scored_props": [
            {
                "timestamp": r["timestamp"].isoformat() if hasattr(r["timestamp"], "isoformat") else str(r["timestamp"]),
                "player": r["player"], "sport": r["sport"], "prop": r["prop"],
                "side": r["side"], "line": float(r["line"]), "score": float(r["score"])
            }
            for r in recent
        ]
    }


# ---------------------------------------------------------------------------
# Leaderboard query
# ---------------------------------------------------------------------------

def fetch_leaderboard(sport=None, prop=None, side=None, since=None,
                      window_n=10, limit=10, today=False, environment=None):
    """
    Rank (player, sport, prop, side) combinations by average score.

    For each combination, takes the latest `window_n` records (after applying
    filters), computes aggregates, and returns them ordered by average_score
    DESC with latest_score as tiebreaker.

    `side` must already be normalized to 'MORE', 'LESS', or None.
    When `today=True`, filters to rows where game_date = today's UTC date.
    """
    conn = get_db_conn()

    # Build filter conditions (no player filter — leaderboard ranks players)
    conditions, params = build_filter_clause(
        player=None, sport=sport, prop=prop, side=side, since=since,
        environment=environment
    )
    if today:
        conditions.append("game_date = CURRENT_DATE")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # ROW_NUMBER partitions per (player, sport, prop, normalized_side) combo.
    # Side is normalized in SQL defensively so any legacy values group correctly.
    sql = f"""
        WITH normalized AS (
            SELECT *,
                   {_SIDE_NORM_EXPR} AS norm_side
            FROM scoring_requests
            {where}
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY player, sport, prop, norm_side
                       ORDER BY timestamp DESC
                   ) AS rn
            FROM normalized
        ),
        windowed AS (
            SELECT * FROM ranked WHERE rn <= %s
        )
        SELECT
            player,
            sport,
            prop,
            norm_side                                        AS side,
            COUNT(*)                                         AS record_count,
            ROUND(AVG(score)::numeric, 2)                   AS average_score,
            ROUND(MAX(score)::numeric, 2)                   AS max_score,
            ROUND(MIN(score)::numeric, 2)                   AS min_score,
            MAX(CASE WHEN rn = 1 THEN score END)            AS latest_score,
            MAX(CASE WHEN rn = 1 THEN line  END)            AS latest_line,
            MAX(timestamp)                                   AS latest_timestamp,
            JSON_AGG(score ORDER BY timestamp ASC)          AS scores
        FROM windowed
        GROUP BY player, sport, prop, norm_side
        ORDER BY average_score DESC, latest_score DESC
        LIMIT %s
    """
    with conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params + [window_n, limit])
            rows = cur.fetchall()
    conn.close()

    return [
        {
            "rank":             i + 1,
            "player":           r["player"],
            "sport":            r["sport"],
            "prop":             r["prop"],
            "side":             r["side"],
            "record_count":     int(r["record_count"]),
            "average_score":    float(r["average_score"]) if r["average_score"] is not None else None,
            "max_score":        float(r["max_score"]) if r["max_score"] is not None else None,
            "min_score":        float(r["min_score"]) if r["min_score"] is not None else None,
            "latest_score":     float(r["latest_score"]) if r["latest_score"] is not None else None,
            "latest_line":      float(r["latest_line"]) if r["latest_line"] is not None else None,
            "latest_timestamp": r["latest_timestamp"].isoformat()
                                if hasattr(r["latest_timestamp"], "isoformat")
                                else str(r["latest_timestamp"]),
            "scores":           [float(s) for s in (r["scores"] or [])],
        }
        for i, r in enumerate(rows)
    ]


# ---------------------------------------------------------------------------
# Parameter parsing helpers
# ---------------------------------------------------------------------------

def parse_window(raw):
    """Returns (window_label, window_n) or (None, None). Raises ValueError on bad input."""
    if not raw:
        return None, None
    upper = raw.strip().upper()
    if upper not in VALID_WINDOWS:
        raise ValueError(f"Invalid window '{raw}'. Allowed: {', '.join(VALID_WINDOWS)}")
    return upper, VALID_WINDOWS[upper]


def parse_since(raw):
    """Returns a datetime or None. Raises ValueError on bad input."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise ValueError(
            f"Invalid 'since' value '{raw}'. Use ISO 8601 format, "
            f"e.g. 2026-05-01 or 2026-05-01T00:00:00Z"
        )


def normalize_side(raw):
    """
    Returns normalized side ('MORE' or 'LESS') or None.
    over/more/OVER/MORE → MORE, under/less/UNDER/LESS → LESS.
    Raises ValueError on unknown values.
    """
    if not raw:
        return None
    normalized = SIDE_MAP.get(raw.strip().lower()) or SIDE_MAP.get(raw.strip())
    if not normalized:
        raise ValueError(
            f"Invalid side '{raw}'. Accepted: over, more, under, less"
        )
    return normalized


def normalize_environment(raw):
    """
    Returns normalized environment ('test' or 'live') or None.
    Raises ValueError on unknown values.
    """
    if not raw:
        return None
    v = raw.strip().lower()
    if v not in VALID_ENVIRONMENTS:
        raise ValueError(
            f"Invalid environment '{raw}'. Accepted: test, live"
        )
    return v


def parse_common_filters():
    """
    Parse shared query params for /stats and /request-log.
    Returns a dict of parsed values or raises ValueError.
    Filter application order: since → player/sport/prop → side → environment → window.
    """
    window_label, window_n = parse_window(request.args.get("window", ""))
    since_dt   = parse_since(request.args.get("since", ""))
    side_norm  = normalize_side(request.args.get("side", ""))
    env_norm   = normalize_environment(request.args.get("environment", ""))
    return {
        "player":       request.args.get("player", "").strip() or None,
        "sport":        request.args.get("sport",  "").strip() or None,
        "prop":         request.args.get("prop",   "").strip() or None,
        "side":         side_norm,
        "since":        since_dt,
        "window_label": window_label,
        "window_n":     window_n,
        "environment":  env_norm,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/debug/source-status", methods=["GET"])
def debug_source_status():
    return jsonify({
        "ok": True,
        "secrets": {
            "ODDS_API_KEY":    "present" if os.getenv("ODDS_API_KEY")    else "missing",
            "RUNDOWN_API_KEY": "present" if os.getenv("RUNDOWN_API_KEY") else "missing",
            "SCORING_API_KEY": "present" if os.getenv("SCORING_API_KEY") else "missing",
        }
    })


@app.route("/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "WOW Sports Prop Scoring API",
        "version": "1.0.0",
        "label": "Support Layer Only",
        "disclaimer": DISCLAIMER,
        "auth": "X-API-Key header required on protected endpoints",
        "endpoints": {
            "health":         "GET /health (no auth)",
            "score":          "POST /random-forest-score (X-API-Key required)",
            "analyze_board":  "POST /analyze-board (X-API-Key required) — vision extraction from screenshots",
            "log":            "GET /request-log?window=L5|L10&since=...&player=...&sport=...&prop=...&side=...&limit=...",
            "stats":          "GET /stats?window=L5|L10&since=...&player=...&sport=...&prop=...&side=...&limit=...",
            "leaderboard":    "GET /leaderboard?window=L5|L10(default L10)&sport=...&prop=...&side=...&limit=...",
            "schema":         "GET /openapi.json (no auth)",
            "fbref_stats":    "GET /fbref-stats?player=...&league=... (X-API-Key required) — soccer player season stats",
            "fbref_fixtures": "GET /fbref-stats/fixtures?date=YYYY-MM-DD&leagues=epl,mls&fresh=1 (X-API-Key required) — soccer fixtures, cache-first with live fallback + write-through",
            "fbref_fixtures_refresh": "POST /fbref-stats/fixtures/refresh {start_date,end_date} (X-API-Key required) — pre-populate soccer_fixtures_cache; designed for daily external scheduler",
            "tennis_stats":        "GET /tennis-stats?player=...&tour=atp|wta&year=...&limit=... (X-API-Key required) — ATP/WTA match stats via JeffSackmann",
            "tennis_stats_player": "GET /tennis-stats/player?player=...&tour=atp|wta&surface=Hard|Clay|Grass|Carpet&years=1-5&opponent=... (X-API-Key required) — aggregated career stats (avgAces, avgDFs, firstServePct, surface win rates, H2H) from JeffSackmann",
            "tennis_stats_today":  "GET /tennis-stats/today?tour=atp|wta (X-API-Key required) — today's live ATP/WTA matches from Odds API",
            "api_sports_players":        "GET /api-sports/{baseball|basketball|hockey|nfl|tennis}/players?player=... (X-API-Key required) — search players via api-sports.io",
            "api_sports_stats":          "GET /api-sports/{basketball|nfl|tennis}/stats?player_id=...&team=...&league=...&season=... (X-API-Key required) — season stats via api-sports.io",
            "api_sports_tennis_fixtures":"GET /api-sports/tennis/fixtures?date=YYYY-MM-DD (X-API-Key required) — tennis fixtures for a date via api-sports.io",
            "umpire_stats":          "GET /umpire-stats?name=...&since=YYYY-MM-DD (X-API-Key required) — MLB HP umpire career K/BB/runs aggregates",
            "umpire_stats_populate": "POST /umpire-stats/populate {start_date,end_date} (X-API-Key required) — backfill umpire_games from MLB Stats API (max 180 days)",
            "lines_opening_store":   "POST /lines/opening {player,prop,line,side?,date?,sport?,book?} (X-API-Key required) — capture opening line; first-write-wins",
            "lines_opening_get":     "GET /lines/opening?player=...&prop=...&side=over|under|yes|no&date=YYYY-MM-DD&current=N (X-API-Key required) — lookup stored opening line, optional movement vs current",
            "lines_opening_list":    "GET /lines/opening/list?date=YYYY-MM-DD&sport=... (X-API-Key required) — bulk list of opening lines for a day",
            "gpt_score_enriched":    "POST /gpt-score/enriched {player,sport,prop,side,line,...,skip_claude?} (X-API-Key required) — wraps /gpt-score with a Claude narrative pulling opening-line movement context"
        }
    })


@app.route("/gpt-score", methods=["POST"])
@require_api_key
def gpt_score():
    """
    GPT-friendly scoring endpoint. Accepts free-form analysis fields alongside
    the required pick fields. Returns a concise response optimised for GPT to
    read and relay to the user.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    required_fields = ["player", "sport", "prop", "side", "line"]
    missing = [f for f in required_fields if f not in data]
    if missing:
        return jsonify({
            "error": "Missing required fields",
            "missing_fields": missing,
            "required_fields": required_fields,
            "hint": "side must be MORE or LESS. line is a numeric value (e.g. 27.5)."
        }), 422

    player = str(data["player"]).strip()
    sport  = str(data["sport"]).strip().upper()
    prop   = str(data["prop"]).strip().lower()
    try:
        side = normalize_side(str(data["side"]))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    try:
        line = float(data["line"])
    except (TypeError, ValueError):
        return jsonify({"error": "'line' must be a numeric value"}), 422

    # Accept any extra keys as features (GPT analysis fields)
    reserved = set(required_fields + ["features", "game_date", "environment"])
    features = {k: v for k, v in data.items() if k not in reserved}
    features.update(data.get("features", {}) or {})

    # Optional game_date — when the game is actually scheduled (YYYY-MM-DD)
    game_date = None
    raw_game_date = data.get("game_date")
    if raw_game_date:
        from datetime import date as _date
        try:
            game_date = str(_date.fromisoformat(str(raw_game_date)))
        except ValueError:
            return jsonify({"error": "'game_date' must be YYYY-MM-DD format, e.g. 2026-05-08"}), 422

    # Optional environment — "test" (default) or "live"
    try:
        environment = normalize_environment(data.get("environment", "")) or "test"
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    score, signal, msg = compute_wow_score(features, player, prop, side, line)
    persist_request(player, sport, prop, side, line, score, signal,
                    game_date=game_date, environment=environment)

    audit_valid = bool(features.get("raw_l5")) and bool(features.get("raw_l10"))
    invalid_reason = None if audit_valid else "L5/L10 raw rows not provided in request"

    return jsonify({
        "wow_score": score,
        "signal": signal,
        "message": msg,
        "saved_to_lobby": True,
        "environment": environment,
        "player": player,
        "sport": sport,
        "prop": prop,
        "side": side,
        "line": line,
        "audit_valid": audit_valid,
        "invalid_reason": invalid_reason,
    })


@app.route("/random-forest-score", methods=["POST"])
@require_api_key
def random_forest_score():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    required_fields = ["player", "sport", "prop", "side", "line"]
    missing = [f for f in required_fields if f not in data]
    if missing:
        return jsonify({
            "error": "Missing required fields",
            "missing_fields": missing,
            "required_fields": required_fields
        }), 422

    player = str(data["player"])
    sport  = str(data["sport"])
    prop   = str(data["prop"])
    try:
        side = normalize_side(str(data["side"]))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    features = data.get("features", {})

    try:
        line = float(data["line"])
    except (TypeError, ValueError):
        return jsonify({"error": "'line' must be a numeric value"}), 422

    if not isinstance(features, dict):
        return jsonify({"error": "'features' must be a JSON object (key-value pairs)"}), 422

    # Also accept flat extra keys as features (same as /gpt-score)
    reserved = {"player", "sport", "prop", "side", "line", "features", "game_date", "environment"}
    flat_features = {k: v for k, v in data.items() if k not in reserved}
    flat_features.update(features)

    game_date = None
    raw_game_date = data.get("game_date")
    if raw_game_date:
        from datetime import date as _date
        try:
            game_date = str(_date.fromisoformat(str(raw_game_date)))
        except ValueError:
            return jsonify({"error": "'game_date' must be YYYY-MM-DD format"}), 422

    # Optional environment — "test" (default) or "live"
    try:
        environment = normalize_environment(data.get("environment", "")) or "test"
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    score, signal, msg = compute_wow_score(flat_features, player, prop, side, line)
    persist_request(player, sport, prop, side, line, score, signal,
                    game_date=game_date, environment=environment)

    audit_valid = bool(flat_features.get("raw_l5")) and bool(flat_features.get("raw_l10"))
    invalid_reason = None if audit_valid else "L5/L10 raw rows not provided in request"

    return jsonify({
        "wow_score": score,
        "signal": signal,
        "message": msg,
        "saved_to_lobby": True,
        "can_approve_bets": False,
        "environment": environment,
        "player": player,
        "sport": sport,
        "prop": prop,
        "side": side,
        "line": line,
        "features_received": len(flat_features),
        "audit_valid": audit_valid,
        "invalid_reason": invalid_reason,
    })


@app.route("/request-log", methods=["GET"])
def request_log():
    raw_limit = request.args.get("limit", "50")
    try:
        limit = max(1, min(int(raw_limit), 200))
    except (ValueError, TypeError):
        return jsonify({"error": "'limit' must be a positive integer"}), 422

    try:
        f = parse_common_filters()
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    try:
        entries = fetch_log(
            player=f["player"], sport=f["sport"], prop=f["prop"],
            side=f["side"], since=f["since"],
            window_n=f["window_n"], limit=limit, environment=f["environment"]
        )
    except Exception as exc:
        return jsonify({"error": "Database unavailable", "detail": str(exc)}), 503

    return jsonify({
        "count": len(entries),
        "limit": f["window_n"] if f["window_n"] else limit,
        "window": f["window_label"],
        "order": "most recent first",
        "storage": "postgresql",
        "filters": {
            "player": f["player"], "sport": f["sport"],
            "prop": f["prop"], "side": f["side"],
            "since": f["since"].isoformat() if f["since"] else None,
            "environment": f["environment"],
        },
        "requests": entries
    })


@app.route("/stats", methods=["GET"])
def stats():
    raw_limit = request.args.get("limit", "10")
    try:
        top_limit = max(1, min(int(raw_limit), 100))
    except (ValueError, TypeError):
        return jsonify({"error": "'limit' must be a positive integer"}), 422

    try:
        f = parse_common_filters()
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    try:
        data = fetch_stats(
            player=f["player"], sport=f["sport"], prop=f["prop"],
            side=f["side"], since=f["since"],
            window_n=f["window_n"], top_limit=top_limit, environment=f["environment"]
        )
    except Exception as exc:
        return jsonify({"error": "Database unavailable", "detail": str(exc)}), 503

    return jsonify({
        "storage": "postgresql",
        "window": f["window_label"],
        "filters": {
            "player": f["player"], "sport": f["sport"],
            "prop": f["prop"], "side": f["side"],
            "since": f["since"].isoformat() if f["since"] else None,
            "environment": f["environment"],
            "limit": top_limit
        },
        **data
    })


@app.route("/leaderboard", methods=["GET"])
def leaderboard():
    # window — defaults to L10 for leaderboard
    raw_window = request.args.get("window", "L10")
    try:
        window_label, window_n = parse_window(raw_window)
        # parse_window returns (None, None) for empty string; treat as default
        if window_n is None:
            window_label, window_n = "L10", 10
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    # limit
    try:
        limit = max(1, min(int(request.args.get("limit", "10")), 100))
    except (ValueError, TypeError):
        return jsonify({"error": "'limit' must be a positive integer"}), 422

    # side normalization
    try:
        side_norm = normalize_side(request.args.get("side", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    # since
    try:
        since_dt = parse_since(request.args.get("since", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    # today=1 → filter by game_date = CURRENT_DATE (overrides since)
    today_flag = request.args.get("today", "0") in ("1", "true", "yes")
    if today_flag:
        since_dt = None  # game_date filter takes precedence

    sport = request.args.get("sport", "").strip() or None
    prop  = request.args.get("prop",  "").strip() or None

    try:
        env_norm = normalize_environment(request.args.get("environment", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    try:
        entries = fetch_leaderboard(
            sport=sport, prop=prop, side=side_norm, since=since_dt,
            window_n=window_n, limit=limit, today=today_flag, environment=env_norm
        )
    except Exception as exc:
        return jsonify({"error": "Database unavailable", "detail": str(exc)}), 503

    return jsonify({
        "storage":      "postgresql",
        "window":       window_label,
        "limit":        limit,
        "ranked_by":    "average_score DESC, latest_score DESC",
        "filters": {
            "sport": sport,
            "prop":  prop,
            "side":  side_norm,
            "since": since_dt.isoformat() if since_dt else None,
            "environment": env_norm,
        },
        "leaderboard": entries
    })


# ---------------------------------------------------------------------------
# Daily scan routes
# ---------------------------------------------------------------------------

@app.route("/wow-daily-scan", methods=["POST"])
@require_api_key
def wow_daily_scan():
    """
    Run the WOW daily prop scanner and return completed results directly.

    Runs synchronously by default — scans all requested sports, saves full
    results to the scan_results table, then returns a compact classified
    summary (same shape as /scan-results/summary) so GPT stays within
    response size limits.

    Pass async=true to run in background instead (response: {status: started}).

    Accepted params:
      sports              — list of sport names (default: all)
      environment         — "live" or "test" (default: live)
      limit_per_sport     — max props per sport (default: 50)
      include_execution_report — include execution_report block (default: true)
      async               — run in background, return immediately (default: false)
      date / mode / strict_board_lock / require_prizepicks_for_final /
      run_manual_l10_if_needed — accepted for compatibility, ignored internally
    """
    try:
        from jobs.wow_daily_scan import run_scan
    except Exception as e:
        return jsonify({"ok": False, "error": f"Scanner import failed: {e}"}), 500

    data = request.get_json(silent=True) or {}
    sports_param  = data.get("sports") or None
    environment   = data.get("environment", "live")
    limit         = int(data.get("limit_per_sport", 50))
    async_mode    = bool(data.get("async", False))   # default False — return results directly
    include_exec  = data.get("include_execution_report", True)

    try:
        from services.status import get_injuries  # noqa: F401 — validate imports work
    except Exception as e:
        return jsonify({"ok": False, "error": f"Service import failed: {e}"}), 500

    # -----------------------------------------------------------------------
    # Async mode — fire and forget, let caller poll /scan-results/summary
    # -----------------------------------------------------------------------
    if async_mode:
        def _run():
            try:
                run_scan(sports=sports_param, environment=environment, limit_per_sport=limit)
            except Exception as ex:
                print(f"[wow_daily_scan async] error: {ex}")
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return jsonify({
            "ok":      True,
            "status":  "started",
            "message": "Scan running in background. Poll /scan-results/summary for results.",
            "sports":  sports_param,
            "environment": environment,
        })

    # -----------------------------------------------------------------------
    # Synchronous mode — run scan, then return compact summary from DB
    # -----------------------------------------------------------------------
    try:
        scan_result = run_scan(sports=sports_param, environment=environment, limit_per_sport=limit)
    except Exception as e:
        return jsonify({"ok": False, "status": "error", "error": str(e)}), 500

    # Pull coverage info from the scan result (not from DB — it's authoritative)
    requested_sports = scan_result.get("requested_sports", sports_param or [])
    scanned_sports   = scan_result.get("scanned_sports",   [])
    missing_sports   = scan_result.get("missing_sports",   [])
    scan_valid       = scan_result.get("scan_valid",       True)

    # Build compact response from what was just saved to the DB
    try:
        from storage.results import get_scan_summary, get_compact_scan_rows, get_scan_source_flags
        from datetime import date as _date
        run_date = _date.today().isoformat()

        summary_counts = get_scan_summary(run_date)
        total_rows     = sum(summary_counts.values())
        rows           = get_compact_scan_rows(run_date, limit=10 * 6)
        flags          = get_scan_source_flags(run_date)

        CAT_KEYS = {
            "market_verified":         "Market Verified Approved",
            "final_approved_internal": "Final Approved — Internal Projection",
            "model_qualified":         "Model Qualified — PrizePicks",
            "conditional":             "Conditional",
            "watch":                   "Watch",
            "reject":                  "Reject",
            "data_insufficient":       "Data Insufficient",
        }
        CAT_REVERSE = {v: k for k, v in CAT_KEYS.items()}

        grouped = {k: [] for k in CAT_KEYS}
        for row in rows:
            cat_key = CAT_REVERSE.get(row.get("classification", ""))
            if cat_key and len(grouped[cat_key]) < 10:
                grouped[cat_key].append(_compact_prop(row))

        counts = {k: summary_counts.get(cls_name, 0) for k, cls_name in CAT_KEYS.items()}
        counts["total_final_approved"] = counts["market_verified"] + counts["final_approved_internal"]
        counts["playable_count"]       = counts["total_final_approved"] + counts["model_qualified"]

        def _avail(key):
            return "AVAILABLE" if int(flags.get(key, 0) or 0) > 0 else "NOT_CALLED"

        source_access_status = {
            "market_odds":    _avail("odds_avail"),
            "player_logs":    _avail("logs_avail"),
            "injury_status":  _avail("status_avail"),
            "rundown_backup": _avail("rundown_avail"),
        }

        audit_valid_count   = int(flags.get("audit_valid_count",   0) or 0)
        audit_invalid_count = int(flags.get("audit_invalid_count", 0) or 0)

        execution_report = {}
        if include_exec:
            execution_report = {
                "daily_scan_executed":            total_rows > 0,
                "get_events_called":              int(flags.get("odds_called",   0) or 0) > 0,
                "get_event_markets_called":       int(flags.get("odds_called",   0) or 0) > 0,
                "get_event_odds_called":          int(flags.get("odds_avail",    0) or 0) > 0,
                "l5_l10_called":                  int(flags.get("logs_called",   0) or 0) > 0,
                "status_lineups_called":          int(flags.get("status_called", 0) or 0) > 0,
                "internal_projection_called":     True,
                "external_projection_available":  False,
                "final_approved_count":           counts["total_final_approved"],
                "market_verified_count":          counts["market_verified"],
                "final_approved_internal_count":  counts["final_approved_internal"],
                "model_qualified_count":          counts["model_qualified"],
                "watch_count":                    counts["watch"],
                "reject_count":                   counts["reject"],
                "audit_valid_count":              audit_valid_count,
                "audit_invalid_count":            audit_invalid_count,
                "internal_proj_count":            int(flags.get("internal_proj_count", 0) or 0),
                "missing_proj_count":             int(flags.get("missing_proj_count",  0) or 0),
            }

        sports_scanned_db = [s for s in (flags.get("sports") or []) if s]
        execution_notes = list(scan_result.get("execution_notes", []))
        if not execution_notes:
            if sports_scanned_db:
                execution_notes.append(f"Sports scanned: {', '.join(sorted(sports_scanned_db))}")
            if total_rows > 0:
                execution_notes.append(f"Total props evaluated: {total_rows}")

        return jsonify({
            "ok":                       True,
            "status":                   "completed",
            "scan_valid":               scan_valid,
            "run_date":                 run_date,
            "requested_sports":         requested_sports,
            "scanned_sports":           scanned_sports,
            "missing_sports":           missing_sports,
            "source_access_status":     source_access_status,
            "execution_report":         execution_report,
            "counts":                   counts,
            "market_verified":          grouped["market_verified"],
            "final_approved_internal":  grouped["final_approved_internal"],
            "model_qualified":          grouped["model_qualified"],
            "conditional":              grouped["conditional"],
            "watch":                    grouped["watch"],
            "reject":                   grouped["reject"],
            "data_insufficient":        grouped["data_insufficient"],
            "final_approved_picks":     grouped["market_verified"] + grouped["final_approved_internal"],
            "playable_card":            grouped["market_verified"] + grouped["final_approved_internal"] + grouped["model_qualified"],
            "execution_notes":          execution_notes,
        })

    except Exception as e:
        return jsonify({
            "ok":     False,
            "status": "error",
            "error":  f"Scan completed but summary failed: {e}",
        }), 500


@app.route("/final-lock", methods=["POST"])
@require_api_key
def final_lock():
    """
    POST /final-lock — WOW v14.9.1 Final Lock gate.

    Accepts per-prop inputs and runs the full approval decision tree:
      1. status_confirmed gate   → reject on fail
      2. line_verified gate      → reject on fail
      3. L10 data gate           → downgrade to MODEL_QUALIFIED on fail
      4. Projection resolution   → external if provided, else internal model
      5. Projection margin gate  → downgrade to WATCH if margin < 5%
      6. Market sanity gate      → downgrade to WATCH on fail
      7. Approve: FINAL APPROVED — INTERNAL PROJECTION

    Optionally saves result to scan_results table (saved_to_lobby).
    """
    from jobs.wow_daily_scan import compute_internal_projection
    from storage.results import save_scan_result
    from datetime import date

    body = request.get_json(silent=True) or {}

    # --- Required identity fields ---
    player     = body.get("player", "")
    sport      = body.get("sport",  "")
    prop       = body.get("prop",   "")
    side       = (body.get("side") or "MORE").upper()
    line       = body.get("line")

    if not player or not sport or not prop or line is None:
        return jsonify({
            "ok": False,
            "error": "Missing required fields: player, sport, prop, line",
        }), 422

    try:
        line = float(line)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "'line' must be a number"}), 422

    # --- Gate inputs ---
    l5_values        = body.get("l5_values")  or []
    l10_values       = body.get("l10_values") or []
    recent_avg       = body.get("recent_avg")
    l10_median       = body.get("l10_median")
    role_score       = body.get("role_score")
    status_confirmed = body.get("status_confirmed", False)
    line_verified    = body.get("line_verified",    False)
    market_sanity    = body.get("market_sanity",    False)
    market_gap       = body.get("market_gap")
    ext_projection   = body.get("external_projection")   # numeric or null
    environment      = body.get("environment", "live")

    REQUIRED_MARGIN = 5.0

    def _reject(code, detail=None):
        return {
            "ok":             False,
            "classification": "REJECT",
            "blocker_code":   code,
            "blocker_detail": detail or code,
            "projection_status":  None,
            "projection_value":   None,
            "projection_margin":  None,
            "projection_source":  None,
            "final_approval_blocker": code,
            "saved_to_lobby": False,
        }

    def _downgrade(tier, code, detail=None):
        return {
            "ok":             True,
            "classification": tier,
            "blocker_code":   code,
            "blocker_detail": detail or code,
            "projection_status":  proj_status,
            "projection_value":   proj_value,
            "projection_margin":  proj_margin,
            "projection_source":  proj_source,
            "final_approval_blocker": code,
            "saved_to_lobby": False,
        }

    # Projection placeholders (populated below)
    proj_status = None
    proj_value  = None
    proj_margin = None
    proj_source = None

    # ---- Gate 1: Official status ----
    if not status_confirmed:
        return jsonify({**_reject("STATUS_NOT_CONFIRMED",
            "Player status has not been confirmed as active/available")}), 200

    # ---- Gate 2: Line verified ----
    if not line_verified:
        return jsonify({**_reject("LINE_NOT_VERIFIED",
            "Line could not be verified against an active market")}), 200

    # ---- Gate 3: L10 data ----
    if not l10_values or l10_median is None:
        proj_status = "MISSING"
        return jsonify({**_downgrade("MODEL_QUALIFIED", "L10_UNVERIFIED",
            "l10_values or l10_median not provided — cannot compute projection")}), 200

    # ---- Gate 4: Projection resolution ----
    log_stats = {
        "l10_median":      l10_median,
        "l10_avg":         recent_avg if recent_avg is not None
                           else (sum(l10_values) / len(l10_values) if l10_values else None),
        "games_available": len(l10_values),
        "raw_l5":          [{"stat": float(v)} for v in l5_values],
        "raw_l10":         [{"stat": float(v)} for v in l10_values],
    }

    if ext_projection is not None:
        # External projection provided — use it directly
        try:
            proj_value = float(ext_projection)
        except (TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": "'external_projection' must be a number or null"}), 422
        proj_status = "EXTERNAL"
        proj_source = "external_api"
        if line > 0:
            proj_margin = round(
                (proj_value - line) / line * 100 if side == "MORE"
                else (line - proj_value) / line * 100,
                2,
            )
        else:
            proj_margin = 0.0
    else:
        # Internal model projection
        result = compute_internal_projection(log_stats, line, side)
        proj_status = result["projection_status"]
        proj_value  = result["projection_value"]
        proj_margin = result["projection_margin"]
        proj_source = result["projection_source"]

        if proj_status == "MISSING":
            return jsonify({**_downgrade("MODEL_QUALIFIED", "PROJECTION_MISSING",
                result["final_approval_blocker"])}), 200

    # ---- Gate 5: Projection margin ----
    if proj_margin is None or proj_margin < REQUIRED_MARGIN:
        thin_msg = (
            f"projection margin {proj_margin:.1f}% < required {REQUIRED_MARGIN:.0f}%"
            if proj_margin is not None else "projection margin unavailable"
        )
        return jsonify({**_downgrade("WATCH", "PROJECTION_MARGIN_TOO_THIN", thin_msg)}), 200

    # ---- Gate 6: Market sanity ----
    if not market_sanity:
        return jsonify({**_downgrade("WATCH", "MARKET_SANITY_MISSING",
            "Market sanity check not satisfied — line may be mispriced or stale")}), 200

    # ---- All gates passed → Final Approved ----
    classification = "FINAL APPROVED — INTERNAL PROJECTION"

    # Save to lobby (scan_results table)
    saved = False
    try:
        wow_score, signal, message = compute_wow_score(
            {
                "l5_hit_rate":  sum(1 for v in l5_values  if (v > line if side == "MORE" else v < line)) / len(l5_values)  if l5_values  else None,
                "l10_hit_rate": sum(1 for v in l10_values if (v > line if side == "MORE" else v < line)) / len(l10_values) if l10_values else None,
                "recent_avg":   recent_avg,
                "median_edge":  round((l10_median - line) / line, 4) if l10_median and line else None,
                "injury_flag":  0,
                "role_score":   role_score,
            },
            player, prop, side, line,
        )
        save_scan_result({
            "run_date":     date.today().isoformat(),
            "sport":        sport,
            "player":       player,
            "prop":         prop,
            "line":         line,
            "side":         side,
            "game_date":    date.today().isoformat(),
            "wow_score":    wow_score,
            "signal":       signal,
            "message":      message,
            "classification": classification,
            "environment":  environment,
            "source_odds":   "NOT_CALLED",
            "source_rundown": "NOT_CALLED",
            "source_logs":   "MANUAL_INPUT",
            "source_status": "CONFIRMED" if status_confirmed else "NOT_CONFIRMED",
            "l5_hit_rate":  log_stats["raw_l5"] and sum(1 for r in log_stats["raw_l5"]  if (r["stat"] > line if side == "MORE" else r["stat"] < line)) / len(log_stats["raw_l5"]),
            "l10_hit_rate": log_stats["raw_l10"] and sum(1 for r in log_stats["raw_l10"] if (r["stat"] > line if side == "MORE" else r["stat"] < line)) / len(log_stats["raw_l10"]),
            "l10_median":   l10_median,
            "l10_avg":      log_stats["l10_avg"],
            "raw_features": {"role_score": role_score, "market_gap": market_gap},
            "notes":        f"final-lock endpoint; market_gap={market_gap}; role_score={role_score}",
            "raw_l5":       log_stats["raw_l5"],
            "raw_l10":      log_stats["raw_l10"],
            "games_available":      len(l10_values),
            "sample_scope":         "manual_input",
            "cross_season_used":    False,
            "manual_fallback_used": False,
            "audit_valid":          True,
            "invalid_reason":       None,
            "projection_status":    proj_status,
            "projection_value":     proj_value,
            "projection_margin":    proj_margin,
            "projection_source":    proj_source,
            "final_approval_blocker": None,
        })
        saved = True
    except Exception as save_err:
        saved = False

    return jsonify({
        "ok":                    True,
        "classification":        classification,
        "player":                player,
        "sport":                 sport,
        "prop":                  prop,
        "side":                  side,
        "line":                  line,
        "projection_status":     proj_status,
        "projection_value":      proj_value,
        "projection_margin":     proj_margin,
        "projection_source":     proj_source,
        "final_approval_blocker": None,
        "gates_passed": {
            "status_confirmed": True,
            "line_verified":    True,
            "l10_verified":     True,
            "projection_ok":    True,
            "market_sanity":    True,
        },
        "saved_to_lobby": saved,
    }), 200


@app.route("/scan-results", methods=["GET"])
@require_api_key
def scan_results():
    """
    Retrieve persisted scan results from the database.
    Optional filters: run_date, classification, sport, limit.
    """
    try:
        from storage.results import get_scan_results, get_scan_summary
    except Exception as e:
        return jsonify({"error": f"Storage import failed: {e}"}), 500

    run_date       = request.args.get("run_date", "").strip() or None
    classification = request.args.get("classification", "").strip() or None
    sport          = request.args.get("sport", "").strip() or None
    summary_only   = request.args.get("summary", "0") in ("1", "true", "yes")

    try:
        limit = max(1, min(int(request.args.get("limit", "200")), 1000))
    except (ValueError, TypeError):
        return jsonify({"error": "'limit' must be a positive integer"}), 422

    if not run_date:
        from datetime import date as _date
        run_date = _date.today().isoformat()

    try:
        summary = get_scan_summary(run_date)
        if summary_only:
            return jsonify({"run_date": run_date, "summary": summary})

        rows = get_scan_results(
            run_date=run_date,
            classification=classification,
            sport=sport,
            limit=limit,
        )
        # Serialize non-JSON-native types
        for r in rows:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif isinstance(v, (bytes, memoryview)):
                    r[k] = str(v)

        return jsonify({
            "run_date":  run_date,
            "count":     len(rows),
            "summary":   summary,
            "filters":   {
                "classification": classification,
                "sport":          sport,
                "limit":          limit,
            },
            "results": rows,
        })
    except Exception as e:
        return jsonify({"error": "Database unavailable", "detail": str(e)}), 503


# ---------------------------------------------------------------------------
# Compact prop serializer (used by /scan-results/summary)
# ---------------------------------------------------------------------------

def _compact_prop(row):
    """
    Serialize one scan_results DB row into a compact, GPT-safe prop dict.
    Excludes raw event payloads, market arrays, and full game logs.
    """
    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    s_odds   = str(row.get("source_odds",    "") or "")
    s_run    = str(row.get("source_rundown", "") or "")
    s_logs   = str(row.get("source_logs",   "") or "")
    s_status = str(row.get("source_status", "") or "")

    # Platform / book source
    if "AVAILABLE" in s_odds:
        platform = "The Odds API"
    elif "AVAILABLE" in s_run:
        platform = "TheRundown"
    else:
        platform = None

    # model_probability — wow_score normalised to 0-1
    wow = _f(row.get("wow_score"))
    model_prob = round(wow / 100, 4) if wow is not None else None

    # failure_paths — sources that were not AVAILABLE (max 3)
    failure_paths = []
    for label, val in [("odds", s_odds), ("logs", s_logs), ("status", s_status)]:
        if val and "AVAILABLE" not in val:
            failure_paths.append(f"{label}: {val[:80]}")
        if len(failure_paths) >= 3:
            break

    return {
        "player":            row.get("player"),
        "sport":             row.get("sport"),
        "prop":              row.get("prop"),
        "side":              row.get("side"),
        "line":              _f(row.get("line")),
        "platform":          platform,
        "model_probability": model_prob,
        "l5_hit_rate":       _f(row.get("l5_hit_rate")),
        "l10_hit_rate":      _f(row.get("l10_hit_rate")),
        "l10_median":        _f(row.get("l10_median")),
        "games_available":   row.get("games_available"),
        "sample_scope":      row.get("sample_scope"),
        "final_label":       row.get("classification"),
        "reason":            row.get("message"),
        "failure_paths":     failure_paths[:3],
        "audit_valid":       row.get("audit_valid"),
        "invalid_reason":    row.get("invalid_reason"),
        "projection_status":      row.get("projection_status"),
        "projection_value":       float(row["projection_value"]) if row.get("projection_value") is not None else None,
        "projection_margin":      float(row["projection_margin"]) if row.get("projection_margin") is not None else None,
        "projection_source":      row.get("projection_source"),
        "final_approval_blocker": row.get("final_approval_blocker"),
    }


@app.route("/scan-results/summary", methods=["GET"])
@require_api_key
def scan_results_summary():
    """
    Compact scan summary for GPT Actions.
    Excludes raw event payloads, market arrays, and full game logs.
    Returns only classified picks with essential stats per prop.

    Query params:
      run_date  — YYYY-MM-DD, defaults to today
      limit     — max items per category (default 10, max 50)
      category  — filter to one category key (e.g. market_verified)
      status    — only return if scan matches this status (completed|pending)
    """
    try:
        from storage.results import (
            get_scan_summary, get_compact_scan_rows, get_scan_source_flags
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Storage import failed: {e}"}), 500

    # --- params ---
    run_date = request.args.get("run_date", "").strip() or None
    if not run_date:
        from datetime import date as _date
        run_date = _date.today().isoformat()

    category_param = (
        request.args.get("category", "").strip().lower().replace(" ", "_") or None
    )
    status_filter = request.args.get("status", "").strip().lower() or None

    try:
        limit = max(1, min(int(request.args.get("limit", "10")), 50))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "'limit' must be a positive integer"}), 422

    # Classification name ↔ response key mapping
    CAT_KEYS = {
        "market_verified":         "Market Verified Approved",
        "final_approved_internal": "Final Approved — Internal Projection",
        "model_qualified":         "Model Qualified — PrizePicks",
        "conditional":             "Conditional",
        "watch":                   "Watch",
        "reject":                  "Reject",
        "data_insufficient":       "Data Insufficient",
    }
    CAT_REVERSE = {v: k for k, v in CAT_KEYS.items()}

    db_category = CAT_KEYS.get(category_param) if category_param else None

    _empty_report = {
        "daily_scan_executed": False, "get_events_called": False,
        "get_event_markets_called": False, "get_event_odds_called": False,
        "l5_l10_called": False, "status_lineups_called": False,
        "internal_projection_called": False, "external_projection_available": False,
        "final_approved_count": 0, "market_verified_count": 0,
        "final_approved_internal_count": 0,
        "model_qualified_count": 0, "watch_count": 0, "reject_count": 0,
        "audit_valid_count": 0, "audit_invalid_count": 0,
        "internal_proj_count": 0, "missing_proj_count": 0,
    }

    try:
        summary_counts = get_scan_summary(run_date)
        total_rows     = sum(summary_counts.values())
        scan_status    = "completed" if total_rows > 0 else "pending"

        # Early exit if caller requested a specific status that doesn't match
        if status_filter and status_filter != scan_status:
            return jsonify({
                "ok": True, "status": scan_status, "run_date": run_date,
                "message": f"Scan status is '{scan_status}', not '{status_filter}'",
                "source_access_status": {},
                "execution_report": _empty_report,
                "counts":                  {k: 0 for k in CAT_KEYS},
                "market_verified":          [], "final_approved_internal": [],
                "model_qualified":          [], "conditional": [],
                "watch":                    [], "reject": [], "data_insufficient": [],
                "final_approved_picks":     [], "playable_card": [],
                "playable_count":           0, "execution_notes": [],
            })

        # Fetch compact rows — enough to fill every category up to limit
        fetch_limit = (limit * len(CAT_KEYS)) if not db_category else limit
        rows  = get_compact_scan_rows(run_date, category=db_category, limit=fetch_limit)
        flags = get_scan_source_flags(run_date)

        # Group rows by category key; already sorted wow_score DESC
        grouped = {k: [] for k in CAT_KEYS}
        for row in rows:
            cat_key = CAT_REVERSE.get(row.get("classification", ""))
            if cat_key and len(grouped[cat_key]) < limit:
                grouped[cat_key].append(_compact_prop(row))

        # Counts come from DB summary, not the sliced grouped lists
        counts = {k: summary_counts.get(cls_name, 0) for k, cls_name in CAT_KEYS.items()}

        # Source access status
        def _avail(key):
            return "AVAILABLE" if int(flags.get(key, 0) or 0) > 0 else "NOT_CALLED"

        source_access_status = {
            "market_odds":    _avail("odds_avail"),
            "player_logs":    _avail("logs_avail"),
            "injury_status":  _avail("status_avail"),
            "rundown_backup": _avail("rundown_avail"),
        }

        # Execution report (derived from aggregated row data)
        execution_report = {
            "daily_scan_executed":           total_rows > 0,
            "get_events_called":             int(flags.get("odds_called",  0) or 0) > 0,
            "get_event_markets_called":      int(flags.get("odds_called",  0) or 0) > 0,
            "get_event_odds_called":         int(flags.get("odds_avail",   0) or 0) > 0,
            "l5_l10_called":                 int(flags.get("logs_called",  0) or 0) > 0,
            "status_lineups_called":         int(flags.get("status_called",0) or 0) > 0,
            "internal_projection_called":    True,
            "external_projection_available": False,
            "final_approved_count":          counts.get("total_final_approved", counts["market_verified"] + counts["final_approved_internal"]),
            "market_verified_count":         counts["market_verified"],
            "final_approved_internal_count": counts["final_approved_internal"],
            "model_qualified_count":         counts["model_qualified"],
            "watch_count":                   counts["watch"],
            "reject_count":                  counts["reject"],
            "audit_valid_count":             int(flags.get("audit_valid_count",   0) or 0),
            "audit_invalid_count":           int(flags.get("audit_invalid_count", 0) or 0),
            "internal_proj_count":           int(flags.get("internal_proj_count", 0) or 0),
            "missing_proj_count":            int(flags.get("missing_proj_count",  0) or 0),
        }

        # Execution notes
        sports = [s for s in (flags.get("sports") or []) if s]
        execution_notes = []
        if sports:
            execution_notes.append(f"Sports scanned: {', '.join(sorted(sports))}")
        if total_rows > 0:
            execution_notes.append(f"Total props evaluated: {total_rows}")

        counts["total_final_approved"] = counts["market_verified"] + counts["final_approved_internal"]
        counts["playable_count"]       = counts["total_final_approved"] + counts["model_qualified"]

        return jsonify({
            "ok":                       True,
            "status":                   scan_status,
            "run_date":                 run_date,
            "source_access_status":     source_access_status,
            "execution_report":         execution_report,
            "counts":                   counts,
            "market_verified":          grouped["market_verified"],
            "final_approved_internal":  grouped["final_approved_internal"],
            "model_qualified":          grouped["model_qualified"],
            "conditional":              grouped["conditional"],
            "watch":                    grouped["watch"],
            "reject":                   grouped["reject"],
            "data_insufficient":        grouped["data_insufficient"],
            "final_approved_picks":     grouped["market_verified"] + grouped["final_approved_internal"],
            "playable_card":            grouped["market_verified"] + grouped["final_approved_internal"] + grouped["model_qualified"],
            "playable_count":           counts["total_final_approved"] + counts["model_qualified"],
            "execution_notes":          execution_notes,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": "Database unavailable", "detail": str(e)}), 503


# ---------------------------------------------------------------------------
# OpenAPI schema
# ---------------------------------------------------------------------------

WINDOW_PARAM = {
    "name": "window",
    "in": "query",
    "description": "Limit to the latest N scored records after applying other filters. L5 = last 5, L10 = last 10. Overrides limit when set.",
    "required": False,
    "schema": {"type": "string", "enum": ["L5", "L10"]}
}
SINCE_PARAM = {
    "name": "since",
    "in": "query",
    "description": "Only include records at or after this ISO 8601 timestamp (e.g. 2026-05-01 or 2026-05-01T00:00:00Z). Applied before window.",
    "required": False,
    "schema": {"type": "string", "format": "date-time"}
}
PLAYER_PARAM = {"name": "player", "in": "query", "description": "Filter by player name (partial match)", "required": False, "schema": {"type": "string"}}
SPORT_PARAM  = {"name": "sport",  "in": "query", "description": "Filter by sport (case-insensitive)", "required": False, "schema": {"type": "string"}}
PROP_PARAM   = {"name": "prop",   "in": "query", "description": "Filter by prop type (partial match)", "required": False, "schema": {"type": "string"}}
SIDE_PARAM   = {"name": "side",   "in": "query", "description": "Filter by side. over/more = MORE (over bets), under/less = LESS (under bets). Case-insensitive.", "required": False, "schema": {"type": "string", "enum": ["over", "more", "under", "less"]}}


@app.route("/openapi.json", methods=["GET"])
def openapi_schema():
    server_url = get_public_url()
    schema = {
        "openapi": "3.1.0",
        "info": {
            "title": "WOW Sports Prop Scoring API",
            "description": (
                "Support-layer scoring API for WOW sports prop betting analysis. "
                "Returns a statistical support score (0-100) for a given player prop. "
                "This API is a support layer only and cannot approve, authorize, or "
                "recommend any bet or wager."
            ),
            "version": "1.0.0"
        },
        "servers": [{"url": server_url}],
        "security": [{"ApiKeyAuth": []}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "healthCheck",
                    "summary": "Health check",
                    "description": "Returns service status. No auth required.",
                    "security": [],
                    "responses": {
                        "200": {"description": "Service is healthy", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HealthResponse"}}}}
                    }
                }
            },
            "/random-forest-score": {
                "post": {
                    "operationId": "scoreProp",
                    "summary": "Score a player prop",
                    "description": "Returns a support score 0–100. Requires X-API-Key. SUPPORT LAYER ONLY.",
                    "security": [{"ApiKeyAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ScoreRequest"},
                                "example": {
                                    "player": "Patrick Mahomes", "sport": "NFL",
                                    "prop": "passing_yards", "side": "over", "line": 285.5,
                                    "features": {"last_5_avg": 312.4, "vs_defense_rank": 8, "home_game": 1, "rest_days": 7}
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Score returned", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ScoreResponse"}}}},
                        "401": {"description": "Missing or invalid X-API-Key"},
                        "400": {"description": "Invalid JSON"},
                        "422": {"description": "Missing or invalid fields"}
                    }
                }
            },
            "/stats": {
                "get": {
                    "operationId": "getStats",
                    "summary": "Aggregate scoring statistics",
                    "description": (
                        "Returns aggregate stats over the request log. "
                        "Use window=L5 or window=L10 to scope stats to the latest 5 or 10 records after filtering. "
                        "Use since to filter by date first, then apply the window. Requires X-API-Key."
                    ),
                    "security": [{"ApiKeyAuth": []}],
                    "parameters": [
                        WINDOW_PARAM, SINCE_PARAM,
                        PLAYER_PARAM, SPORT_PARAM, PROP_PARAM, SIDE_PARAM,
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "Max entries for top_scored_props and most_recent_scored_props (default 10, max 100)",
                            "required": False,
                            "schema": {"type": "integer", "default": 10, "maximum": 100}
                        }
                    ],
                    "responses": {
                        "200": {"description": "Stats returned", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/StatsResponse"}}}},
                        "401": {"description": "Missing or invalid X-API-Key"},
                        "422": {"description": "Invalid query parameter"},
                        "503": {"description": "Database unavailable"}
                    }
                }
            },
            "/request-log": {
                "get": {
                    "operationId": "getRequestLog",
                    "summary": "View recent scoring requests",
                    "description": (
                        "Returns raw scoring records from PostgreSQL. "
                        "Use window=L5 or window=L10 to get the latest 5 or 10 records after filtering. "
                        "Apply since before window. Requires X-API-Key. API keys are never stored."
                    ),
                    "security": [{"ApiKeyAuth": []}],
                    "parameters": [
                        WINDOW_PARAM, SINCE_PARAM,
                        PLAYER_PARAM, SPORT_PARAM, PROP_PARAM, SIDE_PARAM,
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "Max records (default 50, max 200). Ignored when window is set.",
                            "required": False,
                            "schema": {"type": "integer", "default": 50, "maximum": 200}
                        }
                    ],
                    "responses": {
                        "200": {"description": "Log returned", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LogResponse"}}}},
                        "401": {"description": "Missing or invalid X-API-Key"},
                        "422": {"description": "Invalid query parameter"},
                        "503": {"description": "Database unavailable"}
                    }
                }
            },
            "/leaderboard": {
                "get": {
                    "operationId": "getLeaderboard",
                    "summary": "Player leaderboard by average score",
                    "description": (
                        "Ranks (player, sport, prop, side) combinations by average score "
                        "within the selected window. For each combination, takes the latest "
                        "window_n records after applying filters, computes aggregates, and "
                        "returns them ordered by average_score DESC with latest_score as "
                        "tiebreaker. Default window=L10. Requires X-API-Key."
                    ),
                    "security": [{"ApiKeyAuth": []}],
                    "parameters": [
                        {
                            "name": "window",
                            "in": "query",
                            "description": "Per-combo window size. L5 = last 5 records, L10 = last 10. Defaults to L10.",
                            "required": False,
                            "schema": {"type": "string", "enum": ["L5", "L10"], "default": "L10"}
                        },
                        SINCE_PARAM, SPORT_PARAM, PROP_PARAM, SIDE_PARAM,
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "Max leaderboard entries (default 10, max 100)",
                            "required": False,
                            "schema": {"type": "integer", "default": 10, "maximum": 100}
                        }
                    ],
                    "responses": {
                        "200": {"description": "Leaderboard returned", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LeaderboardResponse"}}}},
                        "401": {"description": "Missing or invalid X-API-Key"},
                        "422": {"description": "Invalid query parameter"},
                        "503": {"description": "Database unavailable"}
                    }
                }
            }
        },
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
            },
            "schemas": {
                "HealthResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "example": "ok"},
                        "service": {"type": "string"},
                        "version": {"type": "string"},
                        "label": {"type": "string"},
                        "disclaimer": {"type": "string"}
                    }
                },
                "ScoreRequest": {
                    "type": "object",
                    "required": ["player", "sport", "prop", "side", "line"],
                    "properties": {
                        "player": {"type": "string", "example": "Patrick Mahomes"},
                        "sport":  {"type": "string", "example": "NFL"},
                        "prop":   {"type": "string", "example": "passing_yards"},
                        "side":   {"type": "string", "example": "over"},
                        "line":   {"type": "number", "example": 285.5},
                        "features": {
                            "type": "object", "additionalProperties": True,
                            "example": {"last_5_avg": 312.4, "vs_defense_rank": 8}
                        }
                    }
                },
                "ScoreResponse": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "example": "Support Layer Only"},
                        "score": {"type": "number", "example": 74.3},
                        "score_range": {"type": "string", "example": "0-100"},
                        "input": {"type": "object"},
                        "disclaimer": {"type": "string"},
                        "can_approve_bets": {"type": "boolean", "example": False}
                    }
                },
                "LogEntry": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "example": "2026-05-08T14:32:01+00:00"},
                        "player": {"type": "string"}, "sport": {"type": "string"},
                        "prop": {"type": "string"}, "side": {"type": "string"},
                        "line": {"type": "number"}, "score": {"type": "number"},
                        "label": {"type": "string"}
                    }
                },
                "LogResponse": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "window": {"type": "string", "nullable": True, "example": "L5"},
                        "order": {"type": "string"},
                        "storage": {"type": "string"},
                        "filters": {"type": "object"},
                        "requests": {"type": "array", "items": {"$ref": "#/components/schemas/LogEntry"}}
                    }
                },
                "SportStat": {
                    "type": "object",
                    "properties": {
                        "sport": {"type": "string"}, "requests": {"type": "integer"}, "avg_score": {"type": "number"}
                    }
                },
                "TopProp": {
                    "type": "object",
                    "properties": {
                        "player": {"type": "string"}, "sport": {"type": "string"},
                        "prop": {"type": "string"}, "side": {"type": "string"},
                        "line": {"type": "number"}, "avg_score": {"type": "number"},
                        "times_scored": {"type": "integer"}
                    }
                },
                "RecentProp": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string"}, "player": {"type": "string"},
                        "sport": {"type": "string"}, "prop": {"type": "string"},
                        "side": {"type": "string"}, "line": {"type": "number"},
                        "score": {"type": "number"}
                    }
                },
                "StatsResponse": {
                    "type": "object",
                    "properties": {
                        "storage": {"type": "string"},
                        "window": {"type": "string", "nullable": True, "example": "L5"},
                        "filters": {"type": "object"},
                        "record_count": {"type": "integer", "description": "Records in the current window/filter"},
                        "total_request_count": {"type": "integer"},
                        "average_score": {"type": "number"},
                        "average_score_overall": {"type": "number"},
                        "max_score": {"type": "number"},
                        "min_score": {"type": "number"},
                        "over_count": {"type": "integer", "description": "Records with side = over/more"},
                        "under_count": {"type": "integer", "description": "Records with side = under/less"},
                        "over_average_score": {"type": "number", "nullable": True, "description": "Avg score for over/more records"},
                        "under_average_score": {"type": "number", "nullable": True, "description": "Avg score for under/less records"},
                        "average_score_by_sport": {"type": "array", "items": {"$ref": "#/components/schemas/SportStat"}},
                        "top_scored_props": {"type": "array", "items": {"$ref": "#/components/schemas/TopProp"}},
                        "over_top_props": {"type": "array", "description": "Top props for over/more side", "items": {"$ref": "#/components/schemas/TopProp"}},
                        "under_top_props": {"type": "array", "description": "Top props for under/less side", "items": {"$ref": "#/components/schemas/TopProp"}},
                        "most_recent_scored_props": {"type": "array", "items": {"$ref": "#/components/schemas/RecentProp"}}
                    }
                },
                "LeaderboardEntry": {
                    "type": "object",
                    "properties": {
                        "rank":             {"type": "integer", "example": 1},
                        "player":           {"type": "string",  "example": "Patrick Mahomes"},
                        "sport":            {"type": "string",  "example": "NFL"},
                        "prop":             {"type": "string",  "example": "passing_yards"},
                        "side":             {"type": "string",  "example": "over"},
                        "record_count":     {"type": "integer", "description": "Records in the window for this combo"},
                        "average_score":    {"type": "number",  "example": 87.4},
                        "max_score":        {"type": "number",  "example": 99.2},
                        "min_score":        {"type": "number",  "example": 71.6},
                        "latest_score":     {"type": "number",  "example": 93.1},
                        "latest_timestamp": {"type": "string",  "example": "2026-05-08T19:05:59+00:00"}
                    }
                },
                "LeaderboardResponse": {
                    "type": "object",
                    "properties": {
                        "storage":     {"type": "string"},
                        "window":      {"type": "string", "example": "L10"},
                        "limit":       {"type": "integer"},
                        "ranked_by":   {"type": "string", "example": "average_score DESC, latest_score DESC"},
                        "filters":     {"type": "object"},
                        "leaderboard": {"type": "array", "items": {"$ref": "#/components/schemas/LeaderboardEntry"}}
                    }
                }
            }
        }
    }
    return jsonify(schema)


_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "public")

# Top-level path segments that belong to the Flask API.
# The catch-all will NEVER serve HTML for these — it returns JSON 404 instead.
_API_PREFIXES = frozenset([
    "scan-results", "wow-daily-scan", "random-forest-score", "gpt-score",
    "request-log", "stats", "leaderboard", "health", "openapi", "openapi.json",
    "debug", "picks", "gpt-action-schema.json", "gpt-action-schema.yaml",
    "score", "scores",
])


@app.route("/debug/routes", methods=["GET"])
def debug_routes():
    """List all registered Flask routes — no auth required."""
    routes = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
        routes.append({
            "path":     rule.rule,
            "methods":  methods,
            "endpoint": rule.endpoint,
        })
    return jsonify({
        "route_count": len(routes),
        "routes": routes,
    })


@app.route("/picks", methods=["DELETE"])
def delete_picks():
    """
    Remove all scored records for a (player, sport, prop, side) combination.
    No API key required — this is an app-internal management endpoint.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    player = str(data.get("player", "")).strip()
    sport  = str(data.get("sport",  "")).strip()
    prop   = str(data.get("prop",   "")).strip()
    side   = str(data.get("side",   "")).strip().upper()

    if not all([player, sport, prop, side]):
        return jsonify({"error": "player, sport, prop, and side are all required"}), 422

    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM scoring_requests "
                    "WHERE LOWER(player) = LOWER(%s) AND sport = %s "
                    "AND LOWER(prop) = LOWER(%s) AND side = %s",
                    (player, sport, prop, side)
                )
                deleted = cur.rowcount
        conn.close()
    except Exception as exc:
        return jsonify({"error": "Database error", "detail": str(exc)}), 503

    return jsonify({"deleted": deleted, "player": player, "sport": sport,
                    "prop": prop, "side": side})


@app.route("/gpt-action-schema.json", methods=["GET"])
def gpt_action_schema():
    import json as _json
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpt-action-schema.json")
    with open(schema_path) as f:
        schema = _json.load(f)
    response = jsonify(schema)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.route("/gpt-action-schema.yaml", methods=["GET"])
def gpt_action_schema_yaml():
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpt-action-schema.yaml")
    return send_from_directory(
        os.path.dirname(schema_path),
        os.path.basename(schema_path),
        mimetype="text/yaml"
    )


@app.route("/analyze-board", methods=["POST"])
@require_api_key
def analyze_board():
    """
    POST /analyze-board

    Accepts a PrizePicks (or similar sportsbook) board screenshot and uses
    Claude vision to extract every visible player prop into structured JSON.

    Body (JSON — send one of image_base64 or image_url):
      image_base64  — base64-encoded image data (no data:// prefix)
      image_url     — publicly accessible image URL (alternative to base64)
      media_type    — "image/jpeg" | "image/png" | "image/webp"  (default: image/jpeg)
      sport         — optional sport hint: "NBA", "MLB", etc.
      platform      — optional platform hint: "PrizePicks", "Underdog", etc.

    Returns:
      ok            — true/false
      props         — array of extracted prop objects
      count         — number of props found
      model         — claude model used
      usage         — input/output token counts
    """
    if not _ANTHROPIC_AVAILABLE:
        return jsonify({"ok": False, "error": "anthropic package not installed"}), 503

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({
            "ok": False,
            "error": "ANTHROPIC_API_KEY secret is not set — add it in Replit Secrets",
        }), 503

    import base64 as _base64

    body         = request.get_json(silent=True) or {}
    form         = request.form
    image_base64 = None
    image_url    = None
    media_type   = body.get("media_type") or form.get("media_type", "image/jpeg")
    sport_hint   = body.get("sport")      or form.get("sport", "")
    platform     = body.get("platform")   or form.get("platform", "PrizePicks")

    # ── Strategy 1: any uploaded file (multipart/form-data) ──────────────────
    file = (request.files.get("image")
            or request.files.get("file")
            or request.files.get("screenshot")
            or (list(request.files.values())[0] if request.files else None))
    if file:
        file_bytes   = file.read()
        image_base64 = _base64.b64encode(file_bytes).decode("utf-8")
        mime         = (file.content_type or "").lower()
        if mime and mime not in ("application/octet-stream", ""):
            media_type = mime

    # ── Strategy 2: JSON body (any of several field names) ───────────────────
    if not image_base64 and not image_url:
        for key in ("image_b64", "image_base64", "imageBase64", "image_data",
                    "imageData", "data", "image", "screenshot"):
            val = body.get(key) or form.get(key)
            if val and isinstance(val, str) and len(val) > 100:
                image_base64 = val
                break

    # ── Strategy 3: JSON image_url ────────────────────────────────────────────
    if not image_base64:
        for key in ("image_url", "imageUrl", "url"):
            val = body.get(key) or form.get(key)
            if val and isinstance(val, str) and val.startswith("http"):
                image_url = val
                break

    # ── Strategy 4: raw binary body (Content-Type: image/*) ──────────────────
    if not image_base64 and not image_url:
        ct = request.content_type or ""
        if ct.startswith("image/"):
            raw = request.get_data()
            if raw:
                image_base64 = _base64.b64encode(raw).decode("utf-8")
                media_type   = ct.split(";")[0].strip()

    if not image_base64 and not image_url:
        # Return debug info so we can see exactly what arrived
        return jsonify({
            "ok":    False,
            "error": "No image found in request — see debug for what arrived",
            "debug": {
                "content_type":  request.content_type,
                "files_keys":    list(request.files.keys()),
                "form_keys":     list(form.keys()),
                "json_keys":     list(body.keys()),
                "data_bytes":    len(request.get_data()),
            },
        }), 422

    # Build Claude image content block
    if image_base64:
        # Strip data URL prefix if caller included it
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]

        # Auto-detect real media type from magic bytes — ignore what caller sent
        try:
            header = _base64.b64decode(image_base64[:16])
            if header[:8] == b'\x89PNG\r\n\x1a\n':
                media_type = "image/png"
            elif header[:2] == b'\xff\xd8':
                media_type = "image/jpeg"
            elif header[:4] == b'RIFF' and header[8:12] == b'WEBP':
                media_type = "image/webp"
            elif header[:6] in (b'GIF87a', b'GIF89a'):
                media_type = "image/gif"
        except Exception:
            pass

        # Compress large images — phone screenshots can be 1-3 MB which causes
        # slow/failed Anthropic calls. Resize to max 1024px and convert to JPEG.
        try:
            import io as _io
            from PIL import Image as _Image
            raw_bytes  = _base64.b64decode(image_base64)
            if len(raw_bytes) > 400_000:          # only compress if > 400 KB
                img = _Image.open(_io.BytesIO(raw_bytes)).convert("RGB")
                img.thumbnail((1024, 1024), _Image.LANCZOS)
                buf = _io.BytesIO()
                img.save(buf, format="JPEG", quality=85, optimize=True)
                image_base64 = _base64.b64encode(buf.getvalue()).decode("utf-8")
                media_type   = "image/jpeg"
        except Exception:
            pass  # if compression fails, send the original

        image_block = {
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": media_type,
                "data":       image_base64,
            },
        }
    else:
        image_block = {
            "type": "image",
            "source": {"type": "url", "url": image_url},
        }

    sport_line = f"Sport context hint: {sport_hint}\n" if sport_hint else ""
    prompt = f"""You are a sports prop extraction assistant. Analyze this {platform} board screenshot and extract every visible player prop.

{sport_line}For each prop on the board return a JSON array. Each element must have:
- "player":    full player name (string)
- "sport":     sport abbreviation — NBA, MLB, NFL, NHL, WNBA, etc.
- "prop":      stat category — e.g. "points", "rebounds", "assists", "hits", "pitcher strikeouts"
- "side":      "MORE" or "LESS" if shown, otherwise null
- "line":      numeric line/target value (number, not string)
- "platform":  platform name if visible, else "{platform}"

Return ONLY valid JSON — a single array with no markdown fences, no explanation.
If you cannot read the image or find no props, return [].

Example:
[
  {{"player": "Nikola Jokic", "sport": "NBA", "prop": "rebounds", "side": "MORE", "line": 12.5, "platform": "{platform}"}},
  {{"player": "Freddie Freeman", "sport": "MLB", "prop": "hits", "side": "MORE", "line": 1.5, "platform": "{platform}"}}
]"""

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    image_block,
                    {"type": "text", "text": prompt},
                ],
            }],
        )

        raw_text = message.content[0].text.strip() if message.content else "[]"

        # Parse JSON — fall back to regex extraction if model wrapped it
        try:
            props = json.loads(raw_text)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", raw_text, re.DOTALL)
            try:
                props = json.loads(match.group()) if match else []
            except Exception:
                props = []

        return jsonify({
            "ok":           True,
            "raw_response": raw_text,   # included for dashboard compatibility
            "props":        props,
            "count":        len(props),
            "model":        message.model,
            "usage": {
                "input_tokens":  message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        })

    except _anthropic.AuthenticationError:
        return jsonify({
            "ok": False,
            "error": "Invalid ANTHROPIC_API_KEY — check the value in Replit Secrets",
        }), 401
    except _anthropic.RateLimitError:
        return jsonify({"ok": False, "error": "Anthropic rate limit — retry shortly"}), 429
    except _anthropic.BadRequestError as e:
        return jsonify({"ok": False, "error": f"Bad request to Anthropic: {e}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/claude-proxy", methods=["POST"])
@require_api_key
def claude_proxy():
    """
    Transparent proxy to Anthropic's /v1/messages API.

    Exists so browser-based dashboards (e.g. GitHub Pages) can call Claude
    without hitting CORS or exposing the API key client-side. The request
    body is forwarded verbatim; the upstream JSON response and status code
    are returned unchanged.
    """
    import requests as _req

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({
            "ok": False,
            "error": "ANTHROPIC_API_KEY secret is not set — add it in Replit Secrets",
        }), 500

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"ok": False, "error": "Request body must be valid JSON"}), 400

    try:
        upstream = _req.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            },
            json=body,
            timeout=120,
        )
    except _req.Timeout:
        return jsonify({"ok": False, "error": "Anthropic request timed out"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"Upstream call failed: {e}"}), 502

    try:
        return jsonify(upstream.json()), upstream.status_code
    except ValueError:
        return (upstream.text, upstream.status_code,
                {"Content-Type": upstream.headers.get("Content-Type", "text/plain")})


# ── Shared helpers for soccer stats endpoints ─────────────────────────────────

_SOCCER_LEAGUE_MAP = {
    "epl": 39, "pl": 39, "premier-league": 39,
    "ucl": 2,  "cl": 2,  "champions-league": 2,
    "laliga": 140, "la-liga": 140, "liga": 140,
    "bundesliga": 78,
    "seriea": 135, "serie-a": 135,
    "ligue1": 61,  "ligue-1": 61,
    "mls": 253,
    "eredivisie": 88,
    "liga-nos": 94, "portugal": 94,
}

# ESPN public scoreboard slugs — used as a backup when api-football is quota'd
# or suspended. Keep aligned to keys in _SOCCER_LEAGUE_MAP.
_ESPN_LEAGUE_MAP = {
    "epl": "eng.1", "pl": "eng.1", "premier-league": "eng.1",
    "ucl": "uefa.champions", "cl": "uefa.champions", "champions-league": "uefa.champions",
    "laliga": "esp.1", "la-liga": "esp.1", "liga": "esp.1",
    "bundesliga": "ger.1",
    "seriea": "ita.1", "serie-a": "ita.1",
    "ligue1": "fra.1", "ligue-1": "fra.1",
    "mls": "usa.1",
    "eredivisie": "ned.1",
    "liga-nos": "por.1", "portugal": "por.1",
}

# Canonical league_key per api-football league_id — used so we always cache
# under one stable key regardless of which alias the caller used.
_LEAGUE_ID_CANONICAL = {
    39: "epl", 2: "ucl", 140: "laliga", 78: "bundesliga",
    135: "seriea", 61: "ligue1", 253: "mls", 88: "eredivisie", 94: "portugal",
}

def _canonical_league_key(key):
    lid = _SOCCER_LEAGUE_MAP.get(key)
    if lid is None:
        return key
    return _LEAGUE_ID_CANONICAL.get(lid, key)


def _espn_fetch_fixtures(date_str, league_keys):
    """Fetch fixtures for a date from ESPN's public scoreboard, normalized to
    our schema. Returns (fixtures_list, errors_list). IDs are stored as
    *negative* BIGINTs to avoid PK collision with positive api-football IDs in
    the shared soccer_fixtures_cache table.
    """
    import requests as _req
    fixtures = []
    errors   = []
    yyyymmdd = date_str.replace("-", "")
    seen_keys = set()
    for key in league_keys:
        slug = _ESPN_LEAGUE_MAP.get(key)
        if not slug or slug in seen_keys:
            continue
        seen_keys.add(slug)
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
        try:
            r = _req.get(url, params={"dates": yyyymmdd}, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            errors.append({"espn_league": key, "error": str(e)})
            continue

        season_year = (data.get("season") or {}).get("year")
        for ev in data.get("events", []) or []:
            try:
                fid_raw = ev.get("id")
                if fid_raw is None:
                    continue
                fid = -int(fid_raw)   # negate → namespace separation from api-football IDs
                comps = (ev.get("competitions") or [{}])[0]
                home = away = None
                for c in comps.get("competitors", []) or []:
                    side = c.get("homeAway")
                    name = (c.get("team") or {}).get("displayName")
                    if side == "home": home = name
                    elif side == "away": away = name
                status = ((ev.get("status") or {}).get("type") or {}).get("shortDetail") \
                         or ((ev.get("status") or {}).get("type") or {}).get("description")
                fixtures.append({
                    "fixture_id":    fid,
                    "commence_time": ev.get("date"),
                    "status":        status,
                    "venue":         (comps.get("venue") or {}).get("fullName"),
                    "league":        _canonical_league_key(key),
                    "league_id":     _SOCCER_LEAGUE_MAP.get(key) or 0,
                    "season":        season_year,
                    "home_team":     home,
                    "away_team":     away,
                })
            except Exception as e:
                errors.append({"espn_event": ev.get("id"), "error": str(e)})
    return fixtures, errors

# ── ESPN player-stats fallback (used when api-football is suspended/empty) ──
#
# ESPN's public site API is keyless, unmetered, and covers every league we
# care about. The trade-off vs api-football is that ESPN's overview endpoint
# only exposes a fixed set of season totals (starts, goals, assists, shots,
# shots-on-target, fouls, cards, offsides) — no passes/dribbles/tackles/rating
# breakdowns. That's still enough for the dashboard's prop-scoring needs.
#
# Flow:  search by name → pick best match in requested league →
#        fetch /athletes/{id}/overview → flatten statistics.splits into our
#        existing stats_list shape with unmapped fields set to None.

_ESPN_PLAYER_SEARCH_CACHE = {}                # (name_lower, league_slug) -> (ts, results)
_ESPN_PLAYER_SEARCH_TTL_SECONDS = 60 * 60      # 1 hour
_ESPN_PLAYER_SEARCH_LOCK = threading.Lock()


def _espn_search_player(name, preferred_league_slug=None):
    """Search ESPN for soccer players matching `name`. Returns
    (results_list, error_reason_or_None). Distinguishes between
    'no_candidate' (search worked, nothing matched) and HTTP/timeout/parse
    failures so callers can diagnose ESPN outages vs. typos."""
    import requests as _req, time as _time

    name_lower = (name or "").strip().lower()
    if not name_lower:
        return [], "empty_query"

    cache_key = (name_lower, preferred_league_slug or "")
    now = _time.time()
    with _ESPN_PLAYER_SEARCH_LOCK:
        hit = _ESPN_PLAYER_SEARCH_CACHE.get(cache_key)
        if hit and (now - hit[0]) < _ESPN_PLAYER_SEARCH_TTL_SECONDS:
            cached = hit[1]
            return cached, (None if cached else "no_candidate")

    url = "https://site.web.api.espn.com/apis/common/v3/search"
    try:
        # NB: `type=player` is silently ignored by ESPN unless `sport=soccer`
        # is also present; without both, the search returns teams + leagues.
        r = _req.get(url, params={"limit": 15, "query": name,
                                  "type": "player", "sport": "soccer"}, timeout=10)
    except _req.exceptions.Timeout:
        return [], "search_timeout"
    except _req.exceptions.RequestException as e:
        return [], f"search_network_error: {e}"
    if r.status_code != 200:
        return [], f"search_http_{r.status_code}"
    try:
        data = r.json()
    except ValueError:
        return [], "search_parse_error"

    results = []
    for item in data.get("items", []) or []:
        if (item.get("sport") or "").lower() != "soccer":
            continue
        results.append({
            "id":          item.get("id"),
            "name":        item.get("displayName"),
            "league_slug": item.get("league"),
            "jersey":      item.get("jersey"),
        })

    if preferred_league_slug:
        results.sort(key=lambda r: 0 if r.get("league_slug") == preferred_league_slug else 1)

    with _ESPN_PLAYER_SEARCH_LOCK:
        _ESPN_PLAYER_SEARCH_CACHE[cache_key] = (now, results)
    return results, (None if results else "no_candidate")


def _espn_player_overview(league_slug, athlete_id):
    """Fetch ESPN athlete overview. Returns (dict_or_None, error_or_None)."""
    import requests as _req
    url = f"https://site.web.api.espn.com/apis/common/v3/sports/soccer/{league_slug}/athletes/{athlete_id}/overview"
    try:
        r = _req.get(url, timeout=10)
    except _req.exceptions.Timeout:
        return None, "overview_timeout"
    except _req.exceptions.RequestException as e:
        return None, f"overview_network_error: {e}"
    if r.status_code != 200:
        return None, f"overview_http_{r.status_code}"
    try:
        return r.json(), None
    except ValueError:
        return None, "overview_parse_error"


# Map ESPN canonical stat names → our flatten_soccer_stats keys. ESPN does NOT
# expose passes/dribbles/tackles/rating/penalty/minutes/position breakdowns in
# the overview endpoint, so those stay None when ESPN is the source.
_ESPN_STAT_NAME_MAP = {
    "totalGoals":      "goals",
    "goalAssists":     "assists",
    "totalShots":      "shots_total",
    "shotsOnTarget":   "shots_on",
    "yellowCards":     "yellow_cards",
    "redCards":        "red_cards",
    "starts":          "appearances",
    "foulsCommitted":  "fouls_committed",
    "foulsSuffered":   "fouls_suffered",
    "offsides":        "offsides",
}


def _normalize_espn_stats(overview, preferred_league_slug=None):
    """Convert ESPN overview.statistics.splits into a list of stats dicts in the
    same shape /fbref-stats already returns. Splits matching the preferred
    league slug come first. Returns (stats_list, player_meta_dict)."""
    if not overview:
        return [], {}

    stats_block = overview.get("statistics") or {}
    names = stats_block.get("names") or []
    splits = stats_block.get("splits") or []

    # Build per-split dicts keyed by our internal names
    out = []
    for sp in splits:
        if not isinstance(sp, dict):
            continue
        raw = sp.get("stats") or []
        per_stat = {}
        for n, v in zip(names, raw):
            mapped = _ESPN_STAT_NAME_MAP.get(n)
            if not mapped:
                continue
            try:
                per_stat[mapped] = float(v) if v not in (None, "", "-") else None
            except (ValueError, TypeError):
                per_stat[mapped] = v

        # Cast counts to ints where safe
        for ik in ("goals", "assists", "shots_total", "shots_on",
                   "yellow_cards", "red_cards", "appearances",
                   "fouls_committed", "fouls_suffered", "offsides"):
            v = per_stat.get(ik)
            if isinstance(v, float) and v.is_integer():
                per_stat[ik] = int(v)

        # Always-null fields (ESPN doesn't provide these)
        for nk in ("minutes", "position", "rating",
                   "passes_total", "passes_key",
                   "dribbles_attempts", "dribbles_success",
                   "tackles", "penalties_scored", "penalties_missed"):
            per_stat.setdefault(nk, None)

        per_stat["team"]      = sp.get("teamSlug") or sp.get("displayName")
        per_stat["team_id"]   = sp.get("teamId")
        per_stat["league"]    = sp.get("displayName")
        per_stat["league_id"] = sp.get("leagueId")
        per_stat["league_slug"] = sp.get("leagueSlug")
        per_stat["season"]    = sp.get("displayName")  # ESPN bakes season into displayName

        out.append(per_stat)

    if preferred_league_slug:
        out.sort(key=lambda s: 0 if s.get("league_slug") == preferred_league_slug else 1)

    return out, {}


def _try_espn_player_stats(player, league_key):
    """Search ESPN by name, fetch overview for best match, return
    (response_dict_or_None, espn_diagnostics_list). The diagnostics list
    explains why fallback came up empty (search_timeout, no_candidate,
    overview_http_404, overview_no_stats, etc.) so callers can surface a
    meaningful error instead of a generic 'not found'."""
    diagnostics = []
    preferred_slug = _ESPN_LEAGUE_MAP.get((league_key or "").lower())
    candidates, search_err = _espn_search_player(player, preferred_league_slug=preferred_slug)
    if search_err:
        diagnostics.append({"stage": "search", "reason": search_err})
    if not candidates:
        return None, diagnostics

    # Try the top candidate; if its overview has no stats, fall back to next.
    for cand in candidates[:5]:
        cand_slug = cand.get("league_slug") or preferred_slug or "eng.1"
        cand_id   = cand.get("id")
        if not cand_id:
            continue
        overview, ov_err = _espn_player_overview(cand_slug, cand_id)
        if ov_err:
            diagnostics.append({"stage": "overview", "id": cand_id, "reason": ov_err})
            continue
        stats_list, _ = _normalize_espn_stats(overview, preferred_league_slug=preferred_slug)
        if not stats_list:
            diagnostics.append({"stage": "overview", "id": cand_id, "reason": "overview_no_stats"})
            continue

        return {
            "ok":          True,
            "source":      "espn-fallback",
            "cache_hit":   False,
            "player": {
                "id":          cand_id,
                "name":        cand.get("name"),
                "age":         None,
                "nationality": None,
                "height":      None,
                "weight":      None,
                "photo":       f"https://a.espncdn.com/i/headshots/soccer/players/full/{cand_id}.png",
            },
            "season":         None,   # ESPN bakes season into per-split displayName
            "league_filter":  _SOCCER_LEAGUE_MAP.get((league_key or "").lower()),
            "stats":          stats_list,
            "count":          len(stats_list),
            "fallback_note":  "api-football unavailable or empty; data sourced from ESPN public API (subset of fields)",
        }, diagnostics

    return None, diagnostics


# ─────────────────────────── ESPN BASKETBALL FALLBACK ────────────────────────
# Same pattern as the soccer fallback: search ESPN public APIs by player name,
# pull the athlete overview, normalize stat names. Covers NBA + WNBA when the
# api-sports.io basketball endpoint is unavailable (account suspended, key
# missing, validation errors, etc.).

# api-sports basketball league ids: 12=NBA, 13=WNBA. Accept either form.
_ESPN_BBALL_LEAGUE_MAP = {
    "nba":  "nba",  "wnba": "wnba",
    "12":   "nba",  "13":   "wnba",
    12:     "nba",  13:     "wnba",
}

# Map ESPN basketball stat names → our normalized keys. Same vocabulary for
# NBA and WNBA — only the order in `names[]` differs, and we align by index.
_ESPN_BBALL_STAT_NAME_MAP = {
    "gamesPlayed":     "games",
    "avgMinutes":      "minutes_per_game",
    "avgPoints":       "points_per_game",
    "avgRebounds":     "rebounds_per_game",
    "avgAssists":      "assists_per_game",
    "avgSteals":       "steals_per_game",
    "avgBlocks":       "blocks_per_game",
    "avgTurnovers":    "turnovers_per_game",
    "avgFouls":        "fouls_per_game",
    "fieldGoalPct":    "field_goal_pct",
    "threePointPct":   "three_point_pct",
    "freeThrowPct":    "free_throw_pct",
}


def _espn_basketball_search(name, preferred_league_slug=None):
    """Search ESPN basketball players. Returns (results, error_or_None).
    Each result: {id, name, league_slug, jersey}. preferred_league_slug is
    'nba' or 'wnba' — matches are sorted to that league first."""
    import requests as _req, time as _time

    name_lower = (name or "").strip().lower()
    if not name_lower:
        return [], "empty_query"

    cache_key = ("bball:" + name_lower, preferred_league_slug or "")
    now = _time.time()
    with _ESPN_PLAYER_SEARCH_LOCK:
        hit = _ESPN_PLAYER_SEARCH_CACHE.get(cache_key)
        if hit and (now - hit[0]) < _ESPN_PLAYER_SEARCH_TTL_SECONDS:
            cached = hit[1]
            return cached, (None if cached else "no_candidate")

    url = "https://site.web.api.espn.com/apis/common/v3/search"
    try:
        r = _req.get(url, params={"limit": 15, "query": name,
                                  "type": "player", "sport": "basketball"},
                     timeout=10)
    except _req.exceptions.Timeout:
        return [], "search_timeout"
    except _req.exceptions.RequestException as e:
        return [], f"search_network_error: {e}"
    if r.status_code != 200:
        return [], f"search_http_{r.status_code}"
    try:
        data = r.json()
    except ValueError:
        return [], "search_parse_error"

    results = []
    for item in data.get("items", []) or []:
        if (item.get("sport") or "").lower() != "basketball":
            continue
        results.append({
            "id":          item.get("id"),
            "name":        item.get("displayName"),
            "league_slug": item.get("league"),
            "jersey":      item.get("jersey"),
        })

    if preferred_league_slug:
        results.sort(key=lambda r: 0 if r.get("league_slug") == preferred_league_slug else 1)

    with _ESPN_PLAYER_SEARCH_LOCK:
        _ESPN_PLAYER_SEARCH_CACHE[cache_key] = (now, results)
    return results, (None if results else "no_candidate")


def _espn_basketball_overview(league_slug, athlete_id):
    """Fetch ESPN basketball athlete overview. Returns (dict, error_or_None).
    league_slug is 'nba' or 'wnba'."""
    import requests as _req
    url = f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/{league_slug}/athletes/{athlete_id}/overview"
    try:
        r = _req.get(url, timeout=10)
    except _req.exceptions.Timeout:
        return None, "overview_timeout"
    except _req.exceptions.RequestException as e:
        return None, f"overview_network_error: {e}"
    if r.status_code != 200:
        return None, f"overview_http_{r.status_code}"
    try:
        return r.json(), None
    except ValueError:
        return None, "overview_parse_error"


def _normalize_espn_basketball_stats(overview):
    """Convert ESPN overview['statistics'] into a flat list of split dicts.
    Each split: {split_name, games, points_per_game, ...}. ESPN exposes
    'Regular Season', 'Postseason', 'Career' for NBA; 'Regular Season',
    'Career' for WNBA."""
    stats = (overview or {}).get("statistics") or {}
    names = stats.get("names") or []
    splits = stats.get("splits") or []

    out = []
    for split in splits:
        values = split.get("stats") or []
        rec = {"split_name": split.get("displayName")}
        for idx, espn_key in enumerate(names):
            our_key = _ESPN_BBALL_STAT_NAME_MAP.get(espn_key)
            if not our_key or idx >= len(values):
                continue
            raw = values[idx]
            # ESPN returns stats as strings; coerce numerics where possible.
            try:
                rec[our_key] = float(raw) if "." in str(raw) else int(raw)
            except (TypeError, ValueError):
                rec[our_key] = raw
        out.append(rec)
    return out


def _try_espn_basketball_player_search(name, league_hint=None):
    """High-level: search ESPN basketball by name. Returns
    (list_of_player_dicts, diagnostics). Each player dict mirrors enough of
    the api-sports.io /players shape that downstream consumers can read it:
        {id, name, league, team: {name: league_slug}, photo}
    """
    diagnostics = []
    preferred = _ESPN_BBALL_LEAGUE_MAP.get(league_hint) if league_hint else None
    candidates, search_err = _espn_basketball_search(name, preferred_league_slug=preferred)
    if search_err:
        diagnostics.append({"stage": "search", "reason": search_err})

    players = []
    for c in candidates:
        league_slug = c.get("league_slug")
        if league_slug not in ("nba", "wnba"):
            continue   # filter out NCAA / international clutter
        cid = c.get("id")
        players.append({
            "id":     cid,
            "name":   c.get("name"),
            "league": league_slug.upper(),
            "team":   {"name": None},
            "jersey": c.get("jersey"),
            "photo":  f"https://a.espncdn.com/i/headshots/{league_slug}/players/full/{cid}.png",
        })
    return players, diagnostics


def _try_espn_basketball_stats(player_id=None, player_name=None, league_hint=None):
    """High-level: pull season stats from ESPN. Caller may supply an ESPN
    athlete id directly, or a player name (which we search first). Returns
    (list_of_split_records, player_dict_or_None, diagnostics)."""
    diagnostics = []
    preferred_slug = _ESPN_BBALL_LEAGUE_MAP.get(league_hint) if league_hint else None

    # Resolve to (athlete_id, league_slug, player_name)
    resolved = []
    if player_id:
        # Try the hinted league first, then the other. ESPN ids are unique
        # across NBA + WNBA so at most one will succeed.
        slugs_to_try = [preferred_slug] if preferred_slug else []
        for s in ("nba", "wnba"):
            if s not in slugs_to_try:
                slugs_to_try.append(s)
        for slug in slugs_to_try:
            resolved.append((player_id, slug, None))
    elif player_name:
        candidates, search_err = _espn_basketball_search(player_name, preferred_league_slug=preferred_slug)
        if search_err:
            diagnostics.append({"stage": "search", "reason": search_err})
        for c in candidates[:5]:
            if c.get("league_slug") in ("nba", "wnba") and c.get("id"):
                resolved.append((c["id"], c["league_slug"], c.get("name")))
    else:
        return [], None, [{"stage": "input", "reason": "need player_id or player_name"}]

    for athlete_id, slug, name in resolved:
        overview, ov_err = _espn_basketball_overview(slug, athlete_id)
        if ov_err:
            diagnostics.append({"stage": "overview", "id": athlete_id, "slug": slug, "reason": ov_err})
            continue
        splits = _normalize_espn_basketball_stats(overview)
        if not splits:
            diagnostics.append({"stage": "overview", "id": athlete_id, "slug": slug, "reason": "overview_no_stats"})
            continue
        player_dict = {
            "id":     athlete_id,
            "name":   name or (overview.get("athlete") or {}).get("displayName"),
            "league": slug.upper(),
            "photo":  f"https://a.espncdn.com/i/headshots/{slug}/players/full/{athlete_id}.png",
        }
        return splits, player_dict, diagnostics

    return [], None, diagnostics


def _is_api_sports_suspended_or_auth_error(upstream_errors):
    """Detect when api-sports.io is returning an account-level failure
    (suspended, invalid key, etc.) vs. a recoverable validation error. We
    only fall back to ESPN for the former — validation errors should still
    surface so callers fix their query.

    We deliberately require explicit auth-context tokens in the value (e.g.
    'suspend', 'invalid key', 'quota') rather than weak words like 'access'
    alone, since api-sports also returns validation errors keyed by 'access'
    in some cases. The 'access' KEY is treated as auth-context only when
    paired with a value containing one of those tokens."""
    if not upstream_errors or not isinstance(upstream_errors, dict):
        return False
    AUTH_TOKENS = (
        "suspend", "subscribe", "missing key", "invalid key",
        "rate limit", "quota", "not active", "account is",
        "unauthorized", "forbidden", "dashboard.api-football.com",
    )
    for _key, val in upstream_errors.items():
        v = str(val).lower()
        if any(tok in v for tok in AUTH_TOKENS):
            return True
    return False


def _soccer_api_get(path, params, api_key):
    import requests as _req
    base = "https://v3.football.api-sports.io"
    hdrs = {"x-apisports-key": api_key}
    r = _req.get(f"{base}/{path}", headers=hdrs, params=params, timeout=15)
    r.raise_for_status()
    d = r.json()
    errs = d.get("errors")
    if errs and errs != [] and errs != {}:
        msg = list(errs.values())[0] if isinstance(errs, dict) else str(errs)
        raise ValueError(f"api-football: {msg}")
    return d

def _flatten_soccer_stats(s):
    return {
        "team":              s["team"]["name"],
        "team_id":           s["team"]["id"],
        "league":            s["league"]["name"],
        "league_id":         s["league"]["id"],
        "season":            s["league"]["season"],
        "appearances":       s["games"]["appearences"],
        "minutes":           s["games"]["minutes"],
        "position":          s["games"]["position"],
        "rating":            s["games"].get("rating"),
        "goals":             s["goals"]["total"],
        "assists":           s["goals"]["assists"],
        "shots_total":       s["shots"]["total"],
        "shots_on":          s["shots"]["on"],
        "passes_total":      s["passes"]["total"],
        "passes_key":        s["passes"]["key"],
        "dribbles_attempts": s["dribbles"]["attempts"],
        "dribbles_success":  s["dribbles"]["success"],
        "tackles":           s["tackles"]["total"],
        "yellow_cards":      s["cards"]["yellow"],
        "red_cards":         s["cards"]["red"],
        "penalties_scored":  s["penalty"]["scored"],
        "penalties_missed":  s["penalty"]["missed"],
    }

def _get_cached_player_id(conn, player_lower, leagues_to_try, season):
    """Return (player_id, full_name) from cache or (None, None) on miss.

    Matches on three patterns in priority order:
      1. Exact player_name_lower match (handles abbreviated names stored by populate)
      2. Full search string anywhere in full_name  ("erling haaland" in "Erling Braut Haaland")
      3. Last token of search string in full_name  ("haaland" in "E. Haaland")
    """
    if not conn:
        return None, None
    try:
        # Last word of the search (usually the surname)
        last_token = player_lower.split()[-1] if player_lower.split() else player_lower
        ph = ",".join(["%s"] * len(leagues_to_try))
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT player_id, full_name FROM soccer_player_cache
                    WHERE league_id IN ({ph})
                      AND season = %s
                      AND (
                          player_name_lower = %s
                          OR LOWER(full_name) LIKE %s
                          OR LOWER(full_name) LIKE %s
                      )
                    ORDER BY
                      CASE WHEN player_name_lower = %s      THEN 0
                           WHEN LOWER(full_name) LIKE %s    THEN 1
                           ELSE 2 END
                    LIMIT 1""",
                leagues_to_try + [season,
                 player_lower,
                 f"%{player_lower}%",
                 f"%{last_token}%",
                 player_lower,
                 f"%{player_lower}%"],
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else (None, None)
    except Exception:
        return None, None

def _write_player_cache(conn, player_lower, league_id, season, player_id, full_name):
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO soccer_player_cache
                   (player_name_lower, league_id, season, player_id, full_name)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (player_name_lower, league_id, season)
                   DO UPDATE SET player_id=EXCLUDED.player_id,
                                 full_name=EXCLUDED.full_name,
                                 cached_at=NOW()""",
                [player_lower, league_id, season, player_id, full_name],
            )
        conn.commit()
    except Exception:
        pass


@app.route("/fbref-stats", methods=["GET"])
@require_api_key
def fbref_stats():
    import psycopg2 as _pg

    # api-football free plan only exposes seasons 2022-2024. Cap our default
    # at 2024 so calls don't 4xx with "Free plans do not have access to this
    # season" even though the real-world current season is 2025+.
    _FREE_PLAN_MAX_SEASON = 2024
    from datetime import date as _date
    _today = _date.today()
    _computed = _today.year if _today.month >= 8 else _today.year - 1
    _default_season = str(min(_computed, _FREE_PLAN_MAX_SEASON))

    player = request.args.get("player", "").strip()
    league = request.args.get("league", "").strip().lower()
    season = request.args.get("season", _default_season).strip()

    if not player:
        return jsonify({"ok": False, "error": "Missing required param: player"}), 400

    # api-football key is now soft-optional. When missing, we skip api-football
    # entirely and go straight to ESPN — keeps the endpoint usable in keyless
    # / suspended-account scenarios.
    api_key = os.environ.get("API_FOOTBALL_KEY", "")

    league_id     = _SOCCER_LEAGUE_MAP.get(league)
    leagues_to_try = [league_id] if league_id else [39, 140, 78, 135, 61]
    player_lower  = player.lower()

    db_url = os.environ.get("DATABASE_URL", "")
    conn   = _pg.connect(db_url) if db_url else None

    def _espn_or_404(reason):
        """Try ESPN fallback. On hit, return jsonified response. On miss,
        surface specific ESPN diagnostics so outages can be distinguished from
        true 'player not found' cases."""
        espn_response, espn_diag = _try_espn_player_stats(player, league)
        if espn_response:
            return jsonify(espn_response)
        # Classify the ESPN miss for the caller
        if any(d.get("reason", "").startswith(("search_timeout", "search_network",
                                               "search_http", "overview_timeout",
                                               "overview_network", "overview_http",
                                               "search_parse", "overview_parse"))
               for d in espn_diag):
            espn_summary = "ESPN unreachable or returned non-200 — see espn_diagnostics."
        else:
            espn_summary = "ESPN returned no matching soccer player — check spelling."
        return jsonify({
            "ok":    False,
            "error": f"Player '{player}' not available from any source.",
            "tried": ["api-football", "espn-fallback"],
            "api_football_reason": reason,
            "espn_summary": espn_summary,
            "espn_diagnostics": espn_diag,
            "hint":  (
                "Verify spelling, try a fuller name (e.g. 'bukayo saka' not 'saka'), "
                "or call POST /fbref-stats/populate to refresh the api-football "
                "cache (requires API_FOOTBALL_KEY to be active)."
            ),
            "cache_populated": False,
        }), 404

    # If no api-football key, route directly to ESPN.
    if not api_key:
        try:
            return _espn_or_404("API_FOOTBALL_KEY not configured")
        finally:
            if conn:
                conn.close()

    # Narrow set of errors we treat as "api-football is broken, try ESPN".
    # Anything outside this set is a real bug and should bubble up as 500.
    import requests as _req
    _AF_FALLBACK_ERRORS = (ValueError, _req.exceptions.RequestException)

    try:
        # ── 1. Cache hit → 1 API call, instant ───────────────────────────────
        cached_id, cached_name = _get_cached_player_id(conn, player_lower, leagues_to_try, season)

        if cached_id:
            try:
                data = _soccer_api_get("players", {"id": cached_id, "season": season}, api_key)
            except _AF_FALLBACK_ERRORS as af_err:
                # api-football errored on a cached id (e.g. suspended account).
                return _espn_or_404(f"api-football error on cached id: {af_err}")
            if not data.get("response"):
                # Cache resolved id but api-football has no stats — try ESPN.
                return _espn_or_404("api-football returned empty response for cached id")
            entry = data["response"][0]
            cache_hit = True
        else:
            # ── 2. Cache miss: caller may pass a numeric ID directly ──────────
            entry = None
            found_league_id = None

            if player_lower.isdigit():
                try:
                    data = _soccer_api_get("players", {"id": int(player_lower), "season": season}, api_key)
                    if data.get("response"):
                        entry = data["response"][0]
                        found_league_id = leagues_to_try[0]
                except _AF_FALLBACK_ERRORS as af_err:
                    return _espn_or_404(f"api-football error on numeric id: {af_err}")

            if not entry:
                # ── 3. Not in cache and no ID → ESPN is our best free option ──
                return _espn_or_404("player not in api-football cache; no numeric id supplied")

            found_league_id = found_league_id or leagues_to_try[0]
            pid       = entry["player"]["id"]
            full_name = (entry["player"]["firstname"] + " " + entry["player"]["lastname"]).strip()
            _write_player_cache(conn, player_lower, found_league_id, season, pid, full_name)
            cache_hit = False

        pinfo      = entry["player"]
        stats_list = [_flatten_soccer_stats(s) for s in entry.get("statistics", [])]

        return jsonify({
            "ok":      True,
            "source":  "api-football",
            "cache_hit": cache_hit,
            "player": {
                "id":          pinfo["id"],
                "name":        (pinfo["firstname"] + " " + pinfo["lastname"]).strip(),
                "age":         pinfo["age"],
                "nationality": pinfo["nationality"],
                "height":      pinfo["height"],
                "weight":      pinfo["weight"],
                "photo":       pinfo["photo"],
            },
            "season":        season,
            "league_filter": league_id,
            "stats":         stats_list,
            "count":         len(stats_list),
        })

    except _AF_FALLBACK_ERRORS as e:
        # Known api-football failure mode (suspended/quota'd/network) → ESPN.
        # Unexpected exceptions (KeyError, TypeError, AttributeError, etc.)
        # propagate as 500 so genuine regressions stay visible.
        return _espn_or_404(f"api-football error: {e}")
    finally:
        if conn:
            conn.close()


# ── Soccer fixtures cache (write-through Postgres) ──────────────────────────
# Strategy: GET /fbref-stats/fixtures serves from `soccer_fixtures_cache`
# when fresh rows exist for the requested date. POST /fbref-stats/fixtures/refresh
# crawls a date range from api-football and upserts; designed to be hit by an
# external scheduler (e.g. Replit Scheduled Deployment) on a daily cadence.

_FIXTURES_SCHEMA_LOCK = threading.Lock()
_FIXTURES_SCHEMA_READY = False
_FIXTURES_REFRESH_LOCK = threading.Lock()
_FIXTURES_REFRESH_STATE = {"running": False, "started_at": None, "range": None}


def _ensure_fixtures_schema(conn):
    global _FIXTURES_SCHEMA_READY
    if _FIXTURES_SCHEMA_READY:
        return
    with _FIXTURES_SCHEMA_LOCK:
        if _FIXTURES_SCHEMA_READY:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS soccer_fixtures_cache (
                    fixture_id     BIGINT PRIMARY KEY,
                    fixture_date   DATE NOT NULL,
                    commence_time  TIMESTAMPTZ,
                    status         TEXT,
                    venue          TEXT,
                    league_key     TEXT NOT NULL,
                    league_id      INTEGER NOT NULL,
                    season         INTEGER,
                    home_team      TEXT,
                    away_team      TEXT,
                    refreshed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS soccer_fixtures_date_idx
                    ON soccer_fixtures_cache (fixture_date);
                CREATE INDEX IF NOT EXISTS soccer_fixtures_league_idx
                    ON soccer_fixtures_cache (league_key);
            """)
            conn.commit()
        _FIXTURES_SCHEMA_READY = True


def _fixtures_row_to_dict(r):
    return {
        "fixture_id":    int(r["fixture_id"]),
        "commence_time": r["commence_time"].isoformat() if r["commence_time"] else None,
        "status":        r["status"],
        "venue":         r["venue"],
        "league":        r["league_key"],
        "league_id":     int(r["league_id"]),
        "season":        int(r["season"]) if r["season"] is not None else None,
        "home_team":     r["home_team"],
        "away_team":     r["away_team"],
    }


def _crawl_fixtures_into_cache(api_key, date_strs, db_url, league_keys=None):
    """Fetch fixtures for each date and upsert into soccer_fixtures_cache.
    Tries api-football first; falls back to ESPN public scoreboard per date
    when api-football errors (suspended/quota/network) or returns empty.
    Returns (inserted, updated, errors)."""
    import psycopg2 as _pg, time as _t

    if not league_keys:
        league_keys = sorted({k for k in _SOCCER_LEAGUE_MAP.keys()
                              if k in _ESPN_LEAGUE_MAP})

    inserted = updated = 0
    errors = []

    def _upsert(cur, date_str, rec):
        nonlocal inserted, updated
        cur.execute("""
            INSERT INTO soccer_fixtures_cache
                (fixture_id, fixture_date, commence_time, status, venue,
                 league_key, league_id, season, home_team, away_team, refreshed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (fixture_id) DO UPDATE SET
                commence_time = EXCLUDED.commence_time,
                status        = EXCLUDED.status,
                venue         = EXCLUDED.venue,
                league_key    = EXCLUDED.league_key,
                league_id     = EXCLUDED.league_id,
                season        = EXCLUDED.season,
                home_team     = EXCLUDED.home_team,
                away_team     = EXCLUDED.away_team,
                refreshed_at  = NOW()
            RETURNING (xmax = 0) AS was_insert
        """, (
            rec["fixture_id"], date_str, rec["commence_time"],
            rec["status"], rec["venue"],
            rec["league"], rec["league_id"], rec["season"],
            rec["home_team"], rec["away_team"],
        ))
        row = cur.fetchone()
        if row and row[0]:
            inserted += 1
        else:
            updated += 1

    conn = _pg.connect(db_url)
    try:
        _ensure_fixtures_schema(conn)
        with conn.cursor() as cur:
            for date_str in date_strs:
                af_records = []
                af_failed  = False
                try:
                    data = _soccer_api_get("fixtures", {"date": date_str}, api_key) if api_key else {"response": []}
                    if api_key:
                        upstream_errors = data.get("errors")
                        if isinstance(upstream_errors, dict) and upstream_errors:
                            errors.append({"date": date_str, "upstream": upstream_errors})
                            af_failed = True
                        for f in data.get("response", []):
                            lg = f.get("league", {}) or {}
                            lid = lg.get("id")
                            league_key = _LEAGUE_ID_CANONICAL.get(lid)
                            if not league_key:
                                continue
                            fx    = f.get("fixture", {}) or {}
                            teams = f.get("teams",   {}) or {}
                            af_records.append({
                                "fixture_id": fx.get("id"),
                                "commence_time": fx.get("date"),
                                "status": (fx.get("status") or {}).get("short"),
                                "venue":  (fx.get("venue")  or {}).get("name"),
                                "league": league_key,
                                "league_id": lid,
                                "season": lg.get("season"),
                                "home_team": (teams.get("home") or {}).get("name"),
                                "away_team": (teams.get("away") or {}).get("name"),
                            })
                except Exception as e:
                    errors.append({"date": date_str, "apifootball_error": str(e)})
                    af_failed = True

                # Fallback to ESPN if api-football errored or returned nothing.
                if af_failed or not af_records:
                    espn_records, espn_errs = _espn_fetch_fixtures(date_str, league_keys)
                    if espn_errs:
                        errors.extend([{"date": date_str, **e} for e in espn_errs])
                    records = espn_records
                else:
                    records = af_records

                for rec in records:
                    try:
                        _upsert(cur, date_str, rec)
                    except Exception as e:
                        errors.append({"fixture_id": rec.get("fixture_id"), "error": str(e)})

                conn.commit()
                _t.sleep(6.5)   # rate-limit gap (covers api-football 10 req/min)
    finally:
        conn.close()
    return inserted, updated, errors


@app.route("/fbref-stats/fixtures", methods=["GET"])
@require_api_key
def fbref_stats_fixtures():
    """
    Return soccer fixtures for a given date across the configured leagues
    (default: EPL + MLS). Reads from soccer_fixtures_cache when populated;
    falls back to a live api-football call (and write-through caches) when
    the cache is empty for that date.

    Query params:
      date     — YYYY-MM-DD (default today, UTC)
      leagues  — comma-separated league keys (default "epl,mls")
      fresh    — '1' to force a live fetch + cache refresh, bypassing cache

    Response: { ok, date, count, fixtures: [...], errors, source }
    """
    from datetime import date as _date, datetime as _dt, timezone as _tz
    import psycopg2 as _pg, psycopg2.extras as _pgx

    # api_key is optional — when missing, fall straight to ESPN.
    api_key = os.environ.get("API_FOOTBALL_KEY", "")

    date_str = (request.args.get("date") or _dt.now(_tz.utc).date().isoformat()).strip()
    try:
        _date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400

    raw_leagues = (request.args.get("leagues") or "epl,mls").strip()
    league_keys = [s.strip().lower() for s in raw_leagues.split(",") if s.strip()]
    fresh_flag  = request.args.get("fresh", "").strip() == "1"

    wanted_ids = {}
    unknown    = []
    for key in league_keys:
        lid = _SOCCER_LEAGUE_MAP.get(key)
        if lid:
            wanted_ids[lid] = _LEAGUE_ID_CANONICAL.get(lid, key)
        else:
            unknown.append(key)
    errors = [{"league": k, "error": "unknown league key"} for k in unknown]
    # Canonicalize requested league keys for cache reads so an alias request
    # (e.g. ?leagues=ucl) still hits rows stored under the canonical key.
    canonical_keys = sorted({_canonical_league_key(k) for k in league_keys if k in _SOCCER_LEAGUE_MAP})

    db_url = os.environ.get("DATABASE_URL", "")
    fixtures_out = []
    source = None

    # 1) Try cache first (unless fresh=1)
    if db_url and not fresh_flag:
        try:
            conn = _pg.connect(db_url)
            try:
                _ensure_fixtures_schema(conn)
                with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT * FROM soccer_fixtures_cache
                        WHERE fixture_date = %s AND league_key = ANY(%s)
                        ORDER BY commence_time
                    """, (date_str, canonical_keys or league_keys))
                    rows = cur.fetchall()
                    fixtures_out = [_fixtures_row_to_dict(r) for r in rows]
                if fixtures_out:
                    source = "cache"
            finally:
                conn.close()
        except Exception as e:
            errors.append({"cache": str(e)})

    # 2) Cache miss (or fresh=1) → live fetch.
    #    Try api-football first (if key present); fall back to ESPN public
    #    scoreboard if api-football errors (suspended/quota) or returns nothing.
    if not fixtures_out:
        af_failed = api_key == ""    # treat missing key as "skip api-football"
        if api_key:
            try:
                data = _soccer_api_get("fixtures", {"date": date_str}, api_key)
                upstream_errors = data.get("errors")
                if isinstance(upstream_errors, dict) and upstream_errors:
                    errors.append({"upstream": upstream_errors})
                    af_failed = True
                for f in data.get("response", []):
                    lg = f.get("league", {}) or {}
                    lid = lg.get("id")
                    if lid not in wanted_ids:
                        continue
                    fx    = f.get("fixture", {}) or {}
                    teams = f.get("teams",   {}) or {}
                    fixtures_out.append({
                        "fixture_id":    fx.get("id"),
                        "commence_time": fx.get("date"),
                        "status":        (fx.get("status") or {}).get("short"),
                        "venue":         (fx.get("venue") or {}).get("name"),
                        "league":        wanted_ids[lid],
                        "league_id":     lid,
                        "season":        lg.get("season"),
                        "home_team":     (teams.get("home") or {}).get("name"),
                        "away_team":     (teams.get("away") or {}).get("name"),
                    })
                if fixtures_out:
                    source = "live-apifootball"
            except Exception as e:
                errors.append({"apifootball_error": str(e)})
                af_failed = True

        # Fall back to ESPN when api-football skipped/failed/empty.
        if af_failed or not fixtures_out:
            espn_recs, espn_errs = _espn_fetch_fixtures(date_str, league_keys)
            if espn_errs:
                errors.extend(espn_errs)
            if espn_recs:
                fixtures_out = espn_recs
                source = "live-espn"

        # Explicit dual-failure marker so operators can spot total outages.
        if not fixtures_out:
            errors.append({"both_sources_empty": True,
                           "hint": "api-football and ESPN both returned no fixtures for this date/league set"})

        # Write-through: persist whatever we got (api-football OR ESPN) so the
        # next call serves from cache.
        if db_url and fixtures_out:
            try:
                conn = _pg.connect(db_url)
                try:
                    _ensure_fixtures_schema(conn)
                    with conn.cursor() as cur:
                        for fxo in fixtures_out:
                            cur.execute("""
                                INSERT INTO soccer_fixtures_cache
                                    (fixture_id, fixture_date, commence_time, status, venue,
                                     league_key, league_id, season, home_team, away_team, refreshed_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                                ON CONFLICT (fixture_id) DO UPDATE SET
                                    commence_time = EXCLUDED.commence_time,
                                    status        = EXCLUDED.status,
                                    venue         = EXCLUDED.venue,
                                    league_key    = EXCLUDED.league_key,
                                    league_id     = EXCLUDED.league_id,
                                    season        = EXCLUDED.season,
                                    home_team     = EXCLUDED.home_team,
                                    away_team     = EXCLUDED.away_team,
                                    refreshed_at  = NOW()
                            """, (
                                fxo["fixture_id"], date_str, fxo["commence_time"],
                                fxo["status"], fxo["venue"],
                                fxo["league"], fxo["league_id"], fxo["season"],
                                fxo["home_team"], fxo["away_team"],
                            ))
                        conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                errors.append({"writethrough": str(e)})

    return jsonify({
        "ok":       True,
        "date":     date_str,
        "leagues":  league_keys,
        "count":    len(fixtures_out),
        "fixtures": fixtures_out,
        "source":   source,
        "errors":   errors,
    })


@app.route("/fbref-stats/fixtures/refresh", methods=["POST"])
@require_api_key
def fbref_stats_fixtures_refresh():
    """
    Pre-populate soccer_fixtures_cache for a date range (default: today
    through +2 days, covering timezone edges). Designed for an external
    daily scheduler (e.g. Replit Scheduled Deployment).

    JSON body: { "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD" }
    Defaults: start_date = today UTC, end_date = today + 2 days.
    Max 10 days per call (api-football rate-limit budget).

    Runs synchronously when range ≤ 1 day (fast); in background otherwise.
    Single-flight: returns 409 if a refresh is already running.
    """
    import threading, psycopg2 as _pg, time as _t
    from datetime import date as _date, timedelta as _td, datetime as _dt, timezone as _tz

    # api_key is optional — refresh will fall back to ESPN when missing.
    api_key = os.environ.get("API_FOOTBALL_KEY", "")
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return jsonify({"ok": False, "error": "DATABASE_URL not configured"}), 500

    body = request.get_json(silent=True) or {}
    today = _dt.now(_tz.utc).date()
    start_s = body.get("start_date") or today.isoformat()
    end_s   = body.get("end_date")   or (today + _td(days=2)).isoformat()

    try:
        start = _date.fromisoformat(start_s)
        end   = _date.fromisoformat(end_s)
    except ValueError:
        return jsonify({"ok": False, "error": "start_date/end_date must be YYYY-MM-DD"}), 400
    if end < start:
        return jsonify({"ok": False, "error": "end_date must be >= start_date"}), 400
    days = (end - start).days + 1
    if days > 10:
        return jsonify({"ok": False, "error": "Range too large; max 10 days per call"}), 400

    with _FIXTURES_REFRESH_LOCK:
        if _FIXTURES_REFRESH_STATE["running"]:
            return jsonify({
                "ok":         False,
                "error":      "A fixtures refresh is already running.",
                "status":     "in_progress",
                "started_at": _FIXTURES_REFRESH_STATE["started_at"],
                "range":      _FIXTURES_REFRESH_STATE["range"],
            }), 409
        _FIXTURES_REFRESH_STATE["running"] = True
        _FIXTURES_REFRESH_STATE["started_at"] = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
        _FIXTURES_REFRESH_STATE["range"] = [start_s, end_s]

    date_strs = [(start + _td(days=i)).isoformat() for i in range(days)]

    def _run():
        try:
            inserted, updated, errs = _crawl_fixtures_into_cache(api_key, date_strs, db_url)
            app.logger.info("fixtures refresh done: inserted=%d updated=%d errors=%d",
                            inserted, updated, len(errs))
        finally:
            with _FIXTURES_REFRESH_LOCK:
                _FIXTURES_REFRESH_STATE["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "ok":          True,
        "message":     f"Fixtures refresh started for {start_s}..{end_s} ({days} days).",
        "eta_seconds": int(days * 7),   # ~6.5s rate-limit gap per day
        "leagues":     sorted(_SOCCER_LEAGUE_MAP.keys()),
        "hint":        "Call /fbref-stats/fixtures?date=... after ETA to read from cache.",
    })


@app.route("/fbref-stats/populate", methods=["POST"])
@require_api_key
def fbref_stats_populate():
    """
    Pre-populate the soccer_player_cache for a given league+season.
    Scans every team squad (≤20 teams, 6-second gaps to respect the
    10 req/min rate limit). Runs synchronously — expect ~3 min for EPL.
    Call once per league/season; all subsequent /fbref-stats lookups are instant.
    """
    import threading, time as _time, psycopg2 as _pg

    # See note in /fbref-stats: free plan caps at season 2024.
    _FREE_PLAN_MAX_SEASON = 2024
    from datetime import date as _date
    _today = _date.today()
    _computed = _today.year if _today.month >= 8 else _today.year - 1
    _default_season = str(min(_computed, _FREE_PLAN_MAX_SEASON))

    body = request.get_json(silent=True) or {}

    # Accept either `league` (single, back-compat) or `leagues` (list).
    raw_leagues = body.get("leagues")
    if raw_leagues is None:
        raw_leagues = [body.get("league", "epl")]
    if isinstance(raw_leagues, str):
        raw_leagues = [raw_leagues]

    season = str(body.get("season", _default_season)).strip()

    api_key = os.environ.get("API_FOOTBALL_KEY", "")
    if not api_key:
        return jsonify({"ok": False, "error": "API_FOOTBALL_KEY secret not configured"}), 500

    # Resolve each requested league to its api-football id, with per-league season override.
    # MLS uses calendar-year seasons (currently 2026), unlike European leagues.
    resolved = []
    unknown  = []
    for lname in raw_leagues:
        key = str(lname).strip().lower()
        lid = _SOCCER_LEAGUE_MAP.get(key)
        if not lid:
            unknown.append(key)
            continue
        lseason = str(min(_today.year, _FREE_PLAN_MAX_SEASON)) if key == "mls" else season
        resolved.append({"name": key, "id": lid, "season": lseason})

    if unknown:
        return jsonify({
            "ok":      False,
            "error":   f"Unknown league(s): {unknown}",
            "known":   sorted(set(_SOCCER_LEAGUE_MAP.keys())),
        }), 400
    if not resolved:
        return jsonify({"ok": False, "error": "No leagues provided"}), 400

    db_url = os.environ.get("DATABASE_URL", "")

    def _run_populate_all(leagues):
        import time as _t
        conn = _pg.connect(db_url) if db_url else None
        try:
            for lg in leagues:
                try:
                    teams_data = _soccer_api_get("teams", {"league": lg["id"], "season": lg["season"]}, api_key)
                    teams = teams_data.get("response", [])
                    app.logger.info("populate: league=%s season=%s teams=%d", lg["name"], lg["season"], len(teams))
                    for team in teams:
                        tid = team["team"]["id"]
                        _t.sleep(6.5)   # 10 req/min limit → 6.5s gap for safety
                        try:
                            squad_data = _soccer_api_get("players/squads", {"team": tid}, api_key)
                            for squad_entry in squad_data.get("response", []):
                                for p in squad_entry.get("players", []):
                                    name_lower = (p.get("name") or "").lower()
                                    pid        = p["id"]
                                    full_name  = p.get("name") or ""
                                    _write_player_cache(conn, name_lower, lg["id"], lg["season"], pid, full_name)
                        except Exception as e:
                            app.logger.warning("populate squad failed: team=%s err=%s", tid, e)
                except Exception as e:
                    app.logger.warning("populate league failed: %s err=%s", lg["name"], e)
        finally:
            if conn:
                conn.close()

    t = threading.Thread(target=_run_populate_all, args=(resolved,), daemon=True)
    t.start()

    # ETA: assume ~20 teams per league × 6.5s + 10s buffer per league.
    eta_seconds = int(sum(20 * 6.5 + 10 for _ in resolved))

    return jsonify({
        "ok":          True,
        "message":     f"Cache population started for {len(resolved)} league(s) sequentially.",
        "leagues":     [{"name": l["name"], "id": l["id"], "season": l["season"]} for l in resolved],
        "eta_seconds": eta_seconds,
        "eta_minutes": round(eta_seconds / 60, 1),
        "hint":        "Sequential processing respects the 10 req/min api-football rate limit.",
    })


@app.route("/tennis-stats", methods=["GET"])
@require_api_key
def tennis_stats():
    import requests as _req, csv, io

    player_name = request.args.get("player", "").strip().lower()
    tour        = request.args.get("tour", "atp").strip().lower()   # atp | wta
    year        = request.args.get("year", "").strip()
    limit       = min(int(request.args.get("limit", 15)), 100)

    if not player_name:
        return jsonify({"ok": False, "error": "Missing required param: player"}), 400
    if tour not in ("atp", "wta"):
        return jsonify({"ok": False, "error": "tour must be 'atp' or 'wta'"}), 400

    import datetime
    current_year = datetime.date.today().year
    years_to_try = [year] if year else [str(current_year), str(current_year - 1)]

    base_url = f"https://raw.githubusercontent.com/JeffSackmann/tennis_{tour}/master"

    def _int(v):
        try:
            return int(v) if v not in (None, "", "NA") else None
        except (ValueError, TypeError):
            return None

    def _iso_date(raw):
        # JeffSackmann CSVs encode dates as "YYYYMMDD" with no separators.
        # Browsers (especially Safari) throw "The string did not match the
        # expected pattern" on new Date("20251109"), so emit ISO-8601 instead.
        s = str(raw or "")
        if len(s) == 8 and s.isdigit():
            return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        return s or None

    def _parse_row(row, player_lower):
        won = player_lower in row.get("winner_name", "").lower()
        return {
            "date":        _iso_date(row.get("tourney_date")),
            "date_raw":    row.get("tourney_date"),
            "tourney":     row.get("tourney_name"),
            "surface":     row.get("surface"),
            "round":       row.get("round"),
            "winner":      row.get("winner_name"),
            "loser":       row.get("loser_name"),
            "score":       row.get("score"),
            "won":         won,
            "aces":        _int(row.get("w_ace") if won else row.get("l_ace")),
            "dbl_faults":  _int(row.get("w_df")  if won else row.get("l_df")),
            "serve_pts":   _int(row.get("w_svpt") if won else row.get("l_svpt")),
            "opp_aces":    _int(row.get("l_ace")  if won else row.get("w_ace")),
            "opp_df":      _int(row.get("l_df")   if won else row.get("w_df")),
            "w_aces":      _int(row.get("w_ace")),
            "l_aces":      _int(row.get("l_ace")),
            "w_df":        _int(row.get("w_df")),
            "l_df":        _int(row.get("l_df")),
            "w_svpt":      _int(row.get("w_svpt")),
            "l_svpt":      _int(row.get("l_svpt")),
            "w_games":     _int(row.get("w_games")),
            "l_games":     _int(row.get("l_games")),
        }

    try:
        all_matches = []
        fetched_years = []

        for yr in years_to_try:
            url = f"{base_url}/{tour}_matches_{yr}.csv"
            resp = _req.get(url, timeout=10)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()

            reader = csv.DictReader(io.StringIO(resp.text))
            for row in reader:
                if (player_name in row.get("winner_name", "").lower() or
                        player_name in row.get("loser_name", "").lower()):
                    all_matches.append(_parse_row(row, player_name))

            fetched_years.append(yr)

        if not fetched_years:
            return jsonify({
                "ok":    False,
                "error": f"No {tour.upper()} match files found for years {years_to_try}",
                "hint":  "Try passing year=2025 explicitly, or check the player name spelling.",
            }), 404

        recent = all_matches[-limit:]

        return jsonify({
            "ok":      True,
            "source":  f"JeffSackmann/tennis_{tour}",
            "player":  player_name,
            "tour":    tour.upper(),
            "years":   fetched_years,
            "matches": recent,
            "count":   len(recent),
            "total_found": len(all_matches),
        })

    except _req.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Request to GitHub timed out"}), 504
    except _req.exceptions.RequestException as e:
        return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Tennis per-player career-stats cache ────────────────────────────────────
# Sackmann CSVs are large (~1-2 MB each year). Cache parsed rows per (tour,year)
# in memory with a 12-hour TTL so repeated /tennis-stats/player lookups for
# different players in the same session don't re-download the same files.
_TENNIS_CSV_CACHE = {}   # key: (tour, year) -> {"rows": [...], "fetched_at": ts}
_TENNIS_CSV_TTL   = 12 * 3600
_TENNIS_CSV_LOCK  = threading.Lock()


def _fetch_sackmann_year(tour, year, timeout=10):
    """Return parsed Sackmann rows for (tour, year), cached in memory."""
    import time, requests as _req, csv, io
    key = (tour, str(year))
    now = time.time()
    with _TENNIS_CSV_LOCK:
        entry = _TENNIS_CSV_CACHE.get(key)
        if entry and (now - entry["fetched_at"]) < _TENNIS_CSV_TTL:
            return entry["rows"]

    url = f"https://raw.githubusercontent.com/JeffSackmann/tennis_{tour}/master/{tour}_matches_{year}.csv"
    resp = _req.get(url, timeout=timeout)
    if resp.status_code == 404:
        rows = []
    else:
        resp.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(resp.text)))

    with _TENNIS_CSV_LOCK:
        _TENNIS_CSV_CACHE[key] = {"rows": rows, "fetched_at": now}
    return rows


@app.route("/tennis-stats/player", methods=["GET"])
@require_api_key
def tennis_stats_player():
    """
    Aggregate per-player career stats from JeffSackmann's ATP/WTA dataset.

    Query params:
      player   — player name fragment (required, matched case-insensitive)
      tour     — 'atp' or 'wta' (default 'atp')
      surface  — optional filter: 'Hard', 'Clay', 'Grass', 'Carpet'
      years    — how many recent seasons to scan (default 3, max 5)
      opponent — optional opponent name fragment for H2H record

    Returns aggregated stats: avgAces, avgDFs, firstServePct,
    surfaceWinRate, overallWinRate, recentForm (last 10), matchesAnalyzed.
    """
    import datetime

    player_name = request.args.get("player", "").strip().lower()
    tour        = request.args.get("tour", "atp").strip().lower()
    surface_f   = request.args.get("surface", "").strip().title() or None
    opponent_f  = request.args.get("opponent", "").strip().lower() or None
    try:
        years_n = max(1, min(int(request.args.get("years", 3)), 5))
    except ValueError:
        years_n = 3

    if not player_name:
        return jsonify({"ok": False, "error": "Missing required param: player"}), 400
    if tour not in ("atp", "wta"):
        return jsonify({"ok": False, "error": "tour must be 'atp' or 'wta'"}), 400
    if surface_f and surface_f not in ("Hard", "Clay", "Grass", "Carpet"):
        return jsonify({"ok": False, "error": "surface must be Hard, Clay, Grass, or Carpet"}), 400

    current_year = datetime.date.today().year
    years = [str(current_year - i) for i in range(years_n)]

    def _intf(v):
        try:
            return float(v) if v not in (None, "", "NA") else None
        except (ValueError, TypeError):
            return None

    matches = []
    fetched_years = []
    try:
        for yr in years:
            rows = _fetch_sackmann_year(tour, yr)
            if not rows:
                continue
            fetched_years.append(yr)
            for row in rows:
                winner = (row.get("winner_name") or "").lower()
                loser  = (row.get("loser_name")  or "").lower()
                if player_name in winner:
                    won = True
                elif player_name in loser:
                    won = False
                else:
                    continue
                if surface_f and (row.get("surface") or "") != surface_f:
                    continue
                opp = loser if won else winner
                if opponent_f and opponent_f not in opp:
                    continue

                date_raw = row.get("tourney_date") or ""
                iso_date = (f"{date_raw[0:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
                            if len(date_raw) == 8 and date_raw.isdigit() else date_raw)

                matches.append({
                    "date":      iso_date,
                    "date_raw":  date_raw,
                    "tourney":   row.get("tourney_name"),
                    "surface":   row.get("surface"),
                    "round":     row.get("round"),
                    "opponent":  row.get("loser_name") if won else row.get("winner_name"),
                    "won":       won,
                    "aces":      _intf(row.get("w_ace")  if won else row.get("l_ace")),
                    "dfs":       _intf(row.get("w_df")   if won else row.get("l_df")),
                    "svpt":      _intf(row.get("w_svpt") if won else row.get("l_svpt")),
                    "first_in":  _intf(row.get("w_1stIn") if won else row.get("l_1stIn")),
                    "first_won": _intf(row.get("w_1stWon") if won else row.get("l_1stWon")),
                    "second_won":_intf(row.get("w_2ndWon") if won else row.get("l_2ndWon")),
                })

        if not fetched_years:
            return jsonify({
                "ok": False,
                "error": f"No {tour.upper()} match files found for years {years}",
            }), 404

        # Sort newest first by date_raw (YYYYMMDD sorts lexically)
        matches.sort(key=lambda m: m.get("date_raw") or "", reverse=True)

        def _avg(vals):
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals), 3) if vals else None

        def _pct(num, den):
            num_vals = [v for v in num if v is not None]
            den_vals = [v for v in den if v is not None]
            n, d = sum(num_vals), sum(den_vals)
            return round(n / d, 4) if d > 0 else None

        wins = [m for m in matches if m["won"]]
        # Surface breakdown
        surfaces = {}
        for m in matches:
            s = m.get("surface") or "Unknown"
            bucket = surfaces.setdefault(s, {"played": 0, "won": 0})
            bucket["played"] += 1
            if m["won"]:
                bucket["won"] += 1
        for s, b in surfaces.items():
            b["winRate"] = round(b["won"] / b["played"], 4) if b["played"] else None

        recent_form = [
            {"date": m["date"], "won": m["won"], "opponent": m["opponent"],
             "tourney": m["tourney"], "surface": m["surface"]}
            for m in matches[:10]
        ]

        stats = {
            "avgAces":       _avg([m["aces"] for m in matches]),
            "avgDFs":        _avg([m["dfs"]  for m in matches]),
            "firstServePct": _pct([m["first_in"]  for m in matches],
                                  [m["svpt"]      for m in matches]),
            "firstServeWonPct": _pct([m["first_won"] for m in matches],
                                     [m["first_in"]  for m in matches]),
            "secondServeWonPct": _pct(
                [m["second_won"] for m in matches],
                [(m["svpt"] or 0) - (m["first_in"] or 0)
                 if m["svpt"] is not None and m["first_in"] is not None else None
                 for m in matches],
            ),
            "overallWinRate": round(len(wins) / len(matches), 4) if matches else None,
            "matchesAnalyzed": len(matches),
        }
        if surface_f:
            stats["surfaceWinRate"] = stats["overallWinRate"]
            stats["surfaceFilter"]  = surface_f

        return jsonify({
            "ok":          True,
            "source":      f"JeffSackmann/tennis_{tour}",
            "player":      player_name,
            "tour":        tour.upper(),
            "years":       fetched_years,
            "surface":     surface_f,
            "opponent":    opponent_f,
            "stats":       stats,
            "bySurface":   surfaces,
            "recentForm":  recent_form,
        })

    except Exception as e:
        import requests as _req
        if isinstance(e, _req.exceptions.Timeout):
            return jsonify({"ok": False, "error": "Request to GitHub timed out"}), 504
        if isinstance(e, _req.exceptions.RequestException):
            return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api-sports/<string:sport>/players", methods=["GET"])
@require_api_key
def api_sports_players(sport):
    """
    Search for players via api-sports.io.
    Supported: basketball, nfl, tennis (free plan /players search works).
    Baseball and hockey do not expose a /players search endpoint on the free plan.

    Query params:
      player  — name fragment to search (required)
      league  — league id (optional)
      season  — season string (optional, e.g. "2022-2023" for basketball, "2026" for tennis)
    """
    import requests as _req

    BASES = {
        "baseball":   "https://v1.baseball.api-sports.io",
        "basketball": "https://v1.basketball.api-sports.io",
        "hockey":     "https://v1.hockey.api-sports.io",
        "nfl":        "https://v1.american-football.api-sports.io",
        "tennis":     "https://v1.tennis.api-sports.io",
    }
    sport_lower = sport.lower()
    base = BASES.get(sport_lower)
    if not base:
        return jsonify({
            "ok": False,
            "error": f"Unknown sport '{sport}'. Supported: baseball, basketball, hockey, nfl, tennis",
        }), 400

    player = request.args.get("player", "").strip()
    league = request.args.get("league", "").strip()
    season = request.args.get("season", "").strip()

    if not player:
        return jsonify({"ok": False, "error": "player query param is required"}), 400

    # ── Tennis: uses api-tennis.com with TENNIS_API_KEY ───────────────────────
    if sport_lower == "tennis":
        tennis_key = os.environ.get("TENNIS_API_KEY", "")
        if not tennis_key:
            return jsonify({"ok": False, "error": "TENNIS_API_KEY secret not configured"}), 500
        tennis_base = "https://api.api-tennis.com/tennis/"
        try:
            if player.isdigit():
                # Numeric ID — direct player lookup
                r = _req.get(tennis_base, params={"APIkey": tennis_key, "method": "get_players",
                                                   "player_key": player}, timeout=15)
                r.raise_for_status()
                data = r.json()
                results = data.get("result", []) if data.get("success") == 1 else []
                if not isinstance(results, list):
                    results = [results] if results else []
            else:
                # Name search — scan today + yesterday fixtures for matching names
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
                yest  = (_dt.now(_tz.utc) - _td(days=1)).strftime("%Y-%m-%d")
                r = _req.get(tennis_base, params={"APIkey": tennis_key, "method": "get_fixtures",
                                                   "date_start": yest, "date_stop": today},
                             timeout=15)
                r.raise_for_status()
                fdata = r.json()
                fixtures = fdata.get("result", []) if fdata.get("success") == 1 else []
                seen, results = set(), []
                q = player.lower()
                for f in (fixtures if isinstance(fixtures, list) else []):
                    for pk, pn in [
                        (f.get("first_player_key"),  f.get("event_first_player")),
                        (f.get("second_player_key"), f.get("event_second_player")),
                    ]:
                        if pk and pn and q in pn.lower() and pk not in seen:
                            seen.add(pk)
                            results.append({
                                "player_key":  pk,
                                "player_name": pn,
                                "event_type":  f.get("event_type_type"),
                                "tournament":  f.get("tournament_name"),
                            })
            return jsonify({
                "ok":      True,
                "sport":   "tennis",
                "source":  "api-tennis.com",
                "query":   {"player": player},
                "count":   len(results),
                "players": results,
            })
        except _req.exceptions.Timeout:
            return jsonify({"ok": False, "error": "api-tennis.com request timed out"}), 504
        except _req.exceptions.RequestException as e:
            return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ── All other sports: api-sports.io with API_FOOTBALL_KEY ─────────────────
    api_key = os.environ.get("API_FOOTBALL_KEY", "")

    def _basketball_espn_fallback(api_football_reason):
        """ESPN player search fallback for basketball (NBA + WNBA). Only used
        when api-sports.io is unavailable. Returns a Flask response."""
        players, diag = _try_espn_basketball_player_search(player, league_hint=league or None)
        if players:
            return jsonify({
                "ok":             True,
                "sport":          "basketball",
                "source":         "espn-fallback",
                "query":          {"player": player, "league": league or None, "season": season or None},
                "count":          len(players),
                "players":        players,
                "fallback_note": "api-sports.io unavailable; data sourced from ESPN public API (NBA + WNBA only).",
                "api_sports_reason": api_football_reason,
            })
        return jsonify({
            "ok":              False,
            "sport":           "basketball",
            "tried":           ["api-sports", "espn-fallback"],
            "api_sports_reason": api_football_reason,
            "espn_diagnostics":  diag,
            "hint":            "ESPN returned no NBA/WNBA player matching this name. Check spelling or try a full first+last name.",
        }), 404

    # No api-football key + basketball → ESPN directly
    if not api_key:
        if sport_lower == "basketball":
            return _basketball_espn_fallback("API_FOOTBALL_KEY not configured")
        return jsonify({"ok": False, "error": "API_FOOTBALL_KEY secret not configured"}), 500

    params = {"search": player}
    if league:
        params["league"] = league
    if season:
        params["season"] = season

    try:
        r = _req.get(f"{base}/players",
                     headers={"x-apisports-key": api_key},
                     params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        # Surface any upstream errors from api-sports
        upstream_errors = data.get("errors")
        if upstream_errors and upstream_errors != [] and upstream_errors != {}:
            # If api-sports is suspended / auth-broken AND we're basketball,
            # transparently fall back to ESPN rather than surface a 422.
            if sport_lower == "basketball" and _is_api_sports_suspended_or_auth_error(upstream_errors):
                return _basketball_espn_fallback(f"api-sports upstream: {upstream_errors}")
            if sport_lower in ("baseball", "hockey"):
                hint = (
                    "The /players search endpoint is not available for baseball or hockey "
                    "on the free api-sports plan. Basketball and NFL search work without extra params."
                )
            elif sport_lower == "nfl":
                hint = "NFL /players search failed — check query params."
            else:
                hint = "api-sports returned validation errors — check league/season params."
            return jsonify({
                "ok":              False,
                "sport":           sport_lower,
                "upstream_errors": upstream_errors,
                "hint":            hint,
            }), 422

        results = data.get("response", [])
        return jsonify({
            "ok":      True,
            "sport":   sport_lower,
            "query":   {"player": player, "league": league or None, "season": season or None},
            "count":   len(results),
            "players": results,
        })
    except _req.exceptions.Timeout:
        if sport_lower == "basketball":
            return _basketball_espn_fallback("api-sports request timed out")
        return jsonify({"ok": False, "error": "api-sports request timed out"}), 504
    except _req.exceptions.RequestException as e:
        if sport_lower == "basketball":
            return _basketball_espn_fallback(f"api-sports network error: {e}")
        return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
    except Exception as e:
        # Catch-all so unexpected errors return a sanitized 500 here rather
        # than leaking traceback via the global error handler.
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api-sports/<string:sport>/stats", methods=["GET"])
@require_api_key
def api_sports_stats(sport):
    """
    Fetch season statistics via api-sports.io.

    Basketball: uses /statistics. Required: team, league, season. player_id optional.
    NFL:        uses /players/statistics. Required: player_id. season optional.
    Tennis:     uses /statistics. Required: player_id. season defaults to current year.
                  Uses param name 'player' instead of 'id'.
    Baseball / Hockey: stats endpoint not available; returns 422.

    Query params:
      player_id — numeric player id (required for nfl + tennis; optional for basketball)
      team      — team id (required for basketball)
      league    — league id
      season    — season year (e.g. "2022-2023" for basketball, "2026" for tennis/nfl)
    """
    import requests as _req
    from datetime import datetime as _dt

    BASES = {
        "baseball":   "https://v1.baseball.api-sports.io",
        "basketball": "https://v1.basketball.api-sports.io",
        "hockey":     "https://v1.hockey.api-sports.io",
        "nfl":        "https://v1.american-football.api-sports.io",
        "tennis":     None,   # handled separately via api-tennis.com
    }
    # basketball → /statistics (requires team+league+season)
    # nfl        → /players/statistics (requires player id)
    STAT_CONFIGS = {
        "basketball": ("statistics",         "id"),
        "nfl":        ("players/statistics", "id"),
    }

    sport_lower = sport.lower()
    if sport_lower not in BASES:
        return jsonify({
            "ok": False,
            "error": f"Unknown sport '{sport}'. Supported: baseball, basketball, hockey, nfl, tennis",
        }), 400

    player_id  = request.args.get("player_id", "").strip()
    player_id2 = request.args.get("player_id2", "").strip()  # tennis H2H second player
    team       = request.args.get("team", "").strip()
    league     = request.args.get("league", "").strip()
    season     = request.args.get("season", "").strip()

    # ── Tennis: uses api-tennis.com with TENNIS_API_KEY ───────────────────────
    if sport_lower == "tennis":
        tennis_key = os.environ.get("TENNIS_API_KEY", "")
        if not tennis_key:
            return jsonify({"ok": False, "error": "TENNIS_API_KEY secret not configured"}), 500
        if not player_id:
            return jsonify({
                "ok":   False,
                "error": "player_id is required for tennis stats. "
                         "Use /api-sports/tennis/players?player=<name> to look up the id first.",
            }), 400
        tennis_base = "https://api.api-tennis.com/tennis/"
        try:
            if player_id2:
                # Head-to-head between two players
                r = _req.get(tennis_base, params={
                    "APIkey":            tennis_key,
                    "method":            "get_H2H",
                    "first_player_key":  player_id,
                    "second_player_key": player_id2,
                }, timeout=15)
                r.raise_for_status()
                data = r.json()
                if data.get("success") != 1:
                    return jsonify({
                        "ok":              False,
                        "sport":           "tennis",
                        "source":          "api-tennis.com",
                        "mode":            "h2h",
                        "upstream_errors": data.get("result", data.get("error", "Unknown error")),
                    }), 422
                results = data.get("result", [])
                return jsonify({
                    "ok":    True,
                    "sport": "tennis",
                    "source": "api-tennis.com",
                    "mode":  "h2h",
                    "query": {"player_id": player_id, "player_id2": player_id2},
                    "count": len(results) if isinstance(results, list) else 1,
                    "stats": results,
                })
            else:
                # Player profile + stats/tournaments
                r = _req.get(tennis_base, params={
                    "APIkey":     tennis_key,
                    "method":     "get_players",
                    "player_key": player_id,
                }, timeout=15)
                r.raise_for_status()
                data = r.json()
                if data.get("success") != 1:
                    return jsonify({
                        "ok":              False,
                        "sport":           "tennis",
                        "source":          "api-tennis.com",
                        "mode":            "profile",
                        "upstream_errors": data.get("result", data.get("error", "Unknown error")),
                    }), 422
                results = data.get("result", [])
                if not isinstance(results, list):
                    results = [results] if results else []
                return jsonify({
                    "ok":    True,
                    "sport": "tennis",
                    "source": "api-tennis.com",
                    "mode":  "profile",
                    "query": {"player_id": player_id, "season": season or None},
                    "count": len(results),
                    "stats": results,
                })
        except _req.exceptions.Timeout:
            return jsonify({"ok": False, "error": "api-tennis.com request timed out"}), 504
        except _req.exceptions.RequestException as e:
            return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ── baseball / hockey: not available on free plan ─────────────────────────
    if sport_lower not in STAT_CONFIGS:
        return jsonify({
            "ok":    False,
            "sport": sport_lower,
            "error": (
                f"Player statistics are not available for '{sport_lower}' on the free "
                "api-sports plan. Statistics are currently supported for: basketball, nfl, tennis."
            ),
        }), 422

    stat_path, id_param = STAT_CONFIGS[sport_lower]

    if sport_lower == "nfl" and not player_id:
        return jsonify({
            "ok":   False,
            "error": "player_id is required for NFL stats. "
                     "Use /api-sports/nfl/players?player=<name> to look up the id first.",
        }), 400

    api_key = os.environ.get("API_FOOTBALL_KEY", "")

    # Accept `player` as an alternate way to pass a name (used by the ESPN
    # fallback path when the dashboard doesn't yet have an ESPN athlete id).
    player_name_arg = request.args.get("player", "").strip()

    def _basketball_stats_espn_fallback(api_football_reason):
        splits, player_dict, diag = _try_espn_basketball_stats(
            player_id=player_id or None,
            player_name=player_name_arg or None,
            league_hint=league or None,
        )
        if splits and player_dict:
            return jsonify({
                "ok":     True,
                "sport":  "basketball",
                "source": "espn-fallback",
                "player": player_dict,
                "query":  {
                    "player_id": player_id or None,
                    "player":    player_name_arg or None,
                    "league":    league or None,
                    "season":    season or None,
                },
                "count":  len(splits),
                "stats":  splits,
                "fallback_note":      "api-sports.io unavailable; stats sourced from ESPN public API (Regular Season / Postseason / Career splits, per-game averages).",
                "api_sports_reason":  api_football_reason,
            })
        return jsonify({
            "ok":               False,
            "sport":            "basketball",
            "tried":            ["api-sports", "espn-fallback"],
            "api_sports_reason": api_football_reason,
            "espn_diagnostics":  diag,
            "hint": (
                "ESPN basketball fallback could not resolve this player. "
                "Either pass a valid ESPN athlete id via player_id, or a name "
                "via ?player=<full name>. Hint: call "
                "/api-sports/basketball/players?player=<name> first to get the id."
            ),
        }), 404

    if not api_key:
        if sport_lower == "basketball":
            return _basketball_stats_espn_fallback("API_FOOTBALL_KEY not configured")
        return jsonify({"ok": False, "error": "API_FOOTBALL_KEY secret not configured"}), 500

    base = BASES[sport_lower]
    params = {}
    if player_id:
        params[id_param] = player_id
    if team:
        params["team"] = team
    if league:
        params["league"] = league
    if season:
        params["season"] = season

    try:
        r = _req.get(f"{base}/{stat_path}",
                     headers={"x-apisports-key": api_key},
                     params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        upstream_errors = data.get("errors")
        if upstream_errors and upstream_errors != [] and upstream_errors != {}:
            if sport_lower == "basketball" and _is_api_sports_suspended_or_auth_error(upstream_errors):
                return _basketball_stats_espn_fallback(f"api-sports upstream: {upstream_errors}")
            return jsonify({
                "ok":              False,
                "sport":           sport_lower,
                "upstream_errors": upstream_errors,
                "hint": (
                    "Basketball /statistics requires: team (team id), league, and season. "
                    "Example: ?team=145&league=12&season=2022-2023. "
                    "NFL /players/statistics requires: player_id. "
                    "Example: ?player_id=1197&season=2023"
                ),
            }), 422

        raw_response = data.get("response", [])
        count = data.get("results", len(raw_response) if isinstance(raw_response, list) else 1)
        return jsonify({
            "ok":    True,
            "sport": sport_lower,
            "query": {
                "player_id": player_id or None,
                "team":      team or None,
                "league":    league or None,
                "season":    season or None,
            },
            "count": count,
            "stats": raw_response,
        })
    except _req.exceptions.Timeout:
        if sport_lower == "basketball":
            return _basketball_stats_espn_fallback("api-sports request timed out")
        return jsonify({"ok": False, "error": "api-sports request timed out"}), 504
    except _req.exceptions.RequestException as e:
        if sport_lower == "basketball":
            return _basketball_stats_espn_fallback(f"api-sports network error: {e}")
        return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
    except Exception as e:
        # Catch-all so unexpected errors return a sanitized 500 here rather
        # than leaking traceback via the global error handler.
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── Basketball gamelog cache (per-process, TTL-based) ──────────────────────
_bball_gamelog_cache = {}   # key -> (timestamp, data)
_BBALL_CACHE_TTL = 3600     # 1 hour

def _bball_cache_get(key, ttl=None):
    import time as _t
    entry = _bball_gamelog_cache.get(key)
    if not entry:
        return None
    ts, data = entry
    if _t.time() - ts > (ttl if ttl is not None else _BBALL_CACHE_TTL):
        _bball_gamelog_cache.pop(key, None)
        return None
    return data

def _bball_cache_set(key, data):
    import time as _t
    _bball_gamelog_cache[key] = (_t.time(), data)


@app.route("/api-sports/basketball/gamelog", methods=["GET"])
@require_api_key
def api_sports_basketball_gamelog():
    """
    Fetch real per-game stats for a basketball player via api-sports.io.

    Returns the player's last N completed games with pts/reb/ast/stl/blk/min/opp/date.
    Uses team-level caching to minimize API calls across players on the same team.

    Query params:
      player   — player name (required) — searched via api-sports /players
      league   — league id (12 = NBA, 13 = WNBA) (required)
      season   — season string (defaults to current: "2025-2026" for NBA, "2026" for WNBA)
      last     — number of recent games to return (default 5, max 15)
    """
    import requests as _req
    from datetime import datetime as _dt

    player_name = request.args.get("player", "").strip()
    league      = request.args.get("league", "").strip()
    season      = request.args.get("season", "").strip()
    try:
        last_n = max(1, min(15, int(request.args.get("last", "5") or 5)))
    except ValueError:
        last_n = 5

    if not player_name:
        return jsonify({"ok": False, "error": "player query param required"}), 400
    if not league:
        return jsonify({"ok": False, "error": "league query param required (12=NBA, 13=WNBA)"}), 400

    api_key = os.environ.get("API_FOOTBALL_KEY", "")
    if not api_key:
        return jsonify({"ok": False, "error": "API_FOOTBALL_KEY not configured"}), 500

    if not season:
        now = _dt.utcnow()
        if league == "12":  # NBA spans Oct-June
            yr = now.year if now.month >= 10 else now.year - 1
            season = f"{yr}-{yr+1}"
        else:               # WNBA May-Oct, single year
            season = str(now.year)

    base = "https://v1.basketball.api-sports.io"
    headers = {"x-apisports-key": api_key}

    # ── Step 1: locate player → team ────────────────────────────────────────
    pcache_key = f"player:{league}:{season}:{player_name.lower()}"
    pcached = _bball_cache_get(pcache_key)
    if pcached:
        player_id, team_id, resolved_name = pcached
    else:
        try:
            pr = _req.get(f"{base}/players",
                          headers=headers,
                          params={"search": player_name, "league": league, "season": season},
                          timeout=15)
            pr.raise_for_status()
            pdata = pr.json()
            errs = pdata.get("errors")
            if errs and errs != [] and errs != {}:
                return jsonify({
                    "ok": False, "step": "player_search",
                    "upstream_errors": errs,
                    "hint": "Check league/season. NBA=12 season='2025-2026', WNBA=13 season='2026'.",
                }), 422
            resp = pdata.get("response", []) or []
            if not resp:
                return jsonify({
                    "ok": False, "step": "player_search",
                    "reason": f"No players matching '{player_name}' in league={league} season={season}",
                }), 404
            # Prefer exact / closest name match
            pl = resp[0]
            for cand in resp:
                cname = (cand.get("name") or "").lower()
                if cname == player_name.lower():
                    pl = cand
                    break
            player_id = pl.get("id")
            resolved_name = pl.get("name") or player_name
            # api-sports player response shape varies — try multiple paths
            team_id = None
            for key in ("team", "teams"):
                t = pl.get(key)
                if isinstance(t, dict) and t.get("id"):
                    team_id = t["id"]
                    break
                if isinstance(t, list) and t and isinstance(t[0], dict) and t[0].get("id"):
                    team_id = t[0]["id"]
                    break
            if not team_id:
                return jsonify({
                    "ok": False, "step": "player_search",
                    "player_id": player_id, "name": resolved_name,
                    "reason": "Player found but no team id in api-sports response",
                }), 422
            _bball_cache_set(pcache_key, (player_id, team_id, resolved_name))
        except _req.exceptions.Timeout:
            return jsonify({"ok": False, "step": "player_search", "error": "timeout"}), 504
        except Exception as e:
            return jsonify({"ok": False, "step": "player_search", "error": str(e)}), 502

    # ── Step 2: get team's recent finished games ────────────────────────────
    gcache_key = f"games:{league}:{season}:{team_id}"
    games_list = _bball_cache_get(gcache_key)
    if games_list is None:
        try:
            gr = _req.get(f"{base}/games",
                          headers=headers,
                          params={"team": team_id, "league": league, "season": season},
                          timeout=15)
            gr.raise_for_status()
            gdata = gr.json()
            errs = gdata.get("errors")
            if errs and errs != [] and errs != {}:
                return jsonify({"ok": False, "step": "team_games",
                                "upstream_errors": errs}), 422
            all_games = gdata.get("response", []) or []
            # Filter to finished games (FT=Full Time, AOT/AET=after overtime).
            # NOTE: "POST" = postponed, NOT a completed state — exclude it.
            finished = []
            for g in all_games:
                status = (g.get("status") or {}).get("short") or ""
                if status not in ("FT", "AOT", "AET"):
                    continue
                finished.append(g)
            finished.sort(key=lambda x: x.get("date", ""), reverse=True)
            games_list = finished
            _bball_cache_set(gcache_key, games_list)
        except _req.exceptions.Timeout:
            return jsonify({"ok": False, "step": "team_games", "error": "timeout"}), 504
        except Exception as e:
            return jsonify({"ok": False, "step": "team_games", "error": str(e)}), 502

    if not games_list:
        return jsonify({
            "ok": False, "step": "team_games",
            "player_id": player_id, "team_id": team_id,
            "reason": f"No finished games found for team {team_id} in season {season}",
        }), 404

    # ── Step 3: fetch per-player stats for each of the last N games ─────────
    def _num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0

    # Helper: flatten api-sports per-game player stats — handles both shapes.
    #   Shape A (flat):  [{player:{id,...}, team:{...}, points, rebounds, ...}, ...]
    #   Shape B (nested):[{team:{...}, players:[{player:{...}, points, ...}, ...]}, ...]
    def _iter_player_rows(gstats):
        for item in gstats or []:
            if not isinstance(item, dict):
                continue
            nested = item.get("players")
            if isinstance(nested, list):
                for sub in nested:
                    if isinstance(sub, dict):
                        yield sub
            elif item.get("player"):
                yield item

    # Track upstream errors so we can distinguish "no rows" from "api rejected"
    last_upstream_error = None
    games_out = []
    # Walk through ALL finished games (not just the first last_n) — some recent
    # games may lack player rows; keep collecting until we have last_n matches.
    for g in games_list:
        if len(games_out) >= last_n:
            break
        game_id = g.get("id")
        if not game_id:
            continue
        scache_key = f"gstats:{game_id}"
        gstats = _bball_cache_get(scache_key)
        if gstats is None:
            try:
                sr = _req.get(f"{base}/games/statistics/players",
                              headers=headers,
                              params={"id": game_id},
                              timeout=15)
                sr.raise_for_status()
                sdata = sr.json()
                sd_errs = sdata.get("errors")
                if sd_errs and sd_errs != [] and sd_errs != {}:
                    last_upstream_error = sd_errs
                    continue
                gstats = sdata.get("response", []) or []
                _bball_cache_set(scache_key, gstats)
            except Exception as e:
                last_upstream_error = str(e)
                continue
        # Find this player's row across both possible response shapes
        player_row = None
        for row in _iter_player_rows(gstats):
            row_player = row.get("player") or {}
            if row_player.get("id") == player_id:
                player_row = row
                break
        if not player_row:
            continue
        # Determine opponent from game teams
        teams_obj = g.get("teams") or {}
        home_id = (teams_obj.get("home") or {}).get("id")
        if team_id == home_id:
            opp = (teams_obj.get("away") or {}).get("name") or "?"
            home_away = "vs"
        else:
            opp = (teams_obj.get("home") or {}).get("name") or "?"
            home_away = "@"
        # api-sports basketball rebounds can be int or {total, offence, defence}
        reb_field = player_row.get("rebounds")
        if isinstance(reb_field, dict):
            reb_val = _num(reb_field.get("total"))
        else:
            reb_val = _num(reb_field)
        games_out.append({
            "game_id":   game_id,
            "date":      (g.get("date") or "")[:10],
            "opp":       opp,
            "home_away": home_away,
            "min":       player_row.get("minutes") or "",
            "pts":       _num(player_row.get("points")),
            "reb":       reb_val,
            "ast":       _num(player_row.get("assists")),
            "stl":       _num(player_row.get("steals")),
            "blk":       _num(player_row.get("blocks")),
        })

    if not games_out:
        # Distinguish "api-sports rejected our requests" from "no matching rows"
        if last_upstream_error:
            return jsonify({
                "ok": False, "step": "per_game_stats",
                "player_id": player_id, "team_id": team_id,
                "upstream_errors": last_upstream_error,
                "reason": "api-sports /games/statistics/players returned errors for every game tried",
                "hint": "Usually means this league/season is not on your api-sports plan",
            }), 422
        return jsonify({
            "ok": False, "step": "per_game_stats",
            "player_id": player_id, "team_id": team_id,
            "games_scanned": len(games_list),
            "reason": "No per-game stats rows matched this player across all finished team games",
            "hint": "api-sports basketball may not have per-game player stats for this league/season",
        }), 404

    return jsonify({
        "ok":      True,
        "source":  "api-sports.io",
        "player":  {"id": player_id, "name": resolved_name, "team_id": team_id},
        "league":  league,
        "season":  season,
        "count":   len(games_out),
        "games":   games_out,
    })


# ─── TheRundown events proxy ────────────────────────────────────────────────
# Keeps RUNDOWN_API_KEY server-side (was exposed client-side in older code).
# Path confirmed by directory probe 2026-05-24: therundown.io/api/v1/
# Sport IDs confirmed live: NFL=2, MLB=3, NBA=4, NHL=6, UFC=7, WNBA=8,
#                           MLS=10, EPL=11, NBAPlayoffs=24, NHLPlayoffs=28.

@app.route("/rundown/sports", methods=["GET"])
@require_api_key
def rundown_sports():
    """Return the live TheRundown sport directory (id → name)."""
    import requests as _req
    key = os.getenv('RUNDOWN_API_KEY')
    if not key:
        return jsonify({"ok": False, "error": "RUNDOWN_API_KEY not configured"}), 500
    cached = _bball_cache_get("trd:sports:dir")
    if cached is not None:
        return jsonify(cached)
    try:
        r = _req.get('https://therundown.io/api/v1/sports',
                     headers={'X-TheRundown-Key': key}, timeout=10)
    except Exception as e:
        return jsonify({"ok": False, "step": "fetch", "error": str(e)}), 502
    if not r.ok:
        return jsonify({"ok": False, "status": r.status_code, "body": r.text[:300]}), 502
    try:
        sports = r.json().get('sports', [])
    except Exception as e:
        return jsonify({"ok": False, "step": "parse", "error": str(e)}), 502
    result = {"ok": True, "source": "therundown", "count": len(sports), "sports": sports}
    _bball_cache_set("trd:sports:dir", result)
    return jsonify(result)


@app.route("/rundown/events/<int:sport_id>/<date_str>", methods=["GET"])
@require_api_key
def rundown_events(sport_id, date_str):
    """
    Proxy for TheRundown events endpoint — returns events (with lines if the
    account plan grants access) for a given sport_id on YYYY-MM-DD.

    Distinguishes 401 (plan/auth) from 404 (no events) so the client can
    short-circuit cleanly into its Odds API / ESPN fallback chain.
    """
    import requests as _req
    # Betting lines are volatile — short TTL keeps proxy responses fresh.
    EVENTS_TTL = 300  # 5 minutes
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400

    key = os.getenv('RUNDOWN_API_KEY')
    if not key:
        return jsonify({"ok": False, "error": "RUNDOWN_API_KEY not configured"}), 500

    cache_key = f"trd:ev:{sport_id}:{date_str}"
    cached = _bball_cache_get(cache_key, ttl=EVENTS_TTL)
    if cached is not None:
        return jsonify(cached)

    url = f'https://therundown.io/api/v1/sports/{sport_id}/events/{date_str}'
    try:
        r = _req.get(url, headers={'X-TheRundown-Key': key}, timeout=12)
    except Exception as e:
        return jsonify({"ok": False, "step": "fetch", "error": str(e)}), 502

    if r.status_code == 401:
        # Plan limit — return HTTP 200 with a clean handshake so the browser
        # client treats it as a normal "fall through to Odds API" signal
        # instead of a network error. Matches Claude's v2 client contract.
        return jsonify({
            "ok": True,
            "source": "therundown",
            "sport_id": sport_id,
            "date": date_str,
            "count": 0,
            "events": [],
            "fallback_hint": True,
            "reason": "TheRundown key unauthorized for events endpoint (plan limit)",
            "hint":   "Upgrade plan at therundown.io or rely on the Odds API fallback.",
        }), 200
    if r.status_code == 404:
        # No slate for this sport/date — valid empty result, not an error.
        return jsonify({
            "ok": True,
            "source": "therundown",
            "sport_id": sport_id,
            "date": date_str,
            "count": 0,
            "events": [],
        }), 200
    if not r.ok:
        return jsonify({
            "ok": False, "step": "upstream", "status": r.status_code,
            "body": r.text[:300],
        }), 502

    try:
        data = r.json()
    except Exception as e:
        return jsonify({"ok": False, "step": "parse", "error": str(e)}), 502

    events = data.get('events') or data.get('data') or []
    result = {
        "ok":       True,
        "source":   "therundown",
        "sport_id": sport_id,
        "date":     date_str,
        "count":    len(events),
        "events":   events,
    }
    # Skip caching empty responses — avoids freezing out a late-scheduled
    # game from showing up for 5 minutes after it's added upstream.
    if events:
        _bball_cache_set(cache_key, result)
    return jsonify(result)


# ─── Tier 0 NBA: stats.nba.com via nba_api ──────────────────────────────────
# Free, current-season per-game data. Independent of API-Sports plan status.
_nba_player_id_cache = {}  # name_lower -> player_id

@app.route("/nba-stats/gamelog", methods=["GET"])
@require_api_key
def nba_stats_gamelog():
    """
    Fetch real per-game stats for an NBA player via stats.nba.com (nba_api).

    Returns the player's last N games of the current season with
    pts/reb/ast/stl/blk/min/opp/date. Response shape matches
    /api-sports/basketball/gamelog so client code can swap sources transparently.

    Query params:
      player — player full name (required)
      season — season string like "2025-26" (defaults to current NBA season)
      last   — number of recent games (default 10, max 30)
    """
    from datetime import datetime as _dt
    try:
        from nba_api.stats.endpoints import playergamelog as _pgl
        from nba_api.stats.static import players as _nba_players
    except ImportError:
        return jsonify({"ok": False, "error": "nba_api package not installed on server"}), 500

    player_name = request.args.get("player", "").strip()
    season      = request.args.get("season", "").strip()
    try:
        last_n = max(1, min(30, int(request.args.get("last", "10") or 10)))
    except ValueError:
        last_n = 10

    if not player_name:
        return jsonify({"ok": False, "error": "player query param required"}), 400

    if not season:
        now = _dt.utcnow()
        # NBA season starts Oct of prior year, runs through June.
        yr = now.year if now.month >= 10 else now.year - 1
        season = f"{yr}-{str(yr + 1)[2:]}"

    # Step 1: resolve player name → stats.nba.com id (cached forever, ids are stable)
    key = player_name.lower()
    player_id = _nba_player_id_cache.get(key)
    if player_id is None:
        try:
            matches = _nba_players.find_players_by_full_name(player_name)
            if not matches:
                return jsonify({
                    "ok": False, "step": "player_search",
                    "reason": f"No NBA player matching '{player_name}' in stats.nba.com directory",
                }), 404
            exact = [m for m in matches if (m.get("full_name") or "").lower() == key]
            player_id = (exact[0] if exact else matches[0])["id"]
            _nba_player_id_cache[key] = player_id
        except Exception as e:
            return jsonify({"ok": False, "step": "player_search", "error": str(e)}), 502

    # Step 2: fetch game log (cached per player+season, 1hr TTL)
    gcache_key = f"nbagl:{player_id}:{season}"
    games = _bball_cache_get(gcache_key)
    if games is None:
        try:
            gl = _pgl.PlayerGameLog(player_id=player_id, season=season, timeout=20)
            df = gl.get_data_frames()[0]
            # Coerce numpy types (int64/float64) to JSON-safe Python natives
            # before caching, so any future re-serve from cache is safe.
            games = [
                {k: (v.item() if hasattr(v, 'item') else v) for k, v in rec.items()}
                for rec in df.to_dict(orient='records')
            ]
            _bball_cache_set(gcache_key, games)
        except Exception as e:
            return jsonify({
                "ok": False, "step": "gamelog_fetch",
                "player_id": player_id, "season": season,
                "error": str(e),
                "hint": "stats.nba.com may be rate-limiting or unreachable from this server's IP",
            }), 502

    if not games:
        return jsonify({
            "ok": False, "step": "gamelog_fetch",
            "player_id": player_id, "season": season,
            "reason": f"No games found for player {player_id} in {season}",
        }), 404

    # Step 3: shape to match /api-sports/basketball/gamelog response
    out = []
    for g in games[:last_n]:
        matchup = g.get("MATCHUP") or ""  # "OKC @ LAC" or "OKC vs. LAC"
        if " @ " in matchup:
            home_away, opp = "@", matchup.split(" @ ", 1)[1].strip()
        elif " vs. " in matchup:
            home_away, opp = "vs", matchup.split(" vs. ", 1)[1].strip()
        else:
            home_away, opp = "", ""
        out.append({
            "game_id":   str(g.get("Game_ID") or ""),
            "date":      str(g.get("GAME_DATE") or ""),
            "opp":       opp,
            "home_away": home_away,
            "min":       int(g.get("MIN") or 0),
            "pts":       int(g.get("PTS") or 0),
            "reb":       int(g.get("REB") or 0),
            "ast":       int(g.get("AST") or 0),
            "stl":       int(g.get("STL") or 0),
            "blk":       int(g.get("BLK") or 0),
        })

    return jsonify({
        "ok":     True,
        "source": "stats.nba.com",
        "player": {"id": player_id, "name": player_name},
        "season": season,
        "count":  len(out),
        "games":  out,
    })


@app.route("/api-sports/tennis/fixtures", methods=["GET"])
@require_api_key
def api_sports_tennis_fixtures():
    """
    Return tennis fixtures for a given date from api-tennis.com.
    Query params:
      date      — ISO date YYYY-MM-DD (defaults to today UTC)
      tour      — filter by tour: atp | wta | challenger | itf (optional, case-insensitive)
      live_only — "true" to return only in-progress matches via get_livescore
    """
    import requests as _req
    from datetime import datetime as _dt, timezone as _tz

    api_key = os.environ.get("TENNIS_API_KEY", "")
    if not api_key:
        return jsonify({"ok": False, "error": "TENNIS_API_KEY secret not configured"}), 500

    date      = request.args.get("date", "").strip()
    tour      = request.args.get("tour", "").strip().lower()
    live_only = request.args.get("live_only", "").strip().lower() == "true"

    if not date:
        date = _dt.now(_tz.utc).strftime("%Y-%m-%d")

    base = "https://api.api-tennis.com/tennis/"

    try:
        if live_only:
            r = _req.get(base, params={"APIkey": api_key, "method": "get_livescore"}, timeout=15)
        else:
            r = _req.get(base, params={
                "APIkey":     api_key,
                "method":     "get_fixtures",
                "date_start": date,
                "date_stop":  date,
            }, timeout=15)

        r.raise_for_status()
        data = r.json()

        if data.get("success") != 1:
            errs = data.get("result", data.get("error", "Unknown error"))
            return jsonify({"ok": False, "upstream_errors": errs}), 422

        fixtures = data.get("result", [])
        if not isinstance(fixtures, list):
            fixtures = []

        import re as _re
        _DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        _TIME_RE = _re.compile(r"^(\d{2}):(\d{2})(?::\d{2})?$")

        # Optional tour filter on event_type_type (e.g. "Atp Singles", "Wta Doubles")
        # Coerce to str — upstream may send None for event_type_type.
        if tour:
            fixtures = [f for f in fixtures
                        if tour in str(f.get("event_type_type") or "").lower()]

        # Normalize each fixture so the dashboard can consume it the same way as
        # /tennis-stats/today (commence_time / player1 / player2 / tour / tournament).
        def _derive_tour(etype) -> str:
            et = str(etype or "").lower()
            if "atp" in et: return "ATP"
            if "wta" in et: return "WTA"
            if "challenger" in et: return "Challenger"
            if "itf" in et: return "ITF"
            return ""

        for f in fixtures:
            d = str(f.get("event_date") or "")
            t = str(f.get("event_time") or "")
            t_match = _TIME_RE.match(t)
            if _DATE_RE.match(d) and t_match:
                # Assume UTC — api-tennis.com returns times in UTC.
                f["commence_time"] = f"{d}T{t_match.group(1)}:{t_match.group(2)}:00Z"
            else:
                f["commence_time"] = None
            f["player1"]    = f.get("event_first_player")  or ""
            f["player2"]    = f.get("event_second_player") or ""
            f["tournament"] = f.get("tournament_name")     or ""
            f["tour"]       = _derive_tour(f.get("event_type_type"))
            f["event_id"]   = str(f.get("event_key") or "")

        return jsonify({
            "ok":          True,
            "sport":       "tennis",
            "source":      "api-tennis.com",
            "date":        date,
            "live_only":   live_only,
            "tour_filter": tour or None,
            "count":       len(fixtures),
            "fixtures":    fixtures,
            "matches":     fixtures,   # alias for dashboards expecting the /tennis-stats/today shape
        })
    except _req.exceptions.Timeout:
        return jsonify({"ok": False, "error": "api-tennis.com request timed out"}), 504
    except _req.exceptions.RequestException as e:
        return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/tennis-stats/today", methods=["GET"])
@require_api_key
def tennis_stats_today():
    """
    Returns today's upcoming ATP/WTA matches across all active tournaments,
    sourced from the Odds API.  Optionally filter by tour=atp|wta.
    """
    import requests as _req
    from datetime import datetime, timezone

    tour_filter = request.args.get("tour", "").strip().lower()  # atp | wta | "" = both

    odds_key = os.environ.get("ODDS_API_KEY", "")
    if not odds_key:
        return jsonify({"ok": False, "error": "ODDS_API_KEY secret not configured"}), 500

    base = "https://api.the-odds-api.com/v4"

    try:
        # Step 1: get all active tennis sport keys
        r = _req.get(f"{base}/sports", params={"apiKey": odds_key}, timeout=10)
        r.raise_for_status()
        all_sports = r.json()
        tennis_sports = [
            s for s in all_sports
            if "tennis" in s.get("key", "").lower() and s.get("active", False)
            and (not tour_filter or tour_filter in s.get("key", "").lower())
        ]

        if not tennis_sports:
            return jsonify({
                "ok": True,
                "matches": [],
                "count": 0,
                "message": "No active tennis tournaments found right now.",
            })

        # Step 2: fetch events for each active tournament
        now = datetime.now(timezone.utc)
        all_matches = []

        for sport in tennis_sports:
            sport_key   = sport["key"]
            sport_title = sport["title"]
            r2 = _req.get(f"{base}/sports/{sport_key}/events",
                          params={"apiKey": odds_key}, timeout=10)
            if r2.status_code != 200:
                continue
            events = r2.json()
            for e in events:
                if e.get("completed"):
                    continue
                ct = e.get("commence_time", "")
                # Determine tour from sport key
                if "wta" in sport_key:
                    tour = "WTA"
                elif "atp" in sport_key:
                    tour = "ATP"
                else:
                    tour = "Tennis"
                all_matches.append({
                    "event_id":      e.get("id"),
                    "sport_key":     sport_key,
                    "tournament":    sport_title,
                    "tour":          tour,
                    "commence_time": ct,
                    "player1":       e.get("home_team"),
                    "player2":       e.get("away_team"),
                })

        # Sort by commence_time
        all_matches.sort(key=lambda x: x.get("commence_time", ""))

        return jsonify({
            "ok":          True,
            "source":      "odds-api",
            "date":        now.strftime("%Y-%m-%d"),
            "tournaments": [s["key"] for s in tennis_sports],
            "matches":     all_matches,
            "count":       len(all_matches),
        })

    except _req.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Odds API request timed out"}), 504
    except _req.exceptions.RequestException as e:
        return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    # If the top-level path segment is a known API prefix, never return HTML.
    # This guards against the catch-all intercepting API routes that aren't
    # deployed yet or were mis-spelled by the caller.
    top = path.split("/")[0] if path else ""
    if top in _API_PREFIXES:
        return jsonify({
            "ok": False,
            "error": "Not found",
            "path": f"/{path}",
            "hint": "Check /debug/routes for all registered API endpoints.",
        }), 404

    # Static asset — serve directly if the file exists
    if path:
        full = os.path.join(_STATIC_DIR, path)
        if os.path.isfile(full):
            return send_from_directory(_STATIC_DIR, path)

    # SPA fallback — serve index.html for client-side routing
    index = os.path.join(_STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return send_from_directory(_STATIC_DIR, "index.html")

    return jsonify({"service": "WOW Scoring API", "status": "ok", "version": "1.0.0"}), 200


# ── MLB Umpire stats (HP umpire K/BB/runs aggregates) ──────────────────────
# Data source: MLB Stats API (statsapi.mlb.com) — official, free, no auth.
# Strategy: /populate crawls a date range, computes per-game K/BB/R totals,
# upserts one row per game into `umpire_games`. /umpire-stats GET aggregates
# from that table at query time (cheap; ~2,430 games per season).

_UMPIRE_SCHEMA_LOCK = threading.Lock()
_UMPIRE_SCHEMA_READY = False
_UMPIRE_POPULATE_LOCK = threading.Lock()
_UMPIRE_POPULATE_STATE = {"running": False, "started_at": None, "range": None}


def _ensure_umpire_schema(conn):
    """Create umpire_games table on first use (idempotent)."""
    global _UMPIRE_SCHEMA_READY
    if _UMPIRE_SCHEMA_READY:
        return
    with _UMPIRE_SCHEMA_LOCK:
        if _UMPIRE_SCHEMA_READY:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS umpire_games (
                    game_pk        INTEGER PRIMARY KEY,
                    game_date      DATE NOT NULL,
                    hp_umpire      TEXT NOT NULL,
                    hp_umpire_lower TEXT NOT NULL,
                    so             INTEGER NOT NULL DEFAULT 0,
                    bb             INTEGER NOT NULL DEFAULT 0,
                    runs           INTEGER NOT NULL DEFAULT 0,
                    bf             INTEGER NOT NULL DEFAULT 0,
                    inserted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS umpire_games_lower_idx
                    ON umpire_games (hp_umpire_lower);
                CREATE INDEX IF NOT EXISTS umpire_games_date_idx
                    ON umpire_games (game_date);
            """)
            conn.commit()
        _UMPIRE_SCHEMA_READY = True


def _fetch_game_umpire_stats(game_pk, timeout=8):
    """Pull HP umpire + K/BB/runs/BF for a single completed MLB game."""
    import requests as _req
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
    r = _req.get(url, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    ld = d.get("liveData", {}) or {}
    box = ld.get("boxscore", {}) or {}

    hp = None
    for o in box.get("officials", []) or []:
        if o.get("officialType") == "Home Plate":
            hp = (o.get("official") or {}).get("fullName")
            break
    if not hp:
        return None

    teams = box.get("teams", {}) or {}
    so = bb = bf = 0
    for side in ("home", "away"):
        pitch = ((teams.get(side) or {}).get("teamStats") or {}).get("pitching") or {}
        so += int(pitch.get("strikeOuts")  or 0)
        bb += int(pitch.get("baseOnBalls") or 0)
        bf += int(pitch.get("battersFaced") or 0)

    linescore = (ld.get("linescore") or {}).get("teams") or {}
    runs = int((linescore.get("home") or {}).get("runs") or 0) + \
           int((linescore.get("away") or {}).get("runs") or 0)

    return {"hp_umpire": hp, "so": so, "bb": bb, "runs": runs, "bf": bf}


@app.route("/umpire-stats", methods=["GET"])
@require_api_key
def umpire_stats():
    """
    Career aggregates for an MLB Home Plate umpire, computed from cached
    game-level rows in `umpire_games` (populated via /umpire-stats/populate).

    Query params:
      name   — umpire full name fragment (required, case-insensitive)
      since  — optional ISO date (YYYY-MM-DD); only count games on/after

    Returns: { games, kRate (K/BF), bbRate (BB/BF), runsPerGame, soPerGame,
               bbPerGame, lastGameDate, sampleSince }.
    """
    import psycopg2 as _pg, psycopg2.extras as _pgx

    from datetime import date as _date
    name = (request.args.get("name") or "").strip().lower()
    since = (request.args.get("since") or "").strip() or None
    if not name:
        return jsonify({"ok": False, "error": "Missing required param: name"}), 400
    if since:
        try:
            _date.fromisoformat(since)
        except ValueError:
            return jsonify({"ok": False, "error": "since must be YYYY-MM-DD"}), 400

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return jsonify({"ok": False, "error": "DATABASE_URL not configured"}), 500

    try:
        conn = _pg.connect(db_url)
        try:
            _ensure_umpire_schema(conn)
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                clauses = ["hp_umpire_lower LIKE %s"]
                params  = [f"%{name}%"]
                if since:
                    clauses.append("game_date >= %s")
                    params.append(since)
                where = " AND ".join(clauses)
                cur.execute(f"""
                    SELECT
                        MIN(hp_umpire) AS hp_umpire,
                        COUNT(*)::int  AS games,
                        SUM(so)::int   AS total_so,
                        SUM(bb)::int   AS total_bb,
                        SUM(runs)::int AS total_runs,
                        SUM(bf)::int   AS total_bf,
                        MAX(game_date) AS last_game_date,
                        MIN(game_date) AS first_game_date
                    FROM umpire_games
                    WHERE {where}
                """, params)
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"DB error: {e}"}), 500

    games = (row or {}).get("games") or 0
    if not games:
        return jsonify({
            "ok": True,
            "name": name,
            "games": 0,
            "message": "No games cached for this umpire. Run POST /umpire-stats/populate "
                       "with a date range to backfill.",
        })

    bf = row["total_bf"] or 0
    return jsonify({
        "ok":   True,
        "name": row["hp_umpire"],
        "stats": {
            "games":         games,
            "kRate":         round(row["total_so"] / bf, 4) if bf else None,
            "bbRate":        round(row["total_bb"] / bf, 4) if bf else None,
            "runsPerGame":   round(row["total_runs"] / games, 3),
            "soPerGame":     round(row["total_so"]   / games, 3),
            "bbPerGame":     round(row["total_bb"]   / games, 3),
            "bfPerGame":     round(bf / games, 2),
            "lastGameDate":  str(row["last_game_date"]) if row["last_game_date"] else None,
            "firstGameDate": str(row["first_game_date"]) if row["first_game_date"] else None,
            "sampleSince":   since,
        },
        "source": "statsapi.mlb.com (cached)",
    })


@app.route("/umpire-stats/populate", methods=["POST"])
@require_api_key
def umpire_stats_populate():
    """
    Backfill `umpire_games` from MLB Stats API for a date range.
    Runs in a background thread (returns immediately with ETA).

    JSON body: { "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD" }
    Defaults: end_date = today, start_date = end_date - 14 days.

    Skips games already cached (PRIMARY KEY on game_pk).
    Rate-limited at ~5 requests/sec to be polite to statsapi.mlb.com.
    """
    import threading, requests as _req, psycopg2 as _pg, time as _t
    from datetime import date as _date, timedelta as _td

    body = request.get_json(silent=True) or {}
    end_s   = body.get("end_date")   or _date.today().isoformat()
    start_s = body.get("start_date") or (_date.fromisoformat(end_s) - _td(days=14)).isoformat()

    try:
        start = _date.fromisoformat(start_s)
        end   = _date.fromisoformat(end_s)
    except ValueError:
        return jsonify({"ok": False, "error": "start_date/end_date must be YYYY-MM-DD"}), 400
    if end < start:
        return jsonify({"ok": False, "error": "end_date must be >= start_date"}), 400
    if (end - start).days > 180:
        return jsonify({"ok": False, "error": "Range too large; max 180 days per call"}), 400

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return jsonify({"ok": False, "error": "DATABASE_URL not configured"}), 500

    # Single-flight guard — only one populate crawl may run at a time.
    with _UMPIRE_POPULATE_LOCK:
        if _UMPIRE_POPULATE_STATE["running"]:
            return jsonify({
                "ok":      False,
                "error":   "A populate job is already running.",
                "status":  "in_progress",
                "started_at": _UMPIRE_POPULATE_STATE["started_at"],
                "range":   _UMPIRE_POPULATE_STATE["range"],
            }), 409
        _UMPIRE_POPULATE_STATE["running"] = True
        _UMPIRE_POPULATE_STATE["started_at"] = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
        _UMPIRE_POPULATE_STATE["range"] = [start_s, end_s]

    def _crawl():
        try:
            conn = _pg.connect(db_url)
            try:
                _ensure_umpire_schema(conn)
                sched_url = "https://statsapi.mlb.com/api/v1/schedule"
                params = {"sportId": 1, "startDate": start_s, "endDate": end_s, "hydrate": "officials"}
                try:
                    r = _req.get(sched_url, params=params, timeout=15)
                    r.raise_for_status()
                    sched = r.json()
                except Exception as e:
                    app.logger.warning("umpire populate: schedule fetch failed: %s", e)
                    return

                game_pks = []
                for d in sched.get("dates", []):
                    for g in d.get("games", []):
                        state = (g.get("status") or {}).get("abstractGameState")
                        if state == "Final":
                            game_pks.append((g.get("gamePk"), g.get("officialDate") or d.get("date")))

                inserted = skipped = failed = 0
                with conn.cursor() as cur:
                    for idx, (pk, gdate) in enumerate(game_pks):
                        try:
                            info = _fetch_game_umpire_stats(pk)
                            if not info:
                                failed += 1
                                continue
                            cur.execute("""
                                INSERT INTO umpire_games
                                    (game_pk, game_date, hp_umpire, hp_umpire_lower, so, bb, runs, bf)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (game_pk) DO NOTHING
                            """, (pk, gdate, info["hp_umpire"], info["hp_umpire"].lower(),
                                  info["so"], info["bb"], info["runs"], info["bf"]))
                            if cur.rowcount > 0:
                                inserted += 1
                            else:
                                skipped += 1
                        except Exception as e:
                            failed += 1
                            app.logger.warning("umpire populate: game %s failed: %s", pk, e)
                        # Commit every 25 games to reduce lock-hold time
                        if (idx + 1) % 25 == 0:
                            conn.commit()
                        _t.sleep(0.2)  # ~5 rps
                    conn.commit()
                app.logger.info("umpire populate done: inserted=%d skipped=%d failed=%d",
                                inserted, skipped, failed)
            finally:
                conn.close()
        finally:
            with _UMPIRE_POPULATE_LOCK:
                _UMPIRE_POPULATE_STATE["running"] = False

    threading.Thread(target=_crawl, daemon=True).start()
    days = (end - start).days + 1
    est_games = days * 15  # ~15 MLB games per day in-season
    return jsonify({
        "ok":          True,
        "message":     f"Populating umpire_games from {start_s} to {end_s} ({days} days).",
        "eta_seconds": int(est_games * 0.25),
        "hint":        "Already-cached games are skipped. Call /umpire-stats?name=... afterwards.",
    })


# ── Opening line persistence ────────────────────────────────────────────────
# First-write-wins per (player, prop, side, date) — captures the OPENING line.
# Subsequent POSTs for the same key are silently ignored (ON CONFLICT DO NOTHING).
# Lets the dashboard compute true line movement across page reloads / sessions.

_LINES_SCHEMA_LOCK = threading.Lock()
_LINES_SCHEMA_READY = False


def _ensure_lines_schema(conn):
    global _LINES_SCHEMA_READY
    if _LINES_SCHEMA_READY:
        return
    with _LINES_SCHEMA_LOCK:
        if _LINES_SCHEMA_READY:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS opening_lines (
                    player        TEXT NOT NULL,
                    player_lower  TEXT NOT NULL,
                    prop          TEXT NOT NULL,
                    side          TEXT NOT NULL DEFAULT 'over',
                    line_date     DATE NOT NULL,
                    opening_line  NUMERIC(8,2) NOT NULL,
                    sport         TEXT,
                    book          TEXT,
                    captured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (player_lower, prop, side, line_date)
                );
                CREATE INDEX IF NOT EXISTS opening_lines_date_idx
                    ON opening_lines (line_date);
            """)
            conn.commit()
        _LINES_SCHEMA_READY = True


@app.route("/lines/opening", methods=["POST"])
@require_api_key
def lines_opening_store():
    """
    Capture an opening line. First write per (player, prop, side, date) wins;
    subsequent POSTs are silently ignored so the opening line is preserved.

    JSON body: {
      "player": "Aaron Judge",  (required)
      "prop":   "home_runs",    (required, lowercase identifier)
      "line":   1.5,            (required, numeric)
      "side":   "over",         (optional, default "over")
      "date":   "2026-05-22",   (optional, default today UTC)
      "sport":  "mlb",          (optional)
      "book":   "draftkings"    (optional)
    }

    Returns { ok, stored: bool, opening_line, captured_at }.
    `stored: false` means a line already existed for this key (the existing
    opening line is returned, not overwritten).
    """
    import psycopg2 as _pg, psycopg2.extras as _pgx
    from datetime import date as _date, datetime as _dt, timezone as _tz

    body = request.get_json(silent=True) or {}
    player = (body.get("player") or "").strip()
    prop   = (body.get("prop") or "").strip().lower()
    side   = (body.get("side") or "over").strip().lower()
    sport  = (body.get("sport") or "").strip().lower() or None
    book   = (body.get("book")  or "").strip().lower() or None
    raw_line = body.get("line")
    raw_date = (body.get("date") or "").strip() or _dt.now(_tz.utc).date().isoformat()

    if not player or not prop or raw_line is None:
        return jsonify({"ok": False, "error": "player, prop, and line are required"}), 400
    try:
        line = float(raw_line)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "line must be numeric"}), 400
    try:
        _date.fromisoformat(raw_date)
    except ValueError:
        return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400
    if side not in ("over", "under", "yes", "no"):
        return jsonify({"ok": False, "error": "side must be over|under|yes|no"}), 400

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return jsonify({"ok": False, "error": "DATABASE_URL not configured"}), 500

    try:
        conn = _pg.connect(db_url)
        try:
            _ensure_lines_schema(conn)
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO opening_lines
                        (player, player_lower, prop, side, line_date,
                         opening_line, sport, book)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (player_lower, prop, side, line_date) DO NOTHING
                    RETURNING opening_line, captured_at
                """, (player, player.lower(), prop, side, raw_date,
                      line, sport, book))
                inserted = cur.fetchone()
                if inserted:
                    conn.commit()
                    return jsonify({
                        "ok":           True,
                        "stored":       True,
                        "opening_line": float(inserted["opening_line"]),
                        "captured_at":  inserted["captured_at"].isoformat(),
                    })
                # Conflict — fetch the existing opening line
                cur.execute("""
                    SELECT opening_line, captured_at
                    FROM opening_lines
                    WHERE player_lower = %s AND prop = %s
                      AND side = %s AND line_date = %s
                """, (player.lower(), prop, side, raw_date))
                existing = cur.fetchone()
                return jsonify({
                    "ok":           True,
                    "stored":       False,
                    "opening_line": float(existing["opening_line"]) if existing else None,
                    "captured_at":  existing["captured_at"].isoformat() if existing else None,
                    "note":         "Opening line already captured for this key; not overwritten.",
                })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"DB error: {e}"}), 500


@app.route("/lines/opening", methods=["GET"])
@require_api_key
def lines_opening_get():
    """
    Look up a previously stored opening line.

    Query params:
      player   (required)
      prop     (required, lowercase)
      side     (optional, default 'over'; one of over|under|yes|no)
      date     (optional, default today UTC)

    If `current` is supplied (numeric), the response also includes
    `movement` = current - opening_line, useful for the line-movement
    detector in the dashboard.
    """
    import psycopg2 as _pg, psycopg2.extras as _pgx
    from datetime import date as _date, datetime as _dt, timezone as _tz

    player = (request.args.get("player") or "").strip().lower()
    prop   = (request.args.get("prop") or "").strip().lower()
    side   = (request.args.get("side") or "over").strip().lower()
    raw_date = (request.args.get("date") or "").strip() or _dt.now(_tz.utc).date().isoformat()
    current_s = (request.args.get("current") or "").strip()

    if not player or not prop:
        return jsonify({"ok": False, "error": "player and prop are required"}), 400
    if side not in ("over", "under", "yes", "no"):
        return jsonify({"ok": False, "error": "side must be over|under|yes|no"}), 400
    try:
        _date.fromisoformat(raw_date)
    except ValueError:
        return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400
    current = None
    if current_s:
        try:
            current = float(current_s)
        except ValueError:
            return jsonify({"ok": False, "error": "current must be numeric"}), 400

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return jsonify({"ok": False, "error": "DATABASE_URL not configured"}), 500

    try:
        conn = _pg.connect(db_url)
        try:
            _ensure_lines_schema(conn)
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                cur.execute("""
                    SELECT player, opening_line, sport, book, captured_at
                    FROM opening_lines
                    WHERE player_lower = %s AND prop = %s
                      AND side = %s AND line_date = %s
                """, (player, prop, side, raw_date))
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"DB error: {e}"}), 500

    if not row:
        return jsonify({
            "ok":    True,
            "found": False,
            "player": player, "prop": prop, "side": side, "date": raw_date,
        })

    opening = float(row["opening_line"])
    resp = {
        "ok":           True,
        "found":        True,
        "player":       row["player"],
        "prop":         prop,
        "side":         side,
        "date":         raw_date,
        "opening_line": opening,
        "sport":        row["sport"],
        "book":         row["book"],
        "captured_at":  row["captured_at"].isoformat(),
    }
    if current is not None:
        resp["current"]  = current
        resp["movement"] = round(current - opening, 3)
    return jsonify(resp)


@app.route("/lines/opening/list", methods=["GET"])
@require_api_key
def lines_opening_list():
    """
    List opening lines for a given date (default today UTC). Optional `sport`
    filter. Useful for dashboard bulk-fetch on page load.
    """
    import psycopg2 as _pg, psycopg2.extras as _pgx
    from datetime import date as _date, datetime as _dt, timezone as _tz

    raw_date = (request.args.get("date") or "").strip() or _dt.now(_tz.utc).date().isoformat()
    sport    = (request.args.get("sport") or "").strip().lower() or None
    try:
        _date.fromisoformat(raw_date)
    except ValueError:
        return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return jsonify({"ok": False, "error": "DATABASE_URL not configured"}), 500

    try:
        conn = _pg.connect(db_url)
        try:
            _ensure_lines_schema(conn)
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                if sport:
                    cur.execute("""
                        SELECT player, prop, side, opening_line, sport, book, captured_at
                        FROM opening_lines
                        WHERE line_date = %s AND sport = %s
                        ORDER BY captured_at
                    """, (raw_date, sport))
                else:
                    cur.execute("""
                        SELECT player, prop, side, opening_line, sport, book, captured_at
                        FROM opening_lines
                        WHERE line_date = %s
                        ORDER BY captured_at
                    """, (raw_date,))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"DB error: {e}"}), 500

    return jsonify({
        "ok":    True,
        "date":  raw_date,
        "sport": sport,
        "count": len(rows),
        "lines": [
            {
                "player":       r["player"],
                "prop":         r["prop"],
                "side":         r["side"],
                "opening_line": float(r["opening_line"]),
                "sport":        r["sport"],
                "book":         r["book"],
                "captured_at":  r["captured_at"].isoformat(),
            }
            for r in rows
        ],
    })


# ── /gpt-score enrichment middleware ─────────────────────────────────────────
# Wraps the existing /gpt-score result with a Claude-generated narrative that
# pulls supporting context from the data we already store: opening-line
# movement (Postgres), tennis career aggregates (Sackmann CSV cache). Strictly
# additive — does not change the underlying wow_score logic.

_ENRICH_DEFAULT_MODEL = "claude-opus-4-7"


def _collect_enrichment_context(player, sport, prop, side, line, game_date):
    """Build a context dict from our internal data sources. Returns
    (context_dict, sources_used_list)."""
    import psycopg2 as _pg, psycopg2.extras as _pgx
    from datetime import date as _date, datetime as _dt, timezone as _tz

    ctx = {}
    sources = []
    db_url = os.environ.get("DATABASE_URL", "")
    player_lower = (player or "").strip().lower()
    prop_lower   = (prop or "").strip().lower()
    side_lower   = (side or "").strip().lower()

    # Map MORE/LESS → over/under for opening-line lookup
    ol_side = {"more": "over", "less": "under"}.get(side_lower, side_lower)

    # 1) Opening-line movement (any sport)
    if db_url and player_lower and prop_lower:
        try:
            today = (game_date or _dt.now(_tz.utc).date().isoformat())
            conn = _pg.connect(db_url)
            try:
                with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT opening_line, captured_at, side, line_date
                        FROM opening_lines
                        WHERE player_lower = %s AND prop = %s AND side = %s
                          AND line_date <= %s
                        ORDER BY line_date DESC LIMIT 1
                    """, (player_lower, prop_lower, ol_side, today))
                    row = cur.fetchone()
                    if row:
                        opening = float(row["opening_line"])
                        movement = round(line - opening, 4)
                        ctx["opening_line"] = {
                            "opening":       opening,
                            "current":       line,
                            "movement":      movement,
                            "side":          row["side"],
                            "captured_date": row["line_date"].isoformat(),
                        }
                        sources.append("opening_lines")
            finally:
                conn.close()
        except Exception as e:
            ctx["opening_line_error"] = str(e)

    # NOTE: Tennis-specific career aggregates are intentionally NOT pulled here.
    # The /tennis-stats/player endpoint computes aggregates on demand from raw
    # Sackmann CSVs but does not memoise the aggregate dict by player, so there
    # is no zero-cost lookup. Adding one would mean re-parsing tens of MB of
    # CSV per request. Leaving as a future enhancement — opening-line movement
    # above already provides cross-sport enrichment for tennis props too.

    return ctx, sources


def _build_enrichment_prompt(player, sport, prop, side, line, wow_score, signal, ctx):
    """Build a tight prompt for Claude — short, factual, no recommendations."""
    import json as _json
    ctx_blob = _json.dumps(ctx, indent=2) if ctx else "(no supporting data found)"
    return (
        "You are a SUPPORT LAYER for sports prop analysis. You DO NOT recommend bets. "
        "You write a concise (2-4 sentence) factual narrative explaining what the "
        "supporting data suggests about the prop, in plain language a casual reader "
        "would understand. Avoid hedging clichés like 'tough call' or 'time will tell'.\n\n"
        f"PROP:  {player} — {prop} {side} {line} ({sport})\n"
        f"WOW SCORE: {wow_score} ({signal})\n\n"
        "SUPPORTING DATA:\n"
        f"{ctx_blob}\n\n"
        "Write the narrative now. Reference specific numbers from the supporting data "
        "where useful. Do not invent numbers not present above. If supporting data is "
        "empty, say so in one sentence and stop."
    )


@app.route("/gpt-score/enriched", methods=["POST"])
@require_api_key
def gpt_score_enriched():
    """
    Enriched scoring: runs the standard /gpt-score logic, then appends a
    Claude-generated narrative that draws from our internal data (opening-line
    movement, tennis aggregates).

    Request body: identical to /gpt-score, plus optional:
      model       — Claude model (default claude-opus-4-7)
      max_tokens  — Claude response cap (default 350)
      skip_claude — if true, returns the score + raw context without calling Claude

    Response: standard /gpt-score response shape, plus:
      enrichment: { narrative, model, sources_used, context, claude_error? }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    required_fields = ["player", "sport", "prop", "side", "line"]
    missing = [f for f in required_fields if f not in data]
    if missing:
        return jsonify({
            "error": "Missing required fields",
            "missing_fields": missing,
            "required_fields": required_fields,
            "hint": "side must be MORE or LESS. line is a numeric value (e.g. 27.5).",
        }), 422

    player = str(data["player"]).strip()
    sport  = str(data["sport"]).strip().upper()
    prop   = str(data["prop"]).strip().lower()
    try:
        side = normalize_side(str(data["side"]))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    try:
        line = float(data["line"])
    except (TypeError, ValueError):
        return jsonify({"error": "'line' must be a numeric value"}), 422

    reserved = set(required_fields + ["features", "game_date", "environment",
                                      "model", "max_tokens", "skip_claude"])
    features = {k: v for k, v in data.items() if k not in reserved}
    features.update(data.get("features", {}) or {})

    game_date = None
    raw_game_date = data.get("game_date")
    if raw_game_date:
        from datetime import date as _date
        try:
            game_date = str(_date.fromisoformat(str(raw_game_date)))
        except ValueError:
            return jsonify({"error": "'game_date' must be YYYY-MM-DD format, e.g. 2026-05-08"}), 422

    try:
        environment = normalize_environment(data.get("environment", "")) or "test"
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    # Strict boolean coercion for skip_claude — accept true/false/1/0/yes/no
    raw_skip = data.get("skip_claude", False)
    if isinstance(raw_skip, bool):
        skip_claude = raw_skip
    elif isinstance(raw_skip, (int, float)):
        skip_claude = bool(raw_skip)
    elif isinstance(raw_skip, str):
        sv = raw_skip.strip().lower()
        if sv in ("true", "1", "yes", "y", "on"):
            skip_claude = True
        elif sv in ("false", "0", "no", "n", "off", ""):
            skip_claude = False
        else:
            return jsonify({"error": "'skip_claude' must be a boolean (true/false)"}), 422
    else:
        return jsonify({"error": "'skip_claude' must be a boolean (true/false)"}), 422

    # 1) Compute the underlying score (same as /gpt-score)
    score, signal, msg = compute_wow_score(features, player, prop, side, line)
    saved_ok = persist_request(player, sport, prop, side, line, score, signal,
                               game_date=game_date, environment=environment)
    audit_valid = bool(features.get("raw_l5")) and bool(features.get("raw_l10"))

    base_response = {
        "wow_score":      score,
        "signal":         signal,
        "message":        msg,
        "saved_to_lobby": bool(saved_ok),
        "environment":    environment,
        "player":         player,
        "sport":          sport,
        "prop":           prop,
        "side":           side,
        "line":           line,
        "audit_valid":    audit_valid,
        "invalid_reason": None if audit_valid else "L5/L10 raw rows not provided in request",
    }

    # 2) Gather enrichment context
    ctx, sources = _collect_enrichment_context(player, sport, prop, side, line, game_date)

    enrichment = {
        "model":        None,
        "sources_used": sources,
        "context":      ctx,
        "narrative":    None,
    }

    # 3) Call Claude (unless skipped or unavailable)
    if skip_claude:
        enrichment["narrative"] = "(skipped — skip_claude=true)"
    elif not _ANTHROPIC_AVAILABLE:
        enrichment["claude_error"] = "anthropic package not installed"
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            enrichment["claude_error"] = "ANTHROPIC_API_KEY not set"
        else:
            model = str(data.get("model") or _ENRICH_DEFAULT_MODEL)
            try:
                max_tokens = int(data.get("max_tokens") or 350)
            except (TypeError, ValueError):
                max_tokens = 350
            max_tokens = max(64, min(max_tokens, 1024))
            prompt = _build_enrichment_prompt(player, sport, prop, side, line, score, signal, ctx)
            try:
                client = _anthropic.Anthropic(api_key=api_key)
                resp = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                # Concatenate text blocks (Claude returns content as a list)
                parts = []
                for block in resp.content or []:
                    txt = getattr(block, "text", None)
                    if txt:
                        parts.append(txt)
                enrichment["narrative"] = "".join(parts).strip() or "(empty response)"
                enrichment["model"] = model
            except _anthropic.AuthenticationError:
                enrichment["claude_error"] = "Invalid ANTHROPIC_API_KEY"
            except _anthropic.RateLimitError:
                enrichment["claude_error"] = "Anthropic rate limit hit"
            except _anthropic.BadRequestError as e:
                enrichment["claude_error"] = f"Bad request to Anthropic: {e}"
            except Exception as e:
                enrichment["claude_error"] = f"Anthropic call failed: {e}"

    base_response["enrichment"] = enrichment
    return jsonify(base_response)


# ═══════════════════════════════════════════════════════════════
# WOW /wow/l10 ENDPOINT — per-sport L10 game-log aggregator
# NBA via nba_api (free); MLB/WNBA/NFL via Sports Reference scrape.
# ═══════════════════════════════════════════════════════════════

try:
    from bs4 import BeautifulSoup, Comment
    import pandas as pd
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

try:
    from nba_api.stats.static import players as _nba_players_static
    from nba_api.stats.endpoints import playergamelog as _nba_gamelog_ep
    _NBA_API_AVAILABLE = True
except ImportError:
    _NBA_API_AVAILABLE = False


# ── TTL cache (1 hour) ────────────────────────────────────────
_L10_CACHE: dict = {}
_L10_CACHE_TTL = 3600

def _l10_cache_get(key: str):
    entry = _L10_CACHE.get(key)
    if entry and (time.time() - entry[0]) < _L10_CACHE_TTL:
        return entry[1]
    return None

def _l10_cache_set(key: str, data: dict):
    _L10_CACHE[key] = (time.time(), data)


# ── nba_api prop column map ───────────────────────────────────
_NBA_COL_MAP = {
    "Points":               "PTS",
    "Rebounds":             "REB",
    "Assists":              "AST",
    "3-PT Made":            "FG3M",
    "Steals":               "STL",
    "Blocks":               "BLK",
    "Free Throws Made":     "FTM",
    "Free Throws Attempted":"FTA",
    "Pts+Rebs+Asts":        ["PTS", "REB", "AST"],
    "Pts+Rebs":             ["PTS", "REB"],
    "Pts+Asts":             ["PTS", "AST"],
    "Rebs+Asts":            ["REB", "AST"],
}

# ── BBRef prop column map (MLB) ───────────────────────────────
_MLB_COL_MAP = {
    "Pitcher Strikeouts":   "SO",
    "Hits Allowed":         "H",
    "Earned Runs":          "ER",
    "Walks Allowed":        "BB",
    "Hitter Hits":          "H",
    "Total Bases":          "TB",
    "Runs Scored":          "R",
    "RBIs":                 "RBI",
    "Hitter Strikeouts":    "SO",
    "H+R+RBI":              ["H", "R", "RBI"],
}

_NICHE_PROPS = {
    "1st Inn. Pitches Thrown",
    "Pitcher Fantasy Score",
    "Goalie Saves",
}


# ── Helpers ───────────────────────────────────────────────────

def _safe_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None

def _hit_label(value: float, line: float, direction: str) -> str:
    if value == line:
        return "PUSH"
    if direction == "MORE":
        return "HIT" if value > line else "MISS"
    return "HIT" if value < line else "MISS"

def _calc_stats(games: list, line: float, direction: str) -> dict:
    if not games:
        return {}
    vals   = [g["value"] for g in games]
    l5v    = vals[:5]
    l10v   = vals[:10]
    l5h    = sum(1 for g in games[:5]  if g["hit"] == "HIT")
    l10h   = sum(1 for g in games[:10] if g["hit"] == "HIT")
    avg10  = sum(l10v) / len(l10v)
    edge   = round((avg10 - line) if direction == "MORE" else (line - avg10), 2)
    return {
        "l5_avg":      round(sum(l5v)  / len(l5v),  2) if l5v else None,
        "l10_avg":     round(avg10, 2),
        "l10_median":  round(statistics.median(l10v), 2),
        "l5_hit_rate": f"{l5h}/{len(l5v)} ({round(l5h/max(len(l5v),1)*100)}%)",
        "l10_hit_rate":f"{l10h}/{len(l10v)} ({round(l10h/len(l10v)*100)}%)",
        "edge":        edge,
    }

def _confidence_tier(rows: int, complete: bool) -> str:
    if complete and rows >= 10: return "FINAL LOCK ELIGIBLE"
    if rows >= 5:               return "CONDITIONAL — L5 ONLY"
    if rows >= 3:               return "WATCH / RESEARCH ONLY"
    return "REJECT — INSUFFICIENT DATA"


# ── NBA via nba_api ───────────────────────────────────────────

def _l10_nba(first: str, last: str, prop: str,
              direction: str, line: float, season: str) -> dict:
    """Pull NBA L10 via nba_api (no scraping, free)."""
    result = {"source": "stats.nba.com (nba_api)", "games": [],
              "complete": False, "rows": 0, "gap": ""}

    if not _NBA_API_AVAILABLE:
        result["gap"] = "nba_api not installed"
        return result

    col_def = _NBA_COL_MAP.get(prop)
    if not col_def:
        result["gap"] = f"Prop '{prop}' not mapped for NBA. Check _NBA_COL_MAP."
        return result

    full_name = f"{first} {last}".strip()
    matches = _nba_players_static.find_players_by_full_name(full_name)
    if not matches:
        result["gap"] = f"Player '{full_name}' not found in nba_api static list"
        return result

    player_id = matches[0]["id"]

    try:
        gl = _nba_gamelog_ep.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star="Regular Season",
            timeout=10
        )
        df = gl.get_data_frames()[0]
    except Exception as e:
        result["gap"] = f"nba_api fetch error: {e}"
        return result

    if df.empty:
        result["gap"] = "No game log rows returned (player may not have played this season)"
        return result

    games = []
    for _, row in df.head(10).iterrows():
        if isinstance(col_def, list):
            value = sum(_safe_float(row.get(c, 0)) or 0 for c in col_def)
        else:
            value = _safe_float(row.get(col_def))
        if value is None:
            continue

        matchup = str(row.get("MATCHUP", ""))
        opp = matchup.split("vs.")[-1].strip() if "vs." in matchup \
              else matchup.split("@")[-1].strip() if "@" in matchup else matchup

        games.append({
            "g":       len(games) + 1,
            "date":    str(row.get("GAME_DATE", ""))[:10],
            "opp":     opp,
            "context": str(row.get("MIN", "")),
            "value":   round(float(value), 1),
            "hit":     _hit_label(float(value), line, direction),
            "notes":   "",
        })

    result["games"]    = games
    result["rows"]     = len(games)
    result["complete"] = len(games) >= 10
    if not result["complete"]:
        result["gap"] = f"Only {len(games)} rows available this season"
    result.update(_calc_stats(games, line, direction))
    return result


# ── Sports Reference scraper (MLB / WNBA / NFL) ───────────────

_BBREF_LAST_REQUEST = 0.0
_BBREF_DELAY        = 4.0   # max 15 req/min — respects Sports Ref rate limit

def _bbref_fetch(url: str):
    """Rate-limited fetch for Sports Reference."""
    import requests as _req
    global _BBREF_LAST_REQUEST
    elapsed = time.time() - _BBREF_LAST_REQUEST
    if elapsed < _BBREF_DELAY:
        time.sleep(_BBREF_DELAY - elapsed)
    _BBREF_LAST_REQUEST = time.time()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = _req.get(url, headers=headers, timeout=15)
        return resp.text if resp.status_code == 200 else None
    except Exception:
        return None

def _bbref_parse_table(html: str, table_id: str):
    """Parse BBRef table; handles hidden-in-comment case."""
    if not _BS4_AVAILABLE:
        return None
    soup  = BeautifulSoup(html, "lxml")
    table = soup.find("table", {"id": table_id})

    if not table:
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            if table_id in str(comment):
                cs    = BeautifulSoup(str(comment), "lxml")
                table = cs.find("table", {"id": table_id})
                if table:
                    break

    if not table:
        return None
    try:
        from io import StringIO
        df = pd.read_html(StringIO(str(table)))[0]
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [" ".join(str(c) for c in col).strip() for col in df.columns]
        df = df[df.iloc[:, 0] != df.columns[0]]
        df = df[df.iloc[:, 0].notna()].reset_index(drop=True)
        return df
    except Exception:
        return None

def _build_bbref_pid(first: str, last: str, sport: str) -> str:
    f = re.sub(r"[^a-z]", "", first.lower())
    l = re.sub(r"[^a-z]", "", last.lower())
    letter = l[0] if l else "a"
    if sport == "wnba":
        return f"{letter}/{l[:5]}{f[:2]}01w"
    elif sport == "nfl":
        return f"{letter}/{l[:4]}{f[:2]}00"
    return f"{letter}/{l[:5]}{f[:2]}01"

def _l10_bbref(first: str, last: str, sport: str, prop: str,
               direction: str, line: float, year: str) -> dict:
    """Pull MLB (or WNBA/NFL) L10 from Baseball/Basketball/Football Reference."""
    result = {"source": "baseball-reference.com", "games": [],
              "complete": False, "rows": 0, "gap": ""}

    if not _BS4_AVAILABLE:
        result["gap"] = "beautifulsoup4/pandas not installed in Replit env"
        return result

    pid = _build_bbref_pid(first, last, sport)

    cfg = {
        "mlb_pitcher": {
            "base":     "https://www.baseball-reference.com",
            "path":     f"/players/{pid}/pitching_gamelogs/{year}/",
            "table_id": "pitching_gamelogs",
            "ctx_col":  "IP",
        },
        "mlb_batter": {
            "base":     "https://www.baseball-reference.com",
            "path":     f"/players/{pid}/batting_gamelogs/{year}/",
            "table_id": "batting_gamelogs",
            "ctx_col":  "PA",
        },
        "wnba": {
            "base":     "https://www.basketball-reference.com",
            "path":     f"/players/{pid}/gamelog/{year}/",
            "table_id": "pgl_basic",
            "ctx_col":  "MP",
        },
        "nfl": {
            "base":     "https://www.pro-football-reference.com",
            "path":     f"/players/{pid}/gamelog/{year}/",
            "table_id": "stats",
            "ctx_col":  "SnapPct",
        },
    }.get(sport)

    if not cfg:
        result["gap"] = f"BBRef config missing for sport: {sport}"
        return result

    result["source"] = cfg["base"].replace("https://www.", "")
    url  = cfg["base"] + cfg["path"]
    html = _bbref_fetch(url)

    if not html:
        result["gap"] = (
            f"BBRef fetch failed for {url}. "
            "Player ID may need manual correction — check "
            f"{cfg['base']}/players/{pid[0]}/"
        )
        return result

    df = _bbref_parse_table(html, cfg["table_id"])
    if df is None or df.empty:
        result["gap"] = f"Table '{cfg['table_id']}' not found — player ID may be wrong"
        return result

    col_def = _MLB_COL_MAP.get(prop)
    if col_def is None:
        result["gap"] = f"Prop '{prop}' not mapped. Check _MLB_COL_MAP."
        return result

    # Sports Reference gamelog tables are chronological (oldest first).
    # Iterate all valid rows then take the LAST 10 = most recent 10 games,
    # then reverse so games[0] is most-recent (matches NBA path + L5 math).
    all_games = []
    for _, row in df.iterrows():
        row_d = row.to_dict()
        if isinstance(col_def, list):
            value = sum(_safe_float(row_d.get(c, 0)) or 0 for c in col_def)
        else:
            value = _safe_float(row_d.get(col_def))
        if value is None:
            continue

        all_games.append({
            "date":    str(row_d.get("Date", ""))[:10],
            "opp":     str(row_d.get("Opp", "")),
            "context": str(row_d.get(cfg["ctx_col"], "")),
            "value":   round(float(value), 1),
            "hit":     _hit_label(float(value), line, direction),
            "notes":   "",
        })

    # Take last 10 chronologically, then reverse → most-recent-first
    games = list(reversed(all_games[-10:]))
    for i, g in enumerate(games):
        g["g"] = i + 1

    result["games"]    = games
    result["rows"]     = len(games)
    result["complete"] = len(games) >= 10
    if not result["complete"]:
        result["gap"] = f"Only {len(games)} rows found — fewer games this season or wrong player ID"
    result.update(_calc_stats(games, line, direction))
    return result


# ── Main route ────────────────────────────────────────────────

@app.route("/wow/l10", methods=["GET"])
def wow_l10():
    """
    WOW L10 Data Endpoint
    GET /wow/l10?player=Victor Wembanyama&sport=nba&prop=Points&direction=MORE&line=19.5
    """
    player    = request.args.get("player",    "").strip()
    sport     = request.args.get("sport",     "").strip().lower()
    prop      = request.args.get("prop",      "").strip()
    direction = request.args.get("direction", "MORE").strip().upper()
    line      = request.args.get("line",      type=float)
    season    = request.args.get("season",    "2025-26")
    year      = request.args.get("year",      "2026")
    nocache   = request.args.get("nocache",   "0") == "1"

    if not player:
        return jsonify({"ok": False, "error": "player param required"}), 400
    if not sport:
        return jsonify({"ok": False, "error": "sport param required"}), 400
    if not prop:
        return jsonify({"ok": False, "error": "prop param required"}), 400
    if line is None:
        return jsonify({"ok": False, "error": "line param required (float)"}), 400
    if direction not in ("MORE", "LESS"):
        return jsonify({"ok": False, "error": "direction must be MORE or LESS"}), 400

    if prop in _NICHE_PROPS:
        return jsonify({
            "ok":    False,
            "error": f"'{prop}' is a niche prop — requires manual data pull. "
                     "See WOW Rule 3: MLB Gameday for pitches, "
                     "PrizePicks scoring formula for Fantasy Score."
        }), 422

    cache_key = f"{player}|{sport}|{prop}|{line}|{direction}|{season}|{year}"
    if not nocache:
        cached = _l10_cache_get(cache_key)
        if cached:
            return jsonify({"ok": True, "cached": True, **cached})

    name_parts = player.split(" ", 1)
    first = name_parts[0]
    last  = name_parts[1] if len(name_parts) > 1 else ""

    if sport == "nba":
        data = _l10_nba(first, last, prop, direction, line, season)
    elif sport in ("mlb_batter", "mlb_pitcher", "wnba", "nfl"):
        data = _l10_bbref(first, last, sport, prop, direction, line, year)
    else:
        return jsonify({
            "ok":    False,
            "error": f"Unsupported sport: '{sport}'. "
                     "Supported: nba, wnba, mlb_batter, mlb_pitcher, nfl"
        }), 400

    response_data = {
        "player":            player,
        "sport":             sport,
        "prop":              prop,
        "direction":         direction,
        "line":              line,
        "pulled_at":         datetime.now().strftime("%H:%M:%S"),
        "source":            data.get("source"),
        "rows":              data.get("rows", 0),
        "complete":          data.get("complete", False),
        "gap":               data.get("gap", ""),
        "games":             data.get("games", []),
        "l5_avg":            data.get("l5_avg"),
        "l10_avg":           data.get("l10_avg"),
        "l10_median":        data.get("l10_median"),
        "l5_hit_rate":       data.get("l5_hit_rate"),
        "l10_hit_rate":      data.get("l10_hit_rate"),
        "edge":              data.get("edge"),
        "confidence_tier":   _confidence_tier(
                                 data.get("rows", 0),
                                 data.get("complete", False)
                             ),
    }

    if data.get("rows", 0) >= 3:
        _l10_cache_set(cache_key, response_data)

    return jsonify({"ok": True, "cached": False, **response_data})


# ═══════════════════════════════════════════════════════════════
# WOW /wow/l10/v2 ADDENDUM — adds MLB Stats API (1st-inn pitches),
# Pitcher Fantasy Score reconstruct, CS2 (HLTV via cloudscraper),
# Tennis (Tennis Abstract). /wow/l10 v1 above is unchanged.
# ═══════════════════════════════════════════════════════════════

try:
    import cloudscraper as _cs_lib
    _CS_OK = True
except ImportError:
    _CS_OK = False

# Re-use module-level _BS4_AVAILABLE / _NBA_API_AVAILABLE from v1.
_BS4_OK = _BS4_AVAILABLE
_NBA_OK = _NBA_API_AVAILABLE

# ── v2 caches ────────────────────────────────────────────────
_L10V2_CACHE: dict = {}
_L10V2_TTL = 3600

# Per-game PBP cache: 24h, stores integer counts only (not raw JSON)
_PBP_CACHE: dict = {}
_PBP_TTL = 86400

def _cache_get(store: dict, key, ttl: int):
    e = store.get(key)
    return e[1] if e and (time.time() - e[0]) < ttl else None

def _cache_set(store: dict, key, val):
    store[key] = (time.time(), val)


# ── Shared helpers (v2 short names) ──────────────────────────
def _f(v):
    try: return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError): return None

def _hit(value: float, line: float, direction: str) -> str:
    if value == line: return "PUSH"
    if direction == "MORE": return "HIT" if value > line else "MISS"
    return "HIT" if value < line else "MISS"

def _stats(games: list, line: float, direction: str) -> dict:
    if not games: return {}
    vals  = [g["value"] for g in games]
    l5v   = vals[:5]; l10v = vals[:10]
    l5h   = sum(1 for g in games[:5]  if g["hit"] == "HIT")
    l10h  = sum(1 for g in games[:10] if g["hit"] == "HIT")
    avg10 = sum(l10v) / len(l10v)
    edge  = round((avg10 - line) if direction == "MORE" else (line - avg10), 2)
    return {
        "l5_avg":       round(sum(l5v) / len(l5v), 2) if l5v else None,
        "l10_avg":      round(avg10, 2),
        "l10_median":   round(statistics.median(l10v), 2),
        "l5_hit_rate":  f"{l5h}/{len(l5v)} ({round(l5h/max(len(l5v),1)*100)}%)",
        "l10_hit_rate": f"{l10h}/{len(l10v)} ({round(l10h/len(l10v)*100)}%)",
        "edge":         edge,
    }

def _tier(rows: int, complete: bool) -> str:
    if complete and rows >= 10: return "FINAL LOCK ELIGIBLE"
    if rows >= 5:               return "CONDITIONAL — L5 ONLY"
    if rows >= 3:               return "WATCH / RESEARCH ONLY"
    return "REJECT — INSUFFICIENT DATA"


# ── NBA via nba_api ──────────────────────────────────────────
_NBA_COLS = {
    "Points":"PTS","Rebounds":"REB","Assists":"AST","3-PT Made":"FG3M",
    "Steals":"STL","Blocks":"BLK","Free Throws Made":"FTM",
    "Free Throws Attempted":"FTA",
    "Pts+Rebs+Asts":["PTS","REB","AST"],"Pts+Rebs":["PTS","REB"],
    "Pts+Asts":["PTS","AST"],"Rebs+Asts":["REB","AST"],
}

def _nba(first, last, prop, direction, line, season):
    r = {"source": "stats.nba.com (nba_api)", "games": [],
         "complete": False, "rows": 0, "gap": ""}
    if not _NBA_OK:
        r["gap"] = "nba_api not installed"; return r
    col = _NBA_COLS.get(prop)
    if not col:
        r["gap"] = f"Prop '{prop}' not in NBA column map"; return r
    full = f"{first} {last}".strip()
    matches = _nba_players_static.find_players_by_full_name(full)
    if not matches:
        r["gap"] = f"'{full}' not found in nba_api static list"; return r
    pid = matches[0]["id"]
    try:
        gl = _nba_gamelog_ep.PlayerGameLog(
            player_id=pid, season=season,
            season_type_all_star="Regular Season", timeout=10)
        df = gl.get_data_frames()[0]
    except Exception as e:
        r["gap"] = f"nba_api error: {e}"; return r
    if df.empty:
        r["gap"] = "No rows returned"; return r
    games = []
    for _, row in df.head(10).iterrows():
        val = sum(_f(row.get(c,0)) or 0 for c in col) if isinstance(col, list) \
              else _f(row.get(col))
        if val is None: continue
        mu  = str(row.get("MATCHUP",""))
        opp = mu.split("vs.")[-1].strip() if "vs." in mu \
              else mu.split("@")[-1].strip() if "@" in mu else mu
        games.append({"g": len(games)+1,
                      "date": str(row.get("GAME_DATE",""))[:10],
                      "opp": opp, "context": str(row.get("MIN","")),
                      "value": round(float(val),1),
                      "hit": _hit(float(val), line, direction), "notes": ""})
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} rows this season"
    r.update(_stats(games, line, direction)); return r


# ── Sports Reference scraper (MLB / WNBA / NFL) ──────────────
_MLB_COLS = {
    "Pitcher Strikeouts":"SO","Hits Allowed":"H","Earned Runs":"ER",
    "Walks Allowed":"BB","Hitter Hits":"H","Total Bases":"TB",
    "Runs Scored":"R","RBIs":"RBI","H+R+RBI":["H","R","RBI"],
    "Hitter Strikeouts":"SO",
}

_BBREF_LAST_V2: float = 0.0
_BBREF_DELAY_V2 = 4.0

def _bbref_get(url: str):
    import requests as _req
    global _BBREF_LAST_V2
    wait = _BBREF_DELAY_V2 - (time.time() - _BBREF_LAST_V2)
    if wait > 0: time.sleep(wait)
    _BBREF_LAST_V2 = time.time()
    try:
        resp = _req.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"})
        return resp.text if resp.status_code == 200 else None
    except Exception:
        return None

def _bbref_table(html: str, tid: str):
    if not _BS4_OK: return None
    from io import StringIO
    soup  = BeautifulSoup(html, "lxml")
    table = soup.find("table", {"id": tid})
    if not table:
        for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
            if tid in str(c):
                cs = BeautifulSoup(str(c), "lxml")
                table = cs.find("table", {"id": tid})
                if table: break
    if not table: return None
    try:
        df = pd.read_html(StringIO(str(table)))[0]
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [" ".join(str(x) for x in c).strip() for c in df.columns]
        df = df[df.iloc[:,0] != df.columns[0]]
        df = df[df.iloc[:,0].notna()].reset_index(drop=True)
        return df
    except Exception:
        return None

def _pid(first, last, sport):
    f = re.sub(r"[^a-z]","", first.lower())
    l = re.sub(r"[^a-z]","", last.lower())
    ltr = l[0] if l else "a"
    if sport == "wnba": return f"{ltr}/{l[:5]}{f[:2]}01w"
    if sport == "nfl":  return f"{ltr}/{l[:4]}{f[:2]}00"
    return f"{ltr}/{l[:5]}{f[:2]}01"

_BBREF_CFG = {
    "mlb_pitcher": ("https://www.baseball-reference.com",
                    "/players/{pid}/pitching_gamelogs/{year}/",
                    "pitching_gamelogs", "IP"),
    "mlb_batter":  ("https://www.baseball-reference.com",
                    "/players/{pid}/batting_gamelogs/{year}/",
                    "batting_gamelogs", "PA"),
    "wnba":        ("https://www.basketball-reference.com",
                    "/players/{pid}/gamelog/{year}/", "pgl_basic", "MP"),
    "nfl":         ("https://www.pro-football-reference.com",
                    "/players/{pid}/gamelog/{year}/", "stats", "SnapPct"),
}

def _bbref(first, last, sport, prop, direction, line, year):
    r = {"source": "", "games": [], "complete": False, "rows": 0, "gap": ""}
    if not _BS4_OK:
        r["gap"] = "beautifulsoup4/lxml not installed"; return r
    cfg = _BBREF_CFG.get(sport)
    if not cfg:
        r["gap"] = f"No BBRef config for sport: {sport}"; return r
    base, path_tpl, tid, ctx = cfg
    r["source"] = base.replace("https://www.","")
    url  = base + path_tpl.format(pid=_pid(first, last, sport), year=year)
    html = _bbref_get(url)
    if not html:
        r["gap"] = f"BBRef fetch failed — {url}"; return r
    df = _bbref_table(html, tid)
    if df is None or df.empty:
        r["gap"] = f"Table '{tid}' not found — player ID may be wrong"; return r
    col = _MLB_COLS.get(prop)
    if not col:
        r["gap"] = f"Prop '{prop}' not in MLB column map"; return r
    all_games = []
    for _, row in df.iterrows():
        rd = row.to_dict()
        val = sum(_f(rd.get(c,0)) or 0 for c in col) if isinstance(col,list) \
              else _f(rd.get(col))
        if val is None: continue
        all_games.append({"date": str(rd.get("Date",""))[:10],
                          "opp":  str(rd.get("Opp","")),
                          "context": str(rd.get(ctx,"")),
                          "value": round(float(val),1),
                          "hit": _hit(float(val), line, direction),
                          "notes": ""})
    games = list(reversed(all_games[-10:]))
    for i, g in enumerate(games): g["g"] = i+1
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} rows found"
    r.update(_stats(games, line, direction)); return r


# ── Pitcher Fantasy Score (BBRef reconstruct) ────────────────
def _ip_to_outs(ip):
    try:
        ip = float(str(ip))
        full = int(ip)
        frac = round(ip - full, 1)
        return float(full * 3 + {0.0:0, 0.1:1, 0.2:2}.get(frac, 0))
    except (ValueError, TypeError):
        return 0.0

def _pitcher_fs(outs, ks, er, win=False):
    qs = outs >= 18 and er <= 3
    return round(outs + ks*3 - er*3 + (4 if qs else 0) + (6 if win else 0), 1)

def _pitcher_fantasy_score(first, last, direction, line, year):
    r = {"source": "baseball-reference.com (PP formula reconstruct)",
         "formula": "+1/out  +3/K  -3/ER  +4/QS  +6/W",
         "games": [], "complete": False, "rows": 0, "gap": ""}
    if not _BS4_OK:
        r["gap"] = "beautifulsoup4/lxml not installed"; return r
    url  = (f"https://www.baseball-reference.com"
            f"/players/{_pid(first,last,'mlb_pitcher')}"
            f"/pitching_gamelogs/{year}/")
    html = _bbref_get(url)
    if not html:
        r["gap"] = f"BBRef fetch failed — {url}"; return r
    df = _bbref_table(html, "pitching_gamelogs")
    if df is None or df.empty:
        r["gap"] = "pitching_gamelogs table not found"; return r
    all_games = []
    for _, row in df.iterrows():
        rd = row.to_dict()
        ip = rd.get("IP","0")
        if not ip or str(ip) in ("IP","nan",""): continue
        outs = _ip_to_outs(ip)
        if outs == 0: continue
        ks  = int(_f(rd.get("SO",0)) or 0)
        er  = int(_f(rd.get("ER",0)) or 0)
        win = str(rd.get("Dec","")).strip().upper() == "W"
        fs  = _pitcher_fs(outs, ks, er, win)
        qs  = outs >= 18 and er <= 3
        all_games.append({"date": str(rd.get("Date",""))[:10],
                          "opp":  str(rd.get("Opp","")),
                          "context": f"{ip} IP",
                          "value": fs,
                          "hit": _hit(fs, line, direction),
                          "notes": f"{ks}K {er}ER"
                                   + (" QS" if qs  else "")
                                   + (" W"  if win else "")})
    games = list(reversed(all_games[-10:]))
    for i, g in enumerate(games): g["g"] = i+1
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} starts found"
    r.update(_stats(games, line, direction)); return r


# ── 1st Inning Pitches — MLB Stats API ───────────────────────
_MLB_API = "https://statsapi.mlb.com/api/v1"

def _mlb_get(path, params=None):
    import requests as _req
    try:
        r = _req.get(f"{_MLB_API}{path}", params=params,
                     timeout=10, headers={"User-Agent":"WOW/1.0"})
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None

def _mlb_player_id(first, last):
    d = _mlb_get("/people/search", {"names": f"{first} {last}", "sportId": 1})
    if not d: return None
    people = d.get("people", [])
    if not people: return None
    for p in people:
        if p.get("primaryPosition",{}).get("abbreviation") in ("P","SP","RP"):
            return p["id"]
    return people[0]["id"]

def _mlb_game_pks(player_id, season):
    d = _mlb_get(f"/people/{player_id}/stats",
                 {"stats":"gameLog","group":"pitching","season":season})
    if not d: return []
    splits = d.get("stats",[{}])[0].get("splits",[])
    out = []
    for s in splits:
        gm = s.get("game",{})
        out.append({"gamePk": gm.get("gamePk"),
                    "date":   s.get("date","")[:10],
                    "opp":    s.get("opponent",{}).get("abbreviation","?")})
    return out[-10:]

def _count_pitches_inning1(game_pk: int, pitcher_id: int):
    cache_key = f"{game_pk}:{pitcher_id}"
    cached = _cache_get(_PBP_CACHE, cache_key, _PBP_TTL)
    if cached is not None:
        return cached
    d = _mlb_get(f"/game/{game_pk}/playByPlay")
    if not d: return None
    count = 0
    found = False
    for play in d.get("allPlays", []):
        ab = play.get("about", {})
        if ab.get("inning", 0) > 1: break
        if ab.get("inning", 0) != 1: continue
        if play.get("matchup",{}).get("pitcher",{}).get("id") != pitcher_id:
            continue
        found = True
        for ev in play.get("playEvents", []):
            if ev.get("isPitch", False):
                count += 1
    result = count if found else None
    if result is not None:
        _cache_set(_PBP_CACHE, cache_key, result)
    return result

def _first_inn_pitches(first, last, direction, line, season):
    r = {"source": "statsapi.mlb.com (official, no key)",
         "games": [], "complete": False, "rows": 0, "gap": ""}
    pid = _mlb_player_id(first, last)
    if not pid:
        r["gap"] = f"'{first} {last}' not found in MLB Stats API"; return r
    pks = _mlb_game_pks(pid, season)
    if not pks:
        r["gap"] = f"No game log found for season {season}"; return r
    games = []
    for entry in reversed(pks):
        gp = entry.get("gamePk")
        if not gp: continue
        pitches = _count_pitches_inning1(gp, pid)
        if pitches is None: continue
        games.append({"g": len(games)+1,
                      "date": entry["date"], "opp": entry["opp"],
                      "context": "1st Inn.", "value": float(pitches),
                      "hit": _hit(float(pitches), line, direction),
                      "notes": f"gamePk:{gp}"})
        if len(games) == 10: break
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} starts in MLB API"
    r.update(_stats(games, line, direction)); return r


# ══════════════════════════════════════════════════════════════════════
# WOW Component B — pybaseball MLB Automation Patch
# Tier 1 equivalent (derived from Baseball Reference, Savant, FanGraphs)
# Never Tier 0. On failure, mark as Failed and fall back to manual Tier 1.
# ══════════════════════════════════════════════════════════════════════

# Availability flag — checked before every pybaseball call.
try:
    import pybaseball as _pb
    _PYBASEBALL_OK = True
except ImportError:
    _PYBASEBALL_OK = False

# Prop-to-event mapping for Statcast aggregation.
# "key" is the pybaseball `events` column value we count per game.
_PB_STATCAST_EVENTS = {
    "Pitcher Strikeouts":  "strikeout",
    "Hits Allowed":        "hit",
    "Earned Runs":         "home_run",
    "Hitter Hits":         "hit",
    "Total Bases":         None,
    "Runs Scored":         "home_run",
    "RBIs":                None,
    "H+R+RBI":             None,
    "Hitter Strikeouts":   "strikeout",
    "Walks Allowed":       "walk",
}

# New MLB workflow fields (attached to every MLB-scored prop).
_PB_MLB_WORKFLOW_FIELDS = [
    "weather_checked", "weather_source", "weather_risk",
    "weather_park_not_checked",
    "mlb_starter_confirmed", "starter_confirmation_source",
    "lineup_confirmed", "batting_order_slot",
    "statcast_checked", "fangraphs_checked",
    "market_sanity_checked",
]


def _pb_lookup_mlbam_id(first: str, last: str) -> int | None:
    """Map a name to an MLBAM ID via pybaseball's lookup table.
    Returns None on failure (lookup table empty or name not found)."""
    if not _PYBASEBALL_OK:
        return None
    try:
        df = _pb.playerid_lookup(last, first)
        if df.empty:
            return None
        return int(df.iloc[0]["key_mlbam"])
    except Exception:
        return None


def _pb_statcast_ledgers(player_id: int, prop: str, direction: str,
                         line: float, year: str, window: int = 10):
    """Pull L5/L10/L20 from pybaseball Statcast for an MLBAM player.

    Only works for props that map cleanly to statcast `events`:
    Pitcher Strikeouts (events==strikeout), Hitter Hits (hit), etc.
    For composite props (Total Bases, H+R+RBI) the statcast event
    model is not a clean substitute; we return a failure so the
    caller falls back to manual Tier 1.

    Returns a WOW-compatible dict with `games`, `rows`, `complete`,
    `gap`, `source`, `l5_avg`, `l10_avg`, `l10_median`, hit rates,
    and `edge` — or a dict with `gap` on failure.
    """
    event = _PB_STATCAST_EVENTS.get(prop)
    if event is None:
        return {"gap": f"pybaseball statcast does not support composite prop '{prop}'",
                "source": "pybaseball (Tier 1 equivalent)", "games": [],
                "complete": False, "rows": 0}
    try:
        is_pitcher = prop in ("Pitcher Strikeouts", "Hits Allowed",
                              "Earned Runs", "Walks Allowed")
        raw = _pb.statcast_pitcher(
            f"{year}-01-01", f"{year}-12-31", player_id) if is_pitcher else \
              _pb.statcast_batter(
            f"{year}-01-01", f"{year}-12-31", player_id)
        if raw.empty:
            return {"gap": "pybaseball statcast returned empty",
                    "source": "pybaseball (Tier 1 equivalent)", "games": [],
                    "complete": False, "rows": 0}
        raw["game_date"] = pd.to_datetime(raw["game_date"])
        # Filter rows to the event type
        mask = raw["events"] == event
        if event == "hit":
            mask = raw["events"].isin(["single", "double", "triple", "home_run"])
        elif event == "strikeout":
            mask = raw["events"] == "strikeout"
        elif event == "walk":
            mask = raw["events"].isin(["walk", "intent_walk", "hit_by_pitch"])
        evt = raw[mask]
        # Aggregate per game
        daily = evt.groupby("game_date").size().reset_index(name="value")
        daily = daily.sort_values("game_date", ascending=False)
        # Build WOW-format games list
        games = []
        for _, row in daily.iterrows():
            val = int(row["value"])
            games.append({
                "date":    row["game_date"].strftime("%Y-%m-%d"),
                "opp":     "",   # opponent not available in statcast row
                "context": "statcast",
                "value":   val,
                "hit":     _hit_label(val, line, direction),
                "notes":   "",
            })
        if not games:
            return {"gap": f"pybaseball statcast: no '{event}' events found",
                    "source": "pybaseball (Tier 1 equivalent)", "games": [],
                    "complete": False, "rows": 0}
        # Cap at 20 for L20 support
        games = games[:20]
        for i, g in enumerate(games):
            g["g"] = i + 1
        r = {"source": "pybaseball (Tier 1 equivalent)", "games": games,
             "rows": len(games), "complete": len(games) >= 10, "gap": ""}
        if not r["complete"]:
            r["gap"] = f"Only {len(games)} games in pybaseball statcast"
        r.update(_calc_stats(games, line, direction))
        return r
    except Exception as e:
        return {"gap": f"pybaseball statcast exception: {type(e).__name__}: {str(e)[:200]}",
                "source": "pybaseball (Tier 1 equivalent)", "games": [],
                "complete": False, "rows": 0}


def _pb_statcast_window(player_id: int, year: str, lookback_days: int = 30):
    """Pull a recent statcast form window for a player.
    Returns dict with `has_data` (True only if rows returned), `game_count`,
    `avg_event_rate`, `last_date`, `source`, `fetched_at`, `error`.
    """
    if not _PYBASEBALL_OK:
        return {"has_data": False, "error": "pybaseball not installed"}
    try:
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=lookback_days)
        raw = _pb.statcast_pitcher(
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), player_id)
        if raw.empty:
            return {"has_data": False, "game_count": 0, "avg_event_rate": 0,
                    "last_date": None, "source": "pybaseball statcast window",
                    "fetched_at": datetime.now().isoformat()}
        raw["game_date"] = pd.to_datetime(raw["game_date"])
        games = raw["game_date"].nunique()
        return {"has_data": True, "game_count": games,
                "avg_event_rate": round(len(raw) / max(games, 1), 1),
                "last_date": raw["game_date"].max().strftime("%Y-%m-%d"),
                "source": "pybaseball statcast window",
                "fetched_at": datetime.now().isoformat()}
    except Exception as e:
        return {"has_data": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def _pb_fangraphs_baseline(player_id: int, year: str):
    """Pull FanGraphs baseline (projections / season stats) for a player.
    pybaseball's batting_stats / pitching_stats return season aggregates.
    Returns dict with `has_data`, `source`, `fetched_at`, and `stats` or `error`.
    """
    if not _PYBASEBALL_OK:
        return {"has_data": False, "error": "pybaseball not installed"}
    try:
        # Try pitching first (higher chance of success for pitchers)
        stats = _pb.pitching_stats(year, year)
        row = stats[stats["playerid"] == player_id]
        if row.empty:
            stats = _pb.batting_stats(year, year)
            row = stats[stats["playerid"] == player_id]
        if row.empty:
            return {"has_data": False, "source": "pybaseball FanGraphs",
                    "fetched_at": datetime.now().isoformat(),
                    "stats": None, "error": "Player not found in FanGraphs data"}
        return {"has_data": True, "source": "pybaseball FanGraphs",
                "fetched_at": datetime.now().isoformat(),
                "stats": row.iloc[0].to_dict(), "error": None}
    except Exception as e:
        return {"has_data": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def _pb_pitcher_vs_opponent_k(pitcher_id: int, opponent: str, year: str):
    """Simulate pitcher K vs a specific opponent using statcast history.
    Returns dict with `checked`, `head_to_head_games`, `avg_k`, `max_k`,
    `source`, `fetched_at`, `error` or None on success.
    """
    if not _PYBASEBALL_OK:
        return {"checked": False, "error": "pybaseball not installed"}
    try:
        raw = _pb.statcast_pitcher(
            f"{year}-01-01", f"{year}-12-31", pitcher_id)
        if raw.empty:
            return {"checked": True, "head_to_head_games": 0, "avg_k": 0,
                    "max_k": 0, "source": "pybaseball H2H", "error": None}
        raw["game_date"] = pd.to_datetime(raw["game_date"])
        # Filter to opponent (case-insensitive on team abbreviations)
        opp_upper = opponent.upper()
        mask = (raw["home_team"].str.upper() == opp_upper) | \
               (raw["away_team"].str.upper() == opp_upper)
        h2h = raw[mask]
        if h2h.empty:
            return {"checked": True, "head_to_head_games": 0, "avg_k": 0,
                    "max_k": 0, "source": "pybaseball H2H", "error": None}
        # Count strikeouts per game
        k_rows = h2h[h2h["events"] == "strikeout"]
        daily = k_rows.groupby("game_date").size().reset_index(name="k")
        avg_k = round(daily["k"].mean(), 1) if not daily.empty else 0.0
        max_k = int(daily["k"].max()) if not daily.empty else 0
        return {"checked": True, "head_to_head_games": len(daily),
                "avg_k": avg_k, "max_k": max_k,
                "source": "pybaseball H2H", "error": None,
                "fetched_at": datetime.now().isoformat()}
    except Exception as e:
        return {"checked": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def _pb_to_wow_format(pb_data: dict, prop: str, direction: str, line: float):
    """Standardize pybaseball output to the WOW format.
    pb_data is the dict returned by _pb_statcast_ledgers.
    Returns a dict compatible with WOW's `data` field."""
    if not pb_data or pb_data.get("gap"):
        return None
    return {
        "source": pb_data.get("source", "pybaseball"),
        "games": pb_data.get("games", []),
        "rows": pb_data.get("rows", 0),
        "complete": pb_data.get("complete", False),
        "gap": pb_data.get("gap", ""),
        "l5_avg": pb_data.get("l5_avg"),
        "l10_avg": pb_data.get("l10_avg"),
        "l10_median": pb_data.get("l10_median"),
        "l5_hit_rate": pb_data.get("l5_hit_rate"),
        "l10_hit_rate": pb_data.get("l10_hit_rate"),
        "edge": pb_data.get("edge"),
    }


# ── WOW MLB Pitching Phase 1 helpers ─────────────────────────────────────────
# Inserted after existing pybaseball block. DO NOT consolidate BBRef clients.

def _calculate_efficiency_gap(bbref_log: list, line_pitches_per_inn: float) -> dict:
    """Compare pitcher's most-recent P/inn to the prop line (pitch-count props only).
    DO NOT call for strikeout lines — a K number is not pitches per inning.
    Valid props: '1st Inning Pitches', 'Pitcher Pitches', 'Total Pitches'.
    """
    if not bbref_log:
        return {"recent_p_per_inn": None, "gap": None, "flag": "EFFICIENCY_DATA_MISSING"}
    most_recent = bbref_log[-1]
    # WOW game objects use "value" for the stat and "context" for the ctx_col (IP).
    # Raw BBRef rows use "Pit"/"pitches" and "IP" directly.
    pitches = most_recent.get("value") or most_recent.get("Pit") or most_recent.get("pitches")
    ip_raw   = most_recent.get("context") or most_recent.get("IP")
    if pitches is None or ip_raw is None:
        return {"recent_p_per_inn": None, "gap": None, "flag": "EFFICIENCY_DATA_MISSING"}
    try:
        pitches_f = float(pitches)
        ip_float  = _ip_to_outs(ip_raw) / 3.0
    except (TypeError, ValueError):
        return {"recent_p_per_inn": None, "gap": None, "flag": "EFFICIENCY_DATA_MISSING"}
    if ip_float <= 0:
        return {"recent_p_per_inn": None, "gap": None, "flag": "EFFICIENCY_DATA_MISSING"}
    recent_p_per_inn = pitches_f / ip_float
    gap = (float(line_pitches_per_inn) - recent_p_per_inn) / recent_p_per_inn
    if gap > 0.15:
        flag = "LINE_ABOVE_RECENT_EFFICIENCY"
    elif gap < -0.15:
        flag = "LINE_BELOW_RECENT_EFFICIENCY"
    else:
        flag = "EFFICIENCY_ALIGNED"
    return {
        "recent_p_per_inn": round(recent_p_per_inn, 2),
        "gap":               round(gap, 3),
        "flag":              flag,
    }


def _leash_score(bbref_log: list) -> dict:
    """Numeric 1–10 leash score from recent start log (higher = shorter leash).
    Uses last 5 valid rows where IP is parseable.
    """
    import statistics as _stats
    if not bbref_log:
        return {"leash_score": None, "leash_grade": "UNKNOWN",
                "avg_ip_l5": None, "trend": "UNKNOWN", "flag": "LEASH_DATA_MISSING"}
    # Pull last 5 rows with parseable IP
    valid = []
    for row in bbref_log[-5:]:
        # WOW game objects store IP in "context"; raw BBRef rows use "IP" directly.
        ip_raw = row.get("context") or row.get("IP")
        if ip_raw is None:
            continue
        try:
            ip_f = _ip_to_outs(ip_raw) / 3.0
            if ip_f > 0:
                valid.append(ip_f)
        except (TypeError, ValueError):
            continue
    if len(valid) < 2:
        return {"leash_score": None, "leash_grade": "UNKNOWN",
                "avg_ip_l5": None, "trend": "UNKNOWN", "flag": "LEASH_DATA_MISSING"}
    avg_ip = sum(valid) / len(valid)
    # ip_score
    if avg_ip >= 6.0:
        ip_score = 0
    elif avg_ip >= 5.0:
        ip_score = 1
    elif avg_ip >= 4.0:
        ip_score = 2
    else:
        ip_score = 3
    # trend_score
    if valid[-1] < valid[0] - 0.5:
        trend_score = 2
        trend = "SHORTENING"
    elif valid[-1] > valid[0] + 0.5:
        trend_score = 0
        trend = "LENGTHENING"
    else:
        trend_score = 1
        trend = "STABLE"
    # consistency_score
    stdev = _stats.stdev(valid) if len(valid) >= 2 else 0.0
    if stdev < 0.5:
        consistency_score = 0
    elif stdev < 1.0:
        consistency_score = 1
    else:
        consistency_score = 2
    raw_score   = ip_score + trend_score + consistency_score
    leash_score = min(10, max(1, raw_score + 1))
    if leash_score <= 3:
        leash_grade = "LONG"
    elif leash_score <= 6:
        leash_grade = "MEDIUM"
    else:
        leash_grade = "SHORT"
    return {
        "leash_score":  leash_score,
        "leash_grade":  leash_grade,
        "avg_ip_l5":    round(avg_ip, 2),
        "trend":        trend,
    }


def _get_pitcher_savant(first: str, last: str, days_back: int = 30) -> dict:
    """Pull recent Statcast pitcher indicators via pybaseball.
    Note: strikeout_events_per_pitch ≠ true K% (denominator is pitches, not BF).
    """
    if not _PYBASEBALL_OK:
        return {"error": "pybaseball not installed"}
    from datetime import datetime, timedelta
    try:
        mlbam_id = _pb_lookup_mlbam_id(first, last)
        if mlbam_id is None:
            return {"error": "SAVANT_PLAYER_NOT_FOUND", "player": f"{first} {last}"}
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=days_back)
        df = _pb.statcast_pitcher(
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
            mlbam_id,
        )
        if df is None or df.empty:
            return {"error": "SAVANT_NO_DATA", "mlbam_id": mlbam_id}
        total_pitches = len(df)
        desc = df.get("description", df.get("des", None))
        if desc is not None:
            whiffs = int(df["description"].isin(
                ["swinging_strike", "swinging_strike_blocked"]).sum())
        else:
            whiffs = 0
        events = df.get("events")
        strikeout_events = int((events == "strikeout").sum())  if events is not None else 0
        walk_events      = int(events.isin(["walk", "intent_walk"]).sum()) if events is not None else 0
        whiff_pct = whiffs / total_pitches if total_pitches else 0.0
        avg_vel   = df["release_speed"].dropna().mean() if "release_speed" in df.columns else None
        return {
            "mlbam_id":                  mlbam_id,
            "days_back":                 days_back,
            "total_pitches":             total_pitches,
            "whiff_pct":                 round(whiff_pct, 3),
            "strikeout_events_per_pitch": round(strikeout_events / total_pitches, 3) if total_pitches else None,
            "walk_events_per_pitch":     round(walk_events      / total_pitches, 3) if total_pitches else None,
            "avg_velocity":              round(float(avg_vel), 1) if avg_vel is not None and not (avg_vel != avg_vel) else None,
            "data_source":               "pybaseball_statcast",
        }
    except Exception as e:
        return {"error": "SAVANT_FETCH_FAILED", "detail": str(e)[:300]}


# FanGraphs team name alias map (abbreviation → substring in FanGraphs full name)
_FG_TEAM_ALIASES: dict[str, list[str]] = {
    "ARI": ["Diamondbacks", "Arizona"],
    "ATL": ["Braves", "Atlanta"],
    "BAL": ["Orioles", "Baltimore"],
    "BOS": ["Red Sox", "Boston"],
    "CHC": ["Cubs", "Chicago C"],
    "CWS": ["White Sox", "Chicago W"],
    "CIN": ["Reds", "Cincinnati"],
    "CLE": ["Guardians", "Cleveland"],
    "COL": ["Rockies", "Colorado"],
    "DET": ["Tigers", "Detroit"],
    "HOU": ["Astros", "Houston"],
    "KC":  ["Royals", "Kansas City"],
    "LAA": ["Angels", "Los Angeles A", "L.A. Angels"],
    "LAD": ["Dodgers", "Los Angeles D", "L.A. Dodgers"],
    "MIA": ["Marlins", "Miami"],
    "MIL": ["Brewers", "Milwaukee"],
    "MIN": ["Twins", "Minnesota"],
    "NYM": ["Mets", "New York M"],
    "NYY": ["Yankees", "New York Y"],
    "OAK": ["Athletics", "Oakland"],
    "PHI": ["Phillies", "Philadelphia"],
    "PIT": ["Pirates", "Pittsburgh"],
    "SD":  ["Padres", "San Diego"],
    "SF":  ["Giants", "San Francisco"],
    "SEA": ["Mariners", "Seattle"],
    "STL": ["Cardinals", "St. Louis"],
    "TB":  ["Rays", "Tampa Bay"],
    "TEX": ["Rangers", "Texas"],
    "TOR": ["Blue Jays", "Toronto"],
    "WSH": ["Nationals", "Washington"],
}


def _get_opponent_k_rate(team_abbr: str, handedness: str = "overall") -> dict:
    """Pull opponent team batting K% — tries pybaseball.team_batting() first
    (wraps FanGraphs natively), falls back to direct HTML scrape."""
    import datetime as _dt

    def _match_team(tbl, abbr):
        """Return a matched DataFrame row or None, using _FG_TEAM_ALIASES."""
        aliases = _FG_TEAM_ALIASES.get(abbr.upper(), [abbr])
        team_col = next((c for c in tbl.columns
                         if str(c).lower() in ("team", "tm", "name", "team name")), None)
        if team_col is None:
            return None, None
        for _, row in tbl.iterrows():
            cell = str(row[team_col])
            if any(a.lower() in cell.lower() for a in aliases):
                return row, team_col
            if abbr.upper() in cell.upper():
                return row, team_col
        return None, team_col

    # ── Primary: MLB Stats API (free, no auth, all 30 teams) ─────────────────
    import requests as _req
    try:
        season = _dt.datetime.now().year
        url = (
            f"https://statsapi.mlb.com/api/v1/teams/stats"
            f"?season={season}&sportId=1&stats=season&group=hitting"
        )
        resp = _req.get(url, timeout=10)
        resp.raise_for_status()
        splits = resp.json().get("stats", [{}])[0].get("splits", [])
        aliases = _FG_TEAM_ALIASES.get(team_abbr.upper(), [team_abbr])
        matched = None
        for s in splits:
            team_name = s.get("team", {}).get("name", "")
            if any(a.lower() in team_name.lower() for a in aliases):
                matched = s["stat"]
                break
        if matched:
            pa = int(matched.get("plateAppearances", 0))
            ks = int(matched.get("strikeOuts", 0))
            if pa > 0:
                k_pct = round(ks / pa, 4)
                result = {
                    "team":       team_abbr,
                    "k_pct":      k_pct,
                    "handedness": handedness,
                    "source":     "mlb_stats_api",
                    "strikeOuts": ks,
                    "plateAppearances": pa,
                }
                if handedness != "overall":
                    result["note"] = "SPLIT_NOT_AVAILABLE_FREE"
                    result["handedness"] = "overall_only"
                return result
    except Exception:
        pass  # fall through

    # ── Fallback: pybaseball team_batting ─────────────────────────────────────
    if _PYBASEBALL_OK:
        try:
            tbl = _pb.team_batting(_dt.datetime.now().year, _dt.datetime.now().year)
            if tbl is not None and not tbl.empty:
                tbl.columns = [str(c) for c in tbl.columns]
                k_col = next((c for c in tbl.columns if c == "K%"), None)
                matched_row, _ = _match_team(tbl, team_abbr)
                if matched_row is not None and k_col:
                    raw_k = str(matched_row[k_col]).replace("%", "").strip()
                    k_pct = float(raw_k) / 100.0 if float(raw_k) > 1 else float(raw_k)
                    result = {
                        "team":       team_abbr,
                        "k_pct":      round(k_pct, 4),
                        "handedness": handedness,
                        "source":     "pybaseball_team_batting",
                    }
                    if handedness != "overall":
                        result["note"] = "SPLIT_NOT_AVAILABLE_FREE"
                    return result
        except Exception:
            pass

    # ── Final fallback: FanGraphs HTML ───────────────────────────────────────
    import requests as _req
    try:
        url = (
            "https://www.fangraphs.com/leaders/major-league"
            "?pos=all&stats=bat&lg=all&qual=0&type=8&season=2026"
            "&season1=2026&ind=0&team=0%2Cts&rost=0&age=0&filter=&players=0"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
        resp = _req.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        from io import StringIO as _SIO
        tables = pd.read_html(_SIO(resp.text))
        tbl = next((t for t in tables
                    if any("K%" in str(c) for c in t.columns)), None)
        if tbl is None:
            return {"error": "FANGRAPHS_FETCH_FAILED", "team": team_abbr,
                    "detail": "No K% column found in any parsed table"}
        tbl.columns = [str(c) for c in tbl.columns]
        k_col = next((c for c in tbl.columns if "K%" in c), None)
        matched_row, _ = _match_team(tbl, team_abbr)
        if matched_row is None:
            return {"error": "FANGRAPHS_TEAM_NOT_FOUND", "team": team_abbr}
        raw_k = str(matched_row[k_col]).replace("%", "").strip()
        k_pct = float(raw_k) / 100.0 if float(raw_k) > 1 else float(raw_k)
        result = {
            "team":       team_abbr,
            "k_pct":      round(k_pct, 4),
            "handedness": handedness,
            "source":     "fangraphs_html",
        }
        if handedness != "overall":
            result["note"] = "SPLIT_NOT_AVAILABLE_FREE"
        return result
    except Exception as e:
        return {"error": "FANGRAPHS_FETCH_FAILED", "team": team_abbr,
                "detail": str(e)[:300]}


def _leash_score_from_statcast(first: str, last: str, days_back: int = 90) -> dict:
    """Leash score fallback when BBRef is unavailable.
    Groups Statcast pitch-by-pitch data by game_date to reconstruct a per-start
    log, using max inning reached as an IP proxy, then feeds _leash_score().
    """
    if not _PYBASEBALL_OK:
        return {"leash_score": None, "leash_grade": "UNKNOWN",
                "avg_ip_l5": None, "trend": "UNKNOWN", "flag": "LEASH_DATA_MISSING"}
    from datetime import datetime, timedelta
    try:
        mlbam_id = _pb_lookup_mlbam_id(first, last)
        if mlbam_id is None:
            return {"leash_score": None, "leash_grade": "UNKNOWN",
                    "avg_ip_l5": None, "trend": "UNKNOWN", "flag": "LEASH_DATA_MISSING"}
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=days_back)
        df = _pb.statcast_pitcher(
            start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), mlbam_id)
        if df is None or df.empty:
            return {"leash_score": None, "leash_grade": "UNKNOWN",
                    "avg_ip_l5": None, "trend": "UNKNOWN", "flag": "LEASH_DATA_MISSING"}
        df["game_date"] = pd.to_datetime(df["game_date"])
        # Per-start: use max inning pitched as IP proxy
        per_game = (
            df.groupby("game_date")
              .agg(max_inning=("inning", "max"))
              .reset_index()
              .sort_values("game_date")
        )
        # Build synthetic log using "context" key (matches _leash_score key lookup)
        synthetic_log = [
            {"context": str(int(row["max_inning"])), "source": "statcast_proxy"}
            for _, row in per_game.iterrows()
        ]
        result = _leash_score(synthetic_log)
        result["leash_source"] = "statcast_inning_proxy"
        return result
    except Exception as e:
        return {"leash_score": None, "leash_grade": "UNKNOWN",
                "avg_ip_l5": None, "trend": "UNKNOWN",
                "flag": "LEASH_DATA_MISSING", "error": str(e)[:200]}


# ── END WOW MLB Pitching Phase 1 helpers ──────────────────────────────────────


# ═══════════════════════════════════════════════════════════════════════════════
# WOW Cross-Sport Phase 2 helpers
#  1. Line movement  (all sports)
#  2. MLB pitcher handedness splits (platoon K%)
#  3. WNBA / NBA defensive rating
# ═══════════════════════════════════════════════════════════════════════════════

# ── Prop → ODDS API market key ─────────────────────────────────────────────
_PROP_TO_ODDS_MARKET: dict[str, str] = {
    # MLB pitcher
    "pitcher strikeouts":                "pitcher_strikeouts",
    "pitcher outs recorded":             "pitcher_outs",
    "pitcher hits allowed":              "pitcher_hits_allowed",
    "pitcher walks":                     "pitcher_walks",
    "pitcher earned runs":               "pitcher_earned_runs",
    "pitcher pitches":                   "pitcher_pitches",
    "total pitches":                     "pitcher_pitches",
    "1st inning pitches":               "pitcher_strikeouts_1st_inning",
    # MLB batter
    "batter home runs":                  "batter_home_runs",
    "batter hits":                       "batter_hits",
    "batter total bases":                "batter_total_bases",
    "batter rbi":                        "batter_rbis",
    "batter runs scored":                "batter_runs_scored",
    "batter stolen bases":               "batter_stolen_bases",
    # NBA / WNBA
    "player points":                     "player_points",
    "player rebounds":                   "player_rebounds",
    "player assists":                    "player_assists",
    "player threes":                     "player_threes",
    "player steals":                     "player_steals",
    "player blocks":                     "player_blocks",
    "player points rebounds assists":    "player_points_rebounds_assists",
    "player points+rebounds+assists":    "player_points_rebounds_assists",
    "player points + rebounds + assists":"player_points_rebounds_assists",
    # NFL
    "player passing yards":              "player_pass_yds",
    "player rushing yards":              "player_rush_yds",
    "player receiving yards":            "player_reception_yds",
    "player receptions":                 "player_receptions",
    "player passing touchdowns":         "player_pass_tds",
    "player rushing touchdowns":         "player_rush_tds",
    "player receiving touchdowns":       "player_reception_tds",
    "player tackles":                    "player_solo_tackles",
    "player kicking points":             "player_kicking_points",
}

# ── Sport → ODDS API sport key ─────────────────────────────────────────────
_SPORT_TO_ODDS_KEY: dict[str, str] = {
    "mlb":   "baseball_mlb",
    "nba":   "basketball_nba",
    "wnba":  "basketball_wnba",
    "nfl":   "americanfootball_nfl",
    "nhl":   "icehockey_nhl",
    "ncaab": "basketball_ncaab",
    "ncaaf": "americanfootball_ncaaf",
}


def _get_cross_book_variance(player_name: str, odds_market: str,
                              sport_odds_key: str) -> dict:
    """Fetch current line for a player across all books.  Returns consensus
    (median), min/max, and per-book variance.  Uses ODDS API — costs quota.
    Only called when the caller explicitly requests variance data."""
    import requests as _req, statistics as _stats
    odds_key = os.environ.get("ODDS_API_KEY", "")
    if not odds_key:
        return {"error": "ODDS_API_KEY_MISSING"}
    base = "https://api.the-odds-api.com/v4"
    try:
        r = _req.get(f"{base}/sports/{sport_odds_key}/events",
                     params={"apiKey": odds_key}, timeout=10)
        r.raise_for_status()
        events = r.json()
        name_lower = player_name.lower()
        points: list[float] = []
        books:  list[str]   = []
        for event in events:
            r2 = _req.get(
                f"{base}/sports/{sport_odds_key}/events/{event['id']}/odds",
                params={"apiKey": odds_key, "regions": "us",
                        "markets": odds_market, "oddsFormat": "american"},
                timeout=10,
            )
            if r2.status_code != 200:
                continue
            for bk in r2.json().get("bookmakers", []):
                for mkt in bk.get("markets", []):
                    for outcome in mkt.get("outcomes", []):
                        desc = (outcome.get("description") or "").lower()
                        if name_lower in desc or desc in name_lower:
                            pt = outcome.get("point")
                            if pt is not None:
                                points.append(float(pt))
                                books.append(bk.get("key", "?"))
        if not points:
            return {"error": "PLAYER_NOT_FOUND_IN_PROPS"}
        return {
            "consensus_line":     _stats.median(points),
            "books_sampled":      len(points),
            "book_names":         list(set(books))[:6],
            "line_range":         [min(points), max(points)],
            "cross_book_variance": round(max(points) - min(points), 1),
        }
    except Exception as e:
        return {"error": "ODDS_API_FETCH_FAILED", "detail": str(e)[:200]}


def _get_line_movement(player_name: str, prop: str, sport: str,
                       side: str, current_line: float,
                       game_date: str = "",
                       include_variance: bool = False) -> dict:
    """Compare current line (from caller's request) against opening line in DB.

    Movement = current_line - opening_line.
    Positive = line moved up (harder to hit MORE / easier to hit LESS).
    Negative = line moved down (easier to hit MORE / harder to hit LESS).
    Steam threshold: |movement| >= 0.5.

    When include_variance=True, also calls ODDS API for cross-book spread
    (costs API quota — use sparingly).

    Returns:
      opening_line        float | None
      current_line        float
      movement            float | None
      direction           STEAM_MORE | STEAM_LESS | STABLE | NO_OPENING_LINE
      steam               bool
      steam_favors        "BET" | "AGAINST" | "NONE"   (relative to `side`)
      cross_book_variance float | None  (only if include_variance)
      books_sampled       int           (only if include_variance)
    """
    import psycopg2 as _pg
    from datetime import datetime as _dt, timezone as _tz

    today    = game_date or _dt.now(_tz.utc).date().isoformat()
    side_db  = side.lower().replace("more", "over").replace("less", "under")

    # 1. Opening line from DB
    opening_line = None
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        try:
            conn = _pg.connect(db_url)
            try:
                _ensure_lines_schema(conn)
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT opening_line FROM opening_lines
                        WHERE player_lower = %s AND prop = %s
                          AND side = %s AND line_date = %s
                    """, (player_name.lower(), prop.lower(), side_db, today))
                    row = cur.fetchone()
                    if row:
                        opening_line = float(row[0])
            finally:
                conn.close()
        except Exception:
            pass

    # 2. Movement calculation
    movement  = None
    direction = "NO_OPENING_LINE"
    steam     = False
    steam_favors = "NONE"

    if opening_line is not None:
        movement = round(current_line - opening_line, 2)
        steam    = abs(movement) >= 0.5
        if abs(movement) < 0.1:
            direction = "STABLE"
        elif movement > 0:
            direction = "STEAM_MORE"
            # Line went UP: harder to hit MORE, easier to hit LESS
            steam_favors = "AGAINST" if side.upper() in ("MORE", "OVER") else "BET"
        else:
            direction = "STEAM_LESS"
            # Line went DOWN: easier to hit MORE, harder to hit LESS
            steam_favors = "BET" if side.upper() in ("MORE", "OVER") else "AGAINST"

    result: dict = {
        "opening_line":  opening_line,
        "current_line":  current_line,
        "movement":      movement,
        "direction":     direction,
        "steam":         steam,
        "steam_favors":  steam_favors,
    }

    # 3. Optional cross-book variance (ODDS API)
    if include_variance:
        odds_market   = _PROP_TO_ODDS_MARKET.get(prop.lower())
        sport_api_key = _SPORT_TO_ODDS_KEY.get(sport.lower())
        if odds_market and sport_api_key:
            var_data = _get_cross_book_variance(player_name, odds_market, sport_api_key)
            result["cross_book_variance"] = var_data.get("cross_book_variance")
            result["books_sampled"]       = var_data.get("books_sampled", 0)
            result["book_names"]          = var_data.get("book_names", [])
            if var_data.get("error"):
                result["variance_error"] = var_data["error"]
        else:
            result["cross_book_variance"] = None
            result["variance_error"]      = "PROP_OR_SPORT_NOT_MAPPED"
    return result


# ── MLB pitcher platoon (handedness) splits ────────────────────────────────

def _get_pitcher_platoon_splits(first: str, last: str) -> dict:
    """Fetch season platoon K% splits for a pitcher vs RHB and LHB.
    Uses MLB Stats API /stats?stats=season&sitCodes=vr|vl|ov.

    The `statSplits` endpoint returns 0 results mid-season; the correct
    approach is /api/v1/stats with sitCodes= (vr=vs RHB, vl=vs LHB).

    Returns:
      vs_rhb_k_pct   float | None   — K / BF vs right-handed batters
      vs_lhb_k_pct   float | None   — K / BF vs left-handed batters
      overall_k_pct  float | None   — K / BF overall (season stat)
      source         str
    """
    import requests as _req
    try:
        season = datetime.now().year
        # Search for player
        r = _req.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": f"{first} {last}", "sportId": 1},
            timeout=10,
        )
        r.raise_for_status()
        people = r.json().get("people", [])
        if not people:
            return {"error": "PLAYER_NOT_FOUND", "vs_rhb_k_pct": None,
                    "vs_lhb_k_pct": None, "overall_k_pct": None}
        pid = people[0]["id"]

        def _fetch_splits(sit_code: str) -> list[dict]:
            params: dict = {
                "stats": "season", "group": "pitching",
                "season": season, "sportId": 1,
                "playerPool": "All", "playerIds": pid,
            }
            if sit_code:
                params["sitCodes"] = sit_code
            r2 = _req.get("https://statsapi.mlb.com/api/v1/stats",
                          params=params, timeout=10)
            if r2.status_code != 200:
                return []
            return r2.json().get("stats", [{}])[0].get("splits", [])

        def _sum_k_pct(splits: list[dict]) -> float | None:
            total_so = sum(int(s.get("stat", {}).get("strikeOuts") or 0) for s in splits)
            total_bf = sum(int(s.get("stat", {}).get("battersFaced") or 0) for s in splits)
            return round(total_so / total_bf, 4) if total_bf > 0 else None

        vs_rhb_splits   = _fetch_splits("vr")
        vs_lhb_splits   = _fetch_splits("vl")
        overall_splits  = _fetch_splits("")

        return {
            "vs_rhb_k_pct":  _sum_k_pct(vs_rhb_splits),
            "vs_lhb_k_pct":  _sum_k_pct(vs_lhb_splits),
            "overall_k_pct": _sum_k_pct(overall_splits),
            "mlbam_id":      pid,
            "source":        "mlb_stats_api_sitcodes",
        }
    except Exception as e:
        return {"error": str(e)[:200], "vs_rhb_k_pct": None,
                "vs_lhb_k_pct": None, "overall_k_pct": None}


# ── WNBA / NBA defensive rating ────────────────────────────────────────────

_WNBA_ESPN_TEAM_IDS: dict[str, int] = {
    "ATL": 3,  "CHI": 4,  "CONN": 5,  "DAL": 6,  "IND": 8,
    "LV":  9,  "LA":  6,  "MIN": 10,  "NY":  11, "PHX": 14,
    "SEA": 17, "WAS": 19, "GS":  20,  "TOR": 24, "UTA": 18,
    "CLE": 21,
}

def _get_wnba_def_rating(opponent_abbr: str) -> dict:
    """Fetch WNBA opponent defensive rating from ESPN.
    Returns def_rating, rank (1 = best defense), and points allowed per game."""
    import requests as _req
    try:
        # ESPN WNBA standings has defensive data
        r = _req.get(
            "https://site.api.espn.com/apis/v2/sports/basketball/wnba/standings",
            params={"season": datetime.now().year},
            timeout=10,
        )
        r.raise_for_status()
        children = r.json().get("children", [])
        # Collect all team entries
        team_stats: list[dict] = []
        for conf in children:
            for entry in conf.get("standings", {}).get("entries", []):
                tm = entry.get("team", {})
                abbr = tm.get("abbreviation", "")
                stats_list = entry.get("stats", [])
                pts_allowed = next(
                    (float(s["value"]) for s in stats_list
                     if s.get("name") == "avgPointsAgainst"), None)
                team_stats.append({"abbr": abbr, "pts_allowed": pts_allowed})

        if not team_stats:
            raise ValueError("No standings data")

        # Rank by pts_allowed ascending (lower = better defense)
        ranked = sorted([t for t in team_stats if t["pts_allowed"] is not None],
                        key=lambda t: t["pts_allowed"])
        for i, t in enumerate(ranked):
            t["def_rank"] = i + 1

        match = next((t for t in ranked
                      if t["abbr"].upper() == opponent_abbr.upper()), None)
        if match:
            return {
                "team":         opponent_abbr,
                "pts_allowed_pg": match["pts_allowed"],
                "def_rank":     match["def_rank"],
                "teams_ranked": len(ranked),
                "tier":         ("ELITE" if match["def_rank"] <= 4
                                 else "AVERAGE" if match["def_rank"] <= 10
                                 else "WEAK"),
                "source":       "espn_wnba_standings",
            }
        return {"error": "TEAM_NOT_FOUND", "team": opponent_abbr}
    except Exception as e:
        return {"error": str(e)[:200], "team": opponent_abbr}


def _get_nba_def_rating(opponent_abbr: str) -> dict:
    """Fetch NBA opponent defensive rating via nba_api LeagueDashTeamStats."""
    if not _NBA_OK:
        return {"error": "NBA_API_NOT_AVAILABLE", "team": opponent_abbr}
    try:
        from nba_api.stats.endpoints import leaguedashteamstats
        import pandas as _pd
        df = leaguedashteamstats.LeagueDashTeamStats(
            measure_type_detailed_defense="Defense",
            per_mode_simple="PerGame",
            season="2025-26",
        ).get_data_frames()[0]
        # Match by team abbreviation
        row = df[df["TEAM_ABBREVIATION"].str.upper() == opponent_abbr.upper()]
        if row.empty:
            return {"error": "TEAM_NOT_FOUND", "team": opponent_abbr}
        r = row.iloc[0]
        # Rank: sort all teams by DEF_RATING ascending to get rank
        df_sorted = df.sort_values("DEF_RATING")
        df_sorted["def_rank"] = range(1, len(df_sorted) + 1)
        rank_row = df_sorted[df_sorted["TEAM_ABBREVIATION"].str.upper()
                              == opponent_abbr.upper()]
        def_rank = int(rank_row.iloc[0]["def_rank"]) if not rank_row.empty else None
        n = len(df)
        return {
            "team":        opponent_abbr,
            "def_rating":  round(float(r["DEF_RATING"]), 1),
            "opp_pts_pg":  round(float(r["OPP_PTS_PAINT"]), 1) if "OPP_PTS_PAINT" in r else None,
            "def_rank":    def_rank,
            "teams_ranked": n,
            "tier":        ("ELITE"   if def_rank and def_rank <= 8
                            else "AVERAGE" if def_rank and def_rank <= 22
                            else "WEAK"),
            "source":      "nba_api_defense",
        }
    except Exception as e:
        return {"error": str(e)[:200], "team": opponent_abbr}


def _get_def_rating(opponent_abbr: str, sport: str) -> dict:
    """Route defensive rating lookup to the correct sport helper."""
    s = sport.lower()
    if s == "wnba":
        return _get_wnba_def_rating(opponent_abbr)
    if s == "nba":
        return _get_nba_def_rating(opponent_abbr)
    return {"error": f"DEF_RATING_NOT_SUPPORTED_FOR_{s.upper()}", "team": opponent_abbr}


# ── END WOW Cross-Sport Phase 2 helpers ───────────────────────────────────


def _mlb_workflow_fields(first: str, last: str, sport: str, prop: str,
                         year: str, opponent: str = "", game_date: str = "",
                         statcast_data: dict = None):
    """Generate the 11 new MLB workflow fields. Some are populated via
    pybaseball calls, others default to 'not checked' and are filled by
    the downstream orchestrator (weather, starters, market sanity).
    Returns a dict keyed by field names."""
    fields = {
        "weather_checked": False,
        "weather_source": None,
        "weather_risk": None,
        "weather_park_not_checked": True,
        "mlb_starter_confirmed": False,
        "starter_confirmation_source": None,
        "lineup_confirmed": False,
        "batting_order_slot": None,
        "statcast_checked": False,
        "fangraphs_checked": False,
        "market_sanity_checked": False,
    }
    # Statcast window check — `has_data` means usable rows returned
    if _PYBASEBALL_OK and sport in ("mlb_pitcher", "mlb_batter"):
        pid = _pb_lookup_mlbam_id(first, last)
        if pid:
            sc = _pb_statcast_window(pid, year)
            fields["statcast_checked"] = sc.get("has_data", False)
            fields["statcast_window"] = sc
    # FanGraphs baseline check — `has_data` means player found in stats
    if _PYBASEBALL_OK and sport in ("mlb_pitcher", "mlb_batter"):
        pid = _pb_lookup_mlbam_id(first, last)
        if pid:
            fg = _pb_fangraphs_baseline(pid, year)
            fields["fangraphs_checked"] = fg.get("has_data", False)
            fields["fangraphs_baseline"] = fg
    # Pitcher-vs-opponent K simulation
    if _PYBASEBALL_OK and sport == "mlb_pitcher" and prop == "Pitcher Strikeouts" and opponent:
        pid = _pb_lookup_mlbam_id(first, last)
        if pid:
            h2h = _pb_pitcher_vs_opponent_k(pid, opponent, year)
            fields["h2h_simulation"] = h2h
    return fields


def _mlb_validate_with_workflow(data: dict, workflow: dict, prop: str, sport: str):
    """Apply the Component B validation rules on top of a scored prop.
    Returns a dict with the validated result, any capping, and a
    `validation_reason` list.
    
    Rules:
    - pitcher prop with unconfirmed starter >2h before first pitch → WATCH only
    - pitcher prop unconfirmed inside 2h → hard kill (REJECT)
    - hitter TB/HRR without weather check → add weather-park-not-checked, cap
    - pitcher K without Savant/FanGraphs → cannot pass skill-quality gate
    - stale team/no game today → slate purge kill
    """
    result = {**data}
    reasons = []
    # Starter rules (MLB pitcher props)
    if sport == "mlb_pitcher":
        starter_confirmed = workflow.get("mlb_starter_confirmed", False)
        if not starter_confirmed:
            # In the absence of real-time clock info, we default to WATCH
            # The downstream orchestrator should override with hard-kill if <2h
            reasons.append("Starting pitcher not confirmed")
            # If this is a gate call (not just advisory), cap at WATCH
            if not workflow.get("force_advisory_only", False):
                result["confidence_tier"] = "WATCH / RESEARCH ONLY"
                result["workflow_cap"] = "WATCH — starter unconfirmed"
    # Weather rules (MLB hitters)
    if sport == "mlb_batter" and prop in ("Total Bases", "H+R+RBI", "Hitter Hits"):
        if not workflow.get("weather_checked", False):
            reasons.append("weather-park-not-checked")
            result["weather_park_not_checked"] = True
            if not workflow.get("force_advisory_only", False):
                result["confidence_tier"] = "WATCH / RESEARCH ONLY"
                result["workflow_cap"] = "WATCH — weather/park unverified"
    # Skill-quality gate for pitcher K
    if sport == "mlb_pitcher" and prop == "Pitcher Strikeouts":
        if not workflow.get("statcast_checked", False) and not workflow.get("fangraphs_checked", False):
            reasons.append("Pitcher K without Savant/FanGraphs — skill quality gate blocked")
            if not workflow.get("force_advisory_only", False):
                result["confidence_tier"] = "WATCH / RESEARCH ONLY"
                result["workflow_cap"] = "WATCH — statcast/fangraphs unavailable"
    result["workflow_reasons"] = reasons
    result["workflow"] = workflow
    return result


# ── CS2 via HLTV (cloudscraper) ──────────────────────────────
# ── Component C: Soccer Props Source Stack + Process Patch ──────
_SOCCER_TIER1_SOURCES = ("api-football", "fbref", "transfermarkt", "understat")
_SOCCER_HIGH_VARIANCE_PROPS = ("goals", "assists", "shots on target",
                               "saves", "goalkeeper saves")
_SOCCER_LEAGUE_STRENGTH = {
    "epl": 1.00, "ucl": 1.00,
    "laliga": 0.98, "bundesliga": 0.98, "seriea": 0.97, "ligue1": 0.97,
    "mls": 0.90, "eredivisie": 0.88, "portugal": 0.88,
}

def _soccer_score(player, league, prop, direction, line, year="2024"):
    """Score a soccer prop using the existing /fbref-stats pipeline.
    Returns WOW-compatible dict with source provenance and workflow fields."""
    r = {"source": "", "games": [], "complete": False,
         "rows": 0, "gap": ""}
    from urllib.parse import urlencode
    params = {"player": player, "league": league, "season": year}
    url = f"http://localhost:80/fbref-stats?{urlencode(params)}"
    import requests as _req
    try:
        resp = _req.get(url, headers={"X-API-Key": os.environ.get("SCORING_API_KEY", "")},
                        timeout=20)
        if not resp.ok:
            r["gap"] = f"fbref-stats HTTP {resp.status_code}"
            return r
        data = resp.json()
    except Exception as e:
        r["gap"] = f"fbref-stats error: {type(e).__name__}: {str(e)[:200]}"
        return r
    if not data.get("ok"):
        r["gap"] = data.get("error", "fbref-stats returned ok=False")
        return r
    source = data.get("source", "unknown")
    stats  = data.get("stats", [])
    if not stats:
        r["gap"] = "fbref-stats returned no stats"
        r["source"] = source
        return r
    s = stats[0]
    appearances = s.get("appearances") or 0
    prop_lower = prop.lower()
    stat_field = None
    if prop_lower in ("goals", "goals scored"):
        stat_field = "goals"
    elif prop_lower in ("assists",):
        stat_field = "assists"
    elif prop_lower in ("shots", "shots total"):
        stat_field = "shots_total"
    elif prop_lower in ("shots on target", "sot"):
        stat_field = "shots_on"
    elif prop_lower in ("passes", "passes total"):
        stat_field = "passes_total"
    elif prop_lower in ("tackles",):
        stat_field = "tackles"
    elif prop_lower in ("yellow cards", "cards"):
        stat_field = "yellow_cards"
    elif prop_lower in ("red cards",):
        stat_field = "red_cards"
    elif prop_lower in ("fouls", "fouls committed"):
        stat_field = "fouls_committed"
    elif prop_lower in ("minutes", "minutes played"):
        stat_field = "minutes"
    elif prop_lower in ("appearances", "starts"):
        stat_field = "appearances"
    else:
        r["gap"] = f"Unsupported soccer prop: {prop}"
        r["source"] = source
        return r
    val = s.get(stat_field)
    if val is None:
        r["gap"] = f"Stat field '{stat_field}' not available for {player}"
        r["source"] = source
        return r
    r["games"] = [{
        "date": f"{year}-season",
        "stat": val,
        "hit": val >= line if direction == "MORE" else val <= line,
    }]
    r["rows"] = int(appearances) if appearances else 1
    r["complete"] = bool(appearances and val is not None)
    r["source"] = f"{source} (Tier 1 equivalent)"
    r["l5_avg"] = round(float(val), 1)
    r["l10_avg"] = round(float(val), 1)
    r["l5_hit_rate"] = "1/1 (100%)" if r["games"][0]["hit"] else "0/1 (0%)"
    r["l10_hit_rate"] = r["l5_hit_rate"]
    r["edge"] = round(float(val) - line, 1)
    r["formula"] = f"season-avg {stat_field} vs line {line}"
    return r


def _soccer_workflow_fields(player, league, prop, direction, year="2024"):
    """Build the 17 soccer workflow fields for a prop."""
    return {
        "xi_confirmed": False,
        "minutes_l5": None,
        "minutes_l10": None,
        "starts_l5": None,
        "starts_l10": None,
        "sub_pattern": None,
        "early_sub_freq": None,
        "league_tier": _canonical_league_key(league),
        "league_strength_mult": _SOCCER_LEAGUE_STRENGTH.get(
            _canonical_league_key(league), 0.90),
        "prop_risk_class": "high-variance" if any(
            p in prop.lower() for p in _SOCCER_HIGH_VARIANCE_PROPS) else "stable",
        "ref_context_available": False,
        "fixture_congestion_flag": False,
        "rotation_risk_flag": False,
        "minutes_risk_flag": False,
        "high_variance_flag": False,
        "league_translation_risk_flag": False,
        "ref_context_missing_flag": False,
    }


def _soccer_validate_with_workflow(data, workflow, prop, direction, league):
    """Apply Component C validation rules on a scored soccer prop."""
    result = {**data}
    reasons = []
    if direction == "MORE" and not workflow.get("xi_confirmed", False):
        reasons.append("soccer-xi-unconfirmed")
        if not workflow.get("force_advisory_only", False):
            result["confidence_tier"] = "WATCH / RESEARCH ONLY"
            result["workflow_cap"] = "WATCH — XI unconfirmed"
    if workflow.get("prop_risk_class") == "high-variance":
        reasons.append("soccer-high-variance-prop")
        if not workflow.get("force_advisory_only", False):
            result["confidence_tier"] = "WATCH / RESEARCH ONLY"
            result["workflow_cap"] = "WATCH — high-variance prop"
            result["high_variance_flag"] = True
            if "workflow_fields" in result:
                result["workflow_fields"]["high_variance_flag"] = True
    prop_lower = prop.lower()
    if prop_lower in ("yellow cards", "red cards", "cards", "fouls", "fouls committed"):
        if not workflow.get("ref_context_available", False):
            reasons.append("soccer-ref-context-missing")
            result["ref_context_missing_flag"] = True
            if "workflow_fields" in result:
                result["workflow_fields"]["ref_context_missing_flag"] = True
            if not workflow.get("force_advisory_only", False):
                result["confidence_tier"] = "WATCH / RESEARCH ONLY"
                result["workflow_cap"] = "WATCH — ref context missing"
    league_mult = workflow.get("league_strength_mult", 1.0)
    if league_mult < 0.95:
        reasons.append("soccer-league-translation-risk")
        result["league_translation_risk_flag"] = True
        if "workflow_fields" in result:
            result["workflow_fields"]["league_translation_risk_flag"] = True
    if workflow.get("minutes_risk_flag"):
        reasons.append("soccer-minutes-risk")
    if workflow.get("rotation_risk_flag"):
        reasons.append("soccer-rotation-risk")
    if workflow.get("fixture_congestion_flag"):
        reasons.append("soccer-fixture-congestion")
    result["workflow_reasons"] = reasons
    result["workflow"] = workflow
    return result


_CS2_COLS = {
    "Kills":"Kills","Headshots":"Headshots",
    "Maps 1-2 Kills":"Kills","Maps 1-2 Headshots":"Headshots",
    "Rating":"Rating",
}

def _cs2(player_name, hltv_id, prop, direction, line):
    r = {"source": "hltv.org", "games": [], "complete": False,
         "rows": 0, "gap": ""}
    # HLTV is fully behind Cloudflare's active JS challenge as of 2026-05.
    # Verified dead against this endpoint: cloudscraper (6 configs), curl-cffi
    # (5 impersonations), and 6 community proxy APIs. Only a real headless
    # browser (Playwright/Chromium) can bypass it. CS2 is intentionally punted
    # to manual entry, matching Pikkit DES/Proj. The parser below is kept so a
    # future bypass (working proxy, Playwright sidecar) can plug straight in.
    r["gap"] = "CS2/HLTV requires manual entry — Cloudflare blocks automated fetch"
    return r
    # --- unreachable: preserved for future bypass ---
    if not _CS_OK:
        r["gap"] = "cloudscraper not installed — add to requirements.txt"; return r
    slug = player_name.lower().replace(" ","-")
    url  = f"https://www.hltv.org/stats/players/matches/{hltv_id}/{slug}"
    scraper = _cs_lib.create_scraper(
        browser={"browser":"chrome","platform":"windows","desktop":True})
    try:
        resp = scraper.get(url, timeout=20)
    except Exception as e:
        r["gap"] = f"HLTV fetch exception: {e}"; return r
    if resp.status_code != 200:
        r["gap"] = (f"HLTV HTTP {resp.status_code} — "
                    "cloudscraper challenge may be stale, try Playwright fallback")
        return r
    if not _BS4_OK:
        r["gap"] = "beautifulsoup4 not installed"; return r
    soup  = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"class": "stats-table"})
    if not table:
        r["gap"] = "HLTV stats-table not found — page structure may have changed"
        return r
    field = _CS2_COLS.get(prop, "Kills")
    thead = table.find("thead")
    headers = [th.get_text(strip=True) for th in thead.find_all("th")] if thead else []
    try:
        fi = headers.index(field)
    except ValueError:
        r["gap"] = f"Column '{field}' not in HLTV headers: {headers}"; return r
    games = []
    tbody = table.find("tbody")
    rows  = tbody.find_all("tr") if tbody else []
    for tr in rows[:10]:
        cols = tr.find_all("td")
        if len(cols) <= fi: continue
        val = _f(cols[fi].get_text(strip=True))
        if val is None: continue
        date_txt = cols[0].get_text(strip=True) if cols else ""
        opp_a    = cols[2].find("a") if len(cols) > 2 else None
        opp_txt  = opp_a.get_text(strip=True) if opp_a \
                   else (cols[2].get_text(strip=True) if len(cols) > 2 else "")
        map_txt  = cols[3].get_text(strip=True) if len(cols) > 3 else ""
        games.append({"g": len(games)+1, "date": date_txt, "opp": opp_txt,
                      "context": map_txt, "value": val,
                      "hit": _hit(val, line, direction), "notes": ""})
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} HLTV rows"
    r.update(_stats(games, line, direction)); return r


# ── Tennis via Tennis Abstract ───────────────────────────────
_TENNIS_COLS = {
    "Aces":"Aces","Double Faults":"DblFaults","1st Serve %":"1stIn",
}

def _tennis(first, last, prop, direction, line):
    import requests as _req
    from io import StringIO
    r = {"source": "tennisabstract.com", "games": [],
         "complete": False, "rows": 0, "gap": ""}
    if not _BS4_OK:
        r["gap"] = "pandas not installed"; return r
    name = f"{first}{last}".replace(" ","")
    url  = f"http://tennisabstract.com/cgi-bin/player.cgi?p={name}"
    try:
        resp = _req.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if resp.status_code != 200:
            r["gap"] = f"Tennis Abstract HTTP {resp.status_code}"; return r
        tables = pd.read_html(StringIO(resp.text))
    except Exception as e:
        r["gap"] = f"Fetch/parse error: {e}"; return r
    match_df = None
    for t in tables:
        cols = [str(c).strip() for c in t.columns]
        if any(k in cols for k in ("Opponent","opponent","Result","result")):
            match_df = t; break
    if match_df is None:
        r["gap"] = (f"Match table not found — verify name at "
                    f"tennisabstract.com/cgi-bin/player.cgi?p={name}")
        return r
    match_df.columns = [str(c).strip() for c in match_df.columns]
    match_df = match_df.dropna(how="all").reset_index(drop=True)
    field = _TENNIS_COLS.get(prop)
    if not field or field not in match_df.columns:
        r["gap"] = (f"Column '{field}' not found. "
                    f"Available: {list(match_df.columns)}")
        return r
    games = []
    for _, row in match_df.head(10).iterrows():
        val = _f(row.get(field))
        if val is None: continue
        games.append({"g": len(games)+1,
                      "date":    str(row.get("Date", row.get("date",""))).strip()[:10],
                      "opp":     str(row.get("Opponent", row.get("opponent",""))).strip(),
                      "context": str(row.get("Surface", row.get("surface",""))).strip(),
                      "value":   val,
                      "hit":     _hit(val, line, direction),
                      "notes":   str(row.get("Result","")).strip()})
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} match rows"
    r.update(_stats(games, line, direction)); return r


# ── /wow/l10/v2 dispatch helper (callable in-process by orchestrator) ──
def _score_one_prop_v2(*, player, sport, prop, direction, line,
                       season="2025-26", mlb_ssn="2026", year="2026",
                       hltv_id=None, league=None):
    """Dispatch a single prop to the correct sport handler and return
    (data_dict, error_message). error_message is None on success.
    Shared by `wow_l10_v2()` and the Connected Model orchestrator so both
    paths use identical scoring logic."""
    parts = (player or "").split(" ", 1)
    first = parts[0]; last = parts[1] if len(parts) > 1 else ""

    if   prop  == "Pitcher Fantasy Score":
        return _pitcher_fantasy_score(first, last, direction, line, year), None
    elif prop  == "1st Inn. Pitches Thrown":
        return _first_inn_pitches(first, last, direction, line, mlb_ssn), None
    elif sport == "cs2":
        if not hltv_id:
            return None, ("CS2 needs hltv_id=NNNN "
                          "(look up at hltv.org/stats/players)")
        return _cs2(player, hltv_id, prop, direction, line), None
    elif sport == "tennis":
        return _tennis(first, last, prop, direction, line), None
    elif sport == "nba":
        return _nba(first, last, prop, direction, line, season), None
    elif sport in ("mlb_batter","mlb_pitcher"):
        # Component B: pybaseball Tier 1 equivalent first, manual Tier 1 fallback.
        pb_failed = False
        if _PYBASEBALL_OK:
            pid = _pb_lookup_mlbam_id(first, last)
            if pid:
                pb_data = _pb_statcast_ledgers(pid, prop, direction, line, year)
                if not pb_data.get("gap"):
                    # Success — pybaseball delivered usable data.
                    wow_data = _pb_to_wow_format(pb_data, prop, direction, line)
                    if wow_data:
                        # Attach workflow fields
                        wf = _mlb_workflow_fields(first, last, sport, prop, year)
                        wow_data["workflow_fields"] = wf
                        # Apply validation gating (WATCH caps, etc.)
                        wow_data = _mlb_validate_with_workflow(wow_data, wf, prop, sport)
                        return wow_data, None
                pb_failed = True
        # Fallback: manual Tier 1 ledger (BBRef)
        bb_data = _bbref(first, last, sport, prop, direction, line, year)
        if bb_data.get("gap"):
            bb_data["source"] = (bb_data.get("source") or "") + " (Failed/Not Available)"
        elif pb_failed:
            # Preserve pybaseball failure provenance even when BBRef succeeds
            bb_data["source"] = (bb_data.get("source") or "") + " (pybaseball failed)"
        # Apply workflow fields and validation even on BBRef fallback
        wf = _mlb_workflow_fields(first, last, sport, prop, year)
        bb_data["workflow_fields"] = wf
        bb_data = _mlb_validate_with_workflow(bb_data, wf, prop, sport)
        return bb_data, None
    elif sport == "soccer":
        # Component C: soccer scoring with workflow fields and validation
        # League is passed via the `league` param or defaults to epl
        sc_league = league or "epl"
        sc_data = _soccer_score(player, sc_league, prop, direction, line, year)
        wf = _soccer_workflow_fields(player, sc_league, prop, direction, year)
        sc_data["workflow_fields"] = wf
        sc_data = _soccer_validate_with_workflow(sc_data, wf, prop, direction, sc_league)
        return sc_data, None
    elif sport in ("wnba","nfl"):
        return _bbref(first, last, sport, prop, direction, line, year), None
    return None, f"Unknown sport '{sport}'"


# ── /wow/l10/v2 patches: provenance label + input normalization ──
# PATCH 1 — data_quality (provenance label). Derived from `source` +
# `complete`; ADDS a field, never replaces `confidence_tier`.
_V2_TIER1_SOURCES  = ("stats.nba.com", "statsapi.mlb.com", "site.api.espn.com")
_V2_MANUAL_SOURCES = ("baseball-reference.com", "bbref", "pd.read_html")
_V2_PROXY_SOURCES  = ("espn narrative", "rotowire", "fox sports", "season average")

def _v2_data_quality(source, rows, complete):
    """PATCH 1: map the data source + completeness to a provenance label
    (Verified / Manually Reconstructed / Proxy Only / Missing)."""
    if not rows or not complete:
        return "Missing"
    s = (source or "").lower()
    if any(t in s for t in _V2_TIER1_SOURCES):
        return "Verified"
    if any(m in s for m in _V2_MANUAL_SOURCES):
        return "Manually Reconstructed"
    if any(p in s for p in _V2_PROXY_SOURCES):
        return "Proxy Only"
    return "Manually Reconstructed"

# PATCH 3 — input normalization. Canonical keys already accepted by the
# sport handlers are passed through untouched; loose inputs are mapped to a
# real column-map key so bad input can't masquerade as a data gap.
_V2_ALL_PROP_KEYS = (set(_NBA_COLS) | set(_MLB_COLS) | set(_CS2_COLS)
                     | set(_TENNIS_COLS)
                     | {"Pitcher Fantasy Score", "1st Inn. Pitches Thrown"}
                     | {"Goals", "Assists", "Shots", "Shots On Target",
                        "Passes", "Tackles", "Yellow Cards", "Red Cards", "Fouls"})

_V2_PROP_ALIASES = {
    "points": "Points", "rebounds": "Rebounds", "assists": "Assists",
    "steals": "Steals", "blocks": "Blocks",
    "3pm": "3-PT Made", "threes": "3-PT Made", "3-pt made": "3-PT Made",
    "pra": "Pts+Rebs+Asts", "pts+reb+ast": "Pts+Rebs+Asts",
    "pr": "Pts+Rebs", "pa": "Pts+Asts", "ra": "Rebs+Asts",
    "total bases": "Total Bases", "rbis": "RBIs", "runs scored": "Runs Scored",
    "h+r+rbi": "H+R+RBI",
    "hits": "Hitter Hits", "hitter hits": "Hitter Hits",
    "hitter strikeouts": "Hitter Strikeouts",
    "strikeouts": "Pitcher Strikeouts", "pitcher strikeouts": "Pitcher Strikeouts",
    "hits allowed": "Hits Allowed", "earned runs": "Earned Runs",
    "walks allowed": "Walks Allowed",
    "kills": "Kills", "headshots": "Headshots", "rating": "Rating",
    "aces": "Aces", "double faults": "Double Faults", "1st serve %": "1st Serve %",
    "goals": "Goals", "assists": "Assists",
    "shots": "Shots", "shots on target": "Shots On Target", "sot": "Shots On Target",
    "passes": "Passes", "tackles": "Tackles",
    "yellow cards": "Yellow Cards", "cards": "Yellow Cards",
    "red cards": "Red Cards", "fouls": "Fouls",
}

_V2_SPORT_ALIASES = {
    "nba": "nba", "wnba": "wnba", "nfl": "nfl", "tennis": "tennis",
    "cs2": "cs2", "csgo": "cs2", "counter-strike": "cs2", "cs": "cs2",
    "soccer": "soccer", "football": "soccer", "epl": "soccer",
    "premier-league": "soccer", "laliga": "soccer", "bundesliga": "soccer",
    "seriea": "soccer", "ligue1": "soccer", "mls": "soccer",
    "ucl": "soccer", "champions-league": "soccer",
}

def _v2_normalize_inputs(raw_sport, raw_prop, prop_type_hint):
    """PATCH 3: convert loose sport/prop inputs to canonical dispatch keys
    before scoring. Returns (sport, prop, normalization_log)."""
    log  = []
    s_in = (raw_sport or "").strip().lower()
    p_in = (raw_prop or "").strip()

    if s_in == "mlb":
        if prop_type_hint == "pitcher":
            sport = "mlb_pitcher"
        elif prop_type_hint == "batter":
            sport = "mlb_batter"
        else:
            pl = p_in.lower()
            sport = ("mlb_pitcher"
                     if (pl in _MLB_PITCHER_PROPS or "pitcher" in pl)
                     else "mlb_batter")
        log.append(f"sport: 'mlb' → '{sport}'")
    else:
        sport = _V2_SPORT_ALIASES.get(s_in, s_in)
        if sport != s_in:
            log.append(f"sport: '{s_in}' → '{sport}'")

    if p_in in _V2_ALL_PROP_KEYS:
        prop = p_in
    else:
        alias = _V2_PROP_ALIASES.get(p_in.lower())
        if alias:
            prop = alias
            log.append(f"prop: '{p_in}' → '{prop}'")
        else:
            prop = p_in.title()
            if prop != p_in:
                log.append(f"prop: '{p_in}' → '{prop}'")
    return sport, prop, log

def _v2_reject_reason(gap, rows, complete):
    """PATCH 3: classify a REJECT so bad input is distinguishable from a
    real data gap (INPUT_FORMAT_ERROR / FETCH_FAILED / INSUFFICIENT_DATA).
    Order matters: caller-fixable input/identifier errors are checked before
    the broader source-unreachable bucket."""
    g = (gap or "").lower()
    # Bad/unknown inputs or unresolvable player identifiers — caller-fixable.
    if ("column map" in g or "not mapped" in g or "not in " in g
            or "needs hltv_id" in g or "unknown sport" in g
            or "required" in g or "must be" in g
            or "id may be wrong" in g or "static list" in g
            or "verify name" in g
            or ("player" in g and "not found" in g)):
        return "INPUT_FORMAT_ERROR"
    # Source unreachable / tooling missing / parse failure — not the caller.
    if ("fetch" in g or "http" in g or "exception" in g or "timeout" in g
            or "cloudflare" in g or "manual entry" in g or "not installed" in g
            or "no data" in g or "not found" in g or "table" in g
            or "page structure" in g or "parse" in g):
        return "FETCH_FAILED"
    return "INSUFFICIENT_DATA"


# ── /wow/l10/v2 main route ───────────────────────────────────
@app.route("/wow/l10/v2", methods=["GET"])
def wow_l10_v2():
    player    = request.args.get("player",    "").strip()
    raw_sport = request.args.get("sport",     "").strip()
    raw_prop  = request.args.get("prop",      "").strip()
    direction = request.args.get("direction", "MORE").strip().upper()
    line      = request.args.get("line",      type=float)
    season    = request.args.get("season",    "2025-26")
    mlb_ssn   = request.args.get("mlb_season","2026")
    year      = request.args.get("year",      "2026")
    hltv_id   = request.args.get("hltv_id",   type=int)
    prop_type = request.args.get("prop_type", "").strip().lower()
    nocache   = request.args.get("nocache",   "0") == "1"

    # PATCH 3 — normalize loose inputs before validation/dispatch.
    sport, prop, normalization_log = _v2_normalize_inputs(raw_sport, raw_prop, prop_type)

    if not all([player, sport, prop]) or line is None:
        return jsonify({"ok": False,
                        "error": "player, sport, prop, line all required",
                        "reject_reason": "INPUT_FORMAT_ERROR",
                        "data_quality": "Missing",
                        "normalization_log": normalization_log}), 400
    if direction not in ("MORE","LESS"):
        return jsonify({"ok": False,
                        "error": "direction must be MORE or LESS",
                        "reject_reason": "INPUT_FORMAT_ERROR",
                        "data_quality": "Missing",
                        "normalization_log": normalization_log}), 400

    ck = f"v2|{player}|{sport}|{prop}|{line}|{direction}|{season}|{mlb_ssn}|{year}"
    if not nocache:
        hit = _cache_get(_L10V2_CACHE, ck, _L10V2_TTL)
        if hit:
            # normalization_log is request-specific — reflect THIS request's
            # normalization, not whichever caller first populated the entry.
            return jsonify({"ok": True, "cached": True,
                            **{**hit, "normalization_log": normalization_log}})

    # Extract league for soccer; pass to scorer
    league = request.args.get("league", "epl").strip().lower() if sport == "soccer" else None
    data, err = _score_one_prop_v2(
        player=player, sport=sport, prop=prop, direction=direction, line=line,
        season=season, mlb_ssn=mlb_ssn, year=year, hltv_id=hltv_id,
        league=league,
    )
    if err:
        return jsonify({"ok": False, "error": err,
                        "reject_reason": _v2_reject_reason(err, 0, False),
                        "data_quality": "Missing",
                        "normalization_log": normalization_log}), 400

    rows     = data.get("rows", 0)
    complete = data.get("complete", False)
    gap      = data.get("gap", "")
    tier     = _tier(rows, complete)
    resp = {
        "player": player, "sport": sport, "prop": prop,
        "direction": direction, "line": line,
        "pulled_at": datetime.now().strftime("%H:%M:%S"),
        "source":    data.get("source"),
        "formula":   data.get("formula"),
        "rows":      rows,
        "complete":  complete,
        "gap":       gap,
        "games":     data.get("games",[]),
        "l5_avg":    data.get("l5_avg"),
        "l10_avg":   data.get("l10_avg"),
        "l10_median":data.get("l10_median"),
        "l5_hit_rate":  data.get("l5_hit_rate"),
        "l10_hit_rate": data.get("l10_hit_rate"),
        "edge":      data.get("edge"),
        "confidence_tier": tier,
        "data_quality": _v2_data_quality(data.get("source"), rows, complete),  # PATCH 1
        "normalization_log": normalization_log,                                # PATCH 3
    }
    # Component B: attach MLB workflow fields when present
    wf = data.get("workflow_fields")
    if wf:
        resp["workflow_fields"] = wf
    # workflow_reasons and workflow_cap are at the top level of data
    if data.get("workflow_reasons"):
        resp["workflow_reasons"] = data.get("workflow_reasons")
    if data.get("workflow_cap"):
        resp["workflow_cap"] = data.get("workflow_cap")
    if tier == "REJECT — INSUFFICIENT DATA":
        resp["reject_reason"] = _v2_reject_reason(gap, rows, complete)         # PATCH 3
    if rows >= 3:
        _cache_set(_L10V2_CACHE, ck, resp)
    return jsonify({"ok": True, "cached": False, **resp})


# ── WOW MLB Pitcher Prop Gate ─────────────────────────────────────────────────
_PITCH_COUNT_PROPS = {"1st inning pitches", "pitcher pitches", "total pitches"}

@app.route("/wow/mlb/pitcher", methods=["POST"])
@require_api_key
def wow_mlb_pitcher():
    """POST /wow/mlb/pitcher
    Returns leash score, Savant indicators, opponent K%, and gate flags
    for a single MLB pitcher prop.

    Body:
      { "first": str, "last": str, "opponent_team": str,
        "prop": str, "line": number, "side": "MORE"|"LESS" }
    """
    body = request.get_json(silent=True) or {}
    first    = (body.get("first")         or "").strip()
    last     = (body.get("last")          or "").strip()
    opp      = (body.get("opponent_team") or "").strip().upper()
    prop     = (body.get("prop")          or "").strip()
    side     = (body.get("side")          or "MORE").strip().upper()
    try:
        line = float(body.get("line", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "line must be a number"}), 400

    if not first or not last:
        return jsonify({"ok": False, "error": "first and last are required"}), 400

    year = str(datetime.now().year)

    # 1. BBRef game log → leash score
    try:
        bbref_raw = _l10_bbref(first, last, "mlb_pitcher", prop, side, line, year)
        bbref_log = bbref_raw.get("games", []) if isinstance(bbref_raw, dict) else []
    except Exception as e:
        bbref_log = []

    if bbref_log:
        leash = _leash_score(bbref_log)
    else:
        leash = _leash_score_from_statcast(first, last)
    savant      = _get_pitcher_savant(first, last)
    opp_k       = _get_opponent_k_rate(opp) if opp else {"error": "NO_OPPONENT_PROVIDED"}
    platoon     = _get_pitcher_platoon_splits(first, last)
    line_move   = _get_line_movement(f"{first} {last}", prop, "mlb", side, line)

    # 2. Efficiency gap — pitch-count props only, NOT strikeouts
    efficiency = None
    if prop.lower() in _PITCH_COUNT_PROPS and line > 0:
        efficiency = _calculate_efficiency_gap(bbref_log, line)

    # 3. Fantasy score — only if requested
    fantasy = None
    if prop.lower() in ("pitcher fantasy score", "fantasy score"):
        try:
            fantasy = _pitcher_fantasy_score(first, last, side, line, year)
        except Exception as e:
            fantasy = {"error": str(e)[:200]}

    # 4. Gate flags
    gate_flags: list[str] = []

    ls = leash.get("leash_score")
    if ls is not None and ls >= 7:
        gate_flags.append("LEASH_KILL_RISK")

    if efficiency and efficiency.get("flag") == "LINE_ABOVE_RECENT_EFFICIENCY":
        gate_flags.append("LINE_ABOVE_RECENT_EFFICIENCY")

    if prop == "Pitcher Strikeouts" and line <= 5.0:
        gate_flags.append("WATCH_TIGHT_K_LESS_LINE")

    tight_k_trap = (
        prop == "Pitcher Strikeouts"
        and side == "LESS"
        and line <= 5.0
        and leash.get("leash_grade") != "SHORT"
    )
    if tight_k_trap:
        gate_flags.append("REJECT_ONE_K_MARGIN_TRAP")

    # 5. Line movement gate flags
    if line_move.get("steam") and line_move.get("steam_favors") == "AGAINST":
        gate_flags.append("WATCH_STEAM_AGAINST_BET")
    if line_move.get("steam") and line_move.get("steam_favors") == "BET":
        gate_flags.append("NOTE_STEAM_FAVORS_BET")

    # 6. WOW verdict
    if any(f.startswith("REJECT_") for f in gate_flags):
        wow_verdict = "GATE_KILL"
    elif any(f.startswith("WATCH_") or f == "LEASH_KILL_RISK" for f in gate_flags):
        wow_verdict = "GATE_WATCH"
    else:
        wow_verdict = "GATE_PASS"

    data_sources = ["bbref"]
    if not savant.get("error"):
        data_sources.append("statcast")
    if not opp_k.get("error"):
        data_sources.append("mlb_stats_api")
    if not platoon.get("error"):
        data_sources.append("mlb_platoon")
    if line_move.get("opening_line") is not None:
        data_sources.append("line_movement")

    return jsonify({
        "ok":             True,
        "player":         f"{first} {last}",
        "prop":           prop,
        "line":           line,
        "side":           side,
        "opponent":       opp,
        "leash":          leash,
        "savant":         savant,
        "opponent_k_pct": opp_k,
        "platoon_splits": platoon,
        "line_movement":  line_move,
        "efficiency":     efficiency,
        "fantasy":        fantasy,
        "gate_flags":     gate_flags,
        "wow_verdict":    wow_verdict,
        "data_sources":   data_sources,
    })


# ── Cross-sport: line movement query ──────────────────────────────────────
@app.route("/wow/line-movement", methods=["POST"])
@require_api_key
def wow_line_movement():
    """POST /wow/line-movement
    Compare current line to opening line for any sport/prop.

    Body: { player, prop, sport, side, line, date (opt), variance (bool opt) }
    Returns movement direction, steam flag, and optional cross-book spread.
    """
    body    = request.get_json(silent=True) or {}
    player  = (body.get("player")  or "").strip()
    prop    = (body.get("prop")    or "").strip()
    sport   = (body.get("sport")   or "").strip().lower()
    side    = (body.get("side")    or "MORE").strip().upper()
    date    = (body.get("date")    or "").strip()
    variance = bool(body.get("variance", False))
    try:
        line = float(body.get("line", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "line must be numeric"}), 400
    if not player or not prop or not sport:
        return jsonify({"ok": False, "error": "player, prop, and sport are required"}), 400
    result = _get_line_movement(player, prop, sport, side, line,
                                game_date=date, include_variance=variance)
    return jsonify({"ok": True, "player": player, "prop": prop,
                    "sport": sport, "side": side, **result})


# ── WNBA player prop gate ──────────────────────────────────────────────────
@app.route("/wow/wnba/player", methods=["POST"])
@require_api_key
def wow_wnba_player():
    """POST /wow/wnba/player
    WNBA player prop evaluator — L10 game log + opponent defensive rating
    + line movement.

    Body:
      { "first": str, "last": str, "opponent_team": str,
        "prop": str, "line": number, "side": "MORE"|"LESS" }

    Returns:
      l10           — last-10 game log + stats (via _l10_bbref/ESPN fallback)
      def_rating    — opponent defensive rating (ESPN WNBA standings)
      line_movement — opening vs current line
      gate_flags    — WOW gate flags
      wow_verdict   — GATE_PASS | GATE_WATCH | GATE_KILL
    """
    body  = request.get_json(silent=True) or {}
    first = (body.get("first")         or "").strip()
    last  = (body.get("last")          or "").strip()
    opp   = (body.get("opponent_team") or "").strip().upper()
    prop  = (body.get("prop")          or "").strip()
    side  = (body.get("side")          or "MORE").strip().upper()
    try:
        line = float(body.get("line", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "line must be a number"}), 400
    if not first or not last:
        return jsonify({"ok": False, "error": "first and last are required"}), 400

    year = str(datetime.now().year)

    # L10 game log
    try:
        l10_raw = _l10_bbref(first, last, "wnba", prop, side, line, year)
    except Exception:
        l10_raw = {"games": [], "gap": "bbref_unavailable"}

    # Opponent defensive rating
    def_rating = _get_wnba_def_rating(opp) if opp else {"error": "NO_OPPONENT_PROVIDED"}

    # Line movement
    line_move = _get_line_movement(f"{first} {last}", prop, "wnba", side, line)

    # Gate flags
    gate_flags: list[str] = []
    games = l10_raw.get("games", [])
    hit_rate = l10_raw.get("hit_rate") or l10_raw.get("over_rate")

    if hit_rate is not None and hit_rate < 0.30 and side == "MORE":
        gate_flags.append("WATCH_LOW_HIT_RATE_MORE")
    if hit_rate is not None and hit_rate > 0.70 and side == "LESS":
        gate_flags.append("WATCH_LOW_HIT_RATE_LESS")

    tier = def_rating.get("tier", "")
    if tier == "ELITE" and side == "MORE":
        gate_flags.append("WATCH_ELITE_DEF_VS_MORE")
    if tier == "WEAK" and side == "LESS":
        gate_flags.append("WATCH_WEAK_DEF_VS_LESS")

    if line_move.get("steam") and line_move.get("steam_favors") == "AGAINST":
        gate_flags.append("WATCH_STEAM_AGAINST_BET")
    if line_move.get("steam") and line_move.get("steam_favors") == "BET":
        gate_flags.append("NOTE_STEAM_FAVORS_BET")

    if any(f.startswith("REJECT_") for f in gate_flags):
        wow_verdict = "GATE_KILL"
    elif any(f.startswith("WATCH_") for f in gate_flags):
        wow_verdict = "GATE_WATCH"
    else:
        wow_verdict = "GATE_PASS"

    data_sources = []
    if games:
        data_sources.append("bbref_wnba")
    if not def_rating.get("error"):
        data_sources.append("espn_wnba")
    if line_move.get("opening_line") is not None:
        data_sources.append("line_movement")

    return jsonify({
        "ok":          True,
        "player":      f"{first} {last}",
        "prop":        prop,
        "line":        line,
        "side":        side,
        "opponent":    opp,
        "l10":         l10_raw,
        "def_rating":  def_rating,
        "line_movement": line_move,
        "gate_flags":  gate_flags,
        "wow_verdict": wow_verdict,
        "data_sources": data_sources,
    })


# ── CLV (Closing Line Value) tracker ─────────────────────────────────────
_CLV_SCHEMA_READY = False
_CLV_SCHEMA_LOCK  = threading.Lock()

def _ensure_clv_schema(conn) -> None:
    global _CLV_SCHEMA_READY
    if _CLV_SCHEMA_READY:
        return
    with _CLV_SCHEMA_LOCK:
        if _CLV_SCHEMA_READY:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS closing_lines (
                    id            SERIAL PRIMARY KEY,
                    player        TEXT NOT NULL,
                    player_lower  TEXT NOT NULL,
                    prop          TEXT NOT NULL,
                    sport         TEXT,
                    side          TEXT NOT NULL DEFAULT 'over',
                    line_date     DATE NOT NULL,
                    pick_line     NUMERIC(8,2) NOT NULL,
                    closing_line  NUMERIC(8,2),
                    result        TEXT,
                    notes         TEXT,
                    captured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    closed_at     TIMESTAMPTZ,
                    UNIQUE (player_lower, prop, side, line_date)
                );
                CREATE INDEX IF NOT EXISTS closing_lines_date_idx
                    ON closing_lines (line_date);
                CREATE INDEX IF NOT EXISTS closing_lines_player_idx
                    ON closing_lines (player_lower);
            """)
            conn.commit()
        _CLV_SCHEMA_READY = True


@app.route("/lines/closing", methods=["POST"])
@require_api_key
def lines_closing_store():
    """Store or update a closing line for CLV tracking.

    Body: { player, prop, sport, side, pick_line, closing_line,
            result (opt: WIN|LOSS|PUSH), date (opt), notes (opt) }
    - Call with only pick_line at bet time to create the record.
    - Call again with closing_line (and result) after the game to close it.
    """
    import psycopg2 as _pg, psycopg2.extras as _pgx
    from datetime import date as _date, datetime as _dt, timezone as _tz

    body        = request.get_json(silent=True) or {}
    player      = (body.get("player")  or "").strip()
    prop        = (body.get("prop")    or "").strip().lower()
    sport       = (body.get("sport")   or "").strip().lower() or None
    side        = (body.get("side")    or "over").strip().lower()
    raw_date    = (body.get("date")    or "").strip() or _dt.now(_tz.utc).date().isoformat()
    notes       = (body.get("notes")   or None)
    result_val  = (body.get("result")  or None)

    if not player or not prop:
        return jsonify({"ok": False, "error": "player and prop are required"}), 400
    try:
        pick_line    = float(body["pick_line"])   if "pick_line"   in body else None
        closing_line = float(body["closing_line"]) if "closing_line" in body else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pick_line and closing_line must be numeric"}), 400

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return jsonify({"ok": False, "error": "DATABASE_URL not configured"}), 500
    try:
        conn = _pg.connect(db_url)
        try:
            _ensure_clv_schema(conn)
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                if pick_line is not None:
                    cur.execute("""
                        INSERT INTO closing_lines
                            (player, player_lower, prop, sport, side, line_date, pick_line, notes)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (player_lower, prop, side, line_date)
                        DO UPDATE SET notes = EXCLUDED.notes
                        RETURNING id, pick_line, closing_line, captured_at
                    """, (player, player.lower(), prop, sport, side,
                          raw_date, pick_line, notes))
                elif closing_line is not None:
                    cur.execute("""
                        UPDATE closing_lines
                           SET closing_line = %s,
                               result       = %s,
                               closed_at    = NOW()
                         WHERE player_lower = %s AND prop = %s
                           AND side = %s AND line_date = %s
                        RETURNING id, pick_line, closing_line, closed_at
                    """, (closing_line, result_val,
                          player.lower(), prop, side, raw_date))
                else:
                    return jsonify({"ok": False, "error": "provide pick_line or closing_line"}), 400
                row = cur.fetchone()
                conn.commit()
                return jsonify({"ok": True, "record": dict(row) if row else None})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"DB error: {e}"}), 500


@app.route("/lines/clv", methods=["GET"])
@require_api_key
def lines_clv_summary():
    """GET /lines/clv?player=X&prop=Y&sport=Z&days=30

    Compute average closing line value for a player/prop/sport.
    CLV = closing_line - pick_line  (positive = we beat the close).

    Returns: avg_clv, records, win_rate, hit_rate_vs_close, sample.
    """
    import psycopg2 as _pg, psycopg2.extras as _pgx
    from datetime import date as _date, timedelta as _td

    player = request.args.get("player", "").strip()
    prop   = request.args.get("prop",   "").strip().lower()
    sport  = request.args.get("sport",  "").strip().lower() or None
    try:
        days = int(request.args.get("days", 90))
    except ValueError:
        days = 90

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return jsonify({"ok": False, "error": "DATABASE_URL not configured"}), 500
    try:
        conn = _pg.connect(db_url)
        try:
            _ensure_clv_schema(conn)
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                since = (_date.today() - _td(days=days)).isoformat()
                where_clauses = ["line_date >= %s"]
                params: list = [since]
                if player:
                    where_clauses.append("player_lower = %s")
                    params.append(player.lower())
                if prop:
                    where_clauses.append("prop = %s")
                    params.append(prop)
                if sport:
                    where_clauses.append("sport = %s")
                    params.append(sport)
                where = " AND ".join(where_clauses)
                cur.execute(f"""
                    SELECT player, prop, sport, side, line_date,
                           pick_line, closing_line, result
                    FROM closing_lines
                    WHERE {where}
                    ORDER BY line_date DESC
                    LIMIT 200
                """, params)
                rows = [dict(r) for r in cur.fetchall()]

                closed = [r for r in rows if r["closing_line"] is not None]
                clv_vals = [
                    float(r["closing_line"]) - float(r["pick_line"])
                    for r in closed
                ]
                wins = sum(1 for r in closed if r.get("result") == "WIN")
                beat_close = sum(1 for v in clv_vals if v > 0)

                return jsonify({
                    "ok":              True,
                    "records_total":   len(rows),
                    "records_closed":  len(closed),
                    "avg_clv":         round(sum(clv_vals)/len(clv_vals), 3) if clv_vals else None,
                    "beat_close_pct":  round(beat_close/len(clv_vals), 3)   if clv_vals else None,
                    "win_rate":        round(wins/len(closed), 3)            if closed   else None,
                    "days_window":     days,
                    "sample":          rows[:20],
                })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"DB error: {e}"}), 500


# ── Component B: CSV export for MLB props ───────────────────────
@app.route("/wow/mlb-export", methods=["POST"])
@require_api_key
def wow_mlb_export():
    """POST a list of MLB-scored props and receive a CSV export with all
    WOW fields + the 11 Component B workflow fields."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "request body must be a JSON object"}), 400
    props = body.get("props") if isinstance(body.get("props"), list) else []
    if not props:
        return jsonify({"ok": False, "error": "provide a non-empty 'props' array"}), 400

    import csv, io
    out = io.StringIO()
    fieldnames = [
        "player", "sport", "prop", "side", "line",
        "rows", "complete", "confidence_tier", "source", "data_quality",
        "l10_avg", "l10_hit_rate", "edge", "gap",
        "weather_checked", "weather_source", "weather_risk",
        "weather_park_not_checked",
        "mlb_starter_confirmed", "starter_confirmation_source",
        "lineup_confirmed", "batting_order_slot",
        "statcast_checked", "fangraphs_checked",
        "market_sanity_checked",
        "workflow_cap", "workflow_reasons",
    ]
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    for p in props:
        if not isinstance(p, dict):
            continue
        wf = p.get("workflow_fields", {}) if isinstance(p.get("workflow_fields"), dict) else {}
        reasons = p.get("workflow_reasons", []) if isinstance(p.get("workflow_reasons"), list) else []
        row = {
            "player": p.get("player"),
            "sport": p.get("sport"),
            "prop": p.get("prop"),
            "side": p.get("side") or p.get("direction"),
            "line": p.get("line"),
            "rows": p.get("rows"),
            "complete": p.get("complete"),
            "confidence_tier": p.get("confidence_tier"),
            "source": p.get("source"),
            "data_quality": p.get("data_quality"),
            "l10_avg": p.get("l10_avg"),
            "l10_hit_rate": p.get("l10_hit_rate"),
            "edge": p.get("edge"),
            "gap": p.get("gap", ""),
            "weather_checked": wf.get("weather_checked"),
            "weather_source": wf.get("weather_source"),
            "weather_risk": wf.get("weather_risk"),
            "weather_park_not_checked": wf.get("weather_park_not_checked"),
            "mlb_starter_confirmed": wf.get("mlb_starter_confirmed"),
            "starter_confirmation_source": wf.get("starter_confirmation_source"),
            "lineup_confirmed": wf.get("lineup_confirmed"),
            "batting_order_slot": wf.get("batting_order_slot"),
            "statcast_checked": wf.get("statcast_checked"),
            "fangraphs_checked": wf.get("fangraphs_checked"),
            "market_sanity_checked": wf.get("market_sanity_checked"),
            "workflow_cap": p.get("workflow_cap"),
            "workflow_reasons": " | ".join(reasons),
        }
        writer.writerow(row)
    out.seek(0)
    return (
        out.getvalue(),
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": "attachment; filename=wow_mlb_export.csv",
        },
    )


# ── Component C: CSV export for soccer props ───────────────
@app.route("/wow/soccer-export", methods=["POST"])
@require_api_key
def wow_soccer_export():
    """POST a list of soccer-scored props and receive a CSV export with all
    WOW fields + the Component C workflow fields."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "request body must be a JSON object"}), 400
    props = body.get("props") if isinstance(body.get("props"), list) else []
    if not props:
        return jsonify({"ok": False, "error": "provide a non-empty 'props' array"}), 400
    import csv, io
    out = io.StringIO()
    fieldnames = [
        "player", "sport", "prop", "side", "line",
        "rows", "complete", "confidence_tier", "source", "data_quality",
        "l5_avg", "l10_avg", "l5_hit_rate", "l10_hit_rate", "edge", "gap",
        "xi_confirmed", "minutes_l5", "minutes_l10", "starts_l5", "starts_l10",
        "sub_pattern", "early_sub_freq", "league_tier", "league_strength_mult",
        "prop_risk_class", "ref_context_available", "fixture_congestion_flag",
        "rotation_risk_flag", "minutes_risk_flag", "high_variance_flag",
        "league_translation_risk_flag", "ref_context_missing_flag",
        "workflow_cap", "workflow_reasons",
    ]
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    for p in props:
        if not isinstance(p, dict):
            continue
        wf = p.get("workflow_fields", {}) if isinstance(p.get("workflow_fields"), dict) else {}
        reasons = p.get("workflow_reasons", []) if isinstance(p.get("workflow_reasons"), list) else []
        row = {
            "player": p.get("player"),
            "sport": p.get("sport"),
            "prop": p.get("prop"),
            "side": p.get("side") or p.get("direction"),
            "line": p.get("line"),
            "rows": p.get("rows"),
            "complete": p.get("complete"),
            "confidence_tier": p.get("confidence_tier"),
            "source": p.get("source"),
            "data_quality": p.get("data_quality"),
            "l5_avg": p.get("l5_avg"),
            "l10_avg": p.get("l10_avg"),
            "l5_hit_rate": p.get("l5_hit_rate"),
            "l10_hit_rate": p.get("l10_hit_rate"),
            "edge": p.get("edge"),
            "gap": p.get("gap", ""),
            "xi_confirmed": wf.get("xi_confirmed"),
            "minutes_l5": wf.get("minutes_l5"),
            "minutes_l10": wf.get("minutes_l10"),
            "starts_l5": wf.get("starts_l5"),
            "starts_l10": wf.get("starts_l10"),
            "sub_pattern": wf.get("sub_pattern"),
            "early_sub_freq": wf.get("early_sub_freq"),
            "league_tier": wf.get("league_tier"),
            "league_strength_mult": wf.get("league_strength_mult"),
            "prop_risk_class": wf.get("prop_risk_class"),
            "ref_context_available": wf.get("ref_context_available"),
            "fixture_congestion_flag": wf.get("fixture_congestion_flag"),
            "rotation_risk_flag": wf.get("rotation_risk_flag"),
            "minutes_risk_flag": wf.get("minutes_risk_flag"),
            "high_variance_flag": wf.get("high_variance_flag"),
            "league_translation_risk_flag": wf.get("league_translation_risk_flag"),
            "ref_context_missing_flag": wf.get("ref_context_missing_flag"),
            "workflow_cap": p.get("workflow_cap"),
            "workflow_reasons": " | ".join(reasons),
        }
        writer.writerow(row)
    out.seek(0)
    return (
        out.getvalue(),
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": "attachment; filename=wow_soccer_export.csv",
        },
    )


# ── PATCH 2 — /wow/health: per-sport data-source probe ───────────
@app.route("/wow/health", methods=["GET"])
def wow_health():
    """Lightweight per-sport probe of each data source. Returns per-sport
    status plus an overall UP/PARTIAL flag (cs2 is manual-only by design and
    excluded from the overall calc)."""
    import requests as _req
    results = {}

    # NBA — nba_api (stats.nba.com)
    try:
        results["nba"] = "Available" if _NBA_OK else "Degraded — nba_api not installed"
    except Exception as e:
        results["nba"] = f"Degraded — {str(e)[:60]}"

    # MLB pitcher — MLB Stats API
    try:
        r = _req.get("https://statsapi.mlb.com/api/v1/sports", timeout=5)
        results["mlb_pitcher"] = "Available" if r.ok else f"Degraded — HTTP {r.status_code}"
    except Exception as e:
        results["mlb_pitcher"] = f"Degraded — {str(e)[:60]}"

    # MLB batter — baseball-reference
    try:
        r = _req.get("https://www.baseball-reference.com/robots.txt", timeout=5)
        results["mlb_batter"] = "Available" if r.ok else f"Degraded — HTTP {r.status_code}"
    except Exception as e:
        results["mlb_batter"] = f"Degraded — {str(e)[:60]}"

    # WNBA — ESPN public JSON
    try:
        r = _req.get("https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
                     timeout=5)
        results["wnba"] = "Available" if r.ok else f"Degraded — HTTP {r.status_code}"
    except Exception as e:
        results["wnba"] = f"Degraded — {str(e)[:60]}"

    # pybaseball (Component B)
    results["pybaseball"] = "Available" if _PYBASEBALL_OK else "Not Installed"

    # Soccer (Component C) — api-football + ESPN fallback
    try:
        r = _req.get("https://v3.football.api-sports.io/status",
                     headers={"x-apisports-key": os.environ.get("API_FOOTBALL_KEY", "")},
                     timeout=5)
        results["soccer"] = "Available" if r.ok else "Degraded — HTTP {r.status_code}"
    except Exception as e:
        results["soccer"] = f"Degraded — {str(e)[:60]}"

    results["cs2"] = "Manual Only — permanent"

    overall = "UP" if all(v == "Available"
                          for k, v in results.items() if k != "cs2") else "PARTIAL"
    return jsonify({"status": overall, "sports": results})


# ── /wow/analyze — Claude-powered prompt & screenshot extractor ──────
@app.route("/wow/analyze", methods=["POST"])
@require_api_key
def wow_analyze():
    """Parse a free-form text prompt or a base64-encoded betting slip screenshot
    into a structured prop object ready for /wow/l10/v2.

    POST body (JSON):
      { "prompt": str?, "image_base64": str?, "image_mime": str? }

    Returns:
      { ok: true, player, sport, prop, direction, line, league?,
        confidence: "high"|"medium"|"low", raw_response: str }
    """
    if not _ANTHROPIC_AVAILABLE:
        return jsonify({"ok": False,
                        "error": "anthropic package not installed on server"}), 503

    # Prefer Replit AI Integrations proxy; fall back to direct ANTHROPIC_API_KEY
    _ai_base = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL", "").strip()
    _ai_key  = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY",  "").strip()
    _direct  = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if _ai_base and _ai_key:
        # Replit-managed proxy — billed to Replit credits
        _client_kwargs = {"api_key": _ai_key, "base_url": _ai_base}
        _model         = "claude-sonnet-4-6"
    elif _direct:
        # Direct Anthropic key (user-supplied)
        _client_kwargs = {"api_key": _direct}
        _model         = "claude-sonnet-4-6"
    else:
        return jsonify({"ok": False,
                        "error": "No Anthropic credentials — set ANTHROPIC_API_KEY or enable Replit AI Integration"}), 503

    body = request.get_json(silent=True) or {}
    prompt_text   = (body.get("prompt")       or "").strip()
    image_b64     = (body.get("image_base64") or "").strip()
    image_mime    = (body.get("image_mime")   or "image/png").strip()

    if not prompt_text and not image_b64:
        return jsonify({"ok": False,
                        "error": "Provide a prompt text and/or an image_base64"}), 400

    # Build Claude message content
    system_prompt = (
        "You are an expert sports-betting prop analyst. "
        "Your only job is to extract a single structured player prop from the user's input "
        "(which may be natural language, a photo of a betting slip, or both). "
        "Return ONLY valid JSON — no prose, no markdown fences — with these keys:\n"
        "  player    (string, full name)\n"
        "  sport     (string: NBA | NFL | MLB | NHL | Soccer | Tennis | Golf | MMA | NCAAB | NCAAF | WNBA)\n"
        "  prop      (string, normalized: e.g. 'points', 'rebounds', 'pitcher strikeouts', 'goals')\n"
        "  direction (string: MORE | LESS)\n"
        "  line      (number)\n"
        "  league    (string or null — e.g. 'epl', 'laliga', 'mls'; null if not soccer)\n"
        "  confidence (string: high | medium | low — your extraction confidence)\n"
        "If you cannot determine a field with reasonable confidence, set it to null. "
        "Never invent data. If the image is unreadable or the input is too ambiguous, "
        "return {\"error\": \"Could not extract a valid prop from the provided input\"}."
    )

    content = []
    if image_b64:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_mime,
                "data": image_b64,
            }
        })
    if prompt_text:
        content.append({"type": "text", "text": prompt_text})
    elif image_b64:
        content.append({"type": "text", "text": "Extract the player prop from this betting slip."})

    try:
        client = _anthropic.Anthropic(**_client_kwargs)
        msg = client.messages.create(
            model=_model,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )
        raw = msg.content[0].text.strip()
    except _anthropic.AuthenticationError:
        return jsonify({"ok": False, "error": "Invalid Anthropic API key"}), 401
    except _anthropic.RateLimitError:
        return jsonify({"ok": False, "error": "Anthropic rate limit reached — try again shortly"}), 429
    except Exception as e:
        return jsonify({"ok": False, "error": f"Claude API error: {str(e)[:200]}"}), 502

    # Strip markdown fences if Claude wraps in ```json
    clean = raw
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()

    try:
        parsed = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        return jsonify({"ok": False,
                        "error": "Claude returned non-JSON output",
                        "raw_response": raw}), 502

    if "error" in parsed:
        return jsonify({"ok": False, "error": parsed["error"], "raw_response": raw}), 422

    required = ["player", "sport", "prop", "direction", "line"]
    missing  = [k for k in required if not parsed.get(k)]
    if missing:
        return jsonify({
            "ok": False,
            "error": f"Could not extract: {', '.join(missing)}",
            "partial": parsed,
            "raw_response": raw,
        }), 422

    # Normalize direction
    parsed["direction"] = SIDE_MAP.get(str(parsed["direction"]).strip(), "MORE")
    # Normalize line to float
    try:
        parsed["line"] = float(parsed["line"])
    except (TypeError, ValueError):
        return jsonify({"ok": False,
                        "error": "Extracted line is not a number",
                        "partial": parsed, "raw_response": raw}), 422

    return jsonify({
        "ok": True,
        "player":     parsed.get("player"),
        "sport":      parsed.get("sport"),
        "prop":       parsed.get("prop"),
        "direction":  parsed.get("direction"),
        "line":       parsed.get("line"),
        "league":     parsed.get("league"),
        "confidence": parsed.get("confidence", "medium"),
        "raw_response": raw,
    })


# ═══════════════════════════════════════════════════════════════════════
# CONNECTED MODEL ORCHESTRATOR (CM)
# ChatGPT → /input-board → /wow-score → /claude-audit → /final-arbiter
#                       → /build-slips → /postmortem-log
# All endpoints require X-API-Key (SCORING_API_KEY).
# Tables prefixed `cm_` to avoid collisions with the rest of this DB.
# ═══════════════════════════════════════════════════════════════════════

CM_TIER_TO_POOL = {
    "FINAL LOCK ELIGIBLE":      "approved",
    "CONDITIONAL — L5 ONLY":    "conditional",
    "WATCH / RESEARCH ONLY":    "watch",
    "REJECT — INSUFFICIENT DATA": "reject",
}

CM_PRIZEPICKS_FLEX_MULT = {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0, 6: 37.5}

_CM_SCHEMA_READY = False
_CM_SCHEMA_LOCK  = threading.Lock()

_CM_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS cm_boards (
    board_id    TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    board_type  TEXT NOT NULL,
    board_date  DATE NOT NULL,
    props       JSONB NOT NULL,
    meta        JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cm_boards_date_idx ON cm_boards(board_date DESC);

CREATE TABLE IF NOT EXISTS cm_wow_outputs (
    board_id            TEXT PRIMARY KEY REFERENCES cm_boards(board_id) ON DELETE CASCADE,
    model               TEXT NOT NULL DEFAULT 'WOW',
    approved_pool       JSONB NOT NULL,
    conditional_pool    JSONB NOT NULL,
    watch_pool          JSONB NOT NULL,
    reject_pool         JSONB NOT NULL,
    source_access_status JSONB NOT NULL,
    per_prop_results    JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cm_claude_audits (
    board_id        TEXT PRIMARY KEY REFERENCES cm_boards(board_id) ON DELETE CASCADE,
    model_version   TEXT NOT NULL,
    audit           JSONB NOT NULL,
    raw_response    TEXT,
    latency_ms      INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cm_final_decisions (
    board_id              TEXT PRIMARY KEY REFERENCES cm_boards(board_id) ON DELETE CASCADE,
    final_approved_pool   JSONB NOT NULL,
    final_conditional_pool JSONB NOT NULL,
    final_watch_pool      JSONB NOT NULL,
    final_reject_pool     JSONB NOT NULL,
    accepted_claude_flags JSONB NOT NULL,
    rejected_claude_flags JSONB NOT NULL,
    per_prop_reasoning    JSONB NOT NULL,
    raw_response          TEXT,
    latency_ms            INTEGER,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cm_slips (
    slip_id      TEXT PRIMARY KEY,
    board_id     TEXT NOT NULL REFERENCES cm_boards(board_id) ON DELETE CASCADE,
    slip_size    INTEGER NOT NULL,
    legs         JSONB NOT NULL,
    payout_mult  NUMERIC NOT NULL,
    avg_edge     NUMERIC,
    rank_score   NUMERIC,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cm_slips_board_idx ON cm_slips(board_id);

CREATE TABLE IF NOT EXISTS cm_postmortems (
    postmortem_id  TEXT PRIMARY KEY,
    board_id       TEXT REFERENCES cm_boards(board_id) ON DELETE SET NULL,
    slip_id        TEXT REFERENCES cm_slips(slip_id) ON DELETE SET NULL,
    outcome        JSONB NOT NULL,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cm_postmortems_board_idx ON cm_postmortems(board_id);

CREATE TABLE IF NOT EXISTS cm_patch_candidates (
    candidate_id          TEXT PRIMARY KEY,
    source_postmortem_id  TEXT REFERENCES cm_postmortems(postmortem_id) ON DELETE SET NULL,
    rule_change_proposed  JSONB NOT NULL,
    accepted              BOOLEAN,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

def _cm_ensure_schema():
    """Idempotently create all CM tables. Safe to call repeatedly."""
    global _CM_SCHEMA_READY
    if _CM_SCHEMA_READY:
        return
    with _CM_SCHEMA_LOCK:
        if _CM_SCHEMA_READY:
            return
        try:
            with get_db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(_CM_SCHEMA_DDL)
                conn.commit()
            _CM_SCHEMA_READY = True
            app.logger.info("CM schema ready")
        except Exception as e:
            app.logger.error(f"CM schema bootstrap failed: {e}")
            raise

try:
    _cm_ensure_schema()
except Exception:
    app.logger.warning("CM schema not ready at boot; will retry on first request")


def _cm_db():
    """Get DB conn, lazily bootstrapping schema if needed."""
    _cm_ensure_schema()
    return get_db_conn()


def _cm_insert_board(conn, source, board_type, board_date_str, props, meta,
                     max_attempts=20):
    """Insert a board with a generated board_YYYYMMDD_NNN id. Retries on PK
    collision (concurrent inserts for same date), returns the board_id used.
    Replaces a naive COUNT(*)+1 scheme that was race-prone."""
    yyyymmdd = board_date_str.replace("-", "")
    prefix   = f"board_{yyyymmdd}_"
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM cm_boards WHERE board_id LIKE %s",
                    (prefix + "%",))
        base = cur.fetchone()[0]
    last_err = None
    for attempt in range(max_attempts):
        candidate = f"{prefix}{base + 1 + attempt:03d}"
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO cm_boards (board_id, source, board_type, "
                    "board_date, props, meta) "
                    "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                    (candidate, source, board_type, board_date_str,
                     json.dumps(props), json.dumps(meta)),
                )
            conn.commit()
            return candidate
        except psycopg2.IntegrityError as e:
            conn.rollback()
            last_err = e
            continue
    raise RuntimeError(
        f"_cm_insert_board: could not allocate id after {max_attempts} "
        f"attempts (concurrent contention?): {last_err}")


def _cm_coerce_list(d, key):
    """Force d[key] to a list. Defends against Claude returning a string,
    null, dict, or omitted field where a list is contractually required."""
    v = d.get(key)
    if isinstance(v, list):
        return v
    if v in (None, "", 0, False):
        return []
    if isinstance(v, dict):
        app.logger.warning(f"CM: Claude returned dict for '{key}'; wrapping in single-element list")
        return [v]
    app.logger.warning(f"CM: Claude returned {type(v).__name__} for '{key}'; coercing to []")
    return []


def _cm_load_board(conn, board_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM cm_boards WHERE board_id = %s", (board_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def _cm_load_wow(conn, board_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM cm_wow_outputs WHERE board_id = %s", (board_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def _cm_load_audit(conn, board_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM cm_claude_audits WHERE board_id = %s", (board_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def _cm_load_final(conn, board_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM cm_final_decisions WHERE board_id = %s", (board_id,))
        row = cur.fetchone()
    return dict(row) if row else None


# ── Claude call helper ────────────────────────────────────────────────
def _cm_claude_call(system_prompt, user_content, max_tokens=4096):
    """Single Anthropic Messages call. Returns (text, model_version, latency_ms).
    Raises on error."""
    if not _ANTHROPIC_AVAILABLE:
        raise RuntimeError("anthropic SDK not installed")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    model = os.environ.get("CM_CLAUDE_MODEL", "claude-sonnet-4-5")
    client = _anthropic.Anthropic(api_key=api_key)
    t0 = time.time()
    resp = client.messages.create(
        model=model, max_tokens=max_tokens, temperature=0.0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    latency_ms = int((time.time() - t0) * 1000)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return text, model, latency_ms


def _cm_extract_json(text):
    """Strip ```json fences and parse. Raises ValueError on failure."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    # Find first { and last } if there's surrounding prose
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        s = s[i:j+1]
    return json.loads(s)


# ── Pool classification from per-prop scoring ────────────────────────
def _cm_classify_pools(per_prop_results):
    """Take a list of scored prop dicts and split into approved/conditional/watch/reject."""
    pools = {"approved": [], "conditional": [], "watch": [], "reject": []}
    src_counts = {"available": 0, "partial": 0, "failed": 0}
    for pr in per_prop_results:
        tier = pr.get("confidence_tier", "REJECT — INSUFFICIENT DATA")
        bucket = CM_TIER_TO_POOL.get(tier, "reject")
        pools[bucket].append(pr)
        if pr.get("rows", 0) >= 10 and pr.get("complete"):
            src_counts["available"] += 1
        elif pr.get("rows", 0) >= 3:
            src_counts["partial"] += 1
        else:
            src_counts["failed"] += 1
    return pools, src_counts


# ── Active model rules (loaded at request time so they can be patched) ──
def _cm_active_rules():
    return {
        "tier_definitions": {
            "FINAL LOCK ELIGIBLE": "10 complete L10 rows from authoritative source",
            "CONDITIONAL — L5 ONLY": "5-9 rows present, not 10 complete",
            "WATCH / RESEARCH ONLY": "3-4 rows present",
            "REJECT — INSUFFICIENT DATA": "<3 rows or no source access",
        },
        "approval_requirements": [
            "L5 exact-line proof from authoritative source",
            "L10 median visible and not contradicted by line",
            "Official status / lineup confirmed (where applicable)",
            "Projection support exists",
            "Market odds support exists and is not contradictory",
            "MORE/LESS side compared, edge calculated",
        ],
        "auto_reject_archetypes": [
            "CS2 props (HLTV behind Cloudflare, manual entry required)",
            "Pikkit DES/Proj (manual entry required)",
            "Props with stale or unverified official status",
        ],
        "correlation_rules": [
            "No two legs from same game/player in a single slip",
        ],
        "claude_authority": "Claude may challenge approval but cannot create approval. WOW remains source of truth.",
    }


# ── Claude prompts (verbatim from product spec) ──────────────────────
_CM_CLAUDE_AUDIT_SYSTEM = """You are Claude acting as the Red Team Validator for the WOW Betting Model.

You are not the primary picker.
Do not generate new picks first.
Do not approve props.
Do not build slips.

Audit the WOW output for:
- missing L5/L10 exact-line proof
- unsupported L10 median
- missing or stale official status
- missing projection support
- missing or contradictory market support
- overstated probability
- wrong approval label
- fragile failure path
- same-game or same-player correlation risk
- known failed archetypes
- MORE/LESS side not properly compared

Return ONLY a single JSON object with these exact keys (arrays may be empty):
{
  "props_to_keep": [],
  "props_to_downgrade": [],
  "props_to_reject": [],
  "missing_data_flags": [],
  "overconfidence_flags": [],
  "correlation_flags": [],
  "patch_recommendations": []
}

Each prop entry must be an object with at minimum: player, prop, line, side, reason.
Each flag entry must be an object with at minimum: target (player+prop or "board"), severity, reason.

Claude can challenge approval.
Claude cannot create approval.
Respond with ONLY the JSON object, no prose, no markdown fences."""

_CM_CLAUDE_ARBITER_SYSTEM = """You are WOW Final Arbiter.

Compare:
1. WOW primary output
2. Claude audit
3. Active WOW model rules

Accept Claude's critique only if it identifies a real missing checkpoint, stale assumption, unsupported label, market contradiction, role/status issue, L5/L10 issue, median failure, projection gap, or correlation risk.

Reject Claude's critique if it is only preference, narrative, unsupported caution, or a second-model opinion.

WOW remains source of truth. Claude is a validator, not the picker.

Return ONLY a single JSON object with these exact keys:
{
  "final_approved_pool": [],
  "final_conditional_pool": [],
  "final_watch_pool": [],
  "final_reject_pool": [],
  "accepted_claude_flags": [],
  "rejected_claude_flags": [],
  "per_prop_reasoning": []
}

Each pool entry must be an object with: player, prop, line, side, initial_wow_label, claude_recommendation, arbiter_decision, final_label, allowed_in_slips (bool), reason.
Each accepted/rejected flag must be an object with: target, original_severity, decision ("accepted"|"rejected"|"partial"), reason.
per_prop_reasoning is an array of objects: {player, prop, summary}.

Respond with ONLY the JSON object, no prose, no markdown fences."""


# ── Sport normalization (public-API form is UPPER) ────────────────────
# Accepts any casing plus common aliases ("baseball", "basketball",
# "football", "hockey", "soccer"). Returns the canonical UPPER token used
# everywhere downstream. Use _cm_v2_sport_key() to translate to the
# lowercase form the v2 per-prop scorer expects (and to split MLB into
# batter/pitcher based on the prop name).
_CM_SPORT_ALIASES = {
    "":             "",
    "MLB":          "MLB",  "BASEBALL":     "MLB",
    "MLB_BATTER":   "MLB",  "MLB_PITCHER":  "MLB",
    "NBA":          "NBA",  "NBA BASKETBALL": "NBA",
    "WNBA":         "WNBA", "WNBA BASKETBALL":"WNBA",
    "BASKETBALL":   "NBA",  # default; caller can override with WNBA explicitly
    "NCAAB":        "NCAAB","COLLEGE BASKETBALL": "NCAAB",
    "NFL":          "NFL",  "FOOTBALL":     "NFL",
    "NCAAF":        "NCAAF","COLLEGE FOOTBALL": "NCAAF",
    "NHL":          "NHL",  "HOCKEY":       "NHL",
    "SOCCER":       "SOCCER","FOOTBALL (SOCCER)":"SOCCER","FUTBOL":"SOCCER",
    "TENNIS":       "TENNIS","ATP":"TENNIS","WTA":"TENNIS",
    "CS2":          "CS2",  "COUNTER-STRIKE":"CS2","COUNTER STRIKE":"CS2","CSGO":"CS2",
}

def _cm_normalize_sport(s):
    """Return canonical UPPER sport token, or '' if unknown/blank."""
    if s is None: return ""
    key = str(s).upper().strip()
    if key in _CM_SPORT_ALIASES:
        return _CM_SPORT_ALIASES[key]
    # Unknown sports pass through uppercased so the dispatcher can decide.
    return key


_MLB_PITCHER_PROPS = {
    "pitcher strikeouts", "hits allowed", "earned runs", "walks allowed",
    "pitcher fantasy score", "1st inn. pitches thrown", "outs", "walks issued",
    "strikeouts",
}
_MLB_BATTER_PROPS = {
    "hitter hits", "total bases", "runs scored", "rbis", "h+r+rbi",
    "hitter strikeouts",
}

def _cm_v2_sport_key(canonical_upper, prop):
    """
    Translate canonical UPPER sport to the lowercase v2-scorer key.
    For MLB, infer mlb_batter vs mlb_pitcher from the prop name using an
    explicit allowlist (preferred) with a "pitcher" substring fallback.
    """
    c = (canonical_upper or "").upper().strip()
    if c == "MLB":
        p = (prop or "").lower().strip()
        if p in _MLB_PITCHER_PROPS:    return "mlb_pitcher"
        if p in _MLB_BATTER_PROPS:     return "mlb_batter"
        if "pitcher" in p:             return "mlb_pitcher"  # safety net
        if "hitter"  in p:             return "mlb_batter"
        return "mlb_batter"  # default for ambiguous MLB props
    return c.lower()


# ── Endpoint: POST /input-board ───────────────────────────────────────
@app.route("/input-board", methods=["POST"])
@require_api_key
def cm_input_board():
    body = request.get_json(silent=True) or {}
    source     = (body.get("source") or "chatgpt").strip()
    board_type = (body.get("board_type") or "prizepicks").strip()
    model      = (body.get("model") or "").strip().lower()
    board_date = (body.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
    props      = body.get("props") if isinstance(body.get("props"), list) else []
    games      = body.get("games") if isinstance(body.get("games"), list) else []
    meta       = body.get("meta") or {}

    is_team_board = (board_type == "team_betting") or (model == "llp_team")
    if is_team_board and board_type == "prizepicks":
        board_type = "team_betting"

    if not props and not games:
        return jsonify({"ok": False,
                        "error": "Either props or games must be a non-empty array."}), 400
    if is_team_board and not games:
        return jsonify({"ok": False, "error": "games must be a non-empty array"}), 400
    if not is_team_board and board_type == "prizepicks" and not props:
        return jsonify({"ok": False, "error": "props must be a non-empty array"}), 400

    try:
        datetime.strptime(board_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400

    # Normalize sport on every prop and every game to UPPER canonical form.
    body_sport = _cm_normalize_sport(body.get("sport"))
    for p in props:
        if isinstance(p, dict):
            p["sport"] = _cm_normalize_sport(p.get("sport") or body_sport)
    for g in games:
        if isinstance(g, dict):
            g["sport"] = _cm_normalize_sport(g.get("sport") or body_sport)

    meta = dict(meta)
    if games:
        meta["games"] = games
    if model:
        meta["model"] = model
    if body_sport:
        meta["sport"] = body_sport

    with _cm_db() as conn:
        board_id = _cm_insert_board(conn, source, board_type, board_date, props, meta)
    return jsonify({"ok": True, "board_id": board_id, "status": "received",
                    "board_type": board_type,
                    "props_received": len(props),
                    "games_received": len(games)})


# ── Endpoint: POST /wow-score ─────────────────────────────────────────
@app.route("/wow-score", methods=["POST"])
@require_api_key
def cm_wow_score():
    body = request.get_json(silent=True) or {}
    board_id = (body.get("board_id") or "").strip()
    if not board_id:
        return jsonify({"ok": False, "error": "board_id required"}), 400

    with _cm_db() as conn:
        board = _cm_load_board(conn, board_id)
        if not board:
            return jsonify({"ok": False, "error": f"board {board_id} not found"}), 404
        props = board["props"] if isinstance(board["props"], list) else json.loads(board["props"])

        per_prop_results = []
        for p in props:
            try:
                _canonical_sport = _cm_normalize_sport(p.get("sport",""))
                _v2_sport        = _cm_v2_sport_key(_canonical_sport, p.get("prop",""))
                data, err = _score_one_prop_v2(
                    player=p.get("player",""), sport=_v2_sport,
                    prop=p.get("prop",""), direction=(p.get("side","MORE") or "MORE").upper(),
                    line=float(p.get("line", 0)),
                    season=p.get("season","2025-26"), mlb_ssn=p.get("mlb_season","2026"),
                    year=p.get("year","2026"), hltv_id=p.get("hltv_id"),
                )
                if err:
                    pr = {**p, "rows": 0, "complete": False, "gap": err,
                          "confidence_tier": "REJECT — INSUFFICIENT DATA",
                          "source": "n/a", "games": []}
                else:
                    pr = {**p,
                          "source": data.get("source"),
                          "rows": data.get("rows", 0),
                          "complete": data.get("complete", False),
                          "gap": data.get("gap", ""),
                          "games": data.get("games", []),
                          "l5_avg": data.get("l5_avg"),
                          "l10_avg": data.get("l10_avg"),
                          "l10_median": data.get("l10_median"),
                          "l5_hit_rate": data.get("l5_hit_rate"),
                          "l10_hit_rate": data.get("l10_hit_rate"),
                          "edge": data.get("edge"),
                          "confidence_tier": _tier(data.get("rows", 0),
                                                   data.get("complete", False))}
            except Exception as e:
                app.logger.exception(f"score failed for {p}")
                pr = {**p, "rows": 0, "complete": False,
                      "gap": f"scoring exception: {type(e).__name__}: {str(e)[:200]}",
                      "confidence_tier": "REJECT — INSUFFICIENT DATA",
                      "source": "n/a", "games": []}
            per_prop_results.append(pr)

        pools, src_counts = _cm_classify_pools(per_prop_results)
        src_status = {
            "player_logs": ("available" if src_counts["available"] >= len(per_prop_results)//2
                            else ("partial" if src_counts["available"] + src_counts["partial"] > 0 else "failed")),
            "counts": src_counts,
            "total_props": len(per_prop_results),
        }

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cm_wow_outputs (board_id, approved_pool, conditional_pool, "
                "watch_pool, reject_pool, source_access_status, per_prop_results) "
                "VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb) "
                "ON CONFLICT (board_id) DO UPDATE SET "
                "approved_pool=EXCLUDED.approved_pool, "
                "conditional_pool=EXCLUDED.conditional_pool, "
                "watch_pool=EXCLUDED.watch_pool, reject_pool=EXCLUDED.reject_pool, "
                "source_access_status=EXCLUDED.source_access_status, "
                "per_prop_results=EXCLUDED.per_prop_results, created_at=NOW()",
                (board_id, json.dumps(pools["approved"]), json.dumps(pools["conditional"]),
                 json.dumps(pools["watch"]), json.dumps(pools["reject"]),
                 json.dumps(src_status), json.dumps(per_prop_results)),
            )
        conn.commit()

    return jsonify({
        "ok": True, "board_id": board_id, "model": "WOW",
        "approved_pool": pools["approved"], "conditional_pool": pools["conditional"],
        "watch_pool": pools["watch"], "reject_pool": pools["reject"],
        "source_access_status": src_status,
    })


# ── Endpoint: POST /claude-audit ──────────────────────────────────────
@app.route("/claude-audit", methods=["POST"])
@require_api_key
def cm_claude_audit():
    body = request.get_json(silent=True) or {}
    board_id = (body.get("board_id") or "").strip()
    if not board_id:
        return jsonify({"ok": False, "error": "board_id required"}), 400

    with _cm_db() as conn:
        board = _cm_load_board(conn, board_id)
        wow   = _cm_load_wow(conn, board_id)
        if not board: return jsonify({"ok": False, "error": "board not found"}), 404
        if not wow:   return jsonify({"ok": False, "error": "wow_output missing — run /wow-score first"}), 409

        payload = {
            "board_id": board_id, "board_type": board["board_type"],
            "date": str(board["board_date"]),
            "wow_output": {
                "approved_pool":    wow["approved_pool"],
                "conditional_pool": wow["conditional_pool"],
                "watch_pool":       wow["watch_pool"],
                "reject_pool":      wow["reject_pool"],
                "source_access_status": wow["source_access_status"],
            },
            "active_rules": _cm_active_rules(),
        }
        user_content = ("Audit this WOW output. Return ONLY the specified JSON object.\n\n"
                        + json.dumps(payload, default=str))

        try:
            text, model_version, latency_ms = _cm_claude_call(
                _CM_CLAUDE_AUDIT_SYSTEM, user_content, max_tokens=6000)
        except Exception as e:
            return jsonify({"ok": False, "error": f"claude call failed: {e}"}), 502

        try:
            audit = _cm_extract_json(text)
        except Exception:
            try:
                text2, _, _ = _cm_claude_call(
                    _CM_CLAUDE_AUDIT_SYSTEM,
                    user_content + "\n\nREMINDER: Return ONLY valid JSON with the exact keys specified.",
                    max_tokens=6000)
                audit = _cm_extract_json(text2)
                text = text2
            except Exception as e2:
                return jsonify({"ok": False,
                                "error": f"claude returned unparseable JSON: {e2}",
                                "raw": text[:2000]}), 502

        for k in ("props_to_keep","props_to_downgrade","props_to_reject",
                  "missing_data_flags","overconfidence_flags",
                  "correlation_flags","patch_recommendations"):
            audit[k] = _cm_coerce_list(audit, k)

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cm_claude_audits (board_id, model_version, audit, raw_response, latency_ms) "
                "VALUES (%s, %s, %s::jsonb, %s, %s) "
                "ON CONFLICT (board_id) DO UPDATE SET "
                "model_version=EXCLUDED.model_version, audit=EXCLUDED.audit, "
                "raw_response=EXCLUDED.raw_response, latency_ms=EXCLUDED.latency_ms, created_at=NOW()",
                (board_id, model_version, json.dumps(audit), text, latency_ms),
            )
        conn.commit()

    return jsonify({"ok": True, "board_id": board_id,
                    "model_version": model_version, "latency_ms": latency_ms,
                    **audit})


# ── Endpoint: POST /final-arbiter ─────────────────────────────────────
@app.route("/final-arbiter", methods=["POST"])
@require_api_key
def cm_final_arbiter():
    body = request.get_json(silent=True) or {}
    board_id = (body.get("board_id") or "").strip()
    if not board_id:
        return jsonify({"ok": False, "error": "board_id required"}), 400

    with _cm_db() as conn:
        board = _cm_load_board(conn, board_id)
        wow   = _cm_load_wow(conn, board_id)
        audit = _cm_load_audit(conn, board_id)
        if not board: return jsonify({"ok": False, "error": "board not found"}), 404
        if not wow:   return jsonify({"ok": False, "error": "wow_output missing"}), 409
        if not audit: return jsonify({"ok": False, "error": "claude_audit missing — run /claude-audit first"}), 409

        payload = {
            "board_id": board_id,
            "wow_output": {
                "approved_pool":    wow["approved_pool"],
                "conditional_pool": wow["conditional_pool"],
                "watch_pool":       wow["watch_pool"],
                "reject_pool":      wow["reject_pool"],
            },
            "claude_audit": audit["audit"],
            "active_rules": _cm_active_rules(),
        }
        user_content = ("Reconcile WOW and Claude. Return ONLY the specified JSON object.\n\n"
                        + json.dumps(payload, default=str))

        try:
            text, model_version, latency_ms = _cm_claude_call(
                _CM_CLAUDE_ARBITER_SYSTEM, user_content, max_tokens=8000)
        except Exception as e:
            return jsonify({"ok": False, "error": f"claude arbiter call failed: {e}"}), 502

        try:
            decision = _cm_extract_json(text)
        except Exception:
            try:
                text2, _, _ = _cm_claude_call(
                    _CM_CLAUDE_ARBITER_SYSTEM,
                    user_content + "\n\nREMINDER: Return ONLY valid JSON.", max_tokens=8000)
                decision = _cm_extract_json(text2); text = text2
            except Exception as e2:
                return jsonify({"ok": False,
                                "error": f"arbiter returned unparseable JSON: {e2}",
                                "raw": text[:2000]}), 502

        for k in ("final_approved_pool","final_conditional_pool",
                  "final_watch_pool","final_reject_pool",
                  "accepted_claude_flags","rejected_claude_flags",
                  "per_prop_reasoning"):
            decision[k] = _cm_coerce_list(decision, k)

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cm_final_decisions (board_id, final_approved_pool, "
                "final_conditional_pool, final_watch_pool, final_reject_pool, "
                "accepted_claude_flags, rejected_claude_flags, per_prop_reasoning, "
                "raw_response, latency_ms) "
                "VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, "
                "%s::jsonb, %s::jsonb, %s, %s) "
                "ON CONFLICT (board_id) DO UPDATE SET "
                "final_approved_pool=EXCLUDED.final_approved_pool, "
                "final_conditional_pool=EXCLUDED.final_conditional_pool, "
                "final_watch_pool=EXCLUDED.final_watch_pool, "
                "final_reject_pool=EXCLUDED.final_reject_pool, "
                "accepted_claude_flags=EXCLUDED.accepted_claude_flags, "
                "rejected_claude_flags=EXCLUDED.rejected_claude_flags, "
                "per_prop_reasoning=EXCLUDED.per_prop_reasoning, "
                "raw_response=EXCLUDED.raw_response, "
                "latency_ms=EXCLUDED.latency_ms, created_at=NOW()",
                (board_id, json.dumps(decision["final_approved_pool"]),
                 json.dumps(decision["final_conditional_pool"]),
                 json.dumps(decision["final_watch_pool"]),
                 json.dumps(decision["final_reject_pool"]),
                 json.dumps(decision["accepted_claude_flags"]),
                 json.dumps(decision["rejected_claude_flags"]),
                 json.dumps(decision["per_prop_reasoning"]),
                 text, latency_ms),
            )
        conn.commit()

    return jsonify({"ok": True, "board_id": board_id,
                    "model_version": model_version, "latency_ms": latency_ms,
                    **decision})


# ── Endpoint: POST /build-slips ───────────────────────────────────────
def _cm_same_game_or_player(a, b):
    if a.get("player") and a.get("player") == b.get("player"): return True
    g1 = f"{a.get('team','')}@{a.get('opponent','')}"
    g2 = f"{b.get('team','')}@{b.get('opponent','')}"
    g1r = f"{a.get('opponent','')}@{a.get('team','')}"
    return g1 != "@" and (g1 == g2 or g1r == g2)


def _cm_slip_combos(legs, k):
    from itertools import combinations
    out = []
    for combo in combinations(legs, k):
        ok = True
        for i in range(len(combo)):
            for j in range(i+1, len(combo)):
                if _cm_same_game_or_player(combo[i], combo[j]):
                    ok = False; break
            if not ok: break
        if ok: out.append(list(combo))
    return out


@app.route("/build-slips", methods=["POST"])
@require_api_key
def cm_build_slips():
    body = request.get_json(silent=True) or {}
    board_id = (body.get("board_id") or "").strip()
    slip_sizes = body.get("slip_sizes") or [2, 3, 4]
    max_per_size = int(body.get("max_slips_per_size", 5))
    include_conditional = bool(body.get("include_conditional", False))

    if not board_id:
        return jsonify({"ok": False, "error": "board_id required"}), 400

    with _cm_db() as conn:
        final = _cm_load_final(conn, board_id)
        if not final:
            return jsonify({"ok": False, "error": "no final_decision — run /final-arbiter first"}), 409
        pool = list(final["final_approved_pool"] or [])
        if include_conditional:
            pool += list(final["final_conditional_pool"] or [])
        pool = [p for p in pool if p.get("allowed_in_slips", True)]

        all_built = []
        with conn.cursor() as cur:
            for size in slip_sizes:
                size = int(size)
                mult = CM_PRIZEPICKS_FLEX_MULT.get(size)
                if mult is None or len(pool) < size: continue
                combos = _cm_slip_combos(pool, size)
                scored = []
                for c in combos:
                    edges = [float(p.get("edge") or 0) for p in c]
                    avg_edge = sum(edges) / len(edges) if edges else 0.0
                    scored.append((c, avg_edge, avg_edge * mult))
                scored.sort(key=lambda x: x[2], reverse=True)
                for idx, (legs, avg_edge, rank_score) in enumerate(scored[:max_per_size]):
                    slip_id = f"slip_{board_id[6:]}_{size}_{idx+1:02d}"
                    cur.execute(
                        "INSERT INTO cm_slips (slip_id, board_id, slip_size, legs, "
                        "payout_mult, avg_edge, rank_score) "
                        "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s) "
                        "ON CONFLICT (slip_id) DO UPDATE SET legs=EXCLUDED.legs, "
                        "payout_mult=EXCLUDED.payout_mult, avg_edge=EXCLUDED.avg_edge, "
                        "rank_score=EXCLUDED.rank_score",
                        (slip_id, board_id, size, json.dumps(legs),
                         mult, avg_edge, rank_score),
                    )
                    all_built.append({"slip_id": slip_id, "slip_size": size,
                                      "payout_mult": mult, "avg_edge": avg_edge,
                                      "rank_score": rank_score, "legs": legs})
        conn.commit()

    return jsonify({"ok": True, "board_id": board_id,
                    "slips_built": len(all_built), "slips": all_built})


# ══════════════════════════════════════════════════════════════════════
# WOW-JF Slip Engine v1.0 — child lane inside WOW v16 Clean Core.
# Activation (human workflow): "/wow jf".  Machine endpoint: POST /wow/jf.
#
# Pipeline (faithful to the v1.0 spec):
#   1. Accept extracted prop pool.
#   2. Pre-Analysis Slate Purge   (structural + known-dead-archetype purge).
#   3. Existing WOW v16 gates      (_score_one_prop_v2 + _tier + data_quality).
#   4. JF-style eligibility tag    (only WOW-validated props are eligible).
#   5. JF Score 0–10               (consistency + recent form + edge + data).
#   6. Slip shells from validated  (HARD RULE: no slip leg may skip WOW).
#   7. Conservative 2 / Standard 3 / Flex 4 outputs.
#   8. Never force action          (insufficient legs → slip null + reason).
#
# NOT a replacement engine — it consumes WOW's own scoring verbatim and only
# adds a JF score + slip assembly on top. Stateless: computes and returns.
# ══════════════════════════════════════════════════════════════════════

# JF score band thresholds (inclusive lower bound, 0–10 scale).
_JF_BAND_PREMIUM   = 9.0    # 9–10  Premium JF Core
_JF_BAND_ELIGIBLE  = 7.0    # 7–8   JF Slip Eligible
_JF_BAND_WATCH     = 5.0    # 5–6   Watch Only
                            # 0–4   Not JF Style
# A leg may enter a JF slip only at JF Slip Eligible (7.0) or above.
_JF_SLIP_MIN_SCORE = _JF_BAND_ELIGIBLE

# WOW tiers that count as "passed WOW validation" — the hard-rule gate.
_JF_WOW_VALID_TIERS = {"FINAL LOCK ELIGIBLE", "CONDITIONAL — L5 ONLY"}

# Conservative / Standard / Flex slip definitions (label, leg count).
_JF_SLIP_TYPES = [("Conservative", 2), ("Standard", 3), ("Flex", 4)]

# Guardrail: cap eligible legs fed into combinatorial slip building so an
# oversized pool can't cause a CPU/latency spike (C(12,4)=495 combos max).
_JF_MAX_COMBO_LEGS = 12


def _jf_band(score):
    """Map a 0–10 JF score to its named band."""
    if score >= _JF_BAND_PREMIUM:   return "Premium JF Core"
    if score >= _JF_BAND_ELIGIBLE:  return "JF Slip Eligible"
    if score >= _JF_BAND_WATCH:     return "Watch Only"
    return "Not JF Style"


def _jf_score_prop(pr):
    """Compute a transparent 0–10 JF score from a WOW-scored prop dict.

    Components (max 10.0), all sourced from WOW's own output so JF never
    invents data:
      • L10 hit rate → up to 5.0  (consistency is the core JF signal)
      • L5  hit rate → up to 2.0  (recent form)
      • model edge   → up to 2.0  (edge ≥ 0.20 earns the full 2.0)
      • data quality → up to 1.0  (Verified = 1.0, Manually Reconstructed 0.5)
    Returns (score_rounded_1dp, breakdown_dict).
    """
    def _f(x):
        try:    return float(x)
        except (TypeError, ValueError): return 0.0
    def _hr(x):
        """Parse a hit rate to a 0–1 fraction. Accepts a float (0–1 or a
        percentage >1), '80%', or the WOW display form '8/10 (80%)'."""
        if x is None: return 0.0
        if isinstance(x, (int, float)):
            v = float(x); return v / 100.0 if v > 1.0 else v
        s = str(x)
        m = re.search(r'(\d+(?:\.\d+)?)\s*%', s)
        if m: return max(0.0, min(1.0, float(m.group(1)) / 100.0))
        m = re.search(r'(\d+)\s*/\s*(\d+)', s)
        if m and float(m.group(2)) > 0:
            return max(0.0, min(1.0, float(m.group(1)) / float(m.group(2))))
        try:
            v = float(s); return v / 100.0 if v > 1.0 else v
        except ValueError:
            return 0.0
    l10  = max(0.0, min(1.0, _hr(pr.get("l10_hit_rate"))))
    l5   = max(0.0, min(1.0, _hr(pr.get("l5_hit_rate"))))
    edge = _f(pr.get("edge"))
    dq   = pr.get("data_quality") or ""

    c_l10  = l10 * 5.0
    c_l5   = l5  * 2.0
    c_edge = max(0.0, min(2.0, edge * 10.0))
    c_dq   = 1.0 if dq == "Verified" else (0.5 if dq == "Manually Reconstructed" else 0.0)
    score  = max(0.0, min(10.0, round(c_l10 + c_l5 + c_edge + c_dq, 1)))
    return score, {
        "l10_hit_rate": round(c_l10, 2), "l5_hit_rate": round(c_l5, 2),
        "edge": round(c_edge, 2), "data_quality": round(c_dq, 2),
    }


def _jf_slate_purge(props):
    """Pre-Analysis Slate Purge: drop structurally unusable or known-dead
    props *before* any scoring. Returns (kept, purged); purged items carry a
    `purge_reason`. Conservative by design — removes only props that cannot
    be validated, never props that merely look statistically weak.
    """
    kept, purged, seen = [], [], set()
    for p in props:
        if not isinstance(p, dict):
            purged.append({"prop": p, "purge_reason": "malformed entry (not an object)"})
            continue
        player = (p.get("player") or "").strip()
        sport  = (p.get("sport")  or "").strip()
        prop   = (p.get("prop")   or "").strip()
        line   = p.get("line")
        if not player or not sport or not prop:
            purged.append({**p, "purge_reason": "missing player/sport/prop"})
            continue
        try:
            float(line)
        except (TypeError, ValueError):
            purged.append({**p, "purge_reason": "missing or non-numeric line"})
            continue
        # Known auto-reject archetype: CS2 (HLTV behind Cloudflare → manual).
        if _cm_normalize_sport(sport) == "CS2":
            purged.append({**p, "purge_reason": "auto-reject archetype: CS2 (manual entry only)"})
            continue
        key = (player.lower(), sport.lower(), prop.lower(),
               (p.get("side") or "").upper(), str(line))
        if key in seen:
            purged.append({**p, "purge_reason": "duplicate of an earlier prop"})
            continue
        seen.add(key)
        kept.append(p)
    return kept, purged


def _jf_leg_view(p):
    """Compact per-leg view for slip output."""
    return {
        "player": p.get("player"), "sport": p.get("sport"),
        "prop": p.get("prop"), "side": p.get("side"), "line": p.get("line"),
        "jf_score": p.get("jf_score"), "jf_band": p.get("jf_band"),
        "confidence_tier": p.get("confidence_tier"),
        "data_quality": p.get("data_quality"), "edge": p.get("edge"),
        "l10_hit_rate": p.get("l10_hit_rate"),
    }


def _jf_score_pool(props):
    """Run the existing WOW v16 gates on each prop, then attach JF score,
    band, and eligibility. Returns a list of enriched prop dicts."""
    out = []
    for p in props:
        side = (p.get("side") or "MORE").upper()
        if side not in ("MORE", "LESS"):
            side = "MORE"
        # Normalize loose sport/prop to canonical dispatch keys, exactly as
        # /wow/l10/v2 does — _score_one_prop_v2 matches canonical keys only.
        v2_sport, v2_prop, norm_log = _v2_normalize_inputs(
            p.get("sport", ""), p.get("prop", ""),
            (p.get("prop_type") or "").strip().lower())
        try:
            data, err = _score_one_prop_v2(
                player=p.get("player", ""), sport=v2_sport,
                prop=v2_prop, direction=side,
                line=float(p.get("line", 0)),
                season=p.get("season", "2025-26"),
                mlb_ssn=p.get("mlb_season", "2026"),
                year=p.get("year", "2026"), hltv_id=p.get("hltv_id"),
            )
        except Exception as e:
            data, err = None, f"scoring exception: {type(e).__name__}: {str(e)[:200]}"

        if err or not data:
            rec = {**p, "side": side, "rows": 0, "complete": False,
                   "gap": err or "no data", "edge": None,
                   "confidence_tier": "REJECT — INSUFFICIENT DATA",
                   "data_quality": "Missing", "source": "n/a"}
        else:
            rows = data.get("rows", 0); complete = data.get("complete", False)
            rec = {**p, "side": side,
                   "source": data.get("source"), "rows": rows, "complete": complete,
                   "gap": data.get("gap", ""),
                   "l5_avg": data.get("l5_avg"), "l10_avg": data.get("l10_avg"),
                   "l10_median": data.get("l10_median"),
                   "l5_hit_rate": data.get("l5_hit_rate"),
                   "l10_hit_rate": data.get("l10_hit_rate"),
                   "edge": data.get("edge"),
                   "confidence_tier": _tier(rows, complete),
                   "data_quality": _v2_data_quality(data.get("source"), rows, complete)}

        # HARD RULE: JF eligibility requires passing WOW validation first.
        wow_ok = rec["confidence_tier"] in _JF_WOW_VALID_TIERS
        if wow_ok:
            jf_score, jf_breakdown = _jf_score_prop(rec)
        else:
            jf_score, jf_breakdown = 0.0, None
        rec["wow_validated"]    = wow_ok
        rec["jf_score"]         = jf_score
        rec["jf_band"]          = _jf_band(jf_score) if wow_ok else "Not JF Style"
        rec["jf_breakdown"]     = jf_breakdown
        rec["normalization_log"] = norm_log
        # Slip-eligible only when WOW-validated AND JF score clears the bar.
        rec["jf_slip_eligible"] = bool(wow_ok and jf_score >= _JF_SLIP_MIN_SCORE)
        out.append(rec)
    return out


def _jf_build_slips(scored):
    """Build Conservative/Standard/Flex slip shells from slip-eligible legs.
    Never forces action: if a size can't be filled, its slip is unavailable
    with a reason. Reuses _cm_slip_combos so the no-same-game/player
    correlation rule is enforced. Returns a dict keyed by slip label."""
    legs = [p for p in scored if p.get("jf_slip_eligible")]
    legs.sort(key=lambda p: p.get("jf_score", 0), reverse=True)
    # Guardrail: cap the candidate pool before combinatorial slip building.
    # The top legs by JF score dominate the best slips anyway.
    legs = legs[:_JF_MAX_COMBO_LEGS]
    slips = {}
    for label, size in _JF_SLIP_TYPES:
        mult = CM_PRIZEPICKS_FLEX_MULT.get(size)
        if len(legs) < size:
            slips[label] = {"slip_size": size, "payout_mult": mult,
                            "available": False, "legs": [],
                            "reason": f"insufficient JF-eligible legs: need {size}, have {len(legs)}"}
            continue
        combos = _cm_slip_combos(legs, size)
        if not combos:
            slips[label] = {"slip_size": size, "payout_mult": mult,
                            "available": False, "legs": [],
                            "reason": "no correlation-clean combination available"}
            continue
        scored_combos = []
        for c in combos:
            scores = [float(p.get("jf_score") or 0) for p in c]
            avg = sum(scores) / len(scores) if scores else 0.0
            premium_all = all(p.get("jf_band") == "Premium JF Core" for p in c)
            scored_combos.append((c, round(avg, 2), premium_all))
        # Best floor first: all-premium combos win ties, then higher avg JF.
        scored_combos.sort(key=lambda x: (x[2], x[1]), reverse=True)
        best, best_avg, best_premium = scored_combos[0]
        slips[label] = {
            "slip_size": size, "payout_mult": mult, "available": True,
            "avg_jf_score": best_avg, "all_premium": best_premium,
            "legs": [_jf_leg_view(p) for p in best],
            "alternates": [
                {"avg_jf_score": a, "all_premium": pr,
                 "legs": [_jf_leg_view(p) for p in c]}
                for (c, a, pr) in scored_combos[1:3]
            ],
        }
    return slips


@app.route("/wow/jf", methods=["POST"])
@require_api_key
def wow_jf_slip_engine():
    """WOW-JF Slip Engine v1.0 — child lane. POST a prop pool (or a board_id)
    and receive JF-scored props + Conservative/Standard/Flex slip shells.
    Runs the full WOW v16 gate chain first — no prop reaches a slip without
    passing WOW validation. Never forces action."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False,
                        "error": "request body must be a JSON object"}), 400
    board_id = (body.get("board_id") or "").strip()

    # 1. Accept the prop pool: explicit body props, else load a saved board.
    if isinstance(body.get("props"), list) and body["props"]:
        props = body["props"]; board_src = None
    elif board_id:
        with _cm_db() as conn:
            board = _cm_load_board(conn, board_id)
        if not board:
            return jsonify({"ok": False, "error": f"board {board_id} not found"}), 404
        raw_props = board["props"]
        if isinstance(raw_props, list):
            props = raw_props
        else:
            try:
                props = json.loads(raw_props)
            except (TypeError, ValueError):
                return jsonify({"ok": False,
                                "error": f"board {board_id} has unreadable props"}), 422
        if not isinstance(props, list):
            return jsonify({"ok": False,
                            "error": f"board {board_id} props is not a list"}), 422
        board_src = board_id
    else:
        return jsonify({"ok": False,
                        "error": "provide a non-empty 'props' array or a 'board_id'"}), 400

    # 2. Pre-Analysis Slate Purge.
    kept, purged = _jf_slate_purge(props)
    # 3–5. WOW v16 gates → JF eligibility tag → JF Score 0–10.
    scored = _jf_score_pool(kept)
    # 6–8. Slip shells from validated legs only; never force action.
    slips = _jf_build_slips(scored)

    eligible = [p for p in scored if p.get("jf_slip_eligible")]
    by_band = {"Premium JF Core": 0, "JF Slip Eligible": 0,
               "Watch Only": 0, "Not JF Style": 0}
    for p in scored:
        b = p.get("jf_band", "Not JF Style")
        by_band[b] = by_band.get(b, 0) + 1

    return jsonify({
        "ok": True, "engine": "WOW-JF Slip Engine v1.0",
        "activation": "/wow jf", "board_id": board_src,
        "forced_action": False,
        "counts": {
            "received": len(props), "purged": len(purged),
            "scored": len(scored), "slip_eligible": len(eligible),
            "by_band": by_band,
        },
        "slate_purge": purged,
        "scored_props": [
            {**_jf_leg_view(p),
             "rows": p.get("rows"), "complete": p.get("complete"),
             "wow_validated": p.get("wow_validated"),
             "jf_slip_eligible": p.get("jf_slip_eligible"),
             "jf_breakdown": p.get("jf_breakdown"), "gap": p.get("gap")}
            for p in sorted(scored, key=lambda x: x.get("jf_score", 0), reverse=True)
        ],
        "slips": slips,
    })


# ── Endpoint: POST /postmortem-log ────────────────────────────────────
@app.route("/postmortem-log", methods=["POST"])
@require_api_key
def cm_postmortem_log():
    body = request.get_json(silent=True) or {}
    board_id = (body.get("board_id") or "").strip() or None
    slip_id  = (body.get("slip_id")  or "").strip() or None
    outcome  = body.get("outcome") or {}
    notes    = (body.get("notes") or "").strip() or None
    if not outcome:
        return jsonify({"ok": False, "error": "outcome required"}), 400

    pm_id = f"pm_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{random.randint(100,999)}"
    with _cm_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cm_postmortems (postmortem_id, board_id, slip_id, outcome, notes) "
                "VALUES (%s, %s, %s, %s::jsonb, %s)",
                (pm_id, board_id, slip_id, json.dumps(outcome), notes),
            )
        conn.commit()
    return jsonify({"ok": True, "postmortem_id": pm_id})


# ── Endpoint: GET /board/<id> (full bundle) ───────────────────────────
@app.route("/board/<board_id>", methods=["GET"])
@require_api_key
def cm_get_board(board_id):
    with _cm_db() as conn:
        board = _cm_load_board(conn, board_id)
        if not board:
            return jsonify({"ok": False, "error": "board not found"}), 404
        wow   = _cm_load_wow(conn, board_id)
        audit = _cm_load_audit(conn, board_id)
        final = _cm_load_final(conn, board_id)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM cm_slips WHERE board_id=%s ORDER BY slip_size, rank_score DESC",
                        (board_id,))
            slips = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM cm_postmortems WHERE board_id=%s ORDER BY created_at DESC",
                        (board_id,))
            postmortems = [dict(r) for r in cur.fetchall()]
    # Use json.dumps(default=str) to handle date/datetime from RealDictCursor;
    # jsonify() doesn't accept a default= serializer.
    return app.response_class(
        json.dumps({"ok": True, "board": board, "wow_output": wow,
                    "claude_audit": audit, "final_decision": final,
                    "slips": slips, "postmortems": postmortems}, default=str),
        mimetype="application/json")


# ── Endpoint: GET /latest-run ────────────────────────────────────────
@app.route("/latest-run", methods=["GET"])
@require_api_key
def cm_latest_run():
    with _cm_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT board_id FROM cm_boards ORDER BY created_at DESC LIMIT 1")
            row = cur.fetchone()
    if not row:
        return jsonify({"ok": False, "error": "no boards yet"}), 404
    return cm_get_board(row["board_id"])


# ─────────────────────────────────────────────────────────────────────
# LLP team-betting analysis pipeline
# ─────────────────────────────────────────────────────────────────────
# v1: market-driven model that fetches lines from The Odds API, computes
# no-vig implied probabilities, applies small structural adjustments to
# produce a model probability, then derives edge / Kelly / confidence /
# decision. Reuses opening_lines table for CLV tracking.

_LLP_SPORT_MAP = {
    "nba":   "basketball_nba",   "wnba":  "basketball_wnba",
    "ncaab": "basketball_ncaab",
    "mlb":   "baseball_mlb",
    "nfl":   "americanfootball_nfl",
    "ncaaf": "americanfootball_ncaaf",
    "nhl":   "icehockey_nhl",
}

# Simple in-process cache: sport_key -> (fetched_at, [events])
_LLP_ODDS_CACHE = {}
_LLP_CACHE_TTL_SEC = 120


def _llp_american_to_decimal(american):
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a == 0: return None
    return 1.0 + (a/100.0 if a > 0 else 100.0/abs(a))


def _llp_american_to_prob(american):
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a == 0: return None
    return (100.0/(a+100.0)) if a > 0 else (abs(a)/(abs(a)+100.0))


def _llp_no_vig_two_way(p_a, p_b):
    """Devig a two-way market. Returns (p_a_novig, p_b_novig) or (None, None)."""
    if p_a is None or p_b is None: return (None, None)
    tot = p_a + p_b
    if tot <= 0: return (None, None)
    return (p_a/tot, p_b/tot)


def _llp_kelly(model_p, decimal_odds, fraction=0.25):
    """Quarter-Kelly by default. Returns stake as fraction of bankroll, clamped [0,1]."""
    if model_p is None or decimal_odds is None or decimal_odds <= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    q = 1.0 - model_p
    full = (b*model_p - q) / b
    if full <= 0: return 0.0
    return max(0.0, min(1.0, full * fraction))


def _llp_confidence_tier(edge):
    if edge is None: return "UNKNOWN"
    a = abs(edge)
    if a >= 0.045: return "STRONG"
    if a >= 0.025: return "MEDIUM"
    if a >= 0.012: return "SMALL"
    return "PASS"


def _llp_decision(edge, model_p, novig_p, upset_score, trap_flag, failures):
    """Final decision: BET / SMALL BET / WATCH / PASS / TRAP.

    The `>=3` cardinality check filters `_LLP_ADVISORY_TAGS` internally so a
    soft advisory (e.g. F5 preference) cannot stack with 2 real failures to
    cause a WATCH demotion. This makes the function advisory-agnostic by
    design rather than depending on call-site ordering in `_llp_analyze_one`.
    """
    if trap_flag: return "TRAP"
    if edge is None or model_p is None: return "WATCH"
    tier = _llp_confidence_tier(edge)
    if edge < 0: return "PASS"
    hard_failures = [f for f in (failures or []) if f not in _LLP_ADVISORY_TAGS]
    if len(hard_failures) >= 3 and tier in ("MEDIUM","SMALL"): return "WATCH"
    if tier == "STRONG": return "BET"
    if tier == "MEDIUM": return "SMALL BET"
    if tier == "SMALL":  return "WATCH"
    return "PASS"


# ── Discovery + Validation gates (v14.9+ badge ladder) ───────────────
# A play must clear BOTH gates to enter `approved` / qualify for ANCHOR/BET.
# Field semantics here are defensive: if a discovery field doesn't exist
# (because we don't yet pull the underlying data), the check passes —
# never silently reject for a check we can't perform.

# Cost thresholds (no-vig delta). "ok" means ≤ this; "high" means > this.
_LLP_PAYOUT_FRICTION_OK = 0.025
# Explicit bad market_timing values. "unverified" is intentionally NOT here
# — that's the current pipeline-wide placeholder until a real timing source
# is plumbed. Populate with concrete bad states (e.g. "late", "post_lock")
# once the timing feed exists.
_LLP_BAD_TIMING = frozenset()

def _llp_discovery_clean(rec):
    """BET-level Discovery gate: market signal interpretable and cost OK.

    This is the gate for entering `approved` / BET. ANCHOR adds further
    requirements via `_llp_anchor_eligible`.

    Stale-line semantics: per spec, "stale-line or sharp signal alone cannot
    approve the play". A `market_cause == "stale"` (or `stale_line is True`)
    is rejected here so it cannot reach BET/ANCHOR; such records fall to WAIT.
    """
    disc = rec.get("discovery") or {}
    cause = disc.get("market_cause")
    if cause in (None, "unverified", "stale"):
        return False
    if disc.get("stale_line") is True:
        return False
    friction = disc.get("payout_friction")
    if isinstance(friction, (int, float)) and friction > _LLP_PAYOUT_FRICTION_OK:
        return False
    # Defensive: these fields are honest-None today, but if a downstream
    # populates them with the literal "NEGATIVE", block.
    if disc.get("possession_conversion") == "NEGATIVE": return False
    if disc.get("execution_value") == "NEGATIVE":       return False
    return True


def _llp_anchor_eligible(rec):
    """ANCHOR Discovery+Validation gate (single source of truth).

    Requires:
      - `_llp_discovery_clean` (verified cause, cost OK, not stale)
      - `_llp_validation_clean` (positive edge, model present, no fatal flags)
      - edge >= 3.5%
      - independent model signal (`model_adjustments` non-empty)
      - confirmed starter AND confirmed lineup
      - zero failure paths
      - spread_fragility < 0.5 when applicable (None passes — fragility is
        only meaningful for `spreads` market; h2h/totals have None by design)
    """
    if not _llp_discovery_clean(rec):  return False
    if not _llp_validation_clean(rec): return False
    edge = rec.get("edge")
    if not isinstance(edge, (int, float)) or edge < 0.035: return False
    if not (rec.get("model_adjustments") or {}):           return False
    # Use hard-failure helper so soft advisories (e.g. F5 preference)
    # don't disqualify an otherwise ANCHOR-clean play.
    if _llp_hard_failure_paths(rec):                       return False
    if rec.get("starter_status") != "confirmed":           return False
    if rec.get("lineup_status")  != "confirmed":           return False
    disc = rec.get("discovery") or {}
    fragility = disc.get("spread_fragility")
    if isinstance(fragility, (int, float)) and fragility >= 0.5:
        return False
    return True


_LLP_ADVISORY_TAGS = frozenset({
    # Soft advisories that surface in `failure_paths` for dashboard display
    # but MUST NOT count toward `_llp_validation_clean`'s `>= 3` hard-fail
    # or `_llp_decision`'s `>= 3` WATCH demotion. Add new advisory tags here.
    "_LLP_MLB_FULLGAME_PREFERS_F5",
    # LLP Verified-Data-Layer Step 5 chooser tags: informational routing
    # hints (consumer should display the F5 line instead of full-game ML);
    # not real failures, so they must not affect validation cardinality.
    "_LLP_RECOMMEND_F5_ML",
    "_LLP_ML_WATCH_ONLY_NO_F5",
})


def _llp_hard_failure_paths(rec):
    """Return the subset of failure_paths that count as hard failures.

    Filters out `_LLP_ADVISORY_TAGS` so soft advisories (e.g. the MLB F5
    preference) cannot indirectly degrade validation or decision via the
    cardinality (`>=3`) checks.
    """
    fp = rec.get("failure_paths") or []
    return [f for f in fp if f not in _LLP_ADVISORY_TAGS]


def _llp_validation_clean(rec):
    """Validation Engine clean: model+edge+CLV are coherent and non-fatal."""
    if rec.get("favorite_trap_flag"):                       return False
    if rec.get("current_line") is None:                     return False
    edge = rec.get("edge")
    if not isinstance(edge, (int, float)) or edge < 0:      return False
    if rec.get("model_win_probability") is None:            return False
    # ≥3 hard failure paths is treated as a hard validation flag.
    # Advisory tags (e.g. F5 preference) are excluded so a soft advisory
    # cannot stack with 2 real failures to flip validation_clean.
    if len(_llp_hard_failure_paths(rec)) >= 3:              return False
    # CLV: tolerate unknown (None) and positive (True); reject confirmed negative.
    if rec.get("clv_beat") is False:                        return False
    return True


def _llp_compute_badge(rec):
    """v14.9+ canonical badge ladder + LLP team-betting spec ceiling.

    Wraps `_llp_compute_badge_raw` so EVERY return path is funneled through
    `_llp_apply_spec_badge_ceiling`. The raw function has many early
    returns; without this wrapper, only the CANDIDATE/PASS fallback path
    would be capped — which would leave missing-opening_line, missing-CLV,
    Kelly≤0, and short-fav records sitting at ANCHOR/BET.
    """
    return _llp_apply_spec_badge_ceiling(_llp_compute_badge_raw(rec), rec)


def _llp_compute_badge_raw(rec):
    """Raw badge ladder (PASS / CANDIDATE / WAIT / LEAN / QUALIFIED / BET / ANCHOR).

    PASS      → hard fail: TRAP, negative edge, negative CLV, no market
    ANCHOR    → BET-clean + low fragility + edge ≥ 3.5% + independent model
                + verified market_cause + confirmed starter/lineup + no failures
    BET       → discovery+validation clean, BET/SMALL BET decision
    QUALIFIED → discovery+validation clean, edge sub-bet
    LEAN      → positive edge but starter/lineup unverified
    WAIT      → WATCH or actionable edge blocked on unverified market_cause/timing
    CANDIDATE → discovery signal, validation incomplete (no edge yet)
    """
    decision = rec.get("final_decision") or "PASS"
    edge     = rec.get("edge")
    disc     = rec.get("discovery") or {}
    cause    = disc.get("market_cause")
    friction = disc.get("payout_friction")

    # ── Hard PASS conditions (take precedence over every other state). ──
    # Narrowly scoped per spec: TRAP, no market, negative CLV, negative edge,
    # excessive vig. We do NOT hard-fail on `decision == "PASS"` because that
    # bucket includes tiny-positive-edge plays (edge < 1.2%) that may still
    # be valid CANDIDATE/QUALIFIED records on a clean discovery side.
    if decision == "TRAP":                                 return "PASS"
    if rec.get("favorite_trap_flag"):                      return "PASS"
    if rec.get("current_line") is None:                    return "PASS"
    if rec.get("clv_beat") is False:                       return "PASS"
    if isinstance(edge, (int, float)) and edge < 0:        return "PASS"
    if isinstance(friction, (int, float)) and friction > _LLP_PAYOUT_FRICTION_OK:
        return "PASS"

    disc_ok = _llp_discovery_clean(rec)
    val_ok  = _llp_validation_clean(rec)

    # ANCHOR — single source of truth; all gates live in `_llp_anchor_eligible`.
    if _llp_anchor_eligible(rec):
        return "ANCHOR"

    # WAIT (market-side) — stale line, market_cause unverified/stale, or
    # explicit bad market_timing state. Checked BEFORE LEAN because a
    # market-data blocker takes precedence over an execution-side blocker:
    # even with a clean execution, a stale signal cannot approve the play.
    #
    # NOTE: `market_timing == "unverified"` is the current pipeline-wide
    # placeholder (the timing source isn't plumbed yet) and must NOT be
    # treated as a blocker — doing so would collapse every non-ANCHOR
    # record into WAIT. Once a real timing source exists, add its explicit
    # bad-state values (e.g. "late", "post_lock") to _LLP_BAD_TIMING below.
    has_edge = isinstance(edge, (int, float)) and edge > 0
    bad_timing = disc.get("market_timing") in _LLP_BAD_TIMING
    market_blocker = (cause in (None, "unverified", "stale")
                      or disc.get("stale_line") is True
                      or bad_timing)
    if has_edge and market_blocker:
        return "WAIT"

    # LEAN — positive edge with execution-side blocker (starter/lineup
    # unverified). Checked BEFORE BET because BET requires execution cleared.
    if has_edge and (rec.get("starter_status") == "unverified"
                     or rec.get("lineup_status") == "unverified"):
        return "LEAN"

    # BET — actionable decision with both engines clean.
    if disc_ok and val_ok and decision in ("BET", "SMALL BET"):
        return "BET"

    # QUALIFIED — both engines clean but edge below bet tier.
    if disc_ok and val_ok:
        return "QUALIFIED"

    # WAIT (decision-side) — WATCH decision with a real edge that didn't
    # match the market-blocker path above (e.g. fragility-blocked WATCH).
    if has_edge and decision == "WATCH":
        return "WAIT"

    # CANDIDATE — discovery surfaced something, validation incomplete.
    if cause and cause != "unverified" and not val_ok:
        badge = "CANDIDATE"
    else:
        badge = "PASS"
    # Apply LLP team-betting spec hard caps (data-completeness ceiling).
    return _llp_apply_spec_badge_ceiling(badge, rec)


def _llp_choose_mlb_target_market(rec):
    """LLP Verified-Data-Layer Step 5: route MLB full-game ML to F5 when
    full-game ML is not actionable.

    Returns one of:
      - "ML"             — full-game ML approved (strong edge or bullpen tailwind)
      - "F5_ML"          — full-game blocked but F5 market is available
      - "ML_WATCH_ONLY"  — full-game blocked and no F5 line available
      - <market.upper()> — non-h2h MLB markets pass through unchanged
      - "ML"             — non-MLB (no-op default)

    Gates (in priority order):
      1. Sport != MLB           → "ML" (caller's market unchanged downstream)
      2. Market != h2h          → market upper-case (pass through)
      3. Bullpen reliability > 0.50 OR full-game edge >= 0.04
                                → "ML" (full-game is actionable as-is)
      4. F5 market data present → "F5_ML"
      5. Otherwise              → "ML_WATCH_ONLY"
    """
    if not isinstance(rec, dict):                       return "ML"
    if (rec.get("sport")  or "").lower() != "mlb":      return "ML"
    market = (rec.get("market") or "").lower()
    if market != "h2h":
        return market.upper() if market else "ML"
    edge = rec.get("edge")
    bp   = rec.get("bullpen_reliability")
    if isinstance(bp, (int, float)) and bp > 0.50:
        return "ML"
    if isinstance(edge, (int, float)) and edge >= 0.04:
        return "ML"
    if rec.get("f5_available"):
        return "F5_ML"
    return "ML_WATCH_ONLY"


def _llp_mlb_fullgame_f5_advisory(rec):
    """LLP spec advisory: MLB full-game ML defaults to F5 unless bullpen edge
    is positive or full-game edge is strong.

    Unified with `_llp_choose_mlb_target_market` so the advisory and the
    chooser cannot disagree (architect-flagged split-brain). Advisory is
    True iff the chooser routed AWAY from "ML" for an MLB h2h record.
    Returns False for non-MLB, non-h2h, or records the chooser approved
    as full-game ML.
    """
    if not isinstance(rec, dict):                       return False
    if (rec.get("sport")  or "").lower() != "mlb":      return False
    if (rec.get("market") or "").lower() != "h2h":      return False
    return _llp_choose_mlb_target_market(rec) != "ML"


# ── Market alias normalization + segment-market routing ───────────────
# Canonical Odds API market keys live alongside user-facing aliases so
# callers can submit "ml" / "spread" / "f5_ml" / etc. and have them
# routed correctly. F5 (first-5-innings) markets are MLB-only and use
# the documented Odds API segment-market keys.
_LLP_MARKET_ALIASES = {
    # Full game
    "moneyline":"h2h", "ml":"h2h", "h2h":"h2h",
    "spread":"spreads", "spreads":"spreads",
    "total":"totals", "totals":"totals", "ou":"totals",
    # MLB First-5-Innings (F5) — short forms
    "f5":"h2h_1st_5_innings", "f5_ml":"h2h_1st_5_innings",
    "f5_h2h":"h2h_1st_5_innings", "first5":"h2h_1st_5_innings",
    "first5_ml":"h2h_1st_5_innings", "first_5":"h2h_1st_5_innings",
    "first_5_ml":"h2h_1st_5_innings",
    "f5_spread":"spreads_1st_5_innings", "f5_spreads":"spreads_1st_5_innings",
    "first5_spread":"spreads_1st_5_innings",
    "f5_total":"totals_1st_5_innings", "f5_totals":"totals_1st_5_innings",
    "f5_ou":"totals_1st_5_innings", "first5_total":"totals_1st_5_innings",
    # Canonical Odds API segment keys (passthrough)
    "h2h_1st_5_innings":"h2h_1st_5_innings",
    "spreads_1st_5_innings":"spreads_1st_5_innings",
    "totals_1st_5_innings":"totals_1st_5_innings",
}

# Sport-aware default market list for the odds fetcher. MLB pulls the
# F5 markets alongside full-game so a single odds call has everything
# the engine needs (full game + segment routing). Unsupported markets
# silently get dropped by The Odds API rather than 404'ing the call.
#
# COST NOTE: pulling 6 markets instead of 3 ~2x's Odds API credits per
# MLB fetch. Set LLP_DISABLE_MLB_F5=1 in the environment to fall back to
# full-game-only and recover the credits; F5 routing will then degrade
# gracefully (f5_available=False, recommended_market="ML_WATCH_ONLY").
_LLP_DEFAULT_ODDS_MARKETS_FALLBACK = "h2h,spreads,totals"

def _llp_default_odds_markets(sport_key):
    if sport_key == "baseball_mlb" and not os.environ.get("LLP_DISABLE_MLB_F5"):
        return ("h2h,spreads,totals,"
                "h2h_1st_5_innings,spreads_1st_5_innings,totals_1st_5_innings")
    return _LLP_DEFAULT_ODDS_MARKETS_FALLBACK

# Back-compat: tests + readers can still introspect this dict for "what
# markets does sport X pull by default" without exercising the env flag.
_LLP_DEFAULT_ODDS_MARKETS = {
    "baseball_mlb": ("h2h,spreads,totals,"
                     "h2h_1st_5_innings,spreads_1st_5_innings,totals_1st_5_innings"),
}


def _llp_market_base(market_key):
    """Map any market_key (full-game or segment) to its base type.

    Used by extract/expand logic so F5 markets behave identically to
    full-game equivalents at the matching level (h2h/spreads use team
    names, totals use over/under).
    """
    if not market_key: return ""
    m = str(market_key).lower()
    if m.startswith("h2h"):     return "h2h"
    if m.startswith("spreads"): return "spreads"
    if m.startswith("totals"):  return "totals"
    return m


def _llp_fetch_odds(sport_key, regions="us", markets=None):
    """Fetch current odds from The Odds API with TTL cache. Returns list of events or None."""
    import requests as _req
    if markets is None:
        markets = _llp_default_odds_markets(sport_key)
    now = datetime.now().timestamp()
    # Cache key includes markets so requests with different market sets
    # don't poison each other (callers can still override via argument).
    cache_key = (sport_key, markets, regions)
    hit = _LLP_ODDS_CACHE.get(cache_key)
    if hit and (now - hit[0]) < _LLP_CACHE_TTL_SEC:
        return hit[1]
    # Back-compat: also keep the legacy sport_key-only cache key warm so
    # any other reader hitting it via sport_key alone gets the latest.
    key = os.environ.get("ODDS_API_KEY", "")
    if not key: return None
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    try:
        r = _req.get(url, params={"apiKey": key, "regions": regions,
                                  "markets": markets, "oddsFormat": "american"},
                     timeout=10)
        if r.status_code != 200: return None
        events = r.json()
        _LLP_ODDS_CACHE[cache_key] = (now, events)
        _LLP_ODDS_CACHE[sport_key] = (now, events)  # legacy compat
        return events
    except Exception:
        return None


def _llp_norm_team(name):
    if not name: return ""
    return "".join(c for c in name.lower() if c.isalnum())


def _llp_match_event(events, away, home):
    """Best-effort fuzzy match of an event by team names."""
    if not events: return None
    a, h = _llp_norm_team(away), _llp_norm_team(home)
    for e in events:
        ea = _llp_norm_team(e.get("away_team",""))
        eh = _llp_norm_team(e.get("home_team",""))
        if (a in ea or ea in a) and (h in eh or eh in h):
            return e
        if (a in eh or eh in a) and (h in ea or ea in h):
            return e
    return None


def _llp_extract_market(event, market_key, side, line=None):
    """
    From an Odds API event, find the user's selection across all bookmakers.
    Returns dict with chosen book, american_odds, line (point), and a
    list of all (book, american) for the same outcome (used for devig).
    """
    if not event: return None
    side_norm = (side or "").strip()
    side_lc = side_norm.lower()
    bms = event.get("bookmakers") or []

    # Collect outcomes for the requested market across books
    all_outcomes = []  # list of (book_key, outcome_dict, market_pair)
    for bm in bms:
        for mk in (bm.get("markets") or []):
            if mk.get("key") != market_key: continue
            outs = mk.get("outcomes") or []
            for o in outs:
                all_outcomes.append((bm.get("key",""), o, outs))
    if not all_outcomes: return None

    base = _llp_market_base(market_key)  # h2h_1st_5_innings → h2h, etc.

    def _is_match(o):
        # Blank side must never match — caller is required to specify a side.
        if not side_lc: return False
        name = (o.get("name") or "").lower()
        if base == "totals":
            return name == side_lc  # exact "over" / "under"
        if base in ("h2h","spreads"):
            n_norm = _llp_norm_team(name); s_norm = _llp_norm_team(side_norm)
            if not n_norm or not s_norm: return False
            return n_norm == s_norm or n_norm in s_norm or s_norm in n_norm
        return False

    chosen = None
    pair_for_chosen = None
    for book, o, pair in all_outcomes:
        if _is_match(o):
            chosen = {"book": book, "american": o.get("price"),
                      "point": o.get("point"), "name": o.get("name")}
            pair_for_chosen = pair
            break
    if not chosen:
        return None

    # Build devig pair from the same bookmaker/market
    opp_p = None; chosen_p = _llp_american_to_prob(chosen["american"])
    if pair_for_chosen and len(pair_for_chosen) >= 2:
        for o in pair_for_chosen:
            if o is chosen.get("_o"): continue
            n = (o.get("name") or "").lower()
            if n != (chosen["name"] or "").lower():
                opp_p = _llp_american_to_prob(o.get("price"))
                break
    novig_chosen, _ = _llp_no_vig_two_way(chosen_p, opp_p)
    chosen["implied_prob"] = chosen_p
    chosen["novig_prob"]   = novig_chosen
    return chosen


def _llp_opening_line_for_game(away, home, market, side, board_date, current_point):
    """
    Reuse the opening_lines table for team markets by encoding the team market
    as a synthetic (player, prop, side) key. First write per date wins.
    The side token must differ between the two sides of a market, otherwise
    h2h/spreads writes collide and corrupt CLV.
    """
    import psycopg2 as _pg, psycopg2.extras as _pgx
    synth_player = f"{away}@{home}".strip()
    synth_prop   = f"team:{market}".lower()
    s_lc = (side or "").lower().strip()
    if market == "totals":
        synth_side = "over" if s_lc in ("over","yes") else "under"
    else:
        # h2h / spreads: key by normalized team selection so the two sides
        # of the same game don't share a row.
        tok = _llp_norm_team(side)[:60] or "unknown"
        synth_side = f"team_{tok}"
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url or current_point is None:
        return {"opening_line": None, "movement": None, "stored": False}
    try:
        conn = _pg.connect(db_url)
        try:
            _ensure_lines_schema(conn)
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO opening_lines
                      (player, player_lower, prop, side, line_date, opening_line, sport, book)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (player_lower, prop, side, line_date) DO NOTHING
                    RETURNING opening_line
                """, (synth_player, synth_player.lower(), synth_prop, synth_side,
                      board_date, float(current_point), None, None))
                ins = cur.fetchone()
                if ins:
                    conn.commit()
                    op = float(ins["opening_line"])
                    return {"opening_line": op, "movement": 0.0, "stored": True}
                cur.execute("""
                    SELECT opening_line FROM opening_lines
                    WHERE player_lower=%s AND prop=%s AND side=%s AND line_date=%s
                """, (synth_player.lower(), synth_prop, synth_side, board_date))
                row = cur.fetchone()
                if row and row.get("opening_line") is not None:
                    op = float(row["opening_line"])
                    return {"opening_line": op,
                            "movement": float(current_point) - op,
                            "stored": False}
                return {"opening_line": None, "movement": None, "stored": False}
        finally:
            conn.close()
    except Exception:
        return {"opening_line": None, "movement": None, "stored": False}


def _llp_model_prob(sport, market, side, novig_p, ctx):
    """
    v1 model: anchor on no-vig probability, apply structural adjustments.
    ctx supplies optional context (home_team match, line_movement, etc.).
    Returns (model_p, adjustments_dict).
    """
    if novig_p is None: return (None, {})
    adj = {}
    p = float(novig_p)

    # Home advantage on ML (h2h) — small bump for the home side
    if market == "h2h" and ctx.get("is_home_side"):
        bump = {"nba":0.020, "wnba":0.018, "mlb":0.015, "nfl":0.022,
                "nhl":0.018, "ncaab":0.024, "ncaaf":0.025}.get(sport, 0.015)
        p += bump; adj["home_advantage"] = bump

    # Team-strength blend (h2h only): when ESPN-standings ratings are
    # available for BOTH teams, anchor an independent projection on the
    # pythag/win-pct delta and blend it 50/50 with the home-adjusted market
    # baseline. This breaks the "edges cluster at +1.5%/+2.0%" pattern that
    # happens when model_p is essentially novig + a fixed bump — now the
    # model can genuinely agree OR disagree with the market based on real
    # team quality.
    #
    # Math (per architect review):
    #   - `p` already includes the home_advantage bump above.
    #   - `strength_p` is the NEUTRAL-SITE strength projection (no home bump
    #     in it) so the home advantage isn't double-counted via the blend.
    #   - blended = 0.5 * p + 0.5 * strength_p  → home bump counts once
    #     (carried by `p`); strength delta contributes its full weight.
    #   - `team_strength_blend` records only the incremental shift from the
    #     blend so the home_advantage line item stays clean and attributable.
    if market == "h2h":
        our_rating = ctx.get("our_team_rating")
        opp_rating = ctx.get("opp_team_rating")
        if (isinstance(our_rating, dict) and isinstance(opp_rating, dict)
                and isinstance(our_rating.get("strength"), (int, float))
                and isinstance(opp_rating.get("strength"), (int, float))):
            delta = float(our_rating["strength"]) - float(opp_rating["strength"])
            # Sport-specific weight: how much a 0.100 strength delta should
            # shift a single-game win prob. MLB is highest-variance so the
            # delta translates less; NBA/NFL more.
            weight = {"mlb":0.45, "nba":0.65, "wnba":0.60, "nfl":0.70,
                      "nhl":0.40, "ncaab":0.65, "ncaaf":0.65}.get(sport, 0.55)
            strength_p = 0.5 + (delta * weight)           # neutral site
            strength_p = max(0.05, min(0.95, strength_p))
            baseline_before = p                             # = novig + home_bump
            blended = 0.5 * baseline_before + 0.5 * strength_p
            shift = blended - baseline_before               # pure strength contribution
            p = blended
            # `model_adjustments` is additive-only: every numeric value here
            # MUST be a probability delta that contributes to (model_p - novig_p).
            # The raw strength gap (delta) is a rating quantity, not a prob
            # adjustment, so it lives on `record["team_ratings"]["delta"]`
            # (set by the caller in `_llp_analyze_one`), not here.
            adj["team_strength_blend"] = round(shift, 4)

    # Sharp line movement toward our side → small upward nudge
    mv = ctx.get("line_movement_pts")
    if mv is not None and ctx.get("movement_toward_us"):
        nudge = min(0.015, abs(mv) * 0.003)
        p += nudge; adj["sharp_move_with_us"] = nudge
    elif mv is not None and ctx.get("movement_against_us"):
        nudge = min(0.020, abs(mv) * 0.004)
        p -= nudge; adj["sharp_move_against_us"] = -nudge

    # Rest/B2B disadvantage for NBA totals/sides — stub-aware
    if sport == "nba" and ctx.get("b2b_disadvantage"):
        p -= 0.015; adj["b2b_disadvantage"] = -0.015

    # MLB bullpen reliability for totals (stub when unknown)
    if sport == "mlb" and market == "totals":
        rel = ctx.get("bullpen_reliability")  # 0..1 or None
        if rel is not None:
            # weak bullpen → more runs → bias OVER up / UNDER down
            delta = (rel - 0.5) * 0.04  # ±0.02
            if side.lower() == "over": p -= delta
            else:                      p += delta
            adj["bullpen_reliability"] = round(-delta if side.lower()=="over" else delta, 4)

    # MLB weather/park (stub when unknown — applied only if ctx provided)
    if sport == "mlb" and market == "totals" and ctx.get("park_run_factor"):
        prf = ctx["park_run_factor"]  # >1 hitter friendly, <1 pitcher friendly
        delta = (prf - 1.0) * 0.03
        if side.lower() == "over": p += delta
        else:                      p -= delta
        adj["park_run_factor"] = round(delta if side.lower()=="over" else -delta, 4)

    p = max(0.01, min(0.99, p))
    return (p, adj)


def _llp_upset_score(market, side, novig_p, model_p, is_home_side):
    """0..100 upset score; >55 = upset candidate."""
    if market != "h2h" or novig_p is None or model_p is None: return 0
    # Underdog (novig < 0.5) with positive edge
    if novig_p >= 0.50: return 0
    edge = model_p - novig_p
    if edge <= 0: return 0
    base = (0.50 - novig_p) * 200       # 0..100 as dog deepens
    edge_bonus = min(40, edge * 1000)   # +40 max for big edge
    return int(min(100, base * 0.6 + edge_bonus))


def _llp_favorite_trap(market, side, novig_p, model_p, line_movement_pts, against_us):
    """Heavy favorite (≥-200, novig ≥0.67), negative edge, line moving against us."""
    if market != "h2h" or novig_p is None or model_p is None: return False
    if novig_p < 0.67: return False
    if (model_p - novig_p) > -0.01: return False
    if line_movement_pts is None: return False
    return bool(against_us and abs(line_movement_pts) >= 0.5)


def _llp_failure_paths(sport, market, side, ctx):
    """List of risk callouts when context is missing or negative."""
    fp = []
    # Sport-aware availability check:
    #   MLB / NFL → starter confirmation is the meaningful signal (SP / QB).
    #   NBA / NHL / NCAAB / NCAAF / WNBA → lineup confirmation is what matters.
    if sport in ("mlb", "nfl"):
        if ctx.get("starters_unconfirmed"):
            fp.append("Starting pitcher not confirmed" if sport == "mlb"
                      else "Starting QB not confirmed")
    else:
        if ctx.get("lineup_unconfirmed"):
            fp.append("Starting lineup not confirmed")
    if ctx.get("injury_risk"):
        fp.append(f"Key injury concern: {ctx['injury_risk']}")
    if ctx.get("short_rest"):
        fp.append("Short rest / back-to-back disadvantage")
    if sport == "mlb" and market == "totals":
        if ctx.get("bullpen_reliability") is None:
            fp.append("Bullpen reliability data unavailable")
        if not ctx.get("park_run_factor") and not ctx.get("weather_checked"):
            fp.append("Weather/park factors unverified")
    if ctx.get("line_movement_pts") is None:
        fp.append("Opening line not yet captured (no movement signal)")
    if ctx.get("clv_status") == "missing":
        fp.append("CLV reference unavailable")
    return fp


def _llp_analyze_one(game, default_sport, board_date):
    """Analyze a single game/market and return the per-game record."""
    sport_in = (game.get("sport") or default_sport or "").lower().strip()
    sport = sport_in if sport_in in _LLP_SPORT_MAP else sport_in
    sport_key = _LLP_SPORT_MAP.get(sport)
    away = (game.get("away") or game.get("away_team") or "").strip()
    home = (game.get("home") or game.get("home_team") or "").strip()
    market_in = (game.get("market") or "h2h").lower().strip()
    market = _LLP_MARKET_ALIASES.get(market_in, market_in)
    side = (game.get("side") or "").strip()
    requested_line = game.get("line")

    def _empty_record(extra_notes=None, extra_failures=None):
        return {
            "sport": sport, "away_team": away, "home_team": home,
            "market": market, "side": side,
            "book": None, "opening_line": None, "current_line": None,
            "lock_line": None,                  # LLP spec settlement field
            "moneyline_fragility": None,        # LLP spec h2h fragility field
            "mlb_fullgame_prefers_f5": False,   # LLP spec F5 advisory flag
            "implied_probability": None, "no_vig_implied_probability": None,
            "model_win_probability": None, "edge": None,
            "kelly_stake": None, "confidence_tier": "UNKNOWN",
            "starter_status": "unverified",
            "lineup_status":  "unverified",
            "rest_context":   {"b2b": None, "days_rest": None, "short_rest": None, "note": "unverified"},
            "injury_rest_context": "unverified",
            "starter_lineup_confirmation": "unverified",
            "bullpen_reliability": None, "weather_park": None,
            "market_movement_clv_status": "unknown",
            "clv_beat": None, "clv_delta_pts": None,
            "discovery": {
                "stale_line": None, "line_freeze": None, "derivative_desync": None,
                "spread_fragility": None, "possession_conversion": None,
                "payout_friction": None, "market_cause": "unverified",
                "market_efficiency_rank": None, "market_timing": "unverified",
            },
            "upset_score": 0, "favorite_trap_flag": False,
            "prop_correlation_support": None,
            "failure_paths": list(extra_failures or []),
            "model_adjustments": {},
            "final_decision": "PASS",
            "discovery_clean": False,
            "validation_clean": False,
            "llp_badge": "PASS",
            "notes": list(extra_notes or []),
        }

    if not side:
        rec = _empty_record(
            extra_notes=["missing 'side' field"],
            extra_failures=["'side' is required for team-betting games"])
        return rec

    record = _empty_record()

    if not away or not home:
        record["notes"].append("missing away/home team")
        record["failure_paths"] = ["away_team and home_team are required"]
        record["final_decision"] = "PASS"
        return record
    if not sport_key:
        record["notes"].append(f"unsupported sport: {sport!r}")
        record["failure_paths"] = [f"Sport {sport!r} not mapped to Odds API"]
        return record

    events = _llp_fetch_odds(sport_key)
    if not events:
        record["notes"].append("odds-api unavailable or no events")
        record["failure_paths"] = ["Odds API returned no data for this sport"]
        return record

    event = _llp_match_event(events, away, home)
    if not event:
        record["notes"].append("event not found in odds feed")
        record["failure_paths"] = ["Game not found in current odds feed (check team names / date)"]
        return record

    sel = _llp_extract_market(event, market, side)
    if not sel:
        record["notes"].append(f"market/side not found: {market}/{side}")
        record["failure_paths"] = [f"Market {market} side {side!r} not offered by tracked books"]
        return record

    # LLP Pro Data Ingestion: persist every matched market snapshot.
    _llp_persist_odds_snapshot(sport, away, home, market, sel)

    record["book"]         = sel.get("book")
    record["current_line"] = sel.get("point") if sel.get("point") is not None else sel.get("american")
    record["implied_probability"]        = round(sel["implied_prob"], 4) if sel.get("implied_prob") is not None else None
    record["no_vig_implied_probability"] = round(sel["novig_prob"], 4)   if sel.get("novig_prob")   is not None else None

    # Opening-line / movement (only meaningful when there is a numeric point).
    # Primary source: `opening_lines` (first-write-wins per date).
    # Fallback: earliest `odds_snapshots` row from today — useful when a
    # background poller (or earlier analyze run for a different side of the
    # same game) wrote snapshot history before this market's opening_lines
    # row was created.
    op_info = {"opening_line": None, "movement": None, "stored": False}
    if isinstance(sel.get("point"), (int, float)):
        op_info = _llp_opening_line_for_game(away, home, market, side, board_date, sel["point"])
    # Pass the matched book so the snapshot anchor is same-book; falls back
    # to None (any-book) only if `sel` somehow has no book attribution.
    earliest_snap = _llp_earliest_snapshot_today(
        sport, away, home, market, side, board_date,
        book=sel.get("book") if isinstance(sel, dict) else None)
    # If opening_lines had no prior anchor but odds_snapshots does, use it.
    if (op_info.get("opening_line") is None
            and earliest_snap is not None
            and earliest_snap.get("point") is not None
            and isinstance(sel.get("point"), (int, float))):
        op_info = {
            "opening_line": earliest_snap["point"],
            "movement":     float(sel["point"]) - earliest_snap["point"],
            "stored":       False,
        }
    record["opening_line"] = op_info.get("opening_line")
    mv = op_info.get("movement")
    if mv is None:
        record["market_movement_clv_status"] = "no-opening-line"
    elif op_info.get("stored"):
        record["market_movement_clv_status"] = "opening-line-captured"
    else:
        record["market_movement_clv_status"] = f"moved {mv:+.2f} pts"

    # CLV beat flag: did the opening line we captured turn out better than current?
    # Direction-aware so it works for totals (over/under) and spreads (fav/dog).
    # mv == 0 is a push: neither beat nor loss → None (excluded from stats).
    record["clv_delta_pts"] = round(mv, 2) if isinstance(mv, (int, float)) else None
    if market == "h2h":
        # H2H has no point movement, so we anchor CLV on american-odds drift
        # using the earliest snapshot from today. We "beat CLV" when the
        # market price moved AGAINST our side (price got shorter for us =
        # opposing side longer = our locked price was better).
        # Implied-prob change is the cleanest direction-agnostic signal.
        cur_am  = sel.get("american")
        snap_am = (earliest_snap or {}).get("american_odds")
        if (isinstance(cur_am, (int, float)) and isinstance(snap_am, (int, float))
                and abs(cur_am - snap_am) >= 1):
            try:
                cur_dec  = _llp_american_to_decimal(cur_am)
                snap_dec = _llp_american_to_decimal(snap_am)
                cur_ip   = (1.0 / cur_dec)  if cur_dec  and cur_dec  > 0 else None
                snap_ip  = (1.0 / snap_dec) if snap_dec and snap_dec > 0 else None
                if cur_ip is not None and snap_ip is not None:
                    # Market thinks our side is MORE likely now → our earlier
                    # locked price was a CLV beat.
                    record["clv_beat"] = bool(cur_ip > snap_ip + 1e-6)
                    record["clv_delta_pts"] = round((cur_ip - snap_ip) * 100, 2)
                    record["market_movement_clv_status"] = (
                        f"price {snap_am:+.0f} → {cur_am:+.0f} "
                        f"({(cur_ip - snap_ip) * 100:+.1f} pp)")
                else:
                    record["clv_beat"] = None
            except Exception:
                record["clv_beat"] = None
        else:
            record["clv_beat"] = None
    elif mv is None or not isinstance(sel.get("point"), (int, float)) or abs(mv) < 1e-9:
        record["clv_beat"] = None
    elif market == "totals":
        s_lc = (side or "").lower()
        if   s_lc == "over":  record["clv_beat"] = mv > 0   # total moved up vs our opening
        elif s_lc == "under": record["clv_beat"] = mv < 0
        else:                 record["clv_beat"] = None
    elif market == "spreads":
        sp = sel.get("point")
        if   sp < 0: record["clv_beat"] = mv < 0   # favorite: line got more negative → we beat CLV
        elif sp > 0: record["clv_beat"] = mv > 0   # dog: line got more positive
        else:        record["clv_beat"] = None
    else:
        record["clv_beat"] = None

    # Build context for the model
    is_home_side = market == "h2h" and (_llp_norm_team(side) and
                                        _llp_norm_team(side) in _llp_norm_team(home))
    movement_toward_us = False; movement_against_us = False
    if mv is not None and abs(mv) > 0.01:
        if market == "totals":
            # mv>0 = total raised → toward OVER; mv<0 → toward UNDER
            if side.lower() == "over"  and mv > 0: movement_toward_us = True
            if side.lower() == "under" and mv < 0: movement_toward_us = True
            if side.lower() == "over"  and mv < 0: movement_against_us = True
            if side.lower() == "under" and mv > 0: movement_against_us = True
        elif market == "spreads":
            # Direction depends on whether we took the favorite (point<0)
            # or the dog (point>0). For a favorite, the line moving more
            # negative (mv<0) means the market agrees with us → toward us.
            # For a dog, mv>0 (line getting more positive) → toward us.
            sp = sel.get("point")
            if isinstance(sp, (int, float)):
                if sp < 0:   # we took the favorite
                    if mv < 0: movement_toward_us = True
                    else:      movement_against_us = True
                elif sp > 0: # we took the dog
                    if mv > 0: movement_toward_us = True
                    else:      movement_against_us = True
                # sp == 0 (pickem) → no directional signal

    # Rest / fatigue signals (passthrough from caller, all optional)
    days_rest = game.get("days_rest")
    b2b       = game.get("b2b_disadvantage") or game.get("back_to_back")
    short_rest = False
    if isinstance(days_rest, (int, float)) and days_rest <= 1: short_rest = True
    if b2b: short_rest = True

    # LLP Pro Data Ingestion: resolve starter/lineup from official sources
    # FIRST, before building `ctx`. The downstream `_llp_discovery`,
    # `_llp_model_prob`, and `_llp_failure_paths` all read
    # `ctx["starters_unconfirmed"]` / `ctx["lineup_unconfirmed"]`, so if we
    # resolved AFTER ctx was built the official API would never override the
    # body-driven defaults — confirmed MLB games would still carry legacy
    # "Starting pitcher not confirmed" failure paths.
    starter_from_body = "confirmed" if game.get("starters_confirmed") else "unverified"
    lineup_from_body  = ("confirmed" if game.get("lineup_confirmed") else
                         ("confirmed" if game.get("starters_confirmed") else "unverified"))
    if sport == "mlb":
        mlb_ctx = _llp_fetch_mlb_lineup_context(away, home, board_date)
        record["starter_status"] = (mlb_ctx["starter_status"]
                                    if mlb_ctx["starter_status"] == "confirmed"
                                    else starter_from_body)
        record["lineup_status"]  = (mlb_ctx["lineup_status"]
                                    if mlb_ctx["lineup_status"] == "confirmed"
                                    else lineup_from_body)
    else:
        record["starter_status"] = starter_from_body
        record["lineup_status"]  = lineup_from_body
    # Back-compat combined signal now reflects resolved statuses, not raw
    # body flags. "confirmed" if EITHER resolved starter or lineup is confirmed.
    record["starter_lineup_confirmation"] = (
        "confirmed" if (record["starter_status"] == "confirmed"
                        or record["lineup_status"] == "confirmed")
        else "unverified")

    # LLP Pro Data Ingestion: fetch injury context (best-effort, per game).
    # Persists to `injury_status` table inside the fetcher; we keep a small
    # summary on the record so downstream/postmortem can see it.
    injury_home = _llp_fetch_espn_injuries(sport, home)
    injury_away = _llp_fetch_espn_injuries(sport, away)
    record["injury_context"] = {
        "home_team":  home, "home_injuries": injury_home,
        "away_team":  away, "away_injuries": injury_away,
        "home_count": len(injury_home), "away_count": len(injury_away),
        "source":     "site.api.espn.com",
    }

    # LLP Pro Data Ingestion: fetch team strength ratings (ESPN standings,
    # 1h cache, one HTTP call per sport per hour). Look up BOTH teams and
    # decide which is "our" side vs "opp" based on the bet side we're
    # analyzing. Used by `_llp_model_prob` to compute an independent
    # h2h projection instead of just nudging no-vig — fixing the
    # +1.5%/+2.0% edge clustering pattern.
    team_ratings = _llp_fetch_team_ratings(sport)
    home_rating  = _llp_lookup_team_rating(team_ratings, home)
    away_rating  = _llp_lookup_team_rating(team_ratings, away)
    # Compute the raw strength gap once for metadata. Stored on the record
    # (NOT in `model_adjustments`, which is reserved for additive probability
    # deltas — per architect review). Negative = our side rated weaker.
    _home_strength = (home_rating or {}).get("strength") if isinstance(home_rating, dict) else None
    _away_strength = (away_rating or {}).get("strength") if isinstance(away_rating, dict) else None
    record["team_ratings"] = {
        "home": home_rating, "away": away_rating,
        "home_strength": _home_strength, "away_strength": _away_strength,
        "source": "site.api.espn.com",
        "available": bool(home_rating and away_rating),
    }
    # Determine which team is "our" side for the model. Match the side token
    # to home/away using normalized team names (handles aliases like
    # "Yankees" → "New York Yankees", case variants, punctuation).
    #
    # Safety rule (per architect review): for h2h, if the side cannot be
    # confidently mapped to either team, DISABLE the strength blend by
    # passing None — defaulting to "home" would invert the delta sign on
    # mis-mapped sides and silently corrupt edges. For totals (side is
    # over/under and the blend is h2h-only anyway), defaulting to home is
    # harmless and lets the record carry a useful rating snapshot.
    s_norm    = _llp_norm_team(side or "")
    home_norm = _llp_norm_team(home or "")
    away_norm = _llp_norm_team(away or "")
    if s_norm and home_norm and (s_norm == home_norm
                                  or s_norm in home_norm
                                  or home_norm in s_norm):
        our_rating, opp_rating = home_rating, away_rating
    elif s_norm and away_norm and (s_norm == away_norm
                                    or s_norm in away_norm
                                    or away_norm in s_norm):
        our_rating, opp_rating = away_rating, home_rating
    elif market == "h2h":
        # H2H side didn't map to either team — refuse to guess. The model
        # will fall back to pure no-vig + home bump for this record.
        our_rating, opp_rating = None, None
    else:
        # Totals (over/under) — strength blend is gated on market == 'h2h'
        # inside _llp_model_prob, so the choice here is cosmetic.
        our_rating, opp_rating = home_rating, away_rating

    ctx = {
        "is_home_side": is_home_side,
        "line_movement_pts": mv,
        "movement_toward_us": movement_toward_us,
        "movement_against_us": movement_against_us,
        "b2b_disadvantage": bool(b2b),
        "days_rest": days_rest,
        "short_rest": short_rest,
        "bullpen_reliability": game.get("bullpen_reliability"),
        "park_run_factor": game.get("park_run_factor"),
        "weather_checked": bool(game.get("weather_checked")),
        # Resolved statuses (API-first, body fallback) — see block above.
        "starters_unconfirmed": record["starter_status"] != "confirmed",
        "lineup_unconfirmed":   record["lineup_status"]  != "confirmed",
        "injury_risk": game.get("injury_risk"),
        "clv_status": "missing" if mv is None else "tracked",
        # Team-strength ratings (ESPN standings, optional). Consumed by
        # `_llp_model_prob` for the h2h independent-projection blend.
        "our_team_rating": our_rating,
        "opp_team_rating": opp_rating,
    }

    # LLP Discovery pre-pass: classify the team-side market before validation.
    record["discovery"] = _llp_discovery(
        sport, market, side, sel, mv, record["market_movement_clv_status"], ctx)

    model_p, adj = _llp_model_prob(sport, market, side, sel.get("novig_prob"), ctx)
    record["model_win_probability"] = round(model_p, 4) if model_p is not None else None
    record["model_adjustments"]     = adj

    dec_odds = _llp_american_to_decimal(sel.get("american"))
    record["decimal_odds"] = dec_odds
    if model_p is not None and sel.get("novig_prob") is not None:
        edge = model_p - sel["novig_prob"]
        record["edge"] = round(edge, 4)
        record["kelly_stake"] = round(_llp_kelly(model_p, dec_odds), 4)
        record["confidence_tier"] = _llp_confidence_tier(edge)
    else:
        edge = None
    rest_note_parts = []
    if isinstance(days_rest, (int, float)): rest_note_parts.append(f"{days_rest} days rest")
    if b2b:                                  rest_note_parts.append("back-to-back")
    if game.get("injury_risk"):              rest_note_parts.append(f"injury: {game['injury_risk']}")
    record["rest_context"] = {
        "b2b": bool(b2b) if b2b is not None or days_rest is not None else None,
        "days_rest": days_rest,
        "short_rest": short_rest,
        "note": ", ".join(rest_note_parts) if rest_note_parts else "unverified",
    }
    record["injury_rest_context"] = (
        ", ".join(rest_note_parts) if rest_note_parts else "no flags reported")
    if sport == "mlb":
        record["bullpen_reliability"] = game.get("bullpen_reliability")
        wp = []
        if game.get("park_run_factor") is not None:
            wp.append(f"park_run_factor={game['park_run_factor']}")
        if game.get("weather"):
            wp.append(f"weather={game['weather']}")
        record["weather_park"] = ", ".join(wp) if wp else "unverified"
    record["prop_correlation_support"] = game.get("prop_correlation_support")

    record["upset_score"] = _llp_upset_score(market, side, sel.get("novig_prob"),
                                             model_p, is_home_side)
    record["favorite_trap_flag"] = _llp_favorite_trap(market, side,
                                                     sel.get("novig_prob"), model_p,
                                                     mv, movement_against_us)

    record["failure_paths"] = _llp_failure_paths(sport, market, side, ctx)

    record["final_decision"] = _llp_decision(edge, model_p, sel.get("novig_prob"),
                                             record["upset_score"],
                                             record["favorite_trap_flag"],
                                             record["failure_paths"])

    # LLP Verified-Data-Layer Step 5: when this is an MLB full-game h2h
    # record, peek at the SAME event for F5 (first-5-innings) availability
    # so the chooser below can route to F5_ML if full-game isn't actionable.
    # F5 markets are pulled in the same odds call via _LLP_DEFAULT_ODDS_MARKETS
    # so this is a free in-memory lookup — no extra API hit.
    record["f5_available"] = False
    record["f5_book"]      = None
    record["f5_american"]  = None
    record["f5_total_line"] = None
    if sport == "mlb" and market == "h2h" and side:
        f5_sel = _llp_extract_market(event, "h2h_1st_5_innings", side)
        if isinstance(f5_sel, dict) and f5_sel.get("american") is not None:
            record["f5_available"] = True
            record["f5_book"]     = f5_sel.get("book")
            record["f5_american"] = f5_sel.get("american")
            # Also try to grab the F5 total line for downstream display.
            # We probe both sides since either Over or Under will carry the
            # same `point` value.
            for _side in ("Over", "Under"):
                f5_total = _llp_extract_market(event, "totals_1st_5_innings", _side)
                if isinstance(f5_total, dict) and isinstance(f5_total.get("point"), (int, float)):
                    record["f5_total_line"] = float(f5_total["point"])
                    break

    # LLP spec: MLB full-game ML defaults to F5 unless bullpen edge positive
    # or full-game edge strong. Surfaced as a failure_path + top-level flag so
    # the dashboard can redirect users to the F5 line for a cleaner read.
    # Appended AFTER `_llp_decision` so the advisory cannot accidentally
    # trigger the `len(failures) >= 3` downgrade inside `_llp_decision`.
    record["mlb_fullgame_prefers_f5"] = _llp_mlb_fullgame_f5_advisory(record)
    if record["mlb_fullgame_prefers_f5"]:
        record["failure_paths"].append("_LLP_MLB_FULLGAME_PREFERS_F5")

    # LLP Verified-Data-Layer Step 5 chooser: recommended target market.
    # ML / F5_ML / ML_WATCH_ONLY for MLB h2h; pass-through otherwise. Also
    # sets `full_game_edge_allowed` so downstream approval gates can refuse
    # MLB full-game ML bets when the chooser routed us to F5 or watch-only.
    record["recommended_market"]    = _llp_choose_mlb_target_market(record)
    record["full_game_edge_allowed"] = (record["recommended_market"] == "ML")
    if record["recommended_market"] == "F5_ML":
        record["failure_paths"].append("_LLP_RECOMMEND_F5_ML")
    elif record["recommended_market"] == "ML_WATCH_ONLY":
        record["failure_paths"].append("_LLP_ML_WATCH_ONLY_NO_F5")

    # LLP spec field: moneyline_fragility for h2h markets, derived from how
    # extreme the no-vig implied probability is (heavy fav / dog = fragile).
    # Spec accepts "spread_fragility OR moneyline_fragility"; we surface both
    # so consumers can gate on whichever is meaningful per market.
    novig = sel.get("novig_prob") if isinstance(sel, dict) else None
    if market == "h2h" and isinstance(novig, (int, float)):
        record["moneyline_fragility"] = round(abs(novig - 0.5) * 2, 4)
    else:
        record["moneyline_fragility"] = None

    # v14.9+ Discovery+Validation gates and canonical badge.
    record["discovery_clean"]  = _llp_discovery_clean(record)
    record["validation_clean"] = _llp_validation_clean(record)
    # LLP Pro Data Ingestion: detect the 7 spec failure tags AFTER the
    # discovery/validation gates have run (the `candidate-promoted-too-early`
    # check depends on `discovery_clean`/`validation_clean`). Then re-run
    # validation so any new HARD tag counts toward the `>=3` cardinality
    # gate, and compute the badge (which routes through the spec ceiling
    # and applies the CANDIDATE cap for stale/early/fake-edge tags).
    _pro_tags = _llp_pro_detect_failures(record)
    if _pro_tags:
        record["failure_paths"].extend(_pro_tags)
        record["validation_clean"] = _llp_validation_clean(record)
    # WOW v16 Game Winner Payout Discipline: enforce minimum payout / short-price
    # verification on the h2h moneyline lane and surface the inverted-stake flag.
    _gw_tags, _gw_inverted, _gw_reject = _llp_game_winner_discipline(record)
    record["inverted_stake_sizing"] = _gw_inverted
    if _gw_tags:
        record["failure_paths"].extend(_gw_tags)
        record["validation_clean"] = _llp_validation_clean(record)
    if _gw_reject:
        # Sub-1.35x Game Winner is a hard REJECT — drop it entirely.
        record["final_decision"] = "PASS"
    elif (LLP_PRO_FAILURE_GW_SHORT_PRICE_UNVERIFIED in _gw_tags
          and record.get("final_decision") in ("BET", "SMALL BET")):
        # Short-price (1.35–1.50x) unverified is non-actionable (badge capped
        # at CANDIDATE). Demote an actionable call to WATCH so it stays on the
        # board for review but never surfaces in winners_ranked / best_bets.
        record["final_decision"] = "WATCH"
    record["llp_badge"]        = _llp_compute_badge(record)

    # LLP spec field: `lock_line` — snapshot the current_line at the moment
    # the validation engine clears the play. NULL when validation isn't clean
    # so the dashboard never displays a phantom lock for a watchlist record.
    record["lock_line"] = (record.get("current_line")
                           if record["validation_clean"] else None)

    # Honor a user-requested line as a sanity flag
    if requested_line is not None and isinstance(sel.get("point"), (int, float)):
        try:
            if abs(float(requested_line) - float(sel["point"])) > 0.01:
                record["notes"].append(
                    f"line drift: requested {requested_line} vs current {sel['point']}")
        except (TypeError, ValueError):
            pass

    # LLP Pro Data Ingestion: write the final per-game decision to
    # `bet_decisions` for postmortem/learning. Best-effort; logging
    # failures must never break analysis.
    _llp_log_bet_decision(record)

    return record


def _llp_discovery(sport, market, side, sel, mv, opening_status, ctx):
    """LLP Discovery pre-pass: team-side market intelligence.

    Owns stale-line detection, line-freeze flags, derivative desync,
    spread fragility, possession conversion, payout friction, and
    market-cause classification. Runs before validation/execution and
    is surfaced as a `discovery` block on each record so the dashboard
    can show *why* a market is (or isn't) actionable.
    """
    # Key numbers per sport for spread fragility scoring.
    SPREAD_KEYS = {
        "nfl":     [3, 7, 10, 14, 6, 4],
        "ncaaf":   [3, 7, 10, 14, 6, 4],
        "nba":     [2.5, 5, 7, 3],
        "wnba":    [2.5, 5, 7, 3],
        "ncaab":   [2.5, 5, 7, 3],
        "mlb":     [1.5],
        "nhl":     [1.5],
    }

    point   = sel.get("point") if sel else None
    implied = sel.get("implied_prob") if sel else None
    novig   = sel.get("novig_prob")   if sel else None

    # ── Payout friction: vig cost per side (implied minus no-vig). ──
    if isinstance(implied, (int, float)) and isinstance(novig, (int, float)):
        payout_friction = round(max(0.0, implied - novig), 4)
    else:
        payout_friction = None

    # ── Spread fragility: distance from nearest key number (0..1). ──
    spread_fragility = None
    if market == "spreads" and isinstance(point, (int, float)):
        keys = SPREAD_KEYS.get(sport, [])
        if keys:
            dist = min(abs(abs(point) - k) for k in keys)
            spread_fragility = round(max(0.0, 1.0 - dist / 0.5), 3) if dist <= 0.5 else 0.0

    # ── Stale-line + line-freeze flags. ──
    # We have one snapshot per request, so "freeze" is best-effort:
    # opening captured + zero movement = candidate freeze; multi-snapshot
    # history is Phase 3 territory.
    stale_line  = (opening_status == "opening-line-captured" and (mv is None or abs(mv) < 1e-9))
    line_freeze = stale_line  # alias until snapshot history exists

    # ── Derivative desync: needs cross-market join (ML implied vs spread
    # implied vs total implied for same game). Not available in single-row
    # analyze loop — flag explicitly so dashboard knows it's not silently False.
    derivative_desync = None
    # ── Possession conversion: pace-adjusted edge — needs team pace stats
    # we don't pull here. Same honest unknown.
    possession_conversion = None

    # ── Market cause classification (heuristic). ──
    if ctx.get("movement_against_us") and ctx.get("lineup_unconfirmed"):
        market_cause = "injury_lag"
    elif ctx.get("movement_against_us") and isinstance(mv, (int, float)) and abs(mv) >= 1.0:
        market_cause = "sharp_against"
    elif ctx.get("movement_toward_us") and isinstance(mv, (int, float)) and abs(mv) >= 1.0:
        market_cause = "sharp_with"
    elif stale_line:
        market_cause = "stale"
    elif mv is None:
        market_cause = "unverified"
    else:
        market_cause = "clean"

    # ── Market efficiency rank: lower = more inefficient/exploitable. ──
    score = 0.0
    if stale_line:                                   score += 0.4
    if (spread_fragility or 0) > 0.5:                score += 0.2
    if isinstance(payout_friction, (int, float)) and payout_friction > 0.025: score += 0.15
    if market_cause in ("sharp_against","injury_lag"): score += 0.25
    market_efficiency_rank = round(min(1.0, score), 3)  # 0 = efficient, 1 = highly inefficient

    return {
        "stale_line":             stale_line,
        "line_freeze":            line_freeze,
        "derivative_desync":      derivative_desync,
        "spread_fragility":       spread_fragility,
        "possession_conversion":  possession_conversion,
        "payout_friction":        payout_friction,
        "market_cause":           market_cause,
        "market_efficiency_rank": market_efficiency_rank,
        "market_timing":          "unverified",  # needs game.commence_time plumbing
    }


def _llp_plain_english_reason(rec):
    """Build a one-sentence human-readable rationale for a record."""
    decision = rec.get("final_decision") or "PASS"
    edge = rec.get("edge")
    mkt  = rec.get("market") or "h2h"
    side = rec.get("side") or "this side"
    line = rec.get("current_line")
    book = rec.get("book") or "consensus"
    mp   = rec.get("model_win_probability")
    ip   = rec.get("no_vig_implied_probability") or rec.get("implied_probability")
    mkt_label = {"h2h": "moneyline", "spreads": "spread", "totals": "total"}.get(mkt, mkt)
    # For h2h the `current_line` field actually holds the American odds price
    # (e.g. -145, +136) — render with explicit sign and label as odds.
    # For spreads/totals it's the point line; signed for spreads, plain for totals.
    if isinstance(line, (int, float)):
        if mkt == "h2h":      line_str = f" ({line:+d} odds)" if float(line).is_integer() else f" ({line:+g} odds)"
        elif mkt == "spreads": line_str = f" {line:+g}"
        else:                  line_str = f" {line:g}"
    else:
        line_str = ""
    pct = (lambda x: f"{x*100:.1f}%") if isinstance(mp, (int, float)) else (lambda x: "n/a")

    if decision == "BET":
        head = f"Best bet: {side}{line_str} ({mkt_label}) at {book}."
    elif decision == "SMALL BET":
        head = f"Small bet: {side}{line_str} ({mkt_label}) at {book}."
    elif decision == "TRAP":
        head = f"Trap alert: avoid {side}{line_str} ({mkt_label})."
    elif decision == "WATCH":
        head = f"Watch only: {side}{line_str} ({mkt_label}) — wait for confirmation."
    else:
        head = f"Pass: {side}{line_str} ({mkt_label})."

    body_parts = []
    if isinstance(mp, (int, float)) and isinstance(ip, (int, float)):
        body_parts.append(f"model {pct(mp)} vs market {pct(ip)}")
    if isinstance(edge, (int, float)):
        body_parts.append(f"edge {edge*100:+.2f}%")
    if rec.get("clv_beat") is True:
        body_parts.append("beating CLV")
    elif rec.get("clv_beat") is False:
        body_parts.append("losing CLV")
    fp = rec.get("failure_paths") or []
    if fp and decision in ("PASS", "TRAP", "WATCH"):
        body_parts.append(f"risk: {fp[0].lower()}")
    body = ("; " + ", ".join(body_parts)) if body_parts else ""
    return head + body


def _llp_resolve_team_opponent(market, side, away, home):
    """Shared team/opponent resolver used by clean items and postmortem logging.

    For totals, returns the matchup as the "team" field with no opponent.
    For h2h/spreads, uses normalized team matching to assign side → team.
    """
    if market == "totals":
        return (f"{away} @ {home}".strip(" @"), "")
    side_n = _llp_norm_team(side or "")
    home_n = _llp_norm_team(home or "")
    away_n = _llp_norm_team(away or "")
    if side_n and home_n and (side_n in home_n or home_n in side_n):
        return (home, away)
    if side_n and away_n and (side_n in away_n or away_n in side_n):
        return (away, home)
    return (side or "", (home if side != home else away) or "")


def _llp_clean_item(rec):
    """ChatGPT-friendly projection of a full analyze record."""
    sport  = rec.get("sport") or ""
    away   = rec.get("away_team") or ""
    home   = rec.get("home_team") or ""
    side   = rec.get("side") or ""
    market = rec.get("market") or "h2h"
    mkt_label = {"h2h": "moneyline", "spreads": "spread", "totals": "total"}.get(market, market)

    team_field, opponent_field = _llp_resolve_team_opponent(market, side, away, home)

    return {
        "sport":                sport,
        "team":                 team_field,
        "opponent":             opponent_field,
        "market":               mkt_label,
        "side":                 side,
        "line":                 rec.get("current_line"),
        "book":                 rec.get("book"),
        "implied_probability":  rec.get("no_vig_implied_probability") or rec.get("implied_probability"),
        "model_probability":    rec.get("model_win_probability"),
        "edge":                 rec.get("edge"),
        "kelly":                rec.get("kelly_stake"),
        "decision":             rec.get("final_decision"),
        "confidence_tier":      rec.get("confidence_tier"),
        "llp_badge":            rec.get("llp_badge"),
        "discovery_clean":      rec.get("discovery_clean"),
        "validation_clean":     rec.get("validation_clean"),
        "clv_beat":             rec.get("clv_beat"),
        "rest_context":         rec.get("rest_context"),
        "starter_status":       rec.get("starter_status"),
        "lineup_status":        rec.get("lineup_status"),
        "top_failure_paths":    (rec.get("failure_paths") or [])[:3],
        "discovery":            rec.get("discovery"),
        "plain_english_reason": _llp_plain_english_reason(rec),
    }


def _llp_expand_both_sides(games):
    """Adapter: expand every matchup into BOTH sides of every market it touches.

    The engine evaluates one (sport, away, home, market, side) per record.
    Callers historically had to submit each side explicitly, which made it
    impossible to compare both sides of the same market in one slate and
    blocked downstream features that need the partner side (no-vig pricing,
    arbitrage detection, side-specific CLV).

    This adapter takes a list of game payloads and returns an expanded list
    where every (sport, away, home, market) appears with BOTH sides:
      - h2h:     side=<away_team>, side=<home_team>
      - totals:  side="over",      side="under"
      - spreads: side=<away_team>, side=<home_team>  (line sign flips)

    Existing entries are preserved verbatim; only the missing partner side
    is appended. Empty/None `side` on h2h or spreads → expand to both teams.
    Empty/None `side` on totals → expand to both over and under. The
    operation is idempotent: passing an already-expanded slate is a no-op.
    """
    if not games:
        return []
    out = []
    seen = set()  # (sport, away, home, market, normalized_side) → dedupe

    def _key(g, side_override=None):
        sport = (g.get("sport") or "").lower().strip()
        away  = (g.get("away") or g.get("away_team") or "").strip()
        home  = (g.get("home") or g.get("home_team") or "").strip()
        market_raw = (g.get("market") or "h2h").lower().strip()
        market = _LLP_MARKET_ALIASES.get(market_raw, market_raw)
        side = (side_override if side_override is not None
                else (g.get("side") or "")).lower().strip()
        return (sport, away, home, market, side)

    def _add(g):
        k = _key(g)
        if k in seen:
            return
        seen.add(k)
        out.append(g)

    for g in games:
        if not isinstance(g, dict):
            continue
        market_raw = (g.get("market") or "h2h").lower().strip()
        market = _LLP_MARKET_ALIASES.get(market_raw, market_raw)
        base   = _llp_market_base(market)  # F5 markets branch like full-game equivalents
        away = (g.get("away") or g.get("away_team") or "").strip()
        home = (g.get("home") or g.get("home_team") or "").strip()
        side = (g.get("side") or "").strip()
        line = g.get("line")

        if base == "totals":
            if not side or side.lower() not in ("over", "under"):
                # No side specified → emit both.
                base = {k: v for k, v in g.items() if k != "side"}
                _add({**base, "side": "over"})
                _add({**base, "side": "under"})
            else:
                _add(g)
                partner_side = "under" if side.lower() == "over" else "over"
                _add({**{k: v for k, v in g.items() if k != "side"},
                      "side": partner_side})
        elif base in ("h2h", "spreads"):
            if not away or not home:
                _add(g)  # let the engine emit the proper failure path
                continue
            if not side:
                # No side → emit both teams.
                base_g = {k: v for k, v in g.items() if k not in ("side", "line")}
                if base == "h2h":
                    _add({**base_g, "side": away})
                    _add({**base_g, "side": home})
                else:  # spreads — need a line to flip; if none, emit no line
                    if isinstance(line, (int, float)):
                        _add({**base_g, "side": away, "line":  float(line)})
                        _add({**base_g, "side": home, "line": -float(line)})
                    else:
                        _add({**base_g, "side": away})
                        _add({**base_g, "side": home})
            else:
                _add(g)
                # Figure out the partner team. Match case-insensitively;
                # if `side` doesn't match either team, leave the payload
                # alone and let the engine emit the failure path.
                s_lc = side.lower()
                if   s_lc == away.lower(): partner = home
                elif s_lc == home.lower(): partner = away
                else: partner = None
                if partner:
                    base_g = {k: v for k, v in g.items() if k not in ("side", "line")}
                    if base == "spreads" and isinstance(line, (int, float)):
                        _add({**base_g, "side": partner, "line": -float(line)})
                    else:
                        _add({**base_g, "side": partner})
        else:
            # Unknown market — pass through unchanged.
            _add(g)
    return out


def _llp_team_analysis(games, default_sport, board_date):
    """Analyze a list of games and aggregate into the response shape.

    Auto-expands each matchup into both sides via `_llp_expand_both_sides`
    so callers can submit a single game object per market (or omit `side`
    entirely) and still get both-side analysis. Idempotent — already-
    expanded slates are deduped.
    """
    games = _llp_expand_both_sides(games or [])
    analyses = []
    source_status = {"odds_api": "ok" if os.environ.get("ODDS_API_KEY") else "missing",
                     "opening_lines_db": "ok" if os.environ.get("DATABASE_URL") else "missing"}
    failures = 0; matches = 0
    for g in games:
        rec = _llp_analyze_one(g, default_sport, board_date)
        analyses.append(rec)
        if rec.get("current_line") is not None: matches += 1
        else: failures += 1
    if failures and not matches:
        source_status["odds_api"] = "no matches in feed"
    return _llp_build_buckets(analyses, source_status)


def _llp_build_buckets(analyses, source_status):
    """Aggregate per-record analyses into the response/bucket shape.

    Pure function: re-run after the Claude arbiter mutates llp_badge fields
    so the response buckets stay consistent with the post-audit state.

    Defensive: a single malformed entry (None, non-dict, or non-dict
    nested `discovery`) must never crash bucket construction or the
    downstream HTTP response. We sanitize the input here so every
    consumer below can assume dict shape.
    """
    analyses = [r for r in (analyses or []) if isinstance(r, dict)]
    for r in analyses:
        if not isinstance(r.get("discovery"), dict):
            r["discovery"] = {}
    matches  = sum(1 for r in analyses if r.get("current_line") is not None)
    failures = len(analyses) - matches

    def _rank(r):
        edge = r.get("edge") or -1
        return (edge, r.get("model_win_probability") or 0)

    winners_ranked = sorted(
        [r for r in analyses if r["final_decision"] in ("BET","SMALL BET")],
        key=_rank, reverse=True)
    upset_candidates = [r for r in analyses if r["upset_score"] >= 55]
    best_bets        = [r for r in analyses if r["final_decision"] == "BET"]
    pass_traps       = [r for r in analyses
                        if r["final_decision"] in ("PASS","TRAP","WATCH")]
    traps_only       = [r for r in analyses if r["final_decision"] == "TRAP"]

    # Slate-level summary: counts, total stake, edge stats, CLV stats.
    decisions = {"BET":0, "SMALL BET":0, "WATCH":0, "PASS":0, "TRAP":0}
    edges = []; stakes = []; clv_beats = 0; clv_losses = 0; clv_tracked = 0
    for r in analyses:
        d = r.get("final_decision", "PASS")
        if d in decisions: decisions[d] += 1
        if isinstance(r.get("edge"), (int, float)):        edges.append(float(r["edge"]))
        if isinstance(r.get("kelly_stake"), (int, float)): stakes.append(float(r["kelly_stake"]))
        beat = r.get("clv_beat")
        if beat is True:  clv_beats += 1; clv_tracked += 1
        if beat is False: clv_losses += 1; clv_tracked += 1
    slate_summary = {
        "games_analyzed":     len(analyses),
        "games_with_odds":    matches,
        "games_missing_odds": failures,
        "decisions":          decisions,
        "best_bets_count":    len(best_bets),
        "winners_count":      len(winners_ranked),
        "upset_count":        len(upset_candidates),
        "trap_count":         len(traps_only),
        "total_kelly_stake":  round(sum(stakes), 4) if stakes else 0.0,
        "avg_edge":           round(sum(edges)/len(edges), 4) if edges else None,
        "max_edge":           round(max(edges), 4) if edges else None,
        "clv": {
            "tracked":    clv_tracked,
            "beats":      clv_beats,
            "losses":     clv_losses,
            "beat_rate":  round(clv_beats/clv_tracked, 4) if clv_tracked else None,
        },
    }

    # Clean (ChatGPT-friendly) projections.
    clean_winners = [_llp_clean_item(r) for r in winners_ranked]
    clean_upsets  = [_llp_clean_item(r) for r in upset_candidates]
    clean_bets    = [_llp_clean_item(r) for r in best_bets]
    clean_pass    = [_llp_clean_item(r) for r in pass_traps]

    def _edges_for(market_key):
        items = [r for r in analyses
                 if r.get("market") == market_key
                 and isinstance(r.get("edge"), (int, float))]
        items.sort(key=lambda r: r.get("edge") or 0, reverse=True)
        return [_llp_clean_item(r) for r in items]

    # ── Canonical dashboard buckets (aliases alongside legacy arrays). ──
    #   market_verified : full sportsbook line + price + no-vig + model + edge + Kelly
    #   model_qualified : passes model checks but lacks full market verification
    #   approved        : market_verified BET/SMALL BET + strongest model_qualified
    #   watchlist       : WATCH or incomplete confirmation / pending status
    #   conditional     : strong candidates pending starter / lineup / market freshness
    #   rejected        : PASS / TRAP / negative edge / no market support
    #   no_play         : true when no approved or qualified plays survive
    def _is_market_verified(r):
        return (r.get("book") is not None
                and r.get("current_line") is not None
                and isinstance(r.get("no_vig_implied_probability"), (int, float))
                and isinstance(r.get("model_win_probability"), (int, float))
                and isinstance(r.get("edge"), (int, float))
                and isinstance(r.get("kelly_stake"), (int, float)))

    market_verified = [r for r in analyses if _is_market_verified(r)]
    model_qualified = [r for r in analyses
                       if isinstance(r.get("model_win_probability"), (int, float))
                       and not _is_market_verified(r)
                       and r.get("final_decision") != "TRAP"]
    # v14.9+: `approved` requires BOTH Discovery and Validation clean.
    # A record reaches `approved` only if its canonical badge is ANCHOR or BET.
    approved        = ([r for r in market_verified
                        if r.get("final_decision") in ("BET", "SMALL BET")
                        and r.get("llp_badge") in ("ANCHOR", "BET")]
                       + [r for r in model_qualified
                          if r.get("confidence_tier") in ("STRONG", "MEDIUM")
                          and r.get("llp_badge") in ("ANCHOR", "BET")])
    approved_ids    = {id(r) for r in approved}
    # Mutually-exclusive states relative to approved:
    #   conditional  → not approved AND awaiting confirmation
    #   watchlist    → not approved AND signal-only (WATCH / stale / lost CLV)
    #   rejected     → hard PASS/TRAP / negative edge / no market
    conditional     = [r for r in analyses
                       if id(r) not in approved_ids
                       and r.get("final_decision") in ("WATCH", "SMALL BET")
                       and (r.get("starter_status") == "unverified"
                            or r.get("lineup_status") == "unverified"
                            or (r.get("discovery") or {}).get("market_cause") == "unverified")]
    conditional_ids = {id(r) for r in conditional}
    watchlist       = [r for r in analyses
                       if id(r) not in approved_ids and id(r) not in conditional_ids
                       and (r.get("final_decision") == "WATCH"
                            or (r.get("discovery") or {}).get("stale_line") is True
                            or r.get("clv_beat") is False)]
    watchlist_ids   = {id(r) for r in watchlist}
    rejected        = [r for r in analyses
                       if id(r) not in approved_ids
                       and id(r) not in conditional_ids
                       and id(r) not in watchlist_ids
                       and (r.get("final_decision") in ("PASS", "TRAP")
                            or (isinstance(r.get("edge"), (int, float)) and r["edge"] < 0)
                            or r.get("current_line") is None)]
    # no_play: depends on actionable buckets only (not raw market_verified, which
    # can be non-empty while every record is PASS/TRAP/negative-edge).
    no_play         = (len(approved) == 0 and len(conditional) == 0)

    return {
        "team_analysis":     analyses,
        "winners_ranked":    clean_winners,
        "upset_candidates":  clean_upsets,
        "best_bets":         clean_bets,
        "pass_traps":        clean_pass,
        "totals_edges":      _edges_for("totals"),
        "spread_edges":      _edges_for("spreads"),
        "moneyline_edges":   _edges_for("h2h"),
        # Canonical dashboard aliases (do not remove legacy arrays above).
        "approved":          [_llp_clean_item(r) for r in approved],
        "market_verified":   [_llp_clean_item(r) for r in market_verified],
        "model_qualified":   [_llp_clean_item(r) for r in model_qualified],
        "conditional":       [_llp_clean_item(r) for r in conditional],
        "watchlist":         [_llp_clean_item(r) for r in watchlist],
        "rejected":          [_llp_clean_item(r) for r in rejected],
        "no_play":           no_play,
        "slate_summary":     slate_summary,
        "source_access_status": source_status,
    }


# ── LLP Claude Audit: Red Team Validator for the badge ladder ──────────
#
# Claude audits whether LLP's ANCHOR / BET / QUALIFIED / LEAN / WAIT /
# CANDIDATE / PASS labels are justified. It can ONLY challenge / downgrade /
# confirm; it can never create a new BET or ANCHOR. The deterministic
# arbiter `_llp_apply_claude_arbiter` accepts a Claude flag only when the
# flag is corroborated by the record itself (real missing checkpoint,
# stale assumption, market contradiction, CLV issue, status issue, or
# explicit badge-rule violation). Unsupported narrative caution is rejected.

_LLP_CLAUDE_AUDIT_SYSTEM = """You are Claude acting as the Red Team Validator for the LLP Team-Betting Model.

You are NOT the picker.
Do not create new ANCHOR, BET, QUALIFIED, or LEAN plays.
Do not upgrade any badge.
You may only challenge, downgrade, or confirm an LLP badge already assigned.

LLP badge ladder (assigned by the engine before you see the slate):
- ANCHOR    : edge >= 3.5%, discovery+validation clean, independent model adjustments, confirmed starter+lineup, no failure paths, fragility < 0.5
- BET       : discovery+validation clean, decision in {BET, SMALL BET}, sub-ANCHOR edge
- QUALIFIED : discovery+validation clean but edge below the bet tier
- LEAN      : positive edge with starter/lineup unverified
- WAIT      : edge present but market-side blocker (stale line, market_cause unverified/stale, bad market_timing) OR decision == WATCH
- CANDIDATE : discovery signal present, validation incomplete (no actionable edge)
- PASS      : hard fail (TRAP, no current_line, negative CLV, negative edge, payout_friction > 0.025)

For each item with badge in {ANCHOR, BET, QUALIFIED, LEAN}, audit for these specific flag types:
- overpromoted_badge              : badge is higher than the gates support
- stale_line_overconfidence       : market is stale/frozen but record still treated as actionable
- clv_without_validation          : clv_beat is False but record is still BET/ANCHOR
- starter_lineup_unverified       : execution-side status not confirmed but record is BET/ANCHOR
- spread_fragility_overlooked     : ANCHOR with spread_fragility >= 0.5
- possession_conversion_mismatch  : possession/conversion model inconsistent with market cause
- market_cause_unverified         : market_cause is None/"unverified" but record is BET/ANCHOR
- favorite_trap_risk              : favorite_trap_flag is True but record is BET/ANCHOR
- upset_price_temptation          : high upset_score with thin edge being promoted
- rest_context_underweighted      : short rest / back-to-back not reflected in model_adjustments
- injury_market_already_adjusted  : line has already moved on injury news (stale signal)

Return ONLY a single JSON object with these exact keys (arrays may be empty):
{
  "flags": [
    {
      "target":     "<sport>|<market>|<side>",
      "flag_type":  "<one of the 11 types above>",
      "current_badge": "<ANCHOR|BET|QUALIFIED|LEAN|WAIT|CANDIDATE|PASS>",
      "severity":   "<low|medium|high>",
      "reason":     "<one short factual sentence citing the field that contradicts the badge>"
    }
  ],
  "confirmations": [
    { "target": "<sport>|<market>|<side>", "current_badge": "<...>", "reason": "<short factual sentence>" }
  ]
}

Rules:
- Cite a concrete field from the record (e.g. starter_status, clv_beat, discovery.stale_line). Do not speculate.
- Do not flag PASS, WAIT, or CANDIDATE records — they are already non-actionable.
- Do not invent new plays. Do not propose upgrades.
- Respond with ONLY the JSON object. No prose. No markdown fences."""


# Flag-type → (validator, downgrade_target). The validator runs against
# the raw record dict and must return True for the flag to be accepted.
# The downgrade_target is the maximum badge the record may keep after the
# flag is accepted. We always take min(current_badge, downgrade_target).

_LLP_BADGE_RANK = {
    "ANCHOR": 6, "BET": 5, "QUALIFIED": 4, "LEAN": 3,
    "WAIT": 2, "CANDIDATE": 1, "PASS": 0,
}


def _llp_badge_min(a, b):
    """Return the lower-ranked badge per `_LLP_BADGE_RANK`.

    Used by both the Claude arbiter (no-upgrade enforcement) and the
    LLP team-betting spec hard caps (badge ceiling). Unknown badges
    rank as 0 (PASS) so we fail safe.
    """
    return a if _LLP_BADGE_RANK.get(a, 0) <= _LLP_BADGE_RANK.get(b, 0) else b


def _llp_apply_spec_badge_ceiling(badge, rec):
    """Cap `badge` per the LLP team-betting completeness/CLV/Kelly spec.

    Hard rules (source: LLP team-betting patch spec):
      - missing `opening_line`               → max WAIT  (no movement reference)
      - missing CLV reference (clv_beat None) → max WAIT  (no CLV anchor)
      - kelly_stake is None or <= 0          → max CANDIDATE  (no positive Kelly)
      - short-favorite trap: no_vig IP >= 0.55 AND edge < 0.04 → max LEAN

    A non-dict record (defensive) is returned unchanged. PASS is never
    raised; this function only ever lowers the badge.
    """
    if not isinstance(rec, dict):
        return badge
    if rec.get("opening_line") is None:
        badge = _llp_badge_min(badge, "WAIT")
    if rec.get("clv_beat") is None:
        badge = _llp_badge_min(badge, "WAIT")
    ks = rec.get("kelly_stake")
    if ks is None or (isinstance(ks, (int, float)) and ks <= 0):
        badge = _llp_badge_min(badge, "CANDIDATE")
    novig = rec.get("no_vig_implied_probability")
    edge  = rec.get("edge")
    if (isinstance(novig, (int, float)) and novig >= 0.55
        and isinstance(edge,  (int, float)) and edge  <  0.04):
        badge = _llp_badge_min(badge, "LEAN")
    # LLP Pro Data Ingestion: stale-market / early-promoted / fake-edge
    # tags cap the badge at CANDIDATE until Layer 4 validation clears.
    # Tag set is intentionally checked via membership in
    # `_LLP_PRO_CANDIDATE_CEILING_TAGS` so adding a new candidate-cap tag
    # is a one-line change to the set.
    fp = rec.get("failure_paths") or []
    if any(t in _LLP_PRO_CANDIDATE_CEILING_TAGS for t in fp):
        badge = _llp_badge_min(badge, "CANDIDATE")
    # WOW v16 Game Winner Payout Discipline: sub-1.35x moneyline is a hard
    # REJECT — floor the badge at PASS (final_decision is also forced to PASS).
    if LLP_PRO_FAILURE_GW_BELOW_MIN_PAYOUT in fp:
        badge = _llp_badge_min(badge, "PASS")
    return badge

def _llp_v_overpromoted(rec):
    return (rec.get("llp_badge") in ("ANCHOR", "BET")
            and not (_llp_discovery_clean(rec) and _llp_validation_clean(rec)))

def _llp_v_stale_overconf(rec):
    d = rec.get("discovery") or {}
    return (rec.get("llp_badge") in ("ANCHOR", "BET")
            and (d.get("stale_line") is True or d.get("market_cause") == "stale"))

def _llp_v_clv_no_validation(rec):
    return rec.get("llp_badge") in ("ANCHOR", "BET") and rec.get("clv_beat") is False

def _llp_v_starter_unverified(rec):
    return (rec.get("llp_badge") in ("ANCHOR", "BET")
            and (rec.get("starter_status") == "unverified"
                 or rec.get("lineup_status") == "unverified"))

def _llp_v_fragility_overlooked(rec):
    d = rec.get("discovery") or {}
    frag = d.get("spread_fragility")
    return (rec.get("llp_badge") == "ANCHOR"
            and isinstance(frag, (int, float)) and frag >= 0.5)

def _llp_v_possession_mismatch(rec):
    d = rec.get("discovery") or {}
    pc = d.get("possession_conversion")
    return (rec.get("llp_badge") in ("ANCHOR", "BET")
            and isinstance(pc, (int, float)) and pc < 0.5)

def _llp_v_market_cause_unverified(rec):
    d = rec.get("discovery") or {}
    return (rec.get("llp_badge") in ("ANCHOR", "BET")
            and d.get("market_cause") in (None, "unverified"))

def _llp_v_favorite_trap(rec):
    return (rec.get("llp_badge") in ("ANCHOR", "BET")
            and rec.get("favorite_trap_flag") is True)

def _llp_v_upset_temptation(rec):
    edge = rec.get("edge") or 0
    return (rec.get("llp_badge") in ("ANCHOR", "BET")
            and (rec.get("upset_score") or 0) >= 55
            and isinstance(edge, (int, float)) and edge < 0.05)

def _llp_v_rest_underweighted(rec):
    # rest_context is canonically a dict ({b2b, days_rest, short_rest, note});
    # tolerate string form too for forward compat / external callers.
    rc = rec.get("rest_context")
    if isinstance(rc, dict):
        short_rest = bool(rc.get("short_rest")) or bool(rc.get("b2b"))
        dr = rc.get("days_rest")
        if isinstance(dr, (int, float)) and dr <= 1:
            short_rest = True
        if not short_rest:
            note = (rc.get("note") or "").lower()
            short_rest = any(t in note for t in ("short", "b2b", "back-to-back"))
    elif isinstance(rc, str):
        s = rc.lower()
        short_rest = any(t in s for t in ("short", "b2b", "back-to-back", "1 day", "0 day"))
    else:
        short_rest = False
    adj = rec.get("model_adjustments") or {}
    adj_keys = " ".join(adj.keys()).lower() if isinstance(adj, dict) else ""
    return (rec.get("llp_badge") in ("ANCHOR", "BET")
            and short_rest and "rest" not in adj_keys)

def _llp_v_injury_already_adjusted(rec):
    d = rec.get("discovery") or {}
    return (rec.get("llp_badge") in ("ANCHOR", "BET")
            and (d.get("stale_line") is True
                 or d.get("line_freeze") is True
                 or d.get("derivative_desync") is True))

_LLP_ARBITER_RULES = {
    "overpromoted_badge":             (_llp_v_overpromoted,              "QUALIFIED"),
    "stale_line_overconfidence":      (_llp_v_stale_overconf,            "WAIT"),
    "clv_without_validation":         (_llp_v_clv_no_validation,         "CANDIDATE"),
    "starter_lineup_unverified":      (_llp_v_starter_unverified,        "LEAN"),
    "spread_fragility_overlooked":    (_llp_v_fragility_overlooked,      "BET"),
    "possession_conversion_mismatch": (_llp_v_possession_mismatch,       "QUALIFIED"),
    "market_cause_unverified":        (_llp_v_market_cause_unverified,   "WAIT"),
    "favorite_trap_risk":             (_llp_v_favorite_trap,             "CANDIDATE"),
    "upset_price_temptation":         (_llp_v_upset_temptation,          "LEAN"),
    "rest_context_underweighted":     (_llp_v_rest_underweighted,        "LEAN"),
    "injury_market_already_adjusted": (_llp_v_injury_already_adjusted,   "WAIT"),
}


def _llp_record_key(rec):
    """Stable target key for matching Claude flags back to records."""
    sport  = (rec.get("sport") or "").lower().strip()
    market = (rec.get("market") or "").lower().strip()
    side   = (rec.get("side") or "").lower().strip()
    return f"{sport}|{market}|{side}"


def _llp_build_audit_payload(agg):
    """Build the minimal context Claude needs to audit the slate."""
    audit_items = []
    for r in agg.get("team_analysis", []):
        if not isinstance(r, dict):
            continue  # malformed entry — skip rather than crash
        if (r.get("llp_badge") or "PASS") in ("PASS", "CANDIDATE"):
            continue  # already non-actionable; nothing to audit
        # Coerce nested maps defensively so a malformed `discovery` (e.g. int)
        # cannot crash payload construction.
        d = r.get("discovery")
        if not isinstance(d, dict):
            d = {}
        audit_items.append({
            "target":         _llp_record_key(r),
            "sport":          r.get("sport"),
            "market":         r.get("market"),
            "side":           r.get("side"),
            "away_team":      r.get("away_team"),
            "home_team":      r.get("home_team"),
            "current_line":   r.get("current_line"),
            "book":           r.get("book"),
            "edge":           r.get("edge"),
            "model_win_probability":    r.get("model_win_probability"),
            "no_vig_implied_probability": r.get("no_vig_implied_probability"),
            "final_decision":           r.get("final_decision"),
            "confidence_tier":          r.get("confidence_tier"),
            "llp_badge":                r.get("llp_badge"),
            "discovery_clean":          r.get("discovery_clean"),
            "validation_clean":         r.get("validation_clean"),
            "clv_beat":                 r.get("clv_beat"),
            "starter_status":           r.get("starter_status"),
            "lineup_status":            r.get("lineup_status"),
            "favorite_trap_flag":       r.get("favorite_trap_flag"),
            "failure_paths":            r.get("failure_paths") or [],
            "rest_context":             r.get("rest_context"),
            "upset_score":              r.get("upset_score"),
            "model_adjustments":        r.get("model_adjustments"),
            "discovery": {
                "market_cause":          d.get("market_cause"),
                "stale_line":            d.get("stale_line"),
                "line_freeze":           d.get("line_freeze"),
                "derivative_desync":     d.get("derivative_desync"),
                "market_timing":         d.get("market_timing"),
                "spread_fragility":      d.get("spread_fragility"),
                "possession_conversion": d.get("possession_conversion"),
                "payout_friction":       d.get("payout_friction"),
            },
        })
    return {
        "slate_summary":     agg.get("slate_summary"),
        "winners_ranked":    agg.get("winners_ranked"),
        "upset_candidates":  agg.get("upset_candidates"),
        "best_bets":         agg.get("best_bets"),
        "pass_traps":        agg.get("pass_traps"),
        "moneyline_edges":   agg.get("moneyline_edges"),
        "spread_edges":      agg.get("spread_edges"),
        "totals_edges":      agg.get("totals_edges"),
        "audit_items":       audit_items,
    }


def _llp_run_claude_audit_team(agg):
    """Run the LLP Red Team Validator. Returns (audit_json, status, error, latency_ms).

    status ∈ {"ok", "failed", "skipped"}. On failure, audit_json is None and
    error carries the reason; the caller is expected to mark items audited=False.
    """
    if not [r for r in agg.get("team_analysis", [])
            if isinstance(r, dict)
            and (r.get("llp_badge") or "PASS") not in ("PASS", "CANDIDATE")]:
        return None, "skipped", "no actionable badges to audit", 0
    payload = _llp_build_audit_payload(agg)
    try:
        text, _model, latency_ms = _cm_claude_call(
            _LLP_CLAUDE_AUDIT_SYSTEM,
            json.dumps(payload, default=str),
            max_tokens=4096,
        )
    except Exception as e:
        return None, "failed", str(e), 0
    try:
        audit_json = _cm_extract_json(text)
    except Exception as e:
        return None, "failed", f"claude returned non-JSON: {e}", latency_ms
    # Defensive shape coercion.
    if not isinstance(audit_json, dict):
        return None, "failed", "claude audit was not a JSON object", latency_ms
    audit_json.setdefault("flags", [])
    audit_json.setdefault("confirmations", [])
    if not isinstance(audit_json["flags"], list):       audit_json["flags"] = []
    if not isinstance(audit_json["confirmations"], list): audit_json["confirmations"] = []
    return audit_json, "ok", None, latency_ms


def _llp_apply_claude_arbiter(agg, audit_json):
    """Apply the deterministic LLP arbiter to Claude's audit output.

    A flag is ACCEPTED only if its validator (`_LLP_ARBITER_RULES`) returns
    True for the matching record. Accepted flags downgrade the record's
    `llp_badge` to min(current, downgrade_target). Unmatched targets and
    unsupported flag types are REJECTED. Returns (accepted, rejected).
    Claude can never upgrade — `_llp_badge_min` enforces this by rank.
    """
    # Defensive: only key dict-shaped records so a malformed entry cannot
    # crash the request before we even start auditing.
    by_key = {_llp_record_key(r): r
              for r in agg.get("team_analysis", []) if isinstance(r, dict)}
    accepted, rejected = [], []

    for flag in (audit_json.get("flags") or []):
        if not isinstance(flag, dict):
            rejected.append({"flag": flag, "reason": "flag was not a JSON object"})
            continue
        ftype  = (flag.get("flag_type") or "").strip()
        target = (flag.get("target") or "").strip().lower()
        rule   = _LLP_ARBITER_RULES.get(ftype)
        if rule is None:
            rejected.append({"flag": flag, "reason": f"unsupported flag_type: {ftype}"})
            continue
        rec = by_key.get(target)
        if rec is None:
            rejected.append({"flag": flag, "reason": f"target not found in slate: {target}"})
            continue
        validator, downgrade_to = rule
        try:
            corroborated = validator(rec)
        except Exception as e:
            # Never let a malformed record shape break the request — reject
            # the flag with a diagnostic reason and keep auditing the slate.
            rejected.append({"flag": flag,
                             "reason": f"validator error: {e.__class__.__name__}: {e}"})
            continue
        if not corroborated:
            rejected.append({"flag": flag,
                             "reason": "record state does not corroborate the flag"})
            continue
        # Accept: downgrade badge (never upgrade). Use the shared
        # `_llp_badge_min` so the Claude arbiter and the LLP spec ceiling
        # share a single source of truth for no-upgrade enforcement.
        before = rec.get("llp_badge") or "PASS"
        rec["llp_badge"] = _llp_badge_min(before, downgrade_to)
        rec.setdefault("claude_arbiter_flags", []).append({
            "flag_type":    ftype,
            "severity":     flag.get("severity"),
            "reason":       flag.get("reason"),
            "badge_before": before,
            "badge_after":  rec["llp_badge"],
        })
        accepted.append({
            "target":       target,
            "flag_type":    ftype,
            "severity":     flag.get("severity"),
            "reason":       flag.get("reason"),
            "badge_before": before,
            "badge_after":  rec["llp_badge"],
        })

    # On success, every record in the slate is considered audited — the
    # auditor saw the full slate context even when a given record had a
    # non-actionable badge (PASS/CANDIDATE) and therefore needed no flag.
    # Defensive: skip non-dict entries instead of crashing the request.
    for r in agg.get("team_analysis", []):
        if not isinstance(r, dict):
            continue
        r["audited"] = True
        if (r.get("llp_badge") or "PASS") in ("PASS", "CANDIDATE"):
            r["audit_note"] = "non-actionable badge; reviewed, no flag needed"
        else:
            r["audit_note"] = "claude red-team audit applied"

    return accepted, rejected


def _llp_mark_unaudited(agg, reason):
    """Failure path: every record gets audited=False with a consistent note."""
    for r in agg.get("team_analysis", []):
        if not isinstance(r, dict):
            continue
        r["audited"] = False
        r["audit_note"] = reason


# ── LLP Postmortem table: slate-result reconciliation + learning layer ──
_LLP_POSTMORTEM_SCHEMA_LOCK = threading.Lock()
_LLP_POSTMORTEM_SCHEMA_READY = False

def _ensure_llp_postmortem_schema(conn):
    global _LLP_POSTMORTEM_SCHEMA_READY
    if _LLP_POSTMORTEM_SCHEMA_READY:
        return
    with _LLP_POSTMORTEM_SCHEMA_LOCK:
        if _LLP_POSTMORTEM_SCHEMA_READY:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS llp_postmortem (
                    id                       BIGSERIAL PRIMARY KEY,
                    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    slate_date               DATE,
                    board_id                 TEXT,
                    sport                    TEXT,
                    game_id                  TEXT,
                    market_type              TEXT,
                    team                     TEXT,
                    opponent                 TEXT,
                    side                     TEXT,
                    line_at_discovery        DOUBLE PRECISION,
                    line_at_approval         DOUBLE PRECISION,
                    closing_line             DOUBLE PRECISION,
                    odds_at_discovery        DOUBLE PRECISION,
                    odds_at_approval         DOUBLE PRECISION,
                    closing_odds             DOUBLE PRECISION,
                    model_true_prob          DOUBLE PRECISION,
                    no_vig_market_prob       DOUBLE PRECISION,
                    pure_edge_pct            DOUBLE PRECISION,
                    market_cause             TEXT,
                    market_efficiency_rank   DOUBLE PRECISION,
                    spread_fragility         DOUBLE PRECISION,
                    possession_conversion    DOUBLE PRECISION,
                    payout_friction          DOUBLE PRECISION,
                    llp_decision             TEXT,
                    llp_badge                TEXT,
                    recommended_units        DOUBLE PRECISION,
                    actual_result            TEXT,
                    bet_result               TEXT,
                    clv_delta                DOUBLE PRECISION,
                    clv_grade                TEXT,
                    process_grade            TEXT,
                    failure_tags             TEXT[],
                    postmortem_notes         TEXT,
                    patch_needed             BOOLEAN DEFAULT FALSE,
                    model_version            TEXT,
                    stale_line                    BOOLEAN,
                    line_freeze                   BOOLEAN,
                    derivative_desync             BOOLEAN,
                    market_timing                 TEXT,
                    -- LLP team-betting spec settlement columns.
                    closing_implied_probability   DOUBLE PRECISION,
                    bet_implied_probability       DOUBLE PRECISION,
                    actual_clv_beat               BOOLEAN
                );
                -- Forward-compat: add discovery + spec settlement columns if
                -- the table predates them. Safe to re-run.
                ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS stale_line                  BOOLEAN;
                ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS line_freeze                 BOOLEAN;
                ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS derivative_desync           BOOLEAN;
                ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS market_timing               TEXT;
                ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS closing_implied_probability DOUBLE PRECISION;
                ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS bet_implied_probability     DOUBLE PRECISION;
                ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS actual_clv_beat             BOOLEAN;
                CREATE INDEX IF NOT EXISTS llp_postmortem_slate_idx
                    ON llp_postmortem (slate_date);
                CREATE INDEX IF NOT EXISTS llp_postmortem_sport_idx
                    ON llp_postmortem (sport);
                CREATE INDEX IF NOT EXISTS llp_postmortem_decision_idx
                    ON llp_postmortem (llp_decision);
                CREATE INDEX IF NOT EXISTS llp_postmortem_result_idx
                    ON llp_postmortem (bet_result);
                CREATE INDEX IF NOT EXISTS llp_postmortem_process_idx
                    ON llp_postmortem (process_grade);
                CREATE INDEX IF NOT EXISTS llp_postmortem_failure_tags_gin
                    ON llp_postmortem USING GIN (failure_tags);
            """)
            conn.commit()
        _LLP_POSTMORTEM_SCHEMA_READY = True


# ════════════════════════════════════════════════════════════════════════
# LLP PRO DATA INGESTION PATCH
# ════════════════════════════════════════════════════════════════════════
# Implements the LLP team-betting Pro Data Ingestion spec using the free
# provider mix:
#   - Odds + market movement: The Odds API (wired via `_llp_fetch_odds`)
#   - Starters + lineups (MLB): statsapi.mlb.com (official, free JSON)
#   - Injuries (NBA/WNBA/NFL/NHL/MLB): site.api.espn.com (public JSON)
#
# Storage (7 spec tables): odds_snapshots, team_context, lineup_status,
# injury_status, model_outputs, clv_log, bet_decisions.
#
# Hard rule: stale/soft lines, "fake-market-edge" plays, and prematurely
# promoted candidates are capped at CANDIDATE by the spec ceiling until
# Layer 4 validation clears (confirmed starter+lineup AND real model
# adjustments AND positive Kelly).

# Failure tags — HARD (count toward `_llp_validation_clean` cardinality).
LLP_PRO_FAILURE_MISSING_ODDS_FEED           = "missing-odds-feed"
LLP_PRO_FAILURE_MISSING_LINEUP_STATUS       = "missing-lineup-status"
LLP_PRO_FAILURE_MISSING_STARTER_CONFIRM     = "missing-starter-confirmation"
LLP_PRO_FAILURE_STALE_MARKET_NOT_ACTIONABLE = "stale-market-not-actionable"
LLP_PRO_FAILURE_CLV_WITHOUT_VALIDATION      = "clv-without-validation"
LLP_PRO_FAILURE_FAKE_MARKET_EDGE            = "fake-market-edge"
LLP_PRO_FAILURE_CANDIDATE_PROMOTED_EARLY    = "candidate-promoted-too-early"

# WOW v16 Clean Core — Game Winner Payout Discipline (h2h moneyline lane).
LLP_PRO_FAILURE_GW_BELOW_MIN_PAYOUT         = "game-winner-below-min-payout"
LLP_PRO_FAILURE_GW_SHORT_PRICE_UNVERIFIED   = "game-winner-short-price-unverified"

_LLP_PRO_FAILURE_TAGS = frozenset({
    LLP_PRO_FAILURE_MISSING_ODDS_FEED,
    LLP_PRO_FAILURE_MISSING_LINEUP_STATUS,
    LLP_PRO_FAILURE_MISSING_STARTER_CONFIRM,
    LLP_PRO_FAILURE_STALE_MARKET_NOT_ACTIONABLE,
    LLP_PRO_FAILURE_CLV_WITHOUT_VALIDATION,
    LLP_PRO_FAILURE_FAKE_MARKET_EDGE,
    LLP_PRO_FAILURE_CANDIDATE_PROMOTED_EARLY,
    LLP_PRO_FAILURE_GW_BELOW_MIN_PAYOUT,
    LLP_PRO_FAILURE_GW_SHORT_PRICE_UNVERIFIED,
})

# Tags that the spec ceiling caps at CANDIDATE regardless of model edge.
_LLP_PRO_CANDIDATE_CEILING_TAGS = frozenset({
    LLP_PRO_FAILURE_STALE_MARKET_NOT_ACTIONABLE,
    LLP_PRO_FAILURE_CANDIDATE_PROMOTED_EARLY,
    LLP_PRO_FAILURE_FAKE_MARKET_EDGE,
    LLP_PRO_FAILURE_GW_SHORT_PRICE_UNVERIFIED,
})

# Game Winner Payout Discipline thresholds (decimal odds on the chosen side).
_LLP_GW_MIN_PAYOUT_DECIMAL   = 1.35   # below → auto REJECT/PASS
_LLP_GW_SHORT_PRICE_DECIMAL  = 1.50   # below → requires full verification
_LLP_GW_SHORT_PRICE_MIN_EDGE = 0.03   # no-vig edge ≥ +3% to clear short price
_LLP_GW_INVERTED_DECIMAL     = 2.0    # < 2.0 → stake risked exceeds potential win


def _llp_game_winner_discipline(rec):
    """WOW v16 Clean Core — Game Winner payout discipline (h2h moneyline).

    Returns (tags, inverted_stake_sizing, force_reject).

    Decimal odds on the chosen side drive the lane:
      - price < 1.35x          → hard REJECT/PASS (below-min-payout tag).
      - 1.35x ≤ price < 1.50x  → approvable ONLY if ALL hold: no-vig edge
            ≥ +3%, confirmed starter AND lineup, Layer 4 model synthesis
            (`model_adjustments` non-empty), and positive Kelly. Otherwise
            cap at CANDIDATE (short-price-unverified tag).
      - inverted_stake_sizing  → True when price < 2.0x (the stake risked
            exceeds the potential win); the orchestrator consumes this flag
            for stake sizing.

    Dollar bankroll, the $2 floor when bankroll < $25, and post-game
    "Q3 Lucky/False-Signal" labeling are orchestrator concerns and are
    intentionally NOT handled in this engine (Kelly here is a fraction).
    """
    if not isinstance(rec, dict):
        return ([], False, False)
    if (rec.get("market") or "").lower() != "h2h":
        return ([], False, False)
    dec = rec.get("decimal_odds")
    if not isinstance(dec, (int, float)) or dec <= 1.0:
        return ([], False, False)
    inverted = dec < _LLP_GW_INVERTED_DECIMAL
    if dec < _LLP_GW_MIN_PAYOUT_DECIMAL:
        return ([LLP_PRO_FAILURE_GW_BELOW_MIN_PAYOUT], inverted, True)
    if dec < _LLP_GW_SHORT_PRICE_DECIMAL:
        edge = rec.get("edge")
        ks   = rec.get("kelly_stake")
        verified = (
            isinstance(edge, (int, float)) and edge >= _LLP_GW_SHORT_PRICE_MIN_EDGE
            and rec.get("starter_status") == "confirmed"
            and rec.get("lineup_status")  == "confirmed"
            and bool(rec.get("model_adjustments") or {})
            and isinstance(ks, (int, float)) and ks > 0
        )
        if not verified:
            return ([LLP_PRO_FAILURE_GW_SHORT_PRICE_UNVERIFIED], inverted, False)
    return ([], inverted, False)


# ── DB schema: 7 new tables for the LLP Pro data stack ─────────────────
_LLP_PRO_SCHEMA_LOCK  = threading.Lock()
_LLP_PRO_SCHEMA_READY = False


def _ensure_llp_pro_schema(conn):
    """Create the 7 LLP Pro Data Ingestion tables (idempotent, best-effort)."""
    global _LLP_PRO_SCHEMA_READY
    if _LLP_PRO_SCHEMA_READY:
        return
    with _LLP_PRO_SCHEMA_LOCK:
        if _LLP_PRO_SCHEMA_READY:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS odds_snapshots (
                    id            BIGSERIAL PRIMARY KEY,
                    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sport         TEXT NOT NULL,
                    league        TEXT,
                    event_id      TEXT,
                    game_key      TEXT NOT NULL,
                    away_team     TEXT,
                    home_team     TEXT,
                    player        TEXT,
                    market        TEXT NOT NULL,
                    book          TEXT,
                    side          TEXT,
                    american_odds DOUBLE PRECISION,
                    point         DOUBLE PRECISION,
                    snapshot_kind TEXT NOT NULL DEFAULT 'current',
                    source        TEXT NOT NULL DEFAULT 'the-odds-api'
                );
                -- Backfill columns on tables created before the full field contract.
                ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS league   TEXT;
                ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS event_id TEXT;
                ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS player   TEXT;
                CREATE INDEX IF NOT EXISTS odds_snapshots_game_idx
                    ON odds_snapshots (sport, game_key, market, fetched_at DESC);

                CREATE TABLE IF NOT EXISTS team_context (
                    id            BIGSERIAL PRIMARY KEY,
                    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sport         TEXT NOT NULL,
                    team          TEXT NOT NULL,
                    context_kind  TEXT NOT NULL,
                    payload       JSONB,
                    source        TEXT
                );
                CREATE INDEX IF NOT EXISTS team_context_team_idx
                    ON team_context (sport, team, context_kind, fetched_at DESC);

                CREATE TABLE IF NOT EXISTS lineup_status (
                    id                    BIGSERIAL PRIMARY KEY,
                    fetched_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sport                 TEXT NOT NULL,
                    game_key              TEXT NOT NULL,
                    away_team             TEXT,
                    home_team             TEXT,
                    game_date             DATE,
                    starter_status        TEXT NOT NULL DEFAULT 'unverified',
                    lineup_status         TEXT NOT NULL DEFAULT 'unverified',
                    probable_pitchers     JSONB,
                    confirmed_lineup_size INTEGER,
                    source                TEXT
                );
                CREATE INDEX IF NOT EXISTS lineup_status_game_idx
                    ON lineup_status (sport, game_key, fetched_at DESC);

                CREATE TABLE IF NOT EXISTS injury_status (
                    id         BIGSERIAL PRIMARY KEY,
                    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sport      TEXT NOT NULL,
                    team       TEXT NOT NULL,
                    player     TEXT,
                    status     TEXT,
                    detail     TEXT,
                    source     TEXT
                );
                CREATE INDEX IF NOT EXISTS injury_status_team_idx
                    ON injury_status (sport, team, fetched_at DESC);

                CREATE TABLE IF NOT EXISTS model_outputs (
                    id                  BIGSERIAL PRIMARY KEY,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sport               TEXT NOT NULL,
                    game_key            TEXT NOT NULL,
                    market              TEXT NOT NULL,
                    side                TEXT,
                    no_vig_implied_prob DOUBLE PRECISION,
                    model_win_prob      DOUBLE PRECISION,
                    edge_pct            DOUBLE PRECISION,
                    kelly_pct           DOUBLE PRECISION,
                    confidence_tier     TEXT,
                    model_version       TEXT
                );
                CREATE INDEX IF NOT EXISTS model_outputs_game_idx
                    ON model_outputs (sport, game_key, market, created_at DESC);

                CREATE TABLE IF NOT EXISTS clv_log (
                    id           BIGSERIAL PRIMARY KEY,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sport        TEXT NOT NULL,
                    game_key     TEXT NOT NULL,
                    market       TEXT NOT NULL,
                    side         TEXT,
                    opening_line DOUBLE PRECISION,
                    bet_line     DOUBLE PRECISION,
                    closing_line DOUBLE PRECISION,
                    clv_delta    DOUBLE PRECISION,
                    clv_grade    TEXT,
                    clv_beat     BOOLEAN
                );
                CREATE INDEX IF NOT EXISTS clv_log_game_idx
                    ON clv_log (sport, game_key, market, created_at DESC);

                CREATE TABLE IF NOT EXISTS bet_decisions (
                    id                  BIGSERIAL PRIMARY KEY,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sport               TEXT NOT NULL,
                    game_key            TEXT NOT NULL,
                    away_team           TEXT,
                    home_team           TEXT,
                    market              TEXT NOT NULL,
                    side                TEXT,
                    book                TEXT,
                    current_line        DOUBLE PRECISION,
                    opening_line        DOUBLE PRECISION,
                    no_vig_implied_prob DOUBLE PRECISION,
                    model_win_prob      DOUBLE PRECISION,
                    edge_pct            DOUBLE PRECISION,
                    kelly_pct           DOUBLE PRECISION,
                    confidence_tier     TEXT,
                    final_decision      TEXT,
                    llp_badge           TEXT,
                    starter_status      TEXT,
                    lineup_status       TEXT,
                    market_timing       TEXT,
                    market_cause        TEXT,
                    clv_beat            BOOLEAN,
                    failure_tags        TEXT[]
                );
                CREATE INDEX IF NOT EXISTS bet_decisions_game_idx
                    ON bet_decisions (sport, game_key, market, created_at DESC);
                CREATE INDEX IF NOT EXISTS bet_decisions_badge_idx
                    ON bet_decisions (llp_badge);
            """)
            conn.commit()
        _LLP_PRO_SCHEMA_READY = True


# ── Ingestion: MLB Stats API (official, free) for starters + lineups ───
_LLP_MLB_SCHEDULE_CACHE = {}   # keyed by date_str → (fetched_at_ts, schedule_dict)
_LLP_MLB_CTX_CACHE      = {}   # keyed by (away|home|date) → (ts, resolved_dict)
_LLP_INJURY_CACHE       = {}
_LLP_PRO_CACHE_TTL_SEC  = 180  # 3 min — short enough for late-breaking news


def _llp_fetch_mlb_schedule(date_str):
    """Fetch MLB schedule (with probablePitcher+lineups hydrate) for a date.

    Date-keyed cache: a 12-game slate hits statsapi.mlb.com once, not 12×.
    Never raises — returns the raw API dict on success, `{}` otherwise.
    """
    import requests as _req
    now_ts = datetime.now().timestamp()
    hit = _LLP_MLB_SCHEDULE_CACHE.get(date_str)
    if hit and (now_ts - hit[0]) < _LLP_PRO_CACHE_TTL_SEC:
        return hit[1]
    try:
        r = _req.get("https://statsapi.mlb.com/api/v1/schedule",
                     params={"sportId": 1, "date": date_str,
                             "hydrate": "probablePitcher,lineups"},
                     timeout=10)
        if r.status_code != 200:
            _LLP_MLB_SCHEDULE_CACHE[date_str] = (now_ts, {})
            return {}
        data = r.json() or {}
        _LLP_MLB_SCHEDULE_CACHE[date_str] = (now_ts, data)
        return data
    except Exception:
        _LLP_MLB_SCHEDULE_CACHE[date_str] = (now_ts, {})
        return {}


def _llp_fetch_mlb_lineup_context(away, home, game_date=None):
    """Resolve MLB starter/lineup status for one game from the cached schedule.

    Returns dict with `starter_status`, `lineup_status`, `probable_pitchers`,
    `confirmed_lineup_size`, `source`, `fetched_at`. Never raises — on any
    failure returns the unverified shell so callers can emit the appropriate
    failure tags. The underlying schedule HTTP call is cached per date by
    `_llp_fetch_mlb_schedule` so a full slate triggers exactly one fetch.
    """
    base = {"starter_status": "unverified", "lineup_status": "unverified",
            "probable_pitchers": {}, "confirmed_lineup_size": 0,
            "source": "statsapi.mlb.com",
            "fetched_at": datetime.now(timezone.utc).isoformat()}
    if not away or not home:
        return base
    date_str = game_date if game_date else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache_key = f"{_llp_norm_team(away)}|{_llp_norm_team(home)}|{date_str}"
    now_ts = datetime.now().timestamp()
    hit = _LLP_MLB_CTX_CACHE.get(cache_key)
    if hit and (now_ts - hit[0]) < _LLP_PRO_CACHE_TTL_SEC:
        return hit[1]
    data = _llp_fetch_mlb_schedule(date_str)
    if not data:
        _LLP_MLB_CTX_CACHE[cache_key] = (now_ts, base)
        return base
    # Defensive: statsapi can return 200 with an unexpected shape (list/scalar
    # nesting, schema drift). Any traversal error must fall back to the
    # unverified shell, never bubble out of _llp_analyze_one (T003: no raise).
    try:
        an, hn = _llp_norm_team(away), _llp_norm_team(home)
        dates = data.get("dates") if isinstance(data, dict) else None
        for d in (dates or []):
            if not isinstance(d, dict):
                continue
            for g in (d.get("games") or []):
                if not isinstance(g, dict):
                    continue
                teams = g.get("teams") or {}
                if not isinstance(teams, dict):
                    teams = {}
                gh = _llp_norm_team(
                    ((teams.get("home") or {}).get("team") or {}).get("name") or "")
                ga = _llp_norm_team(
                    ((teams.get("away") or {}).get("team") or {}).get("name") or "")
                if not ((an in ga or ga in an) and (hn in gh or gh in hn)):
                    continue
                pp = {}
                for tk in ("away", "home"):
                    name = ((teams.get(tk) or {}).get("probablePitcher") or {}).get("fullName")
                    if name: pp[tk] = name
                lineups = g.get("lineups") or {}
                if not isinstance(lineups, dict):
                    lineups = {}
                away_bo = lineups.get("awayPlayers") or []
                home_bo = lineups.get("homePlayers") or []
                lineup_size = (min(len(away_bo), len(home_bo))
                               if (away_bo and home_bo) else 0)
                starter_ok = bool(pp.get("away") and pp.get("home"))
                lineup_ok  = lineup_size >= 9
                out = {
                    "starter_status": "confirmed" if starter_ok else "unverified",
                    "lineup_status":  "confirmed" if lineup_ok  else "unverified",
                    "probable_pitchers":     pp,
                    "confirmed_lineup_size": lineup_size,
                    "source":     "statsapi.mlb.com",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                _LLP_MLB_CTX_CACHE[cache_key] = (now_ts, out)
                try:
                    conn = get_db_conn()
                    try:
                        _ensure_llp_pro_schema(conn)
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO lineup_status
                                  (sport, game_key, away_team, home_team, game_date,
                                   starter_status, lineup_status, probable_pitchers,
                                   confirmed_lineup_size, source)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                            """, ("mlb", f"{an}@{hn}|{date_str}", away, home,
                                  date_str, out["starter_status"], out["lineup_status"],
                                  json.dumps(pp), lineup_size, out["source"]))
                        conn.commit()
                    finally:
                        conn.close()
                except Exception:
                    pass
                return out
    except Exception:
        _LLP_MLB_CTX_CACHE[cache_key] = (now_ts, base)
        return base
    _LLP_MLB_CTX_CACHE[cache_key] = (now_ts, base)
    return base


_LLP_ESPN_SPORT_PATH = {
    "mlb":  ("baseball",   "mlb"),
    "nba":  ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "nfl":  ("football",   "nfl"),
    "nhl":  ("hockey",     "nhl"),
}


def _llp_fetch_espn_injuries(sport, team=None):
    """Fetch league-wide or team injuries from ESPN's public JSON.

    Never raises. Returns list of `{team, player, status, detail}` dicts.
    Persists to `injury_status` best-effort.
    """
    import requests as _req
    s = (sport or "").lower()
    path = _LLP_ESPN_SPORT_PATH.get(s)
    if not path:
        return []
    cache_key = f"{s}|{_llp_norm_team(team or '')}"
    hit = _LLP_INJURY_CACHE.get(cache_key)
    now_ts = datetime.now().timestamp()
    if hit and (now_ts - hit[0]) < _LLP_PRO_CACHE_TTL_SEC:
        return hit[1]
    out = []
    try:
        url = (f"https://site.api.espn.com/apis/site/v2/sports/"
               f"{path[0]}/{path[1]}/injuries")
        r = _req.get(url, timeout=10)
        if r.status_code != 200:
            _LLP_INJURY_CACHE[cache_key] = (now_ts, [])
            return []
        data = r.json() or {}
        tnorm = _llp_norm_team(team or "")
        for tblock in (data.get("injuries") or []):
            team_name = tblock.get("displayName") or ""
            if team and tnorm and tnorm not in _llp_norm_team(team_name):
                continue
            for inj in (tblock.get("injuries") or []):
                athlete = inj.get("athlete") or {}
                out.append({
                    "team":   team_name,
                    "player": athlete.get("displayName") or athlete.get("shortName") or "",
                    "status": inj.get("status") or "",
                    "detail": ((inj.get("type") or {}).get("description")
                               or inj.get("longComment") or ""),
                })
    except Exception:
        out = []
    _LLP_INJURY_CACHE[cache_key] = (now_ts, out)
    if out:
        try:
            conn = get_db_conn()
            try:
                _ensure_llp_pro_schema(conn)
                with conn.cursor() as cur:
                    psycopg2.extras.execute_batch(cur, """
                        INSERT INTO injury_status
                          (sport, team, player, status, detail, source)
                        VALUES (%s,%s,%s,%s,%s,%s)
                    """, [(s, i["team"], i["player"], i["status"],
                           i["detail"], "site.api.espn.com") for i in out])
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
    return out


def _llp_persist_odds_snapshot(sport, away, home, market, sel):
    """Best-effort: write a row to odds_snapshots after a successful odds match."""
    if not isinstance(sel, dict):
        return
    try:
        conn = get_db_conn()
        try:
            _ensure_llp_pro_schema(conn)
            game_key = f"{_llp_norm_team(away)}@{_llp_norm_team(home)}"
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO odds_snapshots
                      (sport, game_key, away_team, home_team, market, book, side,
                       american_odds, point, snapshot_kind, source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (sport, game_key, away, home, market,
                      sel.get("book"), sel.get("name"),
                      sel.get("american"), sel.get("point"),
                      "current", "the-odds-api"))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _llp_earliest_snapshot_today(sport, away, home, market, side, board_date,
                                  book=None):
    """Return the earliest odds_snapshots row for this (sport, game, market, side,
    book) captured on `board_date` (UTC). Used as the CLV anchor when
    `opening_lines` is empty (e.g. the engine has been polling but no analyze
    call has fired `_llp_opening_line_for_game` yet), and for h2h price-drift
    CLV (which has no point movement). Returns `None` on any failure or no rows.

    Match rules (per architect review):
      - `book` filter (when provided) so CLV drift compares same-book prices,
        not cross-book noise from line shopping.
      - `side` matched against the persisted snapshot side using both raw
        case-insensitive equality AND normalized team-name equality
        (`_llp_norm_team`). The persister stores `sel.get('name')` — the
        sportsbook's full team string (e.g. "New York Yankees") — while
        callers commonly pass aliases ("Yankees"). Without normalization
        the anchor lookup silently missed nearly all h2h/spreads matches.
        For totals, `side` is "over"/"under" and norm is a no-op.
    """
    if not sport or not away or not home or not market:
        return None
    try:
        import psycopg2.extras as _pgx
        game_key = f"{_llp_norm_team(away)}@{_llp_norm_team(home)}"
        side_raw  = (side or "").strip()
        side_norm = _llp_norm_team(side_raw)  # "Yankees" / "New York Yankees" → "yankees" / "newyorkyankees"
        conn = get_db_conn()
        try:
            _ensure_llp_pro_schema(conn)
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                # Side match logic:
                #   1. NULL side row matches anything (totals over/under may
                #      sometimes be stored without a side).
                #   2. Raw lowercase equality (legacy snapshots).
                #   3. Normalized-team equality on both sides via
                #      `regexp_replace` (lowercase then strip non-alnum) —
                #      handles "Yankees" anchor vs "New York Yankees"
                #      persisted side. We also allow substring containment
                #      either direction to cover "Yankees" → "newyorkyankees".
                cur.execute("""
                    SELECT american_odds, point, fetched_at, book, side
                    FROM odds_snapshots
                    WHERE sport = %s
                      AND game_key = %s
                      AND market = %s
                      AND (%s::text IS NULL OR book = %s)
                      AND (
                            side IS NULL
                         OR LOWER(side) = LOWER(%s)
                         OR regexp_replace(LOWER(side), '[^a-z0-9]', '', 'g') = %s
                         OR regexp_replace(LOWER(side), '[^a-z0-9]', '', 'g') LIKE '%%' || %s || '%%'
                         OR %s LIKE '%%' || regexp_replace(LOWER(side), '[^a-z0-9]', '', 'g') || '%%'
                          )
                      AND (fetched_at AT TIME ZONE 'UTC')::date = %s::date
                    ORDER BY fetched_at ASC
                    LIMIT 1
                """, (sport, game_key, market,
                      book, book,
                      side_raw,
                      side_norm, side_norm, side_norm,
                      board_date))
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "american_odds": (float(row["american_odds"])
                                      if row["american_odds"] is not None else None),
                    "point": (float(row["point"])
                              if row["point"] is not None else None),
                    "fetched_at":    row["fetched_at"],
                    "book":          row.get("book"),
                    "side":          row.get("side"),
                }
        finally:
            conn.close()
    except Exception:
        return None


# ── Ingestion: ESPN standings → team strength ratings (free, public) ──
_LLP_TEAM_RATINGS_CACHE = {}   # keyed by sport → (fetched_at_ts, {team_norm: rating_dict})
_LLP_TEAM_RATINGS_TTL_SEC = 3600  # 1h — standings move slowly

_LLP_ESPN_STANDINGS_PATHS = {
    "mlb":   "baseball/mlb",
    "nba":   "basketball/nba",
    "wnba":  "basketball/wnba",
    "nfl":   "football/nfl",
    "nhl":   "hockey/nhl",
    "ncaab": "basketball/mens-college-basketball",
    "ncaaf": "football/college-football",
}

# Pythagorean exponent per sport (Bill James / variants). Used when both
# points-for and points-against are present to compute expected win-pct,
# which is a more stable strength signal than raw W-L early-season.
_LLP_PYTHAG_EXPONENT = {
    "mlb": 1.83, "nba": 14.0, "wnba": 14.0,
    "nfl": 2.37, "nhl": 2.00, "ncaab": 11.5, "ncaaf": 2.50,
}


def _llp_pythag(points_for, points_against, sport):
    """Pythagorean expected win pct. Returns None when inputs unusable."""
    try:
        pf = float(points_for); pa = float(points_against)
        if pf <= 0 and pa <= 0:
            return None
        ex = _LLP_PYTHAG_EXPONENT.get(sport, 2.0)
        num = pf ** ex
        den = num + (pa ** ex)
        if den <= 0:
            return None
        return num / den
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def _llp_fetch_team_ratings(sport):
    """Fetch team strength ratings from ESPN public standings JSON.

    Returns dict keyed by normalized team name → {
        win_pct, pythag_pct, strength, games_played, source, fetched_at
    }. `strength` is `pythag_pct` when available, else `win_pct`. Never
    raises — returns {} on any failure. Cached 1h per sport. Persists each
    team's row to `team_context` table for postmortem.
    """
    import requests as _req
    if not sport:
        return {}
    espn_path = _LLP_ESPN_STANDINGS_PATHS.get(sport.lower())
    if not espn_path:
        return {}
    now_ts = datetime.now().timestamp()
    hit = _LLP_TEAM_RATINGS_CACHE.get(sport)
    if hit and (now_ts - hit[0]) < _LLP_TEAM_RATINGS_TTL_SEC:
        return hit[1]
    out = {}
    try:
        url = f"https://site.api.espn.com/apis/v2/sports/{espn_path}/standings"
        r = _req.get(url, timeout=10)
        if r.status_code != 200:
            _LLP_TEAM_RATINGS_CACHE[sport] = (now_ts, out)
            return out
        data = r.json() or {}
        # ESPN nests standings under children[].standings.entries[]
        # (one child per league/conference). Fall back to top-level
        # standings.entries[] when the league is flat.
        groups = data.get("children") or [data]
        for grp in groups:
            standings = (grp or {}).get("standings") or {}
            for entry in (standings.get("entries") or []):
                team = entry.get("team") or {}
                team_name = team.get("displayName") or team.get("name") or ""
                if not team_name:
                    continue
                # Stats is a list of {name, value, ...} dicts.
                stats = {s.get("name"): s.get("value")
                         for s in (entry.get("stats") or [])
                         if isinstance(s, dict) and s.get("name")}
                wins   = stats.get("wins")
                losses = stats.get("losses")
                gp = ((float(wins) + float(losses))
                      if isinstance(wins, (int, float))
                      and isinstance(losses, (int, float)) else None)
                wp = stats.get("winPercent")
                if wp is None and gp and gp > 0:
                    wp = float(wins) / gp
                pf = stats.get("pointsFor")  or stats.get("avgPointsFor")
                pa = stats.get("pointsAgainst") or stats.get("avgPointsAgainst")
                # MLB uses runsFor / runsAgainst on some endpoints
                if pf is None: pf = stats.get("runsFor")
                if pa is None: pa = stats.get("runsAgainst")
                pythag = _llp_pythag(pf, pa, sport.lower()) if (pf is not None and pa is not None) else None
                strength = pythag if pythag is not None else (
                    float(wp) if isinstance(wp, (int, float)) else None)
                if strength is None:
                    continue  # no usable signal — skip this team
                rating = {
                    "team_name":    team_name,
                    "win_pct":      float(wp) if isinstance(wp, (int, float)) else None,
                    "pythag_pct":   pythag,
                    "strength":     float(strength),
                    "games_played": gp,
                    "source":       "site.api.espn.com",
                    "fetched_at":   datetime.now(timezone.utc).isoformat(),
                }
                out[_llp_norm_team(team_name)] = rating
        _LLP_TEAM_RATINGS_CACHE[sport] = (now_ts, out)
        # Best-effort persist to team_context.
        try:
            conn = get_db_conn()
            try:
                _ensure_llp_pro_schema(conn)
                with conn.cursor() as cur:
                    for tnorm, rating in out.items():
                        cur.execute("""
                            INSERT INTO team_context
                              (sport, team, context_kind, payload, source)
                            VALUES (%s, %s, 'standings_rating', %s::jsonb, %s)
                        """, (sport.lower(), rating["team_name"],
                              json.dumps(rating), rating["source"]))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
        return out
    except Exception:
        _LLP_TEAM_RATINGS_CACHE[sport] = (now_ts, out)
        return out


def _llp_lookup_team_rating(ratings, team_name):
    """Look up a team's rating by name, using normalized matching."""
    if not ratings or not team_name:
        return None
    norm = _llp_norm_team(team_name)
    if norm in ratings:
        return ratings[norm]
    # Fuzzy fallback: substring match in either direction.
    for k, v in ratings.items():
        if not k: continue
        if k in norm or norm in k:
            return v
    return None


def _llp_log_bet_decision(rec):
    """Best-effort: write the final per-game record to `bet_decisions`."""
    if not isinstance(rec, dict):
        return
    try:
        conn = get_db_conn()
        try:
            _ensure_llp_pro_schema(conn)
            disc = rec.get("discovery") or {}
            game_key = (f"{_llp_norm_team(rec.get('away_team'))}"
                        f"@{_llp_norm_team(rec.get('home_team'))}")
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO bet_decisions
                      (sport, game_key, away_team, home_team, market, side, book,
                       current_line, opening_line, no_vig_implied_prob,
                       model_win_prob, edge_pct, kelly_pct, confidence_tier,
                       final_decision, llp_badge, starter_status, lineup_status,
                       market_timing, market_cause, clv_beat, failure_tags)
                    VALUES (%s,%s,%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s,
                            %s,%s,%s,%s, %s,%s,%s, %s)
                """, (rec.get("sport"), game_key,
                      rec.get("away_team"), rec.get("home_team"),
                      rec.get("market"), rec.get("side"), rec.get("book"),
                      rec.get("current_line"), rec.get("opening_line"),
                      rec.get("no_vig_implied_probability"),
                      rec.get("model_win_probability"), rec.get("edge"),
                      rec.get("kelly_stake"), rec.get("confidence_tier"),
                      rec.get("final_decision"), rec.get("llp_badge"),
                      rec.get("starter_status"), rec.get("lineup_status"),
                      disc.get("market_timing"), disc.get("market_cause"),
                      rec.get("clv_beat"),
                      list(rec.get("failure_paths") or [])))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _llp_pro_detect_failures(rec):
    """Detect the 7 LLP Pro Data Ingestion failure tags on a populated record.

    Returns a list of tag strings that apply. Tags are HARD (count toward
    `_llp_validation_clean` cardinality and the spec ceiling caps).
    """
    tags  = []
    sport = (rec.get("sport") or "").lower()
    disc  = rec.get("discovery") or {}
    edge     = rec.get("edge")
    clv_beat = rec.get("clv_beat")
    starter  = rec.get("starter_status")
    lineup   = rec.get("lineup_status")
    adjs     = rec.get("model_adjustments") or {}

    if rec.get("current_line") is None:
        tags.append(LLP_PRO_FAILURE_MISSING_ODDS_FEED)

    # Starter confirmation: MLB-only (probable pitcher = starter for team
    # markets). NHL goalies can be added later via DailyFaceoff ingestion.
    if sport == "mlb" and starter != "confirmed":
        tags.append(LLP_PRO_FAILURE_MISSING_STARTER_CONFIRM)

    # Lineup confirmation: MLB/NBA/WNBA/NHL gate on starting unit availability.
    if sport in ("mlb", "nba", "wnba", "nhl") and lineup != "confirmed":
        tags.append(LLP_PRO_FAILURE_MISSING_LINEUP_STATUS)

    # Stale market with unverified execution context → not actionable.
    is_stale = (disc.get("stale_line") is True
                or disc.get("market_cause") == "stale"
                or disc.get("market_timing") == "stale")
    if is_stale and (starter != "confirmed" or lineup != "confirmed"):
        tags.append(LLP_PRO_FAILURE_STALE_MARKET_NOT_ACTIONABLE)

    # CLV without validation: positive CLV but execution unverified.
    if clv_beat is True and (starter != "confirmed" or lineup != "confirmed"):
        tags.append(LLP_PRO_FAILURE_CLV_WITHOUT_VALIDATION)

    # Fake market edge: large edge but model has no real adjustments AND
    # execution unverified → the edge is just trusting the price.
    if (isinstance(edge, (int, float)) and edge > 0.04
            and not adjs
            and (starter != "confirmed" or lineup != "confirmed")):
        tags.append(LLP_PRO_FAILURE_FAKE_MARKET_EDGE)

    # Candidate promoted too early: discovery-clean but validation NOT
    # clean, yet edge is high enough to risk downstream over-promotion.
    if (rec.get("discovery_clean") is True
            and rec.get("validation_clean") is False
            and isinstance(edge, (int, float)) and edge >= 0.035):
        tags.append(LLP_PRO_FAILURE_CANDIDATE_PROMOTED_EARLY)

    return tags


# Decisions worth logging — exclude TRAP (those are decided rejections, not
# learning candidates unless you want false-positive tracking).
_LLP_POSTMORTEM_DECISIONS = {"BET", "SMALL BET", "WATCH"}
_LLP_MODEL_VERSION = "v14.9-llp-discovery"

def _llp_log_postmortem(analyses, board_id, slate_date):
    """Insert one postmortem row per analyzable LLP record.

    Logs approved + meaningful watchlist decisions so we can later classify
    false positives / false negatives. Returns a structured status dict
    {inserted, skipped, failed, reason}. Logging is best-effort and must
    not block a run, but the structured status surfaces real failures.
    """
    status = {"inserted": 0, "skipped": 0, "failed": False, "reason": None}
    if not analyses:
        return status
    # Defensive: a malformed record (None, non-dict, or non-dict nested
    # `discovery`) must never block postmortem logging for the rest of
    # the slate. Skip non-dict entries outright.
    rows = []
    for r in analyses:
        if not isinstance(r, dict):
            status["skipped"] += 1
            continue
        decision = r.get("final_decision") or "PASS"
        if decision not in _LLP_POSTMORTEM_DECISIONS:
            status["skipped"] += 1
            continue
        disc = r.get("discovery")
        if not isinstance(disc, dict):
            disc = {}
        team, opp = _llp_resolve_team_opponent(
            r.get("market") or "h2h", r.get("side") or "",
            r.get("away_team") or "", r.get("home_team") or "")
        # LLP spec settlement columns. At approval-time we know
        #   - bet_implied_probability: the no-vig market IP at the moment we
        #     locked the play in (so post-mortem can recompute CLV cleanly).
        #   - closing_implied_probability: unknown until close capture; NULL
        #     here and back-filled by the settlement job.
        #   - actual_clv_beat: best-effort snapshot of clv_beat at decision
        #     time; the settlement job overwrites with the true comparison
        #     against the closing line once captured.
        rows.append((
            slate_date, board_id, r.get("sport"),
            None,  # game_id (feed doesn't expose a stable id yet)
            r.get("market"),
            team, opp, r.get("side"),
            r.get("opening_line"), r.get("current_line"), None,
            None, None, None,
            r.get("model_win_probability"), r.get("no_vig_implied_probability"),
            r.get("edge"),
            disc.get("market_cause"), disc.get("market_efficiency_rank"),
            disc.get("spread_fragility"), disc.get("possession_conversion"),
            disc.get("payout_friction"),
            decision, r.get("llp_badge") or r.get("confidence_tier"), r.get("kelly_stake"),
            None, "NOT_BET", r.get("clv_delta_pts"),
            ("STRONG_POSITIVE" if r.get("clv_beat") is True
             else "NEGATIVE" if r.get("clv_beat") is False
             else "UNKNOWN"),
            "NEUTRAL",
            (r.get("failure_paths") or [])[:5],
            None, False, _LLP_MODEL_VERSION,
            disc.get("stale_line"), disc.get("line_freeze"),
            disc.get("derivative_desync"), disc.get("market_timing"),
            None,                                  # closing_implied_probability (back-filled at settlement)
            r.get("no_vig_implied_probability"),   # bet_implied_probability (at approval)
            r.get("clv_beat"),                     # actual_clv_beat (snapshot; overwritten at settlement)
        ))
    if not rows:
        return status
    try:
        conn = get_db_conn()
        try:
            _ensure_llp_postmortem_schema(conn)
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO llp_postmortem (
                        slate_date, board_id, sport, game_id, market_type,
                        team, opponent, side,
                        line_at_discovery, line_at_approval, closing_line,
                        odds_at_discovery, odds_at_approval, closing_odds,
                        model_true_prob, no_vig_market_prob, pure_edge_pct,
                        market_cause, market_efficiency_rank,
                        spread_fragility, possession_conversion, payout_friction,
                        llp_decision, llp_badge, recommended_units,
                        actual_result, bet_result, clv_delta, clv_grade,
                        process_grade, failure_tags, postmortem_notes,
                        patch_needed, model_version,
                        stale_line, line_freeze, derivative_desync, market_timing,
                        closing_implied_probability, bet_implied_probability,
                        actual_clv_beat
                    ) VALUES (
                        %s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s,
                        %s,%s,%s, %s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s,%s,
                        %s,%s,%s, %s,%s, %s,%s,%s,%s, %s,%s,%s
                    )
                """, rows)
            conn.commit()
        finally:
            conn.close()
        status["inserted"] = len(rows)
        return status
    except Exception as e:
        try: app.logger.warning(f"llp_postmortem insert failed: {e}")
        except Exception: pass
        status["failed"] = True
        status["reason"] = type(e).__name__  # class only, no raw message
        return status


# ── Endpoint: GET /llp/postmortem (retrieve learning rows with filters) ──
@app.route("/llp/postmortem", methods=["GET"])
@require_api_key
def llp_postmortem_query():
    args = request.args
    where = []
    params = []
    for col, key in (("slate_date","slate_date"), ("sport","sport"),
                     ("llp_decision","decision"), ("bet_result","result"),
                     ("process_grade","process_grade")):
        v = args.get(key)
        if v:
            where.append(f"{col} = %s"); params.append(v)
    tag = args.get("failure_tag")
    if tag:
        where.append("%s = ANY(failure_tags)"); params.append(tag)
    # Validated limit (422 instead of 500 on bad input).
    raw_limit = args.get("limit", "200")
    try:
        limit = int(raw_limit)
        if limit < 1 or limit > 1000:
            raise ValueError("out of range")
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "limit must be an integer between 1 and 1000"}), 422
    sql = "SELECT * FROM llp_postmortem"
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    try:
        conn = get_db_conn()
        try:
            _ensure_llp_postmortem_schema(conn)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if r.get("created_at") is not None: r["created_at"] = r["created_at"].isoformat()
                    if r.get("slate_date")  is not None: r["slate_date"]  = r["slate_date"].isoformat()
            return jsonify({"ok": True, "count": len(rows), "rows": rows})
        finally:
            conn.close()
    except Exception as e:
        try: app.logger.warning(f"llp_postmortem query failed: {e}")
        except Exception: pass
        return jsonify({"ok": False, "error": "postmortem query failed",
                        "error_type": type(e).__name__}), 500


# ── Endpoint: POST /run-connected-model (full orchestrator) ──────────
@app.route("/run-connected-model", methods=["POST"])
@require_api_key
def cm_run_connected_model():
    body = request.get_json(silent=True) or {}
    slips_requested = bool(body.get("slips_requested", False))
    slip_sizes      = body.get("slip_sizes") or [2, 3]
    skip_arbiter    = bool(body.get("skip_arbiter", False))

    # 1. input-board (inline)
    source     = (body.get("source") or "chatgpt").strip()
    board_type = (body.get("board_type") or "prizepicks").strip()
    model      = (body.get("model") or "").strip().lower()
    board_date = (body.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
    props      = body.get("props") if isinstance(body.get("props"), list) else []
    games      = body.get("games") if isinstance(body.get("games"), list) else []
    meta       = body.get("meta") or {}

    is_team_board = (board_type == "team_betting") or (model == "llp_team")
    if is_team_board and board_type == "prizepicks":
        board_type = "team_betting"

    if not props and not games:
        return jsonify({"ok": False,
                        "error": "Either props or games must be a non-empty array."}), 400
    if is_team_board and not games:
        return jsonify({"ok": False, "error": "games must be a non-empty array"}), 400
    if not is_team_board and board_type == "prizepicks" and not props:
        return jsonify({"ok": False, "error": "props must be a non-empty array"}), 400

    try:
        datetime.strptime(board_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400

    # Normalize sport on every prop and every game to UPPER canonical form.
    body_sport = _cm_normalize_sport(body.get("sport"))
    for p in props:
        if isinstance(p, dict):
            p["sport"] = _cm_normalize_sport(p.get("sport") or body_sport)
    for g in games:
        if isinstance(g, dict):
            g["sport"] = _cm_normalize_sport(g.get("sport") or body_sport)

    meta = dict(meta)
    if games:
        meta["games"] = games
    if model:
        meta["model"] = model
    if body_sport:
        meta["sport"] = body_sport

    execution_notes = []
    with _cm_db() as conn:
        board_id = _cm_insert_board(conn, source, board_type, board_date, props, meta)
    execution_notes.append(f"board saved: {board_id}")

    # Team-betting boards: run the LLP team-side analysis pipeline.
    # Per-prop WOW/Claude/arbiter is for player props only and is skipped here.
    if is_team_board:
        default_sport = (meta.get("sport") or
                         (games[0].get("sport") if games and games[0].get("sport") else "")
                         ).lower().strip()
        execution_notes.append(
            f"team_betting board: running LLP team analysis for {len(games)} game(s)"
        )
        try:
            agg = _llp_team_analysis(games, default_sport, board_date)
        except Exception as e:
            app.logger.exception("LLP team analysis failed")
            return jsonify({"ok": False, "stage": "llp_team_analysis",
                            "board_id": board_id, "error": str(e),
                            "execution_notes": execution_notes}), 500

        execution_notes.append(
            f"LLP team analysis done: "
            f"bets={len(agg['best_bets'])}, "
            f"winners_ranked={len(agg['winners_ranked'])}, "
            f"upsets={len(agg['upset_candidates'])}, "
            f"pass_traps={len(agg['pass_traps'])}, "
            f"approved={len(agg['approved'])}, "
            f"market_verified={len(agg['market_verified'])}, "
            f"no_play={agg['no_play']}"
        )

        # ── LLP Claude Red-Team Audit (post-badge, pre-postmortem) ──
        #
        # The audit can only DOWNGRADE or CONFIRM. It runs against the
        # canonical badges produced by the engine. If Claude fails, we
        # keep engine output verbatim and mark every record audited=False.
        llp_skip_audit = bool(body.get("skip_arbiter", False))
        llp_audit_json      = None
        accepted_flags      = []
        rejected_flags      = []
        llp_audit_latency   = 0
        if llp_skip_audit:
            llp_audit_status = "skipped"
            llp_audit_error  = "arbiter skipped by request"
            llp_decision_src = "llp_engine"
            _llp_mark_unaudited(agg, "claude audit skipped by request")
            execution_notes.append("LLP claude audit skipped by request")
        else:
            llp_audit_json, llp_audit_status, llp_audit_error, llp_audit_latency = \
                _llp_run_claude_audit_team(agg)
            if llp_audit_status == "ok":
                accepted_flags, rejected_flags = _llp_apply_claude_arbiter(
                    agg, llp_audit_json)
                # Rebuild buckets so approved/etc. reflect post-audit badges.
                rebuilt = _llp_build_buckets(agg["team_analysis"],
                                             agg["source_access_status"])
                agg.update(rebuilt)
                llp_decision_src = ("llp_claude_arbiter"
                                    if accepted_flags else "llp_engine")
                execution_notes.append(
                    f"LLP claude audit done in {llp_audit_latency}ms: "
                    f"accepted={len(accepted_flags)}, rejected={len(rejected_flags)}"
                )
            elif llp_audit_status == "skipped":
                # Nothing actionable to audit — engine output stands.
                llp_decision_src = "llp_engine"
                _llp_mark_unaudited(agg, "no actionable badges to audit")
                execution_notes.append(
                    f"LLP claude audit skipped: {llp_audit_error}")
            else:  # failed
                llp_decision_src = "llp_wow_only_fallback"
                _llp_mark_unaudited(agg, "Claude LLP audit unavailable")
                execution_notes.append(
                    f"LLP claude audit failed: {llp_audit_error}")

        # Phase 3: best-effort postmortem logging (does not block the response).
        pm_status = _llp_log_postmortem(agg["team_analysis"], board_id, board_date)
        if pm_status.get("failed"):
            execution_notes.append(
                f"postmortem log failed ({pm_status.get('reason')}); "
                f"skipped={pm_status.get('skipped',0)}")
        else:
            execution_notes.append(
                f"postmortem rows logged: inserted={pm_status.get('inserted',0)}, "
                f"skipped={pm_status.get('skipped',0)}")

        return jsonify({
            "ok": True, "status": "completed", "board_id": board_id,
            "board_type": board_type,
            "games_received": len(games),
            # Engine vs arbiter provenance (spec §7).
            "final_decision_source":     llp_decision_src,
            "llp_final_decision_source": llp_decision_src,
            "claude_audit_status":       llp_audit_status,
            "claude_error":              llp_audit_error if llp_audit_status == "failed" else None,
            "llp_claude_audit":          llp_audit_json,
            "accepted_claude_flags":     accepted_flags,
            "rejected_claude_flags":     rejected_flags,
            "claude_audit_latency_ms":   llp_audit_latency,
            "source_access_status": agg["source_access_status"],
            "slate_summary":       agg["slate_summary"],
            "team_analysis":       agg["team_analysis"],
            # Legacy arrays (preserved for backward compatibility).
            "winners_ranked":      agg["winners_ranked"],
            "upset_candidates":    agg["upset_candidates"],
            "best_bets":           agg["best_bets"],
            "pass_traps":          agg["pass_traps"],
            "totals_edges":        agg["totals_edges"],
            "spread_edges":        agg["spread_edges"],
            "moneyline_edges":     agg["moneyline_edges"],
            # Canonical dashboard buckets (future-facing contract).
            "approved":            agg["approved"],
            "market_verified":     agg["market_verified"],
            "model_qualified":     agg["model_qualified"],
            "conditional":         agg["conditional"],
            "watchlist":           agg["watchlist"],
            "rejected":            agg["rejected"],
            "no_play":             agg["no_play"],
            "execution_notes":     execution_notes,
        })

    # 2. wow-score (via internal call to keep logic in one place)
    with app.test_request_context(json={"board_id": board_id},
                                  headers={"X-API-Key": os.environ.get("SCORING_API_KEY","")}):
        wow_resp = cm_wow_score()
    wow_json = wow_resp.get_json() if hasattr(wow_resp, "get_json") else wow_resp[0].get_json()
    if not wow_json.get("ok"):
        return jsonify({"ok": False, "stage": "wow-score", "board_id": board_id,
                        "error": wow_json.get("error"), "execution_notes": execution_notes}), 500
    execution_notes.append(f"wow-score done: approved={len(wow_json['approved_pool'])}, "
                           f"conditional={len(wow_json['conditional_pool'])}, "
                           f"watch={len(wow_json['watch_pool'])}, "
                           f"reject={len(wow_json['reject_pool'])}")

    # 3. claude-audit
    claude_json          = None
    claude_audit_status  = "skipped" if skip_arbiter else "pending"
    claude_error         = None
    final_json           = None
    if not skip_arbiter:
        with app.test_request_context(json={"board_id": board_id},
                                      headers={"X-API-Key": os.environ.get("SCORING_API_KEY","")}):
            audit_resp = cm_claude_audit()
        ar = audit_resp.get_json() if hasattr(audit_resp, "get_json") else audit_resp[0].get_json()
        if not ar.get("ok"):
            claude_audit_status = "failed"
            claude_error        = ar.get("error") or "unknown error"
            execution_notes.append(f"claude-audit failed: {claude_error}")
            execution_notes.append("Claude audit unavailable; final arbiter skipped.")
        else:
            claude_json         = ar
            claude_audit_status = "ok"
            execution_notes.append(f"claude-audit done in {ar.get('latency_ms')}ms")

            # 4. final-arbiter
            with app.test_request_context(json={"board_id": board_id},
                                          headers={"X-API-Key": os.environ.get("SCORING_API_KEY","")}):
                arb_resp = cm_final_arbiter()
            fr = arb_resp.get_json() if hasattr(arb_resp, "get_json") else arb_resp[0].get_json()
            if not fr.get("ok"):
                arbiter_error = fr.get("error") or "unknown error"
                execution_notes.append(f"final-arbiter failed: {arbiter_error}")
                execution_notes.append("Final arbiter unavailable; using WOW + Claude-audit output unsigned.")
            else:
                final_json = fr
                execution_notes.append(f"final-arbiter done in {fr.get('latency_ms')}ms")

    # ── WOW-only fallback when the arbitrated pool is unavailable ──
    # We never silently present an unaudited result as fully approved. The
    # cause of the fallback (claude failure vs arbiter failure vs skipped)
    # is recorded so messaging is accurate.
    def _downgrade_unaudited(items, cause_note):
        out = []
        for it in (items or []):
            it2 = dict(it)
            tier = (it2.get("confidence_tier") or "").upper()
            label = (it2.get("final_label") or "")
            if ("FINAL LOCK" in tier or "FINAL APPROVED" in tier or
                "final approved" in label.lower() or "final lock" in label.lower()):
                it2["confidence_tier"] = "MODEL QUALIFIED — UNAUDITED"
                it2["final_label"]     = "Model Qualified - Unaudited"
            it2["audited"] = False
            it2["audit_note"] = cause_note
            out.append(it2)
        return out

    if final_json:
        final_decision_source = "claude_arbiter"
        approved_pool    = final_json.get("final_approved_pool",    wow_json["approved_pool"])
        conditional_pool = final_json.get("final_conditional_pool", wow_json["conditional_pool"])
        watch_pool       = final_json.get("final_watch_pool",       wow_json["watch_pool"])
        reject_pool      = final_json.get("final_reject_pool",      wow_json["reject_pool"])
    else:
        # Distinguish the three "no arbitrated pool" causes.
        if claude_audit_status == "skipped":
            final_decision_source = "wow_only_skipped"
            cause_note = "Final arbiter skipped by request; WOW-only output."
            slip_skip_reason = "arbiter skipped by request"
        elif claude_audit_status == "failed":
            final_decision_source = "wow_only_fallback"
            cause_note = "Claude audit unavailable; WOW-only output."
            slip_skip_reason = "Claude audit unavailable"
        else:  # claude ok but arbiter failed
            final_decision_source = "wow_plus_claude_unsigned"
            cause_note = "Final arbiter unavailable; WOW + Claude-audit output unsigned."
            slip_skip_reason = "final arbiter unavailable"
        approved_pool    = _downgrade_unaudited(wow_json["approved_pool"], cause_note)
        conditional_pool = list(wow_json["conditional_pool"])
        watch_pool       = list(wow_json["watch_pool"])
        reject_pool      = list(wow_json["reject_pool"])

    # 5. slips (optional) — only built when we have a full arbiter result
    slips_json = None
    if slips_requested and final_json:
        with app.test_request_context(
            json={"board_id": board_id, "slip_sizes": slip_sizes},
            headers={"X-API-Key": os.environ.get("SCORING_API_KEY","")}):
            slip_resp = cm_build_slips()
        sr = slip_resp.get_json() if hasattr(slip_resp, "get_json") else slip_resp[0].get_json()
        if sr.get("ok"):
            slips_json = sr
            execution_notes.append(f"slips built: {sr.get('slips_built')}")
    elif slips_requested and not final_json:
        execution_notes.append(f"slips skipped: {slip_skip_reason}, no arbitrated pool.")

    return jsonify({
        "ok": True, "status": "completed", "board_id": board_id,
        "wow_output": {
            "approved_pool":    wow_json["approved_pool"],
            "conditional_pool": wow_json["conditional_pool"],
            "watch_pool":       wow_json["watch_pool"],
            "reject_pool":      wow_json["reject_pool"],
            "source_access_status": wow_json["source_access_status"],
        },
        # Back-compat alias for existing consumers
        "wow_summary": {
            "approved_pool":    wow_json["approved_pool"],
            "conditional_pool": wow_json["conditional_pool"],
            "watch_pool":       wow_json["watch_pool"],
            "reject_pool":      wow_json["reject_pool"],
            "source_access_status": wow_json["source_access_status"],
        },
        "claude_audit":         claude_json,
        "claude_audit_status":  claude_audit_status,
        "claude_error":         claude_error,
        "final_decision":       final_json,
        "final_decision_source": final_decision_source,
        "final_approved_pool":  approved_pool,
        "approved_pool":        approved_pool,
        "conditional_pool":     conditional_pool,
        "watch_pool":           watch_pool,
        "reject_pool":          reject_pool,
        "slips": slips_json,
        "execution_notes": execution_notes,
    })


# ── Step 3: in-process odds-snapshot cron (first_seen/current/lock/close + CLV) ──
# Periodically polls The Odds API for every sport in `_LLP_SPORT_MAP`, persisting
# line snapshots to `odds_snapshots` keyed by snapshot_kind and recording market
# open→close CLV to `clv_log`. In-process daemon thread (no external scheduler).
# Disable with LLP_DISABLE_SNAPSHOT_CRON=1. Reuses `_llp_fetch_odds` (so the 120s
# TTL cache + existing F5 market set are honoured — the cron does not add API
# spend beyond what analyze already triggers within a TTL window).
def _llp_env_int(name, default):
    """Parse an int env var with a safe fallback — never raises at import."""
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default

_LLP_CRON_INTERVAL_SEC    = _llp_env_int("LLP_SNAPSHOT_INTERVAL_SEC", 300)
_LLP_CRON_LOCK_WINDOW_MIN = _llp_env_int("LLP_LOCK_WINDOW_MIN", 15)
_LLP_CRON_ENABLED = (os.environ.get("LLP_DISABLE_SNAPSHOT_CRON", "").strip().lower()
                     not in ("1", "true", "yes", "on"))
_LLP_CRON_STARTED = False
_LLP_CRON_LOCK    = threading.Lock()
# Stable advisory-lock key so only ONE poller ticks at a time across gunicorn
# workers / multiple instances sharing the same Postgres. Each tick grabs it
# with pg_try_advisory_lock and skips if another worker already holds it —
# serializing the check-then-insert one-time writes (first_seen/lock/close)
# and CLV so concurrent workers can't double-write.
_LLP_CRON_ADVISORY_KEY = 778597203
_LLP_CRON_STATS   = {
    "ticks": 0, "rows_written": 0, "clv_rows": 0,
    "last_tick": None, "last_error": None, "last_sports": [],
}


def _llp_parse_commence(ct):
    """Parse an Odds API commence_time (ISO-8601, e.g. '2026-06-05T23:05:00Z')
    into a tz-aware UTC datetime. Returns None on any failure."""
    if not ct:
        return None
    try:
        dt = datetime.fromisoformat(str(ct).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _llp_event_market_keys(event):
    """Set of distinct Odds API market keys present across an event's books."""
    keys = set()
    for bm in (event.get("bookmakers") or []):
        for mk in (bm.get("markets") or []):
            k = mk.get("key")
            if k:
                keys.add(k)
    return keys


def _llp_iter_event_selections(event, market_key):
    """Yield (side_name, best_american, point, book) for one market_key, taking
    the most favourable price (highest American value) per outcome name across
    all books. Same-side cross-book noise is collapsed to the best line."""
    best = {}  # name -> (american, point, book)
    for bm in (event.get("bookmakers") or []):
        for mk in (bm.get("markets") or []):
            if mk.get("key") != market_key:
                continue
            for o in (mk.get("outcomes") or []):
                name = o.get("name")
                am   = o.get("price")
                if name is None or am is None:
                    continue
                try:
                    amf = float(am)
                except (TypeError, ValueError):
                    continue
                cur = best.get(name)
                if cur is None or amf > cur[0]:
                    best[name] = (amf, o.get("point"), bm.get("key", ""))
    for name, (am, pt, book) in best.items():
        yield name, am, pt, book


def _llp_preload_today(conn, board_date):
    """One round-trip per map: load today's per-(sport,game_key,market,side)
    snapshot_kinds set and latest (american, point). Replaces per-side SELECTs
    so a full slate costs 2 queries/tick instead of thousands. Keys use a
    lowercased side. Returns (kinds_map, latest_map)."""
    kinds_map, latest_map = {}, {}
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sport, game_key, market, LOWER(side), snapshot_kind
                FROM odds_snapshots
                WHERE (fetched_at AT TIME ZONE 'UTC')::date = %s::date
            """, (board_date,))
            for sp, gk, mk, sl, kind in cur.fetchall():
                kinds_map.setdefault((sp, gk, mk, sl), set()).add(kind)
            cur.execute("""
                SELECT DISTINCT ON (sport, game_key, market, LOWER(side))
                       sport, game_key, market, LOWER(side), american_odds, point
                FROM odds_snapshots
                WHERE (fetched_at AT TIME ZONE 'UTC')::date = %s::date
                ORDER BY sport, game_key, market, LOWER(side), fetched_at DESC
            """, (board_date,))
            for sp, gk, mk, sl, am, pt in cur.fetchall():
                latest_map[(sp, gk, mk, sl)] = (
                    float(am) if am is not None else None,
                    float(pt) if pt is not None else None)
    except Exception:
        pass
    return kinds_map, latest_map


def _llp_earliest_snapshot_conn(conn, sport, game_key, market, side, board_date):
    """Earliest American price today for this side, read on the SAME tick
    connection so a first_seen row inserted earlier in this uncommitted tick is
    visible (a separate connection would miss it and silently drop CLV).
    Returns the opening American odds (float) or None."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT american_odds FROM odds_snapshots
                WHERE sport = %s AND game_key = %s AND market = %s
                  AND LOWER(side) = LOWER(%s)
                  AND american_odds IS NOT NULL
                  AND (fetched_at AT TIME ZONE 'UTC')::date = %s::date
                ORDER BY fetched_at ASC LIMIT 1
            """, (sport, game_key, market, side, board_date))
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def _llp_snap_insert(conn, sport, game_key, away, home, market, side,
                     american, point, book, kind,
                     league=None, event_id=None, player=None):
    """Insert one odds_snapshots row with an explicit snapshot_kind. Records the
    full verified-data field contract: provider/source, book, sport, league,
    event, player/team, market, side, line (point), price (american_odds),
    timestamp (fetched_at default), and snapshot stage (snapshot_kind)."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO odds_snapshots
              (sport, league, event_id, game_key, away_team, home_team, player,
               market, book, side, american_odds, point, snapshot_kind, source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (sport, league, event_id, game_key, away, home, player,
              market, book, side, american, point, kind, "the-odds-api"))


def _llp_grade_clv(open_american, close_american):
    """Grade market open→close drift for a side from its American prices.
    Returns (clv_delta_prob, clv_grade, clv_beat). Positive delta = the side's
    implied probability rose (line moved toward it) → `clv_beat` True."""
    op = _llp_american_to_prob(open_american)
    cp = _llp_american_to_prob(close_american)
    if op is None or cp is None:
        return (None, "UNKNOWN", None)
    delta = cp - op
    a = abs(delta)
    if   a >= 0.030: grade = "STRONG"
    elif a >= 0.015: grade = "MEDIUM"
    elif a >= 0.005: grade = "SMALL"
    else:            grade = "FLAT"
    return (delta, grade, delta > 0)


def _llp_write_clv_log(conn, sport, game_key, market, side,
                       opening_american, closing_american):
    """Write a market open→close CLV row to clv_log. Returns True on insert.
    `bet_line` is left NULL — the cron tracks market movement, not a placed
    bet (per-bet CLV is computed in the analyze path via opening_lines)."""
    delta, grade, beat = _llp_grade_clv(opening_american, closing_american)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clv_log
                  (sport, game_key, market, side, opening_line, bet_line,
                   closing_line, clv_delta, clv_grade, clv_beat)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (sport, game_key, market, side,
                  opening_american, None, closing_american,
                  delta, grade, beat))
        return True
    except Exception:
        return False


def _llp_write_clv_incomplete(conn, sport, game_key, market, side, closing_american):
    """Record an explicit INCOMPLETE CLV row when a close line is captured but no
    opening anchor exists (current-only feed started after this market opened).
    CLV is marked incomplete, never fabricated: opening_line/clv_delta stay NULL
    and clv_beat is NULL so downstream cannot read it as a real beat."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO clv_log
                  (sport, game_key, market, side, opening_line, bet_line,
                   closing_line, clv_delta, clv_grade, clv_beat)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (sport, game_key, market, side,
                  None, None, closing_american, None, "INCOMPLETE", None))
        return True
    except Exception:
        return False


def _llp_snapshot_tick():
    """One polling pass over all sports. Best-effort; never raises."""
    if not os.environ.get("ODDS_API_KEY") or not os.environ.get("DATABASE_URL"):
        _LLP_CRON_STATS["last_error"] = "missing ODDS_API_KEY or DATABASE_URL"
        return
    rows = 0
    clv_rows = 0
    touched = []
    try:
        conn = get_db_conn()
    except Exception as e:
        _LLP_CRON_STATS["last_error"] = f"db-connect: {e}"
        return
    got_lock = False
    try:
        _ensure_llp_pro_schema(conn)
        # Serialize across workers/instances: skip this tick if another poller
        # already holds the advisory lock (prevents concurrent double-writes).
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_LLP_CRON_ADVISORY_KEY,))
            got_lock = bool(cur.fetchone()[0])
        if not got_lock:
            _LLP_CRON_STATS["last_error"] = None
            return
        now = datetime.now(timezone.utc)
        board_date = now.date().isoformat()
        kinds_map, latest_map = _llp_preload_today(conn, board_date)
        for sport_key in _LLP_SPORT_MAP.values():
            events = _llp_fetch_odds(sport_key)
            if not events:
                continue
            touched.append(sport_key)
            for ev in events:
                away = ev.get("away_team")
                home = ev.get("home_team")
                if not away or not home:
                    continue
                game_key = f"{_llp_norm_team(away)}@{_llp_norm_team(home)}"
                league   = ev.get("sport_title") or sport_key
                event_id = ev.get("id")
                commence = _llp_parse_commence(ev.get("commence_time"))
                within_lock = bool(
                    commence
                    and (commence - timedelta(minutes=_LLP_CRON_LOCK_WINDOW_MIN)) <= now < commence
                )
                after_close = bool(commence and now >= commence)
                for market_key in _llp_event_market_keys(ev):
                    for side, american, point, book in _llp_iter_event_selections(ev, market_key):
                        mkey  = (sport_key, game_key, market_key, side.lower())
                        kinds = kinds_map.setdefault(mkey, set())
                        # Genuine pre-close history exists only if a PRIOR tick (or
                        # the analyze path, which writes 'current' rows) already
                        # recorded ANY snapshot for this market today — the preload
                        # reflects only committed rows. Captured before we mutate
                        # `kinds` below so the same-tick close can't pose as the open.
                        had_prior_history = bool(kinds)

                        if "first_seen" not in kinds:
                            _llp_snap_insert(conn, sport_key, game_key, away, home,
                                             market_key, side, american, point, book, "first_seen",
                                             league=league, event_id=event_id)
                            rows += 1
                            kinds.add("first_seen")

                        latest = latest_map.get(mkey)
                        if latest is None or latest[0] != american or latest[1] != point:
                            _llp_snap_insert(conn, sport_key, game_key, away, home,
                                             market_key, side, american, point, book, "current",
                                             league=league, event_id=event_id)
                            rows += 1
                            latest_map[mkey] = (american, point)

                        if within_lock and "lock_line" not in kinds:
                            _llp_snap_insert(conn, sport_key, game_key, away, home,
                                             market_key, side, american, point, book, "lock_line",
                                             league=league, event_id=event_id)
                            rows += 1
                            kinds.add("lock_line")

                        if after_close and "close_line" not in kinds:
                            _llp_snap_insert(conn, sport_key, game_key, away, home,
                                             market_key, side, american, point, book, "close_line",
                                             league=league, event_id=event_id)
                            rows += 1
                            kinds.add("close_line")
                            # Only trust an opening anchor when a prior tick saw
                            # this market — otherwise the just-inserted close row
                            # would masquerade as the open and fabricate a FLAT CLV.
                            opening = (_llp_earliest_snapshot_conn(
                                conn, sport_key, game_key, market_key, side, board_date)
                                if had_prior_history else None)
                            if opening is not None:
                                if _llp_write_clv_log(conn, sport_key, game_key, market_key,
                                                      side, opening, american):
                                    clv_rows += 1
                            else:
                                # No genuine pre-close history (feed started after
                                # this market opened) → mark CLV incomplete, never
                                # fabricate an opening anchor.
                                if _llp_write_clv_incomplete(conn, sport_key, game_key,
                                                             market_key, side, american):
                                    clv_rows += 1
        conn.commit()
        _LLP_CRON_STATS["last_error"] = None
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _LLP_CRON_STATS["last_error"] = f"tick: {e}"
    finally:
        if got_lock:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_LLP_CRON_ADVISORY_KEY,))
                conn.commit()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass
    _LLP_CRON_STATS["ticks"]       += 1
    _LLP_CRON_STATS["rows_written"] += rows
    _LLP_CRON_STATS["clv_rows"]    += clv_rows
    _LLP_CRON_STATS["last_tick"]    = datetime.now(timezone.utc).isoformat()
    _LLP_CRON_STATS["last_sports"]  = touched


def _llp_snapshot_cron_loop():
    time.sleep(10)  # let the app finish binding before the first poll
    while True:
        try:
            _llp_snapshot_tick()
        except Exception as e:
            _LLP_CRON_STATS["last_error"] = f"loop: {e}"
        time.sleep(_LLP_CRON_INTERVAL_SEC)


def _llp_start_snapshot_cron():
    """Start the daemon poller once. No-op if disabled or already running."""
    global _LLP_CRON_STARTED
    if not _LLP_CRON_ENABLED:
        return
    with _LLP_CRON_LOCK:
        if _LLP_CRON_STARTED:
            return
        threading.Thread(target=_llp_snapshot_cron_loop, daemon=True,
                         name="llp-odds-snapshot-cron").start()
        _LLP_CRON_STARTED = True


@app.route("/llp/snapshot-cron/status", methods=["GET"])
def _llp_snapshot_cron_status():
    return jsonify({
        "ok": True,
        "enabled": _LLP_CRON_ENABLED,
        "started": _LLP_CRON_STARTED,
        "interval_sec": _LLP_CRON_INTERVAL_SEC,
        "lock_window_min": _LLP_CRON_LOCK_WINDOW_MIN,
        "stats": _LLP_CRON_STATS,
    })


# Start the cron at import so it runs under both `python app.py` (dev) and
# gunicorn (prod). Daemon thread → no shutdown coordination needed.
_llp_start_snapshot_cron()


# ---------------------------------------------------------------------------
# Gate Engine — WOW v16 PrizePicks deterministic classification pipeline
# ---------------------------------------------------------------------------
from gate_engine.pipeline import run_pipeline as _ge_run_pipeline
from gate_engine import tracker as _ge_tracker


@app.route("/gate-engine/run", methods=["POST"])
@require_api_key
def gate_engine_run():
    """
    Run the full WOW v16 gate engine pipeline on a board of prop rows.

    Request body (JSON):
      {
        "rows": [
          {
            "player":       "LeBron James",
            "sport":        "NBA",
            "prop_type":    "Points",
            "line":         27.5,
            "direction":    "MORE",
            "slate_date":   "2026-06-24",
            "board_source": "PrizePicks",
            "game":         "LAL vs GSW",
            "market_line":  27.0          (optional)
          }
        ],
        "target_date":  "2026-06-24",  (optional, defaults to today UTC)
        "enrichment": {                (optional)
          "<row_id or 'player:prop'>": {
            "game_log":       [25, 28, ...],
            "season_log":     [24, 26, ...],
            "sportsbook_line": 27.0,
            "best_available":  26.5,
            "consensus_line":  27.0,
            "clv_entry_price": -115,
            "closing_price":   -120,
            "status_payload": {
              "status": "ACTIVE", "source": "Rotowire",
              "dnp_risk": false, "minutes_restriction": false
            }
          }
        },
        "record_entries": false
      }

    Response:
      {
        "prop_ledger":        [...],   all rows with full gate audit trail
        "data_status_ledger": [...],   data status per row
        "terminal_labels":    [...],   {row_id, label, blockers}
        "final_card":         [...],   rows that reached FINAL_APPROVED
        "exposure_report":    {...},   exposure snapshot
        "clv_table":          [...],   CLV tracking
        "summary":            {...}    counts by label
      }

    NOTE: This endpoint is a data/classification layer only.
    Final approval remains with WOW/LLP. can_approve_bets = false.
    """
    body = request.get_json(silent=True) or {}

    raw_rows = body.get("rows")
    if not raw_rows or not isinstance(raw_rows, list):
        return jsonify({"error": "rows must be a non-empty list"}), 400

    target_date = None
    if body.get("target_date"):
        try:
            from datetime import date as _date
            target_date = _date.fromisoformat(body["target_date"])
        except ValueError:
            return jsonify({"error": f"Invalid target_date: {body['target_date']}"}), 400

    enrichment    = body.get("enrichment") or {}
    record_entries = bool(body.get("record_entries", False))

    try:
        result = _ge_run_pipeline(
            raw_rows=raw_rows,
            target_date=target_date,
            enrichment=enrichment,
            record_entries=record_entries,
        )
    except Exception as exc:
        req.log.error("gate_engine_run error: %s", exc)
        return jsonify({"error": "Gate engine pipeline error", "detail": str(exc)}), 500

    result["can_approve_bets"] = False
    result["disclaimer"]       = DISCLAIMER
    return jsonify(result), 200


@app.route("/gate-engine/result", methods=["POST"])
@require_api_key
def gate_engine_result():
    """
    Record a post-game result for CLV/hit tracking.

    Request body:
      {
        "row_id":        "row_0_abc123",
        "final_stat":    28.0,
        "closing_price": -118,    (optional)
        "notes":         "..."    (optional)
      }
    """
    body = request.get_json(silent=True) or {}
    row_id = body.get("row_id")
    if not row_id:
        return jsonify({"error": "row_id required"}), 400
    final_stat = body.get("final_stat")
    if final_stat is None:
        return jsonify({"error": "final_stat required"}), 400
    try:
        final_stat = float(final_stat)
    except (TypeError, ValueError):
        return jsonify({"error": "final_stat must be a number"}), 400

    record = _ge_tracker.record_result(
        row_id=row_id,
        final_stat=final_stat,
        closing_price=body.get("closing_price"),
        notes=body.get("notes"),
    )
    return jsonify(record), 200


@app.route("/gate-engine/performance", methods=["GET"])
@require_api_key
def gate_engine_performance():
    """Return hit-rate performance bucketed by terminal label."""
    return jsonify(_ge_tracker.get_bucket_performance()), 200


@app.route("/gate-engine/labels", methods=["GET"])
def gate_engine_labels():
    """Return all valid terminal labels and data statuses (no auth required)."""
    from gate_engine.labels import PropLabel, DataStatus
    return jsonify({
        "terminal_labels": [l.value for l in PropLabel],
        "data_statuses":   [d.value for d in DataStatus],
        "can_approve_bets": False,
        "note": "FINAL_APPROVED is a data classification only. Bet approval stays with WOW/LLP.",
    }), 200


@app.route("/gate-engine/audit-closure", methods=["POST"])
@require_api_key
def gate_engine_audit_closure():
    """
    Run WOW v16 Claude Audit Closure validators on a prop closure object.

    Request body:
      {
        "closure": {
          "l5_line_used":             25.5,
          "approval_timestamp":       "2026-06-24T10:00:00+00:00",
          "approved_line":            25.5,
          "current_line":             25.5,
          "model_prob":               0.62,
          "slip_type":                "POWER",       # POWER | FLEX | NONE
          "edge_vs_friction":         0.04,           # float or "UNKNOWN"
          "market_edge_confirmed":    true,
          "data_provenance":          "RETRIEVED:Rotowire",
          "matchup_grade_source":     "WOW_MODEL",
          "primary_signal":           "L10_TREND",
          "structural_failure_count": 0,
          "unresolved_conflict_flags": [],
          "board_timestamp":          "2026-06-24T09:00:00+00:00",
          "market_timestamp":         "2026-06-24T09:30:00+00:00",
          "coin_flip_killed":         false,         # optional
          "direction":                "MORE"          # optional
        },
        "prior_closure": { ... }    # optional — used for coin-flip kill detection
      }

    Response:
      {
        "passed":         bool,
        "results":        { validator: {passed, code, detail, ceiling} },
        "blockers":       ["VALIDATOR:CODE", ...],
        "label_ceiling":  "WATCH" | "MODEL_QUALIFIED_HOLD" | null,
        "rerun_required": bool,
        "can_approve_bets": false
      }

    Hard rules enforced:
      - l5_line_used within 0.5 of current_line
      - Approval older than 3h → rerun required
      - Line moved 0.5+ since approval → rerun required
      - model_prob must clear break_even(slip_type) + safety_buffer
      - edge_vs_friction must be POSITIVE for Power Play
      - Model Qualified without market_edge_confirmed=True not Power eligible
      - Source conflict blocks FINAL_APPROVED
      - DES conflict persists across sessions
      - 3+ structural failures kill prop
      - Opposite side after coin-flip kill must restart full gate stack
    """
    body = request.get_json(silent=True) or {}
    closure = body.get("closure")
    if not closure or not isinstance(closure, dict):
        return jsonify({"error": "closure object required"}), 400

    prior_closure = body.get("prior_closure") or None

    from gate_engine.audit_closure import run_audit_closure
    try:
        result = run_audit_closure(closure, prior_closure=prior_closure)
    except Exception as exc:
        return jsonify({"error": "Audit closure error", "detail": str(exc)}), 500

    result["can_approve_bets"] = False
    result["disclaimer"]       = DISCLAIMER
    return jsonify(result), 200


@app.route("/gate-engine/audit-closure/fields", methods=["GET"])
def gate_engine_closure_fields():
    """Return required closure fields and their definitions (no auth required)."""
    from gate_engine.audit_closure import (
        CLOSURE_FIELDS, BREAK_EVEN, SAFETY_BUFFER,
        APPROVAL_STALE_HOURS, LINE_MOVEMENT_THRESHOLD,
        L5_LINE_TOLERANCE, MAX_STRUCTURAL_FAILURES,
    )
    return jsonify({
        "required_fields":          CLOSURE_FIELDS,
        "break_even":               BREAK_EVEN,
        "safety_buffer":            SAFETY_BUFFER,
        "approval_stale_hours":     APPROVAL_STALE_HOURS,
        "line_movement_threshold":  LINE_MOVEMENT_THRESHOLD,
        "l5_line_tolerance":        L5_LINE_TOLERANCE,
        "max_structural_failures":  MAX_STRUCTURAL_FAILURES,
        "can_approve_bets":         False,
    }), 200


@app.route("/gate-engine/calibration-health/validate", methods=["POST"])
@require_api_key
def gate_engine_calibration_health_validate():
    """
    Layer 0.5: Calibration Health Gate — validate a candidate against history.

    Request body:
      {
        "candidate": {
          "sport":        "WNBA",
          "market":       "points",
          "player":       "A'ja Wilson",
          "failure_tags": ["STALE_SOURCE", "OUTLIER_CONTAMINATION"]
        }
      }

    Response:
      {
        "passed":            bool,
        "grade":             "GREEN" | "WATCH" | "SUPPRESS" | "DATA_GAP",
        "ceiling":           str | null,   (LLP label ceiling)
        "code":              str,
        "detail":            str,
        "blockers":          [...],
        "dimension_results": {
          "failure_tags": {...},
          "sport_market": {...},
          "player":       {...}
        },
        "can_approve_bets":  false
      }

    Rules:
      3  same-tag failures           → WATCH ceiling (warning)
      5+ same-tag failures + neg CLV → WATCH max (bucket downgrade)
      8+ failures / neg EV           → SUPPRESS (auto-suppress / REJECT max)
      CLV+  + results−               → GREEN (variance hold — no downgrade)
      CLV−  + results−               → SUPPRESS (strongest signal)
    """
    body = request.get_json(silent=True) or {}
    candidate = body.get("candidate")
    if not candidate or not isinstance(candidate, dict):
        return jsonify({"error": "candidate object required"}), 400

    from gate_engine.calibration_health import validate_calibration_health
    try:
        result = validate_calibration_health(candidate)
    except Exception as exc:
        return jsonify({"error": "calibration health error", "detail": str(exc)}), 500

    result["disclaimer"] = DISCLAIMER
    return jsonify(result), 200


@app.route("/gate-engine/calibration-health/summary", methods=["GET"])
@require_api_key
def gate_engine_calibration_health_summary():
    """
    Return health summary across all dimensions from the calibration ledger.

    Response includes:
      total_records       — total entries in calibration ledger
      overall_quadrant    — CLV × result health across all records
      grade               — aggregate health grade
      by_sport            — health per sport
      by_market           — health per market type
      by_failure_tag      — top 10 failure tags by frequency with health stats
      can_approve_bets    — always false

    Use by_failure_tag to identify systemic tooling/data-acquisition problems
    (high CUT or LOSS frequency on specific failure_tags signals a process issue,
    not a betting signal).
    """
    from gate_engine.calibration_health import get_health_summary
    try:
        result = get_health_summary()
    except Exception as exc:
        return jsonify({"error": "health summary error", "detail": str(exc)}), 500

    result["disclaimer"] = DISCLAIMER
    return jsonify(result), 200


@app.route("/gate-engine/llp-governance/validate", methods=["POST"])
@require_api_key
def gate_engine_llp_governance():
    """
    Run LLP-PATCH-2026-06-27 Execution Governance v16.1 validators.

    Request body:
      {
        "candidate": {
          "final_label":         "LLP_APPROVED",   # must be one of 6 allowed labels
          "book":                "DraftKings",
          "odds":                -110,
          "line":                47.5,
          "side":                "OVER",
          "market":              "total",           # liquid_main | WNBA | F5 | alt
          "timestamp":           "2026-06-27T14:00:00+00:00",
          "model_probability":   0.59,
          "no_vig_probability":  0.52,
          "edge":                0.07,              # or omit to auto-compute
          "source":              "WOW_MODEL",
          "opener":              48.0,
          "game_start_time":     "2026-06-27T17:05:00+00:00",
          "final_lock_confirmed": false,
          "stake":               1.0,
          "consensus_implied_drift": 0.01,         # optional steam check
          "prior_label":         null,
          "full_rerun_completed": false,
          "calibration_ledger":  { ...all 21 fields... },
          "market_move_against_thesis": false,     # hard kill flags
          "source_conflict": false,
          "wrong_slate": false, ...
        },
        "session": {
          "bets_today":           1,
          "units_today":          0.5,
          "units_this_game":      0.0,
          "units_same_script":    0.0,
          "calibration_verified": false
        }
      }

    Response:
      {
        "passed":         bool,
        "results":        { validator: {passed, code, detail, ceiling} },
        "blockers":       [...],
        "label_ceiling":  str | null,
        "effective_label": str,
        "rerun_required": bool,
        "can_approve_bets": false
      }

    Banned as final labels: LEAN, CONDITIONAL, FLIP_CANDIDATE,
      SOURCE_CONFLICT, DATA_UNOBTAINABLE, NO_BET, STALE_LINE

    Edge thresholds: liquid_main >= 1.5%, WNBA >= 2.0%,
      derivatives/F5/1H >= 2.5%, alt/niche >= 3.0%

    Probability caps: <52% = REJECT, 52-54% = max WATCH,
      55-57% = max PLAYABLE, 58-60% = APPROVED eligible
    """
    body = request.get_json(silent=True) or {}
    candidate = body.get("candidate")
    if not candidate or not isinstance(candidate, dict):
        return jsonify({"error": "candidate object required"}), 400

    session = body.get("session") or {}

    from gate_engine.llp_governance import run_llp_governance
    try:
        result = run_llp_governance(candidate, session=session)
    except Exception as exc:
        return jsonify({"error": "LLP governance error", "detail": str(exc)}), 500

    result["disclaimer"] = DISCLAIMER
    return jsonify(result), 200


@app.route("/gate-engine/llp-governance/calibration", methods=["GET"])
@require_api_key
def gate_engine_llp_calibration():
    """
    Return the LLP calibration ledger (last N entries) and per-label stats.

    Query params:
      limit   — max entries to return (default 200, max 500)
      stats   — if "1" or "true", return label stats instead of raw entries

    Label stats (TU2) include LLP_CUT so high-CUT frequency is visible —
    it often reveals tooling/data-acquisition problems rather than betting signal.

    Also returns:
      opener_unavailable_count — entries missing opener (OPENER_UNAVAILABLE, RC2)
      no_close_available_count — entries missing close (blocks CLV grading, RC2)
      calibration_graduation_tiers — FULL_FRACTIONAL_KELLY_ELIGIBLE tier table (TU1)
    """
    from gate_engine.llp_governance import get_calibration_ledger, get_calibration_label_stats
    limit = min(int(request.args.get("limit", 200)), 500)
    want_stats = request.args.get("stats", "").lower() in ("1", "true", "yes")

    if want_stats:
        result = get_calibration_label_stats()
        result["disclaimer"] = DISCLAIMER
        return jsonify(result), 200

    return jsonify({
        "entries":          get_calibration_ledger(limit=limit),
        "can_approve_bets": False,
        "disclaimer":       DISCLAIMER,
    }), 200


@app.route("/gate-engine/llp-governance/labels", methods=["GET"])
def gate_engine_llp_labels():
    """Return allowed/banned LLP labels, edge thresholds, and prob caps (no auth)."""
    from gate_engine.llp_governance import (
        LLPLabel, BANNED_AS_FINAL, EDGE_THRESHOLD, MarketType,
        STEAM_RERUN_THRESHOLD, STEAM_DOWNGRADE_THRESHOLD, DEFAULT_EXPOSURE,
    )
    return jsonify({
        "allowed_final_labels": [l.value for l in LLPLabel],
        "banned_as_final":      list(BANNED_AS_FINAL),
        "edge_thresholds":      {k.value: v for k, v in EDGE_THRESHOLD.items()},
        "probability_caps": {
            "<0.52":        "LLP_REJECT",
            "0.52–0.54":    "LLP_WATCH",
            "0.55–0.57":    "LLP_PLAYABLE",
            "0.58–0.60":    "LLP_APPROVED eligible",
            ">0.60":        "rare; requires independent validation",
        },
        "steam_thresholds": {
            "rerun":     STEAM_RERUN_THRESHOLD,
            "downgrade": STEAM_DOWNGRADE_THRESHOLD,
        },
        "session_exposure_defaults": DEFAULT_EXPOSURE,
        "can_approve_bets": False,
    }), 200


# ---------------------------------------------------------------------------
# /lock-api/* — WOW Final Lock Dashboard endpoints
# ---------------------------------------------------------------------------

def _get_db_conn():
    """Open a psycopg2 connection using DATABASE_URL."""
    import psycopg2
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url)


@app.route("/lock-api/submit", methods=["POST"])
@require_api_key
def lock_api_submit():
    """
    Machine-gated Final Lock EV check.

    Validates shrinkage_probability >= per-leg breakeven for the chosen slip format.
    Saves the result to the final_locks table regardless of outcome.
    can_approve_bets: False enforced — advisory only.
    """
    import psycopg2.extras

    from gate_engine.payout_context import ALL_FORMATS

    body = request.get_json(force=True) or {}

    player = (body.get("player") or "").strip()
    market = (body.get("market") or "").strip()
    side   = (body.get("side") or "").strip()

    if not player or not market or not side:
        return jsonify({"error": "player, market, and side are required"}), 400

    slip_type             = body.get("slip_type") or ""
    shrinkage_probability = body.get("shrinkage_probability")
    pick_count            = int(body.get("pick_count") or 2)
    pp_payout             = body.get("pp_payout")

    gate_result: dict = {}
    decision = "MODEL_QUALIFIED_HOLD"
    slip_prob = None
    breakeven_slip = None
    ev = None

    per_leg_be = ALL_FORMATS.get(slip_type)
    if per_leg_be is not None and shrinkage_probability is not None:
        try:
            shrink_p = float(shrinkage_probability)
            payout_m = float(pp_payout) if pp_payout else (1.0 / (per_leg_be ** pick_count))

            slip_prob      = shrink_p ** pick_count
            breakeven_slip = 1.0 / payout_m
            ev             = slip_prob * payout_m - 1.0
            edge_per_leg   = shrink_p - per_leg_be
            gate_pass      = shrink_p >= per_leg_be
            decision       = "FINAL_APPROVED" if gate_pass else "MODEL_QUALIFIED_HOLD"

            gate_result = {
                "per_leg_breakeven":     per_leg_be,
                "shrinkage_probability": shrink_p,
                "slip_probability":      slip_prob,
                "breakeven_slip_prob":   breakeven_slip,
                "estimated_slip_ev":     ev,
                "edge_per_leg":          edge_per_leg,
                "gate_pass":             gate_pass,
                "decision":              decision,
            }
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            gate_result = {"error": str(exc)}

    # Persist to final_locks
    try:
        import psycopg2
        import psycopg2.extras
        conn = _get_db_conn()
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO final_locks (
                player, team, opponent, market, side,
                pp_line, pp_payout, slip_type, pick_count,
                injury_status, teammate_status,
                sb_comp_line, sb_no_vig_prob,
                proj_source1, proj_source2,
                model_probability, shrinkage_probability, correlation_flag,
                slip_probability, breakeven_slip_prob, estimated_slip_ev,
                final_lock_decision, gate_engine_data,
                sport, league, environment, notes
            ) VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,
                %s,%s,
                %s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,
                %s,%s,%s,%s
            )
            RETURNING id, created_at
            """,
            (
                player,
                body.get("team") or None,
                body.get("opponent") or None,
                market,
                side,
                body.get("pp_line") or None,
                body.get("pp_payout") or None,
                slip_type or None,
                pick_count,
                body.get("injury_status") or None,
                body.get("teammate_status") or None,
                body.get("sb_comp_line") or None,
                body.get("sb_no_vig_prob") or None,
                body.get("proj_source1") or None,
                body.get("proj_source2") or None,
                body.get("model_probability") or None,
                shrinkage_probability or None,
                bool(body.get("correlation_flag", False)),
                slip_prob,
                breakeven_slip,
                ev,
                decision,
                psycopg2.extras.Json(gate_result),
                body.get("sport") or None,
                body.get("league") or None,
                body.get("environment") or "live",
                body.get("notes") or None,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        lock_id    = row[0]
        created_at = row[1].isoformat()
    except Exception as exc:
        return jsonify({"error": "DB error", "detail": str(exc)}), 500

    return jsonify({
        "id":               lock_id,
        "created_at":       created_at,
        "decision":         decision,
        "gate":             gate_result,
        "can_approve_bets": False,
        "disclaimer":       DISCLAIMER,
    }), 201


@app.route("/lock-api/history", methods=["GET"])
@require_api_key
def lock_api_history():
    """Return recent final locks from the final_locks table (newest first)."""
    limit = min(int(request.args.get("limit", 50)), 200)
    try:
        import psycopg2.extras
        conn = _get_db_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                id, created_at, player, team, opponent,
                market, side, pp_line, pp_payout,
                slip_type, pick_count,
                injury_status, teammate_status,
                model_probability, shrinkage_probability, correlation_flag,
                slip_probability, breakeven_slip_prob, estimated_slip_ev,
                final_lock_decision,
                sport, league, environment, notes
            FROM final_locks
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        # Convert Decimal / datetime to JSON-safe types
        def safe(v):
            if hasattr(v, "isoformat"):
                return v.isoformat()
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return v

        records = [{k: safe(v) for k, v in dict(r).items()} for r in rows]
        return jsonify({
            "locks":            records,
            "can_approve_bets": False,
        }), 200
    except Exception as exc:
        return jsonify({"error": "DB error", "detail": str(exc)}), 500


# ── Patch 2026-06-27: Settlement Loopback endpoints ──────────────────────────

@app.route("/lock-api/settle", methods=["POST"])
@require_api_key
def lock_api_settle():
    """
    Ingest a settled result into the settlement_ledger.

    POST body (JSON):
      { date, sport, player, market, side, submitted_line, closing_line,
        submitted_odds_or_payout, closing_odds_or_projection,
        model_probability, market_probability, edge, result, CLV,
        failure_tag, dominant_failure_tag, slip_type, pick_count,
        payout_multiplier, slip_EV, actual_slip_result, notes }

    Required: player, market, result, date
    """
    from gate_engine import settlement_loopback as _sl
    body = request.get_json(silent=True) or {}
    result = _sl.ingest_settled_result(body)
    status = 201 if result.get("ok") else 400
    return jsonify({**result, "can_approve_bets": False}), status


@app.route("/lock-api/settle/freshness", methods=["GET"])
@require_api_key
def lock_api_settle_freshness():
    """Return settlement ledger freshness status."""
    from gate_engine import settlement_loopback as _sl
    return jsonify({**_sl.check_freshness(), "can_approve_bets": False}), 200


@app.route("/lock-api/settle/recent", methods=["GET"])
@require_api_key
def lock_api_settle_recent():
    """Return recent settlement records."""
    from gate_engine import settlement_loopback as _sl
    n = min(int(request.args.get("limit", 50)), 500)
    return jsonify({
        "settlements":    _sl.get_recent_settlements(n),
        "can_approve_bets": False,
    }), 200


# ── Patch 2026-06-27: Sharp Anchor standalone endpoint ───────────────────────

@app.route("/gate-engine/sharp-anchor", methods=["POST"])
@require_api_key
def gate_engine_sharp_anchor():
    """
    Directional sharp market anchor check (standalone, no pipeline).

    POST body (JSON):
      {
        pp_line:           number | null,
        sharp_fair_line:   number | null,
        sharp_no_vig_prob: number | null,   # 0–1, probability for OVER/MORE
        side:              "MORE" | "LESS" | "OVER" | "UNDER"
      }

    Returns: { passed, anchor_status, line_status, reject, terminal_label,
               our_side_prob, detail, can_approve_bets: False }
    """
    from gate_engine import sharp_anchor as _sa
    body = request.get_json(silent=True) or {}
    result = _sa.check_standalone(
        pp_line           = body.get("pp_line"),
        sharp_fair_line   = body.get("sharp_fair_line"),
        sharp_no_vig_prob = body.get("sharp_no_vig_prob"),
        side              = body.get("side", "MORE"),
    )
    return jsonify(result), 200


# ── Patch 2026-06-27: House Rules standalone endpoint ────────────────────────

@app.route("/gate-engine/house-rules", methods=["POST"])
@require_api_key
def gate_engine_house_rules():
    """
    House rules matrix check (standalone, no pipeline).

    POST body (JSON):
      {
        platform:           "prizepicks" | "fanduel" | "draftkings" | "sportsbook",
        injury_flag:        bool,
        minutes_dependency: bool,
        model_prob:         number | null,
        per_leg_breakeven:  number | null,
        market_type:        "player_prop"  (optional, default player_prop)
      }

    Returns: { passed, code, haircut_applied, surviving_edge, detail, can_approve_bets: False }
    """
    from gate_engine import house_rules as _hr
    body = request.get_json(silent=True) or {}
    result = _hr.check_standalone(
        platform           = body.get("platform", "prizepicks"),
        injury_flag        = bool(body.get("injury_flag", False)),
        minutes_dependency = bool(body.get("minutes_dependency", True)),
        model_prob         = body.get("model_prob"),
        per_leg_breakeven  = body.get("per_leg_breakeven"),
        market_type        = body.get("market_type", "player_prop"),
    )
    return jsonify(result), 200


# ── Patch 2026-06-27: Execution Friction standalone endpoint ─────────────────

@app.route("/gate-engine/execution-friction", methods=["POST"])
@require_api_key
def gate_engine_execution_friction():
    """
    Execution friction / liquidity gate (standalone, no pipeline).

    POST body (JSON):
      {
        analysis_pp_line:    number | null,
        current_pp_line:     number | null,
        analysis_payout:     number | null,
        current_payout:      number | null,
        analysis_slip_type:  string | null,
        current_slip_type:   string | null,
        analysis_pick_count: integer | null,
        current_pick_count:  integer | null,
        line_timestamp_utc:  string (ISO-8601) | null,
        player_status:       string | null,
        side:                "MORE" | "LESS",
        platform:            "prizepicks" | "sportsbook",
        max_line_age_seconds: integer (default 30)
      }

    Returns: { passed, code, rejects, cautions, checks, can_approve_bets: False }
    """
    from gate_engine import execution_friction as _ef
    body = request.get_json(silent=True) or {}
    result = _ef.check_standalone(
        analysis_pp_line       = body.get("analysis_pp_line"),
        current_pp_line        = body.get("current_pp_line"),
        analysis_payout        = body.get("analysis_payout"),
        current_payout         = body.get("current_payout"),
        analysis_slip_type     = body.get("analysis_slip_type"),
        current_slip_type      = body.get("current_slip_type"),
        analysis_pick_count    = body.get("analysis_pick_count"),
        current_pick_count     = body.get("current_pick_count"),
        line_timestamp_utc     = body.get("line_timestamp_utc"),
        player_status          = body.get("player_status"),
        side                   = body.get("side", "MORE"),
        platform               = body.get("platform", "prizepicks"),
        max_line_age_seconds   = int(body.get("max_line_age_seconds", 30)),
    )
    return jsonify(result), 200


# ── Patch 2026-06-27: Correlation Gate standalone endpoint ───────────────────

@app.route("/gate-engine/correlation", methods=["POST"])
@require_api_key
def gate_engine_correlation():
    """
    Correlation classification for a list of prop legs (standalone, no pipeline).

    POST body (JSON):
      {
        legs: [
          { player: str, stat: str, side: "MORE"|"LESS", team: str? },
          ...
        ],
        slip_type: "3-pick Power" | "4-pick Flex" | ...   (optional)
      }

    Returns:
      { classification, classifications, conflicts, block_power_play,
        note_flex_math, passed, can_approve_bets: False }
    """
    from gate_engine import correlation_gate as _cg
    body  = request.get_json(silent=True) or {}
    legs  = body.get("legs") or []
    if not isinstance(legs, list):
        return jsonify({"ok": False, "error": "legs must be a list"}), 400
    result = _cg.classify_legs(legs)
    return jsonify(result), 200


# ── Gate Engine: Final Lock Orchestrator ─────────────────────────────────────

@app.route("/gate-engine/final-lock", methods=["POST"])
@require_api_key
def gate_engine_final_lock():
    """
    Master gate-engine orchestration endpoint.

    Runs all five patch gates in sequence and returns a single unified
    approval decision. This is the truth source for the Final Lock Dashboard.

    POST body (JSON):
      # Prop identity (required)
      player            str
      sport             str
      market            str
      side              "MORE" | "LESS" | "OVER" | "UNDER"
      pp_line           float
      platform          str   default "prizepicks"

      # Sharp market data (optional — gate skipped if absent)
      sharp_no_vig_prob float | null
      sharp_fair_line   float | null

      # Model / EV data (optional)
      model_probability     float | null
      shrinkage_probability float | null
      per_leg_breakeven     float | null
      payout_multiplier     float | null

      # Execution context (optional — execution gate skipped if absent)
      analysis_pp_line       float | null
      analysis_payout        float | null
      current_payout         float | null
      analysis_slip_type     str | null
      current_slip_type      str | null
      analysis_pick_count    int | null
      current_pick_count     int | null
      line_timestamp_utc     str | null   (ISO-8601)
      player_status          str | null
      max_line_age_seconds   int  default 30

      # House rules inputs (optional)
      injury_flag            bool  default false
      minutes_dependency     bool  default true
      market_type            str   default "player_prop"

      # Slip legs for correlation gate (optional)
      slip_legs   [{player, stat, side, team?}]
      slip_type   str | null

      # Skip settlement DB check (testing only)
      skip_settlement_check  bool  default false

    Returns:
      {
        can_approve_bets:      false,
        final_label:           str,
        approval_ceiling:      str,
        terminal_reject:       str | null,
        settlement_stale:      bool,
        gate_results:          { settlement_loopback, sharp_anchor, house_rules,
                                  execution_friction, correlation_gate },
        gates_passed:          [str],
        gates_failed:          [str],
        gates_skipped:         [str],
        blocking_gates:        [str],
        warnings:              [str],
        required_next_actions: [str],
        EV_before_friction:    float | null,
        EV_after_friction:     float | null,
        usable_probability:    float | null,
        slip_EV:               float | null,
        prop:                  { player, market, side, pp_line, platform }
      }
    """
    from gate_engine.final_lock_orchestrator import run as _flo_run
    body = request.get_json(silent=True) or {}
    result = _flo_run(body)
    return jsonify(result), 200


# ── Kalshi Exchange Layer ─────────────────────────────────────────────────────

@app.route("/kalshi/evaluate-contract", methods=["POST"])
@require_api_key
def kalshi_evaluate_contract():
    """
    Full Kalshi contract evaluation: fetch orderbook, normalize, grade
    settlement, compute adjusted edge, classify into label.

    POST body (JSON):
      {
        ticker:             str   — Kalshi market ticker (e.g. "KXNBATOT-25JUN27-T225.5")
        side:               "YES" | "NO"   default "YES"
        model_probability:  float | null
        category:           str   default "sports"
        settlement_condition: str | null
        resolution_source:  str | null
        uncertainty_tax:    float  default 0.01
        use_live_book:      bool   default true   (false = use provided book data)
        book_override:      dict | null  (normalized book to use if use_live_book=false)
      }

    Returns: ContractEvaluation-shaped dict.
    """
    from kalshi_engine import kalshi_client, orderbook_normalizer, edge_engine
    from kalshi_engine import settlement_risk, market_buckets

    body   = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "").strip()
    side   = (body.get("side") or "YES").upper()
    model_prob   = body.get("model_probability")
    category     = (body.get("category") or "sports").lower()
    uncertainty  = float(body.get("uncertainty_tax", 0.01))
    use_live     = bool(body.get("use_live_book", True))

    if not ticker:
        return jsonify({"ok": False, "error": "ticker is required", "can_approve_bets": False}), 400

    # ── Orderbook ─────────────────────────────────────────────────────────────
    if use_live:
        raw_book = kalshi_client.safe_get_orderbook(ticker)
        if not raw_book or raw_book.get("error"):
            return jsonify({
                "label":            "KALSHI_DATA_UNOBTAINABLE",
                "ticker":           ticker,
                "error":            raw_book.get("error") if raw_book else "fetch failed",
                "can_approve_bets": False,
            }), 200
        norm_book = orderbook_normalizer.normalize(raw_book, ticker=ticker)
    else:
        norm_book = body.get("book_override") or {}

    # ── Settlement grade ──────────────────────────────────────────────────────
    sr = settlement_risk.grade_contract(
        title                = ticker,
        settlement_condition = body.get("settlement_condition"),
        resolution_source    = body.get("resolution_source"),
        category             = category,
        contract_ticker      = ticker,
    )

    # ── Edge evaluation ───────────────────────────────────────────────────────
    ev = edge_engine.evaluate(
        model_probability = model_prob,
        normalized_book   = norm_book,
        category          = category,
        side              = side,
        uncertainty_tax   = uncertainty,
    )

    # ── Market bucket ─────────────────────────────────────────────────────────
    bucket = market_buckets.classify(
        settlement_grade = sr["resolution_clarity_grade"],
        liquidity_grade  = norm_book.get("liquidity_grade", "F"),
        has_history      = False,
        category         = category,
        settlement_risk  = sr["settlement_risk"],
        adjusted_edge    = ev.get("adjusted_edge"),
    )

    # ── Fetch market metadata ─────────────────────────────────────────────────
    market_meta = {}
    if use_live:
        status = kalshi_client.safe_get_market(ticker)
        if status and not status.get("error"):
            m = status.get("market") or status
            market_meta = {
                "status":     m.get("status"),
                "close_time": m.get("close_time"),
                "volume":     m.get("volume"),
                "last_price": m.get("last_price"),
            }

    return jsonify({
        "ticker":              ticker,
        "side":                side,
        "model_probability":   model_prob,
        "current_price":       norm_book.get("best_yes_ask") if side == "YES" else norm_book.get("best_no_ask"),
        "raw_edge":            ev.get("raw_edge"),
        "adjusted_edge":       ev.get("adjusted_edge"),
        "max_playable_price":  ev.get("max_playable_price"),
        "liquidity_grade":     norm_book.get("liquidity_grade"),
        "settlement_risk":     sr["settlement_risk"],
        "settlement_grade":    sr["resolution_clarity_grade"],
        "market_bucket":       bucket["market_bucket"],
        "label":               ev["label"],
        "execution":           ev.get("execution"),
        "blocking_reasons":    ev.get("blocking_reasons", []) + bucket.get("rationale", []),
        "warnings":            ev.get("warnings", []),
        "fee_detail":          ev.get("fee_detail", {}),
        "orderbook":           norm_book,
        "settlement_detail":   sr,
        "market_meta":         market_meta,
        "can_approve_bets":    False,
    }), 200


@app.route("/kalshi/scan-event", methods=["POST"])
@require_api_key
def kalshi_scan_event():
    """
    Scan all markets in a Kalshi event and return a summary evaluation.

    POST body (JSON):
      {
        event_ticker:       str
        model_probabilities: { "TICKER-1": 0.62, "TICKER-2": 0.48, ... }  (optional)
        category:           str   default "sports"
        resolution_source:  str | null
      }

    Returns: list of contract summaries (labels, edges, settlement grades).
    """
    from kalshi_engine import kalshi_client, orderbook_normalizer, edge_engine
    from kalshi_engine import settlement_risk, market_buckets

    body         = request.get_json(silent=True) or {}
    event_ticker = (body.get("event_ticker") or "").strip()
    if not event_ticker:
        return jsonify({"ok": False, "error": "event_ticker is required", "can_approve_bets": False}), 400

    model_probs    = body.get("model_probabilities") or {}
    category       = (body.get("category") or "sports").lower()
    res_source     = body.get("resolution_source")

    try:
        markets = kalshi_client.scan_event_markets(event_ticker)
    except Exception as exc:
        return jsonify({
            "ok": False, "error": str(exc), "can_approve_bets": False
        }), 200

    results = []
    for m in markets[:50]:  # cap at 50 contracts per scan
        t     = m.get("ticker") or ""
        mp    = model_probs.get(t)
        raw_b = kalshi_client.safe_get_orderbook(t)
        if raw_b and not raw_b.get("error"):
            norm_b = orderbook_normalizer.normalize(raw_b, ticker=t)
        else:
            norm_b = {"liquidity_grade": "F"}

        # Gate 1: price must be available before checking model probability.
        # Missing/empty orderbook → DATA_UNOBTAINABLE, not UNCALIBRATED.
        _nb_bid = norm_b.get("best_yes_bid")
        _nb_ask = norm_b.get("best_yes_ask")
        _price_active = (
            (_nb_bid is not None and _nb_bid > 0.0) or
            (_nb_ask is not None and _nb_ask < 1.0)
        )
        if not (raw_b and not raw_b.get("error")) or not _price_active:
            ev = {"label": "KALSHI_DATA_UNOBTAINABLE", "adjusted_edge": None}
        elif mp:
            ev = edge_engine.evaluate(mp, norm_b, category)
        else:
            ev = {"label": "KALSHI_REJECT_UNCALIBRATED", "adjusted_edge": None}

        sr = settlement_risk.grade_contract(
            title=t, settlement_condition=m.get("subtitle"),
            resolution_source=res_source, category=category, contract_ticker=t
        )
        results.append({
            "ticker":           t,
            "title":            m.get("title") or m.get("subtitle") or t,
            "label":            ev["label"],
            "adjusted_edge":    ev.get("adjusted_edge"),
            "settlement_grade": sr["resolution_clarity_grade"],
            "liquidity_grade":  norm_b.get("liquidity_grade"),
            "yes_price":        norm_b.get("mid_price"),
            "best_yes_bid":     _nb_bid,
            "best_yes_ask":     _nb_ask,
            "price_status":     ("available" if _price_active else "missing"),
            "can_approve_bets": False,
        })

    return jsonify({
        "event_ticker":     event_ticker,
        "contracts_scanned": len(results),
        "results":          results,
        "can_approve_bets": False,
    }), 200


@app.route("/kalshi/paper-trade", methods=["POST"])
@require_api_key
def kalshi_paper_trade():
    """
    Log a paper-trade record to the kalshi_forecast_ledger.
    Validates kill switches before logging.

    POST body (JSON):
      {
        ticker:             str
        side:               "YES" | "NO"
        model_probability:  float
        entry_price:        float   (intended limit price)
        contracts:          int     default 1
        adjusted_edge:      float
        label:              str     (must be KALSHI_PLAYABLE_LIMIT_ONLY or similar)
        event_ticker:       str | null
        contract_title:     str | null
        category:           str | null
        settlement_grade:   str | null   (for guard validation)
        liquidity_grade:    str | null
        notes:              str | null
      }
    """
    from kalshi_engine import execution_guard, calibration_ledger

    body = request.get_json(silent=True) or {}

    # Validate kill switches first
    guard = execution_guard.validate_execution_request(
        label            = body.get("label", ""),
        normalized_book  = {"liquidity_grade": body.get("liquidity_grade", "F")},
        limit_price      = body.get("entry_price"),
        settlement_grade = body.get("settlement_grade"),
        contracts        = int(body.get("contracts", 1)),
        mode             = "paper",
    )

    if not guard["allowed"]:
        return jsonify({
            "ok":               False,
            "blocks":           guard["blocks"],
            "kill_switches":    guard["kill_switches"],
            "can_approve_bets": False,
        }), 400

    # Log the paper trade
    entry = {
        "market_ticker":      body.get("ticker"),
        "event_ticker":       body.get("event_ticker"),
        "contract_title":     body.get("contract_title"),
        "category":           body.get("category"),
        "side_yes_no":        (body.get("side") or "YES").upper(),
        "model_probability":  body.get("model_probability"),
        "entry_price":        body.get("entry_price"),
        "best_bid":           body.get("best_bid"),
        "best_ask":           body.get("best_ask"),
        "spread":             body.get("spread"),
        "depth_score":        body.get("liquidity_grade"),
        "adjusted_edge":      body.get("adjusted_edge"),
        "label":              body.get("label"),
        "market_bucket":      body.get("market_bucket"),
        "settlement_source":  body.get("resolution_source"),
        "settlement_grade":   body.get("settlement_grade"),
        "mode":               "paper",
        "notes":              body.get("notes"),
        "blocking_reasons":   body.get("blocking_reasons") or [],
        "warnings":           body.get("warnings") or [],
    }
    result = calibration_ledger.log_paper_trade(entry)
    status = 201 if result.get("ok") else 400
    return jsonify({**result, "can_approve_bets": False}), status


@app.route("/kalshi/ledger", methods=["GET"])
@require_api_key
def kalshi_ledger():
    """
    Query the Kalshi paper-trade / calibration ledger.

    Query params:
      limit:              int  default 50 (max 500)
      settlement_status:  "OPEN" | "SETTLED" | "VOIDED"
      ticker:             str  — filter by market_ticker
      mode:               "paper" | "live"
      summary:            bool  — include Brier score summary
    """
    from kalshi_engine import calibration_ledger

    limit  = min(int(request.args.get("limit", 50)), 500)
    status = request.args.get("settlement_status") or None
    ticker = request.args.get("ticker") or None
    mode   = request.args.get("mode") or None
    include_summary = request.args.get("summary", "false").lower() == "true"

    records = calibration_ledger.get_ledger(
        limit             = limit,
        settlement_status = status,
        market_ticker     = ticker,
        mode              = mode,
    )
    resp = {
        "records":          records,
        "count":            len(records),
        "can_approve_bets": False,
    }
    if include_summary:
        resp["calibration_summary"] = calibration_ledger.get_brier_score()

    return jsonify(resp), 200


@app.route("/kalshi/settle-result", methods=["POST"])
@require_api_key
def kalshi_settle_result():
    """
    Record the settlement outcome for a paper-trade ledger entry.

    POST body (JSON):
      {
        id:             int   — kalshi_forecast_ledger record id
        result:         "YES" | "NO" | "VOID"
        closing_price:  float | null
        net_pnl:        float | null
        clv:            float | null
        dominant_failure_tag: str | null
        notes:          str | null
        run_post_review: bool  default false  (calls Claude post-trade review)
      }
    """
    from kalshi_engine import calibration_ledger, contract_intake

    body = request.get_json(silent=True) or {}

    record_id = body.get("id")
    if not record_id:
        return jsonify({"ok": False, "error": "id is required", "can_approve_bets": False}), 400

    result = calibration_ledger.settle_result(
        record_id            = int(record_id),
        result               = body.get("result", "VOID"),
        closing_price        = body.get("closing_price"),
        net_pnl              = body.get("net_pnl"),
        clv                  = body.get("clv"),
        dominant_failure_tag = body.get("dominant_failure_tag"),
        notes                = body.get("notes"),
    )

    # Optional Claude post-trade review
    review = None
    if body.get("run_post_review") and result.get("ok"):
        # Fetch the record to get model_probability
        records = calibration_ledger.get_ledger(limit=1, market_ticker=None)
        rec = next((r for r in records if r.get("id") == record_id), {})
        if rec:
            review = contract_intake.post_trade_review(
                ticker            = rec.get("market_ticker", ""),
                model_probability = float(rec.get("model_probability") or 0.5),
                entry_price       = float(rec.get("entry_price") or 0.5),
                closing_price     = body.get("closing_price"),
                result            = body.get("result"),
                adjusted_edge     = float(rec.get("adjusted_edge") or 0),
                clv               = body.get("clv"),
                notes             = body.get("notes", ""),
            )

    return jsonify({
        **result,
        "post_trade_review": review,
        "can_approve_bets":  False,
    }), (201 if result.get("ok") else 400)


# ── WOW / Kalshi Daily High Temperature Weather Lane ─────────────────────────
#
# Verified station mapping — hardcoded from live Kalshi contract rule text.
# See WOW-PATCH-001.md + kalshi-weather-station-verification.md for audit trail.
# DO NOT substitute stations via pattern-matching or inference:
#   Chicago → KMDW (Midway), NOT KORD (O'Hare)
#   Miami   → KMIA (Miami Intl), NOT KPBI (West Palm Beach)
#   LA      → KLAX (LAX), NOT KBUR (Burbank)

_KALSHI_WEATHER_STATIONS = {
    "NYC": {
        "series":      "KXHIGHNY",
        "name":        "Central Park, New York",
        "station":     "KNYC",
        "nws_site":    "OKX",
        "nws_issuedby":"NYC",
        "tz":          "America/New_York",
        "lat":          40.7789,
        "lon":         -73.9692,
    },
    "LA": {
        "series":      "KXHIGHLAX",
        "name":        "Los Angeles Airport, CA",
        "station":     "KLAX",
        "nws_site":    "LOX",
        "nws_issuedby":"LAX",
        "tz":          "America/Los_Angeles",
        "lat":          33.9425,
        "lon":        -118.4081,
    },
    "MIA": {
        "series":      "KXHIGHMIA",
        "name":        "Miami International Airport",
        "station":     "KMIA",
        "nws_site":    "MFL",
        "nws_issuedby":"MIA",
        "tz":          "America/New_York",
        "lat":          25.7959,
        "lon":         -80.2870,
    },
    "CHI": {
        "series":      "KXHIGHCHI",
        "name":        "Chicago Midway, IL",
        "station":     "KMDW",
        "nws_site":    "LOT",
        "nws_issuedby":"MDW",
        "tz":          "America/Chicago",
        "lat":          41.7868,
        "lon":         -87.7522,
    },
    "AUS": {
        "series":      "KXHIGHAUS",
        "name":        "Austin Bergstrom, TX",
        "station":     "KAUS",
        "nws_site":    "EWX",
        "nws_issuedby":"AUS",
        "tz":          "America/Chicago",
        "lat":          30.1945,
        "lon":         -97.6699,
    },
}

# NWS gridpoint cache: city → {"gridId":..., "gridX":..., "gridY":..., "cached_at":...}
_nws_gridpoint_cache: dict = {}
_NWS_GRIDPOINT_CACHE_TTL = 86400  # 24h — gridpoints are stable

def _fetch_nws_cli(city: str) -> dict:
    """
    Fetch the NWS Daily Climate Report (CLI product) via the NWS REST API.
    Uses api.weather.gov/products (JSON), NOT the JS-rendered forecast.weather.gov page.

    Settlement source per Kalshi contract rules: NWS CLI product ONLY.
    Consumer weather apps (AccuWeather, Google, Apple) do NOT determine settlement.

    Returns:
      ok, observed_high, report_date, report_status (FINAL|PRELIMINARY|NOT_YET_ISSUED|ERROR),
      revision_risk (bool), source_url, product_id, issuance_time, raw_text
    """
    import requests, re as _re
    station = _KALSHI_WEATHER_STATIONS.get(city)
    if not station:
        return {"ok": False, "error": f"Unknown city: {city}", "observed_high": None,
                "report_status": "ERROR", "revision_risk": False}

    location = station["nws_issuedby"]  # e.g. MDW, NYC, LAX, MIA, AUS

    # Step 1: find the latest CLI product for this location (via connector 1)
    list_url = f"https://api.weather.gov/products?type=CLI&location={location}&limit=1"
    try:
        graph = _nws_get(list_url).get("@graph", [])
        if not graph:
            return {"ok": True, "observed_high": None, "report_status": "NOT_YET_ISSUED",
                    "revision_risk": False, "report_date": None, "source_url": list_url,
                    "product_id": None, "issuance_time": None, "raw_text": None}
        product_id    = graph[0]["id"]
        issuance_time = graph[0].get("issuanceTime", "")
    except Exception as exc:
        return {"ok": False, "error": f"NWS products list error: {exc}",
                "observed_high": None, "report_status": "ERROR", "revision_risk": False}

    # Step 2: fetch the product text (via connector 1)
    text_url = f"https://api.weather.gov/products/{product_id}"
    try:
        text = _nws_get(text_url).get("productText", "")
    except Exception as exc:
        return {"ok": False, "error": f"NWS product text error: {exc}",
                "observed_high": None, "report_status": "ERROR", "revision_risk": False}

    # Step 3: parse maximum temperature.
    # NWS CLI product format (consistent across offices):
    #   "  MAXIMUM         93   2:59 PM 101    1931  84      9       91"
    # The first integer after MAXIMUM is the observed high; time follows it.
    max_match = _re.search(
        r"^\s+MAXIMUM\s+(\d{1,3})\b",
        text, _re.IGNORECASE | _re.MULTILINE
    )
    observed_high = int(max_match.group(1)) if max_match else None

    is_preliminary = bool(_re.search(r"PRELIMINARY", text, _re.IGNORECASE))

    # Extract report date from "THE {CITY} CLIMATE SUMMARY FOR {MONTH} {DAY} {YEAR}"
    date_match = _re.search(
        r"CLIMATE\s+(?:SUMMARY|REPORT)\s+FOR\s+"
        r"(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|"
        r"JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|SEP(?:TEMBER)?|"
        r"OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)"
        r"\s+(\d{1,2})\s+(\d{4})",
        text, _re.IGNORECASE
    )
    report_date = (
        f"{date_match.group(1).title()} {date_match.group(2)} {date_match.group(3)}"
        if date_match else None
    )

    return {
        "ok":            True,
        "observed_high": observed_high,
        "report_date":   report_date,
        "report_status": "PRELIMINARY" if is_preliminary else (
                         "NOT_YET_ISSUED" if observed_high is None else "FINAL"),
        "revision_risk": is_preliminary,
        "source_url":    text_url,
        "product_id":    product_id,
        "issuance_time": issuance_time,
        "raw_text":      text,   # included so evaluate endpoint can surface it for audit
    }


def _fetch_nws_forecast_high(city: str, date_str: str) -> dict:
    """
    Fetch the NWS daily high temperature forecast for a given city and date.
    Uses the NWS points → gridpoint → forecast API (free, no auth).
    Returns {"forecast_high": int|None, "forecast_source": "nws_forecast"|"none", ...}
    """
    import re as _re
    station = _KALSHI_WEATHER_STATIONS.get(city)
    if not station:
        return {"forecast_high": None, "forecast_source": "none",
                "error": f"Unknown city: {city}"}

    lat, lon = station["lat"], station["lon"]
    now_ts = time.time()

    # Step 1: resolve gridpoint (cached 24h)
    cached = _nws_gridpoint_cache.get(city, {})
    if cached.get("cached_at", 0) + _NWS_GRIDPOINT_CACHE_TTL > now_ts:
        grid_id, grid_x, grid_y = cached["gridId"], cached["gridX"], cached["gridY"]
    else:
        try:
            pdata   = _nws_get(f"https://api.weather.gov/points/{lat},{lon}",
                               timeout=8).get("properties", {})
            grid_id = pdata.get("gridId")
            grid_x  = pdata.get("gridX")
            grid_y  = pdata.get("gridY")
            if not all([grid_id, grid_x, grid_y]):
                return {"forecast_high": None, "forecast_source": "none",
                        "error": "NWS points returned incomplete grid"}
            _nws_gridpoint_cache[city] = {
                "gridId": grid_id, "gridX": grid_x, "gridY": grid_y,
                "cached_at": now_ts
            }
        except Exception as exc:
            return {"forecast_high": None, "forecast_source": "none", "error": str(exc)}

    # Step 2: fetch daily forecast (via connector 1)
    try:
        fc_url  = f"https://api.weather.gov/gridpoints/{grid_id}/{grid_x},{grid_y}/forecast"
        fc_resp = _nws_get(fc_url, timeout=8)
        periods = fc_resp.get("properties", {}).get("periods", [])
        # Find the daytime period matching date_str (YYYY-MM-DD)
        for period in periods:
            start = period.get("startTime", "")
            if start.startswith(date_str) and period.get("isDaytime", False):
                return {
                    "forecast_high":   period.get("temperature"),
                    "forecast_source": "nws_forecast",
                    "period_name":     period.get("name", ""),
                }
        # No exact date match — return nearest future daytime
        for period in periods:
            if period.get("isDaytime", False):
                start = period.get("startTime", "")
                return {
                    "forecast_high":   period.get("temperature"),
                    "forecast_source": "nws_forecast_nearest",
                    "period_name":     period.get("name", ""),
                    "note":            f"nearest daytime period ({start[:10]}), not exact date match",
                }
        return {"forecast_high": None, "forecast_source": "none",
                "error": "No daytime periods in NWS forecast"}
    except Exception as exc:
        return {"forecast_high": None, "forecast_source": "none", "error": str(exc)}


# ── WEATHER PATCH-002: Gaussian bracket probability ───────────────────────────

def _gaussian_cdf(x: float, mean: float, sigma: float) -> float:
    """Φ((x − mean) / sigma) via math.erf. Returns 0 or 1 at sigma=0."""
    from math import erf, sqrt as _sqrt
    if sigma <= 0:
        return 1.0 if x >= mean else 0.0
    return 0.5 * (1.0 + erf((x - mean) / (sigma * _sqrt(2.0))))


def _parse_bracket_bounds(label: str):
    """Parse bracket label → (lo, hi) with ±inf for open ends. Returns None if unparseable."""
    import re as _re
    label = label.strip()
    m = _re.match(r"^[≤<][=]?\s*(\d+)$|^under\s+(\d+)$", label, _re.IGNORECASE)
    if m:
        return (float("-inf"), int(m.group(1) or m.group(2)))
    m = _re.match(r"^[≥>][=]?\s*(\d+)$|^over\s+(\d+)$", label, _re.IGNORECASE)
    if m:
        return (int(m.group(1) or m.group(2)), float("inf"))
    m = _re.match(r"^(\d+)\s*[–\-]\s*(\d+)$", label)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def _score_weather_brackets_gaussian(forecast_high, sigma_f: float, brackets: list) -> list:
    """PATCH-002: Score brackets using Gaussian probability distribution.

    Per spec formulas:
      closed [lo, hi]:  Φ((hi − μ) / σ) − Φ((lo − μ) / σ)
      open-low  (≤hi):  Φ((hi − μ) / σ)
      open-high (≥lo):  1 − Φ((lo − μ) / σ)

    Probabilities are normalized so the full bracket set sums to 1.00.
    """
    if forecast_high is None:
        return [{"label": b.get("label",""), "yes_price": float(b.get("yes_price",0)),
                 "model_prob": None, "raw_edge": None, "edge": None,
                 "verdict": "UNKNOWN", "parse_ok": False} for b in brackets]

    raw_probs = []
    for b in brackets:
        bounds = _parse_bracket_bounds(b.get("label",""))
        if bounds is None:
            raw_probs.append(None)
        else:
            lo, hi = bounds
            if lo == float("-inf"):
                p = _gaussian_cdf(hi, forecast_high, sigma_f)
            elif hi == float("inf"):
                p = 1.0 - _gaussian_cdf(lo, forecast_high, sigma_f)
            else:
                p = _gaussian_cdf(hi, forecast_high, sigma_f) - _gaussian_cdf(lo, forecast_high, sigma_f)
            raw_probs.append(max(0.0, min(1.0, p)))

    valid_sum = sum(p for p in raw_probs if p is not None)
    norm_probs = [None if p is None else (p / valid_sum if valid_sum > 0 else p)
                  for p in raw_probs]

    scored = []
    for i, b in enumerate(brackets):
        label     = b.get("label","")
        yes_price = float(b.get("yes_price", 0))
        no_price  = float(b.get("no_price", 1.0 - yes_price))
        model_prob = norm_probs[i]

        if model_prob is None:
            scored.append({"label": label, "yes_price": yes_price, "no_price": no_price,
                           "model_prob": None, "raw_edge": None, "edge": None,
                           "verdict": "UNKNOWN", "parse_ok": False})
            continue

        raw_edge = round(model_prob - yes_price, 4)
        verdict  = ("PLAYABLE" if raw_edge >= 0.10 else
                    "WATCH"    if raw_edge >= 0.05 else
                    "LEAN"     if raw_edge >= 0.0  else "NO_EDGE")
        scored.append({"label": label, "yes_price": yes_price, "no_price": no_price,
                       "model_prob": round(model_prob, 4), "raw_edge": raw_edge,
                       "edge": raw_edge, "verdict": verdict, "parse_ok": True})
    return scored


def _score_weather_brackets_binary(model_high, brackets: list) -> list:
    """Binary scorer for FINAL CLI mode — used only for audit/verification.
    model_prob is 1.0 or 0.0 based on observed temperature.
    PATCH-003 prevents PLAYABLE verdict from reaching terminal label when FINAL.
    """
    if model_high is None:
        return [{"label": b.get("label",""), "yes_price": float(b.get("yes_price",0)),
                 "model_prob": None, "raw_edge": None, "edge": None,
                 "verdict": "UNKNOWN", "parse_ok": False} for b in brackets]
    scored = []
    for b in brackets:
        label     = b.get("label","")
        yes_price = float(b.get("yes_price", 0))
        no_price  = float(b.get("no_price", 1.0 - yes_price))
        bounds    = _parse_bracket_bounds(label)
        if bounds is None:
            scored.append({"label": label, "yes_price": yes_price, "no_price": no_price,
                           "model_prob": None, "raw_edge": None, "edge": None,
                           "verdict": "UNKNOWN", "parse_ok": False})
            continue
        lo, hi = bounds
        in_bracket = (model_high <= hi) if lo == float("-inf") else \
                     (model_high >= lo) if hi == float("inf") else \
                     (lo <= model_high <= hi)
        model_prob = 1.0 if in_bracket else 0.0
        raw_edge   = round(model_prob - yes_price, 4)
        verdict    = ("PLAYABLE" if raw_edge >= 0.10 else
                      "WATCH"    if raw_edge >= 0.05 else
                      "LEAN"     if raw_edge >= 0.0  else "NO_EDGE")
        scored.append({"label": label, "yes_price": yes_price, "no_price": no_price,
                       "model_prob": model_prob, "raw_edge": raw_edge,
                       "edge": raw_edge, "verdict": verdict, "parse_ok": True})
    return scored


def _compute_forecast_horizon_hours(date_str: str, tz_name: str) -> float:
    """Hours from now (UTC) until midnight of event date in local timezone. ≥0."""
    import datetime as _dt, zoneinfo as _zi
    try:
        tz          = _zi.ZoneInfo(tz_name)
        now_utc     = _dt.datetime.now(_dt.timezone.utc)
        event_local = _dt.datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=0, minute=0, second=0, tzinfo=tz)
        return max(0.0, (event_local - now_utc).total_seconds() / 3600.0)
    except Exception:
        return 0.0


# ── WEATHER PATCH-003: Live Kalshi NHIGH price fetch + staleness gate ──────────

# ── WEATHER CONNECTOR LAYER ───────────────────────────────────────────────────
# All secrets are server-side only. Never expose to frontend/VITE/Custom GPT.
# NWS_USER_AGENT, NOAA_CDO_TOKEN, KALSHI_API_BASE are env vars only.
# DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS remains enforced throughout.

def _nws_user_agent() -> str:
    import os
    return os.environ.get("NWS_USER_AGENT", "WOW-v16-Kalshi-Weather contact@example.com")


def _nws_get(path_or_url: str, timeout: int = 12) -> dict:
    """Connector 1: NWS API (api.weather.gov). Free, no auth. Requires User-Agent."""
    import requests, os
    base = "https://api.weather.gov"
    url  = path_or_url if path_or_url.startswith("http") else f"{base}{path_or_url}"
    r = requests.get(url, timeout=timeout,
                     headers={"User-Agent": _nws_user_agent(),
                              "Accept": "application/geo+json"})
    r.raise_for_status()
    return r.json()


def _kalshi_public_get(path: str, params: dict | None = None, timeout: int = 12) -> dict:
    """Connector 4: Kalshi public market-data REST API. No auth needed for read endpoints.
    Uses KALSHI_API_BASE env var (default: production trade API v2).
    Orderbook: YES and NO bids only; asks reconstructed from opposite side (Kalshi spec).
    """
    import requests, os
    base = os.environ.get("KALSHI_API_BASE",
                          "https://external-api.kalshi.com/trade-api/v2")
    url  = f"{base}/{path.lstrip('/')}"
    r    = requests.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _ncei_cdo_get(endpoint: str, params: dict, timeout: int = 20) -> dict:
    """Connector 3: NOAA/NCEI CDO historical climate data.
    Requires NOAA_CDO_TOKEN secret. Returns DATA_UNOBTAINABLE if missing — never 500s.
    """
    import requests, os
    token = os.environ.get("NOAA_CDO_TOKEN", "")
    if not token:
        return {"ok": False, "source_status": "TOKEN_MISSING",
                "reason": "NOAA_CDO_TOKEN env var not set"}
    base = "https://www.ncei.noaa.gov/cdo-web/api/v2"
    url  = f"{base}/{endpoint.lstrip('/')}"
    try:
        r = requests.get(url, headers={"token": token}, params=params, timeout=timeout)
        r.raise_for_status()
        return {"ok": True, "source_status": "RETRIEVED", "data": r.json()}
    except Exception as exc:
        return {"ok": False, "source_status": "FAILED", "reason": str(exc)}


def _open_meteo_forecast(lat: float, lon: float, timeout: int = 10) -> dict:
    """Connector 6: Open-Meteo backup/sanity-check. Free, no auth, no key.
    NEVER primary settlement source. Gated by ENABLE_OPEN_METEO=true env var.
    """
    import requests, os
    if os.environ.get("ENABLE_OPEN_METEO", "false").lower() != "true":
        return {"ok": False, "source_status": "DISABLED"}
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": lat, "longitude": lon,
                                 "daily": "temperature_2m_max",
                                 "temperature_unit": "fahrenheit",
                                 "timezone": "auto",
                                 "forecast_days": 7},
                         timeout=timeout)
        r.raise_for_status()
        return {"ok": True, "source_status": "OK", "data": r.json()}
    except Exception as exc:
        return {"ok": False, "source_status": "FAILED", "reason": str(exc)}


def _fetch_kalshi_nhigh_markets(series: str, date_str: str) -> dict:
    """Connector 4a: Fetch open NHIGH bracket markets for a series + date.
    Uses direct Kalshi public REST API (not kalshi_engine wrapper).
    Returns tickers_found, markets list, market_status, fetch_time.
    """
    import datetime as _dt
    try:
        dt         = _dt.datetime.strptime(date_str, "%Y-%m-%d")
        date_short = dt.strftime("%y%b%d").upper()   # e.g. 26JUL01
        date_digits = date_str.replace("-", "")       # e.g. 20260701
    except Exception:
        date_short = date_digits = ""

    markets = []
    fetch_error = None
    try:
        res     = _kalshi_public_get("/markets",
                                     {"series_ticker": series, "status": "open", "limit": 100})
        markets = res.get("markets") or []
    except Exception as exc:
        fetch_error = str(exc)

    # Filter to event date by matching short-date or digit-date in ticker
    date_markets = [m for m in markets if any(
        s in (m.get("ticker", "") or "") for s in [date_short, date_digits]
    )] if markets else []
    if not date_markets:
        date_markets = markets   # fall back to all returned if no date filter matches

    any_open      = any((m.get("status") or "") == "open" for m in date_markets)
    market_status = "open" if any_open else ("closed" if date_markets else None)
    tickers_found = [m.get("ticker", "") for m in date_markets]

    return {
        "ok":           bool(date_markets),
        "markets":      date_markets,
        "tickers_found": tickers_found,
        "market_status": market_status,
        "fetch_time":   _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "fetch_error":  fetch_error,
    }


def _fetch_kalshi_orderbook(ticker: str) -> dict:
    """Connector 4b: Fetch live orderbook for a Kalshi market ticker.
    Per Kalshi spec: orderbook returns YES bids and NO bids only.
    YES ask = 100 - best NO bid (in cents). NO ask = 100 - best YES bid.
    All prices converted to 0-1 float (divide by 100).
    """
    import datetime as _dt
    try:
        data       = _kalshi_public_get(f"/markets/{ticker}/orderbook")
        orderbook  = data.get("orderbook") or {}
        yes_bids   = orderbook.get("yes") or []   # [[price_cents, qty], ...]
        no_bids    = orderbook.get("no")  or []

        best_yes_bid = max((lvl[0] for lvl in yes_bids), default=None)
        best_no_bid  = max((lvl[0] for lvl in no_bids),  default=None)

        # Reconstruct asks from opposite side (Kalshi binary contract identity)
        best_yes_ask = (100 - best_no_bid)  if best_no_bid  is not None else None
        best_no_ask  = (100 - best_yes_bid) if best_yes_bid is not None else None

        spread_cents = (best_yes_ask - best_yes_bid
                        if best_yes_bid is not None and best_yes_ask is not None
                        else None)

        def c(v):
            return round(v / 100.0, 4) if v is not None else None

        return {
            "ok":                   True,
            "ticker":               ticker,
            "price_source":         "kalshi_live_orderbook",
            "orderbook_source":     "kalshi_public_market_data",
            "price_timestamp":      _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "live_orderbook_checked": True,
            "best_yes_bid":         c(best_yes_bid),
            "best_yes_ask":         c(best_yes_ask),
            "best_no_bid":          c(best_no_bid),
            "best_no_ask":          c(best_no_ask),
            "spread":               c(spread_cents),
            "yes_bid_levels":       len(yes_bids),
            "no_bid_levels":        len(no_bids),
        }
    except Exception as exc:
        return {"ok": False, "ticker": ticker, "price_source": "not_found",
                "live_orderbook_checked": True, "error": str(exc)}


def _fetch_kalshi_nhigh_prices(series: str, date_str: str, brackets: list) -> dict:
    """Fetch live Kalshi NHIGH prices via direct public REST API.
    Replaces kalshi_engine wrapper with connector-layer calls.

    Flow:
      1. Fetch open markets for series + date (connector 4a)
      2. For each bracket, match market by threshold value in subtitle/ticker
      3. Fetch orderbook for matched tickers (connector 4b) to get bid/ask
      4. Return prices_by_bracket with live yes_bid, yes_ask, spread
    """
    import datetime as _dt

    mkt_result    = _fetch_kalshi_nhigh_markets(series, date_str)
    date_markets  = mkt_result.get("markets", [])
    market_status = mkt_result.get("market_status")
    fetch_time    = mkt_result.get("fetch_time", _dt.datetime.now(_dt.timezone.utc).isoformat())
    tickers_found = mkt_result.get("tickers_found", [])

    if not date_markets:
        return {"price_source": "not_found", "live_orderbook_checked": True,
                "error": mkt_result.get("fetch_error", "No markets found"),
                "tickers_found": [], "prices_by_bracket": {},
                "market_status": market_status, "price_timestamp": None}

    # Match brackets to markets by threshold value in subtitle/ticker
    prices_by_bracket = {}
    for b in brackets:
        label  = b.get("label", "")
        bounds = _parse_bracket_bounds(label)
        if not bounds:
            continue
        lo, hi    = bounds
        threshold = int(hi) if lo == float("-inf") else int(lo)
        for m in date_markets:
            sub = ((m.get("subtitle") or m.get("title") or "")).upper()
            tkr = (m.get("ticker") or "").upper()
            if str(threshold) in sub or f"T{threshold}" in tkr:
                # Fetch live orderbook for this ticker
                ob = _fetch_kalshi_orderbook(m.get("ticker", ""))
                prices_by_bracket[label] = {
                    "ticker":      m.get("ticker"),
                    "market_status": m.get("status"),
                    "volume":      m.get("volume"),
                    "last_price":  (m.get("last_price") or 0) / 100.0
                                   if m.get("last_price") is not None else None,
                    # Live orderbook prices (preferred over search_markets snapshot)
                    "yes_bid":     ob.get("best_yes_bid"),
                    "yes_ask":     ob.get("best_yes_ask"),
                    "no_bid":      ob.get("best_no_bid"),
                    "no_ask":      ob.get("best_no_ask"),
                    "spread":      ob.get("spread"),
                    "orderbook_ok": ob.get("ok", False),
                }
                break

    any_prices = bool(prices_by_bracket)
    return {
        "price_source":         "kalshi_live_orderbook" if any_prices else "not_found",
        "price_timestamp":      fetch_time if any_prices else None,
        "market_status":        market_status,
        "live_orderbook_checked": True,
        "tickers_found":        tickers_found,
        "prices_by_bracket":    prices_by_bracket,
    }


def _apply_weather_price_gate(
    price_source: str,
    price_timestamp,
    report_status: str,
    kalshi_prices: dict,
) -> dict:
    """PATCH-003: Price-source and staleness gate.

    Returns additional response fields:
      can_trade, can_execute, execution_rule, price_age_minutes,
      adjusted_terminal_label (overrides weather scorer when set), trade_block_reason.

    KALSHI_PLAYABLE_LIMIT_ONLY is blocked unless BOTH patches pass.
    DRY_RUN_ONLY is always enforced — even kalshi_live_orderbook path never enables live execution.
    """
    import datetime as _dt
    EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

    # Compute price age
    price_age_minutes = None
    if price_timestamp:
        try:
            ts_str = price_timestamp if isinstance(price_timestamp, str) else ""
            ts = _dt.datetime.fromisoformat(ts_str.rstrip("Z")).replace(
                tzinfo=_dt.timezone.utc)
            price_age_minutes = round(
                (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds() / 60, 1)
        except Exception:
            pass

    def _block(reason, label):
        return {"can_trade": False, "can_execute": False, "execution_rule": EXECUTION_RULE,
                "price_age_minutes": price_age_minutes, "trade_block_reason": reason,
                "adjusted_terminal_label": label}

    # synthetic_test / operator_supplied → cap at KALSHI_WATCH (TF-WX-12, TF-WX-13)
    if price_source in ("synthetic_test", "operator_supplied"):
        return _block(
            f"price_source={price_source}: non-live prices; max terminal_label=KALSHI_WATCH",
            "KALSHI_WATCH")

    # FINAL CLI + no live orderbook → trading window closed (TF-WX-11)
    if report_status == "FINAL" and price_source != "kalshi_live_orderbook":
        return _block(
            "CLI FINAL — tradeable window closed or price not live. "
            "Required: kalshi_live_orderbook with market_status=open.",
            "KALSHI_DATA_UNOBTAINABLE")

    # Live orderbook not found
    if price_source == "not_found":
        return _block(
            "Live Kalshi orderbook could not be fetched (TF-WX-19)",
            "KALSHI_DATA_UNOBTAINABLE")

    # kalshi_live_orderbook path
    if price_source == "kalshi_live_orderbook":
        if price_age_minutes is not None and price_age_minutes > 10:
            return _block(f"Price stale: {price_age_minutes}m > 10m maximum",
                          "KALSHI_DATA_UNOBTAINABLE")
        if kalshi_prices.get("market_status") != "open":
            return _block(
                f"Market not open (status={kalshi_prices.get('market_status')})",
                "KALSHI_REJECT_BAD_RULES")
        # Fresh + open → still DRY_RUN_ONLY
        return {"can_trade": False, "can_execute": False, "execution_rule": EXECUTION_RULE,
                "price_age_minutes": price_age_minutes,
                "trade_block_reason": "DRY_RUN_ONLY: execution disabled per system policy",
                "adjusted_terminal_label": None}

    return _block("Unknown price_source", "KALSHI_DATA_UNOBTAINABLE")


def _weather_terminal_label_v2(
    weather_label: str, brackets_scored: list, price_gate: dict
) -> str:
    """Map internal WEATHER_* + PATCH-003 price gate to terminal KALSHI_* label.

    Terminal label set (complete):
      KALSHI_PLAYABLE_LIMIT_ONLY, KALSHI_WATCH, KALSHI_REJECT_NO_EDGE,
      KALSHI_REJECT_BAD_RULES, KALSHI_REJECT_THIN_BOOK, KALSHI_REJECT_FEE_DRAG,
      KALSHI_REJECT_UNCALIBRATED, KALSHI_DATA_UNOBTAINABLE
    """
    # PATCH-003 gate override always wins (TF-WX-12, TF-WX-18)
    if price_gate.get("adjusted_terminal_label"):
        return price_gate["adjusted_terminal_label"]

    # Internal weather label gates
    if weather_label == "WEATHER_REJECT_DATA":
        return "KALSHI_DATA_UNOBTAINABLE"
    if weather_label == "WEATHER_REJECT_SETTLEMENT":
        return "KALSHI_REJECT_BAD_RULES"
    if weather_label == "WEATHER_REJECT_UNCALIBRATED":
        return "KALSHI_REJECT_UNCALIBRATED"
    if weather_label in ("WEATHER_SCOUT", "WEATHER_WATCH"):
        return "KALSHI_WATCH"   # TF-WX-18: WATCH/SCOUT blocks PLAYABLE

    # WEATHER_MODEL_READY — bracket scores decide
    playable = any(b.get("verdict") == "PLAYABLE" for b in brackets_scored)
    watch    = any(b.get("verdict") == "WATCH"    for b in brackets_scored)
    if playable:
        return "KALSHI_PLAYABLE_LIMIT_ONLY"
    if watch:
        return "KALSHI_WATCH"
    return "KALSHI_REJECT_NO_EDGE"


# ── NCEI CDO station IDs for GHCND daily data (connector 3) ──────────────────
_NCEI_CDO_STATION_IDS = {
    "NYC": "GHCND:USW00094728",   # Central Park / KNYC
    "LA":  "GHCND:USW00023174",   # Los Angeles Airport / KLAX
    "MIA": "GHCND:USW00012839",   # Miami International / KMIA
    "CHI": "GHCND:USW00014819",   # Chicago Midway / KMDW
    "AUS": "GHCND:USW00013904",   # Austin Bergstrom / KAUS
}


@app.route("/wow/kalshi/weather/stations", methods=["GET"])
def wow_kalshi_weather_stations():
    """
    Health-check endpoint — returns the hardcoded station mapping table.
    No auth required. Use this to verify the correct stations are deployed
    (KMDW not KORD, KMIA not KPBI, KLAX not KBUR).
    """
    # Emit normalized field names alongside legacy aliases so the ChatGPT
    # Action schema and any older callers both parse cleanly.
    normalized = {}
    for city_code, s in _KALSHI_WEATHER_STATIONS.items():
        normalized[city_code] = {
            # ── normalized names (Action schema v1.4+) ──────────────────────
            "city":                    city_code,
            "series_ticker":           s["series"],
            "settlement_station_name": s["name"],
            "nws_station_code":        s["station"],
            "cli_issuedby":            s["nws_issuedby"],
            "nws_site":                s["nws_site"],
            "timezone":                s["tz"],
            "lat":                     s["lat"],
            "lon":                     s["lon"],
            "verified":                True,
            # ── legacy aliases (keep for backward-compat) ───────────────────
            "series":                  s["series"],
            "name":                    s["name"],
            "station":                 s["station"],
            "nws_issuedby":            s["nws_issuedby"],
            "tz":                      s["tz"],
        }
    return jsonify({
        "ok":             True,
        "station_count":  len(normalized),
        "count":          len(normalized),          # legacy alias
        "deploy_version": "2026-07-01-nhigh-connectors-v1",
        "execution_rule": "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
        "stations":       normalized,
    }), 200


@app.route("/wow/kalshi/weather/source-health", methods=["GET"])
def wow_kalshi_weather_source_health():
    """
    Source connectivity health check for all 6 weather data connectors.
    No auth required. Run this before weather evaluate.

    Returns per-source status: OK | FAILED | TOKEN_MISSING | DISABLED
    and execution_rule to confirm DRY_RUN_ONLY is still active.
    """
    import os as _os, datetime as _dt

    statuses = {}

    # ── Connector 1: NWS API (gridpoint test — Chicago Midway) ────────────────
    try:
        r = _nws_get("https://api.weather.gov/points/41.7868,-87.7522", timeout=8)
        statuses["nws_api"] = "OK" if r.get("properties", {}).get("gridId") else "FAILED"
    except Exception as exc:
        statuses["nws_api"] = f"FAILED ({str(exc)[:60]})"

    # ── Connector 2: NWS CLI product (Chicago Midway MDW) ─────────────────────
    try:
        r = _nws_get("https://api.weather.gov/products?type=CLI&location=MDW&limit=1",
                     timeout=8)
        graph = r.get("@graph", [])
        statuses["nws_cli"] = "OK" if graph else "OK (no product today yet)"
    except Exception as exc:
        statuses["nws_cli"] = f"FAILED ({str(exc)[:60]})"

    # ── Connector 3: NOAA/NCEI CDO ────────────────────────────────────────────
    noaa_token = _os.environ.get("NOAA_CDO_TOKEN", "")
    if not noaa_token:
        statuses["ncei_cdo"] = "TOKEN_MISSING"
    else:
        result = _ncei_cdo_get("datasets", {"limit": 1})
        if result.get("ok"):
            statuses["ncei_cdo"] = "OK"
        else:
            statuses["ncei_cdo"] = f"FAILED ({result.get('reason','')[:60]})"

    # ── Connector 4: Kalshi public market data ────────────────────────────────
    try:
        r = _kalshi_public_get("/markets",
                               {"series_ticker": "KXHIGHCHI", "status": "open", "limit": 5},
                               timeout=8)
        markets = r.get("markets", [])
        statuses["kalshi_market_data"] = f"OK ({len(markets)} KXHIGHCHI markets)"
    except Exception as exc:
        statuses["kalshi_market_data"] = f"FAILED ({str(exc)[:60]})"

    # ── Connector 5: NOMADS/NBM ───────────────────────────────────────────────
    enable_nomads = _os.environ.get("ENABLE_NOMADS_NBM", "false").lower() == "true"
    statuses["nomads_nbm"] = "DISABLED" if not enable_nomads else "ENABLED_NOT_IMPLEMENTED"

    # ── Connector 6: Open-Meteo ───────────────────────────────────────────────
    enable_om = _os.environ.get("ENABLE_OPEN_METEO", "false").lower() == "true"
    if not enable_om:
        statuses["open_meteo"] = "DISABLED"
    else:
        r = _open_meteo_forecast(41.7868, -87.7522, timeout=8)
        statuses["open_meteo"] = "OK" if r.get("ok") else f"FAILED ({r.get('reason','')[:60]})"

    all_required_ok = all(
        not statuses[k].startswith("FAILED")
        for k in ["nws_api", "nws_cli", "kalshi_market_data"]
    )

    return jsonify({
        "ok":                   all_required_ok,
        "checked_at":           _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "nws_api":              statuses["nws_api"],
        "nws_cli":              statuses["nws_cli"],
        "ncei_cdo":             statuses["ncei_cdo"],
        "kalshi_market_data":   statuses["kalshi_market_data"],
        "nomads_nbm":           statuses["nomads_nbm"],
        "open_meteo":           statuses["open_meteo"],
        "execution_rule":       "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
        "nws_user_agent_set":   bool(_os.environ.get("NWS_USER_AGENT")),
        "noaa_token_set":       bool(noaa_token),
        "kalshi_api_base":      _os.environ.get("KALSHI_API_BASE",
                                                "https://external-api.kalshi.com/trade-api/v2"),
    }), 200


@app.route("/wow/kalshi/weather/calibration", methods=["GET"])
def wow_kalshi_weather_calibration():
    """
    NOAA/NCEI CDO historical calibration endpoint (connector 3).
    Returns sigma_f recommendation based on historical TMAX vs NWS forecast error.
    If NOAA_CDO_TOKEN is missing → returns CALIBRATION_UNAVAILABLE (never 500s).

    Query params:
      city  str  — NYC / LA / MIA / CHI / AUS
      days  int  — historical window (default 365, max 730)
    """
    import datetime as _dt, re as _re

    city = (request.args.get("city") or "").strip().upper()
    if city not in _KALSHI_WEATHER_STATIONS:
        return jsonify({"ok": False, "error":
            f"Unknown city: {city}. Supported: {', '.join(sorted(_KALSHI_WEATHER_STATIONS))}"}), 400

    try:
        days = min(int(request.args.get("days", 365)), 730)
    except (ValueError, TypeError):
        days = 365

    station_ghcnd = _NCEI_CDO_STATION_IDS.get(city)
    if not station_ghcnd:
        return jsonify({"ok": False, "source_status": "CALIBRATION_UNAVAILABLE",
                        "reason": f"No NCEI CDO station ID mapped for {city}"}), 200

    end_dt   = _dt.date.today()
    start_dt = end_dt - _dt.timedelta(days=days)

    result = _ncei_cdo_get("data", {
        "datasetid":  "GHCND",
        "stationid":  station_ghcnd,
        "datatypeid": "TMAX",
        "startdate":  start_dt.isoformat(),
        "enddate":    end_dt.isoformat(),
        "limit":      1000,
        "units":      "standard",   # Fahrenheit
    })

    if not result.get("ok"):
        status = result.get("source_status", "FAILED")
        return jsonify({
            "ok":            False,
            "city":          city,
            "station_ghcnd": station_ghcnd,
            "source_status": status,
            "reason":        result.get("reason", ""),
        }), 200

    records = (result.get("data") or {}).get("results") or []
    if not records:
        return jsonify({"ok": False, "city": city, "source_status": "NO_DATA",
                        "reason": "NCEI returned 0 TMAX records for window"}), 200

    # TMAX from GHCND is in tenths of °C; units=standard converts to °F already
    # but some responses still return raw tenths. Detect by magnitude.
    tmax_values = []
    for rec in records:
        v = rec.get("value")
        if v is not None:
            v = float(v)
            # If all values look like tenths-of-C (> 300), convert
            tmax_values.append(v)

    if tmax_values and all(v > 150 for v in tmax_values):
        # Convert tenths-of-°C → °F: (v/10 * 9/5) + 32
        tmax_values = [round((v / 10.0 * 9.0 / 5.0) + 32.0, 1) for v in tmax_values]

    import statistics as _stats
    n        = len(tmax_values)
    mean_t   = _stats.mean(tmax_values)
    std_t    = _stats.stdev(tmax_values) if n > 1 else 3.5
    # sigma_f recommendation: historical daily std ≈ NWS 24h forecast MAE proxy
    # Real calibration needs paired forecast vs observed data; this is a conservative estimate
    sigma_rec = round(min(max(std_t * 0.55, 2.5), 6.0), 2)

    return jsonify({
        "ok":                  True,
        "city":                city,
        "station_id":          city,
        "station_ghcnd":       station_ghcnd,
        "historical_days":     days,
        "records_used":        n,
        "tmax_mean_f":         round(mean_t, 1),
        "tmax_std_f":          round(std_t, 2),
        "sigma_f_recommended": sigma_rec,
        "sigma_f_default":     3.5,
        "source_status":       "RETRIEVED",
        "calibration_note":    (
            "sigma_f_recommended is estimated from historical TMAX std × 0.55. "
            "True calibration requires paired NWS 24h forecast vs observed data."
        ),
    }), 200


# ── WEATHER_SCOUT logging helpers ─────────────────────────────────────────────

def _ensure_weather_scout_schema(conn):
    """Create weather_scout_log table if it does not exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS weather_scout_log (
                id              SERIAL PRIMARY KEY,
                logged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                city            TEXT NOT NULL,
                scout_date      DATE NOT NULL,
                forecast_high   INT,
                sigma_f         REAL,
                scoring_mode    TEXT,
                report_status   TEXT,
                weather_label   TEXT,
                terminal_label  TEXT,
                price_source    TEXT,
                model_high      REAL,
                model_prob_sum  REAL,
                brackets_scored JSONB,
                -- settlement fields (filled next morning)
                settled_at      TIMESTAMPTZ,
                observed_high   INT,
                brier_score     REAL,
                winning_bracket TEXT,
                settlement_notes TEXT,
                UNIQUE (city, scout_date)
            );
            CREATE INDEX IF NOT EXISTS wx_scout_city_date
                ON weather_scout_log (city, scout_date DESC);
            CREATE INDEX IF NOT EXISTS wx_scout_settled
                ON weather_scout_log (settled_at)
                WHERE settled_at IS NOT NULL;
        """)
    conn.commit()


def _log_weather_scout_row(city, scout_date, forecast_high, sigma_f,
                            scoring_mode, report_status, weather_label,
                            terminal_label, price_source, model_high,
                            model_prob_sum, brackets_scored):
    """
    Fire-and-forget: write or update a WEATHER_SCOUT row.
    Only called for gaussian_forecast mode (NOT_YET_ISSUED pre-settlement rows).
    Never raises — DB failures are logged to stderr, never propagate to the caller.
    ON CONFLICT updates forecast data in case of re-evaluation with updated NWS forecast.
    """
    import json as _json, sys as _sys
    try:
        import psycopg2 as _pg
        conn = get_db_conn()
        try:
            _ensure_weather_scout_schema(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO weather_scout_log
                        (city, scout_date, forecast_high, sigma_f, scoring_mode,
                         report_status, weather_label, terminal_label, price_source,
                         model_high, model_prob_sum, brackets_scored)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (city, scout_date) DO UPDATE SET
                        forecast_high  = EXCLUDED.forecast_high,
                        sigma_f        = EXCLUDED.sigma_f,
                        scoring_mode   = EXCLUDED.scoring_mode,
                        report_status  = EXCLUDED.report_status,
                        weather_label  = EXCLUDED.weather_label,
                        terminal_label = EXCLUDED.terminal_label,
                        price_source   = EXCLUDED.price_source,
                        model_high     = EXCLUDED.model_high,
                        model_prob_sum = EXCLUDED.model_prob_sum,
                        brackets_scored = EXCLUDED.brackets_scored,
                        logged_at      = NOW()
                    WHERE weather_scout_log.settled_at IS NULL
                """, (
                    city, scout_date, forecast_high, sigma_f, scoring_mode,
                    report_status, weather_label, terminal_label, price_source,
                    model_high, model_prob_sum,
                    _json.dumps(brackets_scored),
                ))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[WEATHER_SCOUT] log error (non-fatal): {exc}", file=_sys.stderr)


def _compute_brier_score(brackets_scored, observed_high):
    """
    Brier score for a single weather scout row.
    BS = Σ (model_prob_i − outcome_i)²   where outcome_i ∈ {0, 1}
    Lower = better; perfect forecast = 0.0.

    Bracket label parsing: supports ≤N, ≥N, N-M formats.
    Returns None if no brackets can be matched.
    """
    import re as _re

    def _in_bracket(label, temp):
        """Return True if temp falls inside label's range."""
        label = label.strip()
        m = _re.match(r"^[≤<=]+\s*(\d+(?:\.\d+)?)$", label)
        if m:
            return temp <= float(m.group(1))
        m = _re.match(r"^[≥>=]+\s*(\d+(?:\.\d+)?)$", label)
        if m:
            return temp >= float(m.group(1))
        m = _re.match(r"^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)$", label)
        if m:
            return float(m.group(1)) <= temp <= float(m.group(2))
        return False

    bs = 0.0
    matched = False
    for b in (brackets_scored or []):
        p  = b.get("model_prob", 0.0) or 0.0
        o  = 1.0 if _in_bracket(b.get("label", ""), observed_high) else 0.0
        bs += (p - o) ** 2
        if o == 1.0:
            matched = True

    return round(bs, 6) if matched else None


@app.route("/wow/kalshi/weather/scout/log", methods=["GET"])
def wow_kalshi_weather_scout_log():
    """
    Return WEATHER_SCOUT ledger — all logged pre-settlement rows and
    settled rows with Brier scores.

    Query params:
      city    str  — filter by city (optional)
      limit   int  — max rows (default 60, max 200)
      settled bool — "true" to show only settled; "false" for unsettled only
    """
    import psycopg2.extras as _pgx

    city_filter = (request.args.get("city") or "").strip().upper() or None
    try:
        limit = min(int(request.args.get("limit", 60)), 200)
    except (ValueError, TypeError):
        limit = 60
    settled_filter = request.args.get("settled", "").strip().lower()

    try:
        conn = get_db_conn()
        try:
            _ensure_weather_scout_schema(conn)
            clauses = []
            params  = []
            if city_filter:
                clauses.append("city = %s")
                params.append(city_filter)
            if settled_filter == "true":
                clauses.append("settled_at IS NOT NULL")
            elif settled_filter == "false":
                clauses.append("settled_at IS NULL")
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT id, logged_at, city, scout_date, forecast_high,
                           sigma_f, scoring_mode, report_status, weather_label,
                           terminal_label, price_source, model_high, model_prob_sum,
                           settled_at, observed_high, brier_score, winning_bracket,
                           settlement_notes, brackets_scored
                    FROM weather_scout_log
                    {where}
                    ORDER BY scout_date DESC, city
                    LIMIT %s
                """, params + [limit])
                rows = [dict(r) for r in cur.fetchall()]
                # Cast dates to strings for JSON serialisation
                for r in rows:
                    if r.get("scout_date"):
                        r["scout_date"] = str(r["scout_date"])
                    if r.get("logged_at"):
                        r["logged_at"] = r["logged_at"].isoformat()
                    if r.get("settled_at"):
                        r["settled_at"] = r["settled_at"].isoformat()
        finally:
            conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    # Compute aggregate Brier summary for settled rows in result set
    settled = [r for r in rows if r.get("brier_score") is not None]
    brier_mean = (
        round(sum(r["brier_score"] for r in settled) / len(settled), 4)
        if settled else None
    )

    return jsonify({
        "ok":                True,
        "total_rows":        len(rows),
        "settled_count":     len(settled),
        "unsettled_count":   len(rows) - len(settled),
        "brier_mean":        brier_mean,
        "milestone_1_target": 25,
        "milestone_1_ready": len(settled) >= 25,
        "rows":              rows,
        "execution_rule":    "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
    }), 200


@app.route("/wow/kalshi/weather/scout/settle", methods=["POST"])
@require_api_key
def wow_kalshi_weather_scout_settle():
    """
    Settle a WEATHER_SCOUT row with the observed FINAL CLI high.
    Call this the morning after a scout date once NWS has issued the CLI.

    Body:
      city          str  — NYC / LA / MIA / CHI / AUS
      date          str  — YYYY-MM-DD (the scout date to settle)
      observed_high int  — optional override; omit to auto-fetch from NWS CLI
      notes         str  — optional settlement notes

    On success: updates the row with observed_high, brier_score, winning_bracket,
    settled_at. Returns the updated row.
    """
    import psycopg2.extras as _pgx, json as _json

    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON body"}), 400

    city     = (body.get("city") or "").strip().upper()
    date_str = (body.get("date") or "").strip()
    notes    = (body.get("notes") or "").strip() or None

    if city not in _KALSHI_WEATHER_STATIONS:
        return jsonify({"ok": False, "error":
            f"Unknown city: {city}. Supported: {', '.join(sorted(_KALSHI_WEATHER_STATIONS))}"}), 400
    if not date_str:
        return jsonify({"ok": False, "error": "date required (YYYY-MM-DD)"}), 400

    # ── Resolve observed_high ──────────────────────────────────────────────────
    observed_high_override = body.get("observed_high")
    if observed_high_override is not None:
        try:
            observed_high = int(observed_high_override)
            obs_source    = "override"
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "observed_high must be an integer"}), 400
    else:
        cli = _fetch_nws_cli(city)
        if not cli.get("ok") or cli.get("observed_high") is None:
            return jsonify({
                "ok":    False,
                "error": "NWS CLI has not yet issued a FINAL report for this city/date. "
                         "Try again after the morning CLI, or supply observed_high manually.",
                "cli_report_status": cli.get("report_status"),
                "cli_issuance_time": cli.get("issuance_time"),
            }), 422
        # Guard: only accept CLI if issuance date matches scout date
        cli_date = (cli.get("issuance_time") or "")[:10]
        if cli_date and cli_date != date_str:
            return jsonify({
                "ok":    False,
                "error": f"NWS CLI date mismatch: CLI is for {cli_date}, "
                         f"but you are settling {date_str}. "
                         "Supply observed_high manually if the CLI is for a different date.",
                "cli_date":   cli_date,
                "scout_date": date_str,
            }), 422
        observed_high = cli["observed_high"]
        obs_source    = "nws_cli"

    # ── Fetch the scout row ────────────────────────────────────────────────────
    try:
        conn = get_db_conn()
        try:
            _ensure_weather_scout_schema(conn)
            with conn.cursor(cursor_factory=_pgx.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM weather_scout_log
                    WHERE city = %s AND scout_date = %s
                """, (city, date_str))
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"DB read error: {exc}"}), 500

    if not row:
        return jsonify({
            "ok":    False,
            "error": f"No scout row found for {city} on {date_str}. "
                     "Run /wow/kalshi/weather/evaluate first to create it.",
        }), 404

    row = dict(row)
    if row.get("settled_at"):
        return jsonify({
            "ok":               False,
            "error":            f"Row for {city} {date_str} is already settled.",
            "settled_at":       row["settled_at"].isoformat(),
            "observed_high":    row["observed_high"],
            "brier_score":      row["brier_score"],
        }), 409

    # ── Compute Brier score ────────────────────────────────────────────────────
    brackets_scored = row.get("brackets_scored") or []
    if isinstance(brackets_scored, str):
        brackets_scored = _json.loads(brackets_scored)

    brier = _compute_brier_score(brackets_scored, observed_high)

    # Determine winning bracket label
    winning_bracket = None
    for b in brackets_scored:
        lbl = b.get("label", "")
        p   = b.get("model_prob", 0)
        # Re-use Brier helper's bracket test
        import re as _re
        def _in_bracket(label, temp):
            label = label.strip()
            m = _re.match(r"^[≤<=]+\s*(\d+(?:\.\d+)?)$", label)
            if m: return temp <= float(m.group(1))
            m = _re.match(r"^[≥>=]+\s*(\d+(?:\.\d+)?)$", label)
            if m: return temp >= float(m.group(1))
            m = _re.match(r"^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)$", label)
            if m: return float(m.group(1)) <= temp <= float(m.group(2))
            return False
        if _in_bracket(lbl, observed_high):
            winning_bracket = lbl
            break

    # ── Update the row ─────────────────────────────────────────────────────────
    try:
        conn = get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE weather_scout_log
                    SET settled_at       = NOW(),
                        observed_high    = %s,
                        brier_score      = %s,
                        winning_bracket  = %s,
                        settlement_notes = %s
                    WHERE city = %s AND scout_date = %s
                """, (observed_high, brier, winning_bracket, notes, city, date_str))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"DB write error: {exc}"}), 500

    return jsonify({
        "ok":             True,
        "city":           city,
        "scout_date":     date_str,
        "observed_high":  observed_high,
        "observed_source": obs_source,
        "winning_bracket": winning_bracket,
        "brier_score":    brier,
        "brier_note":     (
            "Brier score = Σ(model_prob_i − outcome_i)². "
            "Range 0–2; lower is better; perfect = 0.0. "
            "Below 0.25 is good for a 6-bracket distribution."
        ),
        "settlement_notes": notes,
        "execution_rule": "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
    }), 200


@app.route("/wow/kalshi/weather/evaluate", methods=["POST"])
@require_api_key
def wow_kalshi_weather_evaluate():
    """
    Score Kalshi NHIGH bracket markets — PATCH-002 + PATCH-003 combined.

    Body:
      city         str   — NYC / LA / MIA / CHI / AUS
      date         str   — YYYY-MM-DD
      brackets     list  — [{label, yes_price, no_price?, ticker?}]
      sigma_f      float — Gaussian sigma in °F (default 3.5; TF-WX-16)
      price_source str   — "synthetic_test" | "operator_supplied" (omit to attempt live fetch)

    Scoring:
      - Pre-settlement: Gaussian Φ-based probabilities (PATCH-002)
      - FINAL CLI:      binary scorer for audit, PATCH-003 gate blocks PLAYABLE
      - Live Kalshi orderbook fetched automatically unless price_source overridden
      - KALSHI_PLAYABLE_LIMIT_ONLY only reachable when BOTH patches pass

    Execution: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS always enforced.
    """
    import re as _re, datetime as _dt, zoneinfo as _zi

    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON body"}), 400

    city              = (body.get("city") or "").strip().upper()
    date_str          = (body.get("date") or "").strip()
    brackets          = body.get("brackets") or []
    sigma_f           = float(body.get("sigma_f", 3.5))
    price_source_req  = (body.get("price_source") or "").strip().lower() or None

    # Validate
    if city not in _KALSHI_WEATHER_STATIONS:
        return jsonify({"ok": False, "error":
            f"Unknown city: {city}. Supported: {', '.join(sorted(_KALSHI_WEATHER_STATIONS))}"}), 400
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return jsonify({"ok": False, "error": "date must be YYYY-MM-DD"}), 400
    if not isinstance(brackets, list) or len(brackets) == 0:
        return jsonify({"ok": False, "error": "brackets must be a non-empty list"}), 400
    if sigma_f <= 0 or sigma_f > 20:
        return jsonify({"ok": False, "error": "sigma_f must be in (0, 20]"}), 400

    station = _KALSHI_WEATHER_STATIONS[city]

    # ── Step 1: NWS data ──────────────────────────────────────────────────────
    cli_result      = _fetch_nws_cli(city)
    fc_result       = _fetch_nws_forecast_high(city, date_str)

    observed_high   = cli_result.get("observed_high")
    report_status   = cli_result.get("report_status", "ERROR")
    revision_risk   = cli_result.get("revision_risk", False)
    forecast_high   = fc_result.get("forecast_high")
    forecast_source = fc_result.get("forecast_source", "none")

    # Reject CLI data that is for a different calendar date than requested.
    # The NWS API always returns the latest issued CLI; if the user is scoring
    # a future (or past) date we must not treat a mismatched CLI as FINAL.
    cli_issuance = cli_result.get("issuance_time") or ""
    if observed_high is not None and cli_issuance:
        try:
            cli_date = cli_issuance[:10]   # "YYYY-MM-DD"
            if cli_date != date_str:
                observed_high = None
                report_status = "NOT_YET_ISSUED"
                revision_risk = False
        except Exception:
            pass

    # ── Step 2: Forecast horizon (PATCH-002) ──────────────────────────────────
    horizon_hours = _compute_forecast_horizon_hours(date_str, station["tz"])

    # ── Step 3: Live Kalshi prices (PATCH-003) ────────────────────────────────
    # Explicit price_source override → skip live fetch
    if price_source_req in ("synthetic_test", "operator_supplied"):
        kalshi_prices  = {"price_source": price_source_req, "price_timestamp": None,
                          "market_status": None, "live_orderbook_checked": False,
                          "tickers_found": [], "prices_by_bracket": {}}
        effective_price_source = price_source_req
    else:
        kalshi_prices  = _fetch_kalshi_nhigh_prices(station["series"], date_str, brackets)
        effective_price_source = kalshi_prices.get("price_source", "not_found")

    # If user didn't override and live fetch failed, fall back based on yes_price
    if effective_price_source == "not_found":
        has_operator_prices = any(b.get("yes_price") is not None for b in brackets)
        effective_price_source = "operator_supplied" if has_operator_prices else "not_found"
        kalshi_prices["price_source"] = effective_price_source

    # ── Step 4: Mutual exclusivity ────────────────────────────────────────────
    yes_sum = sum(float(b.get("yes_price", 0)) for b in brackets)
    mutual_exclusivity_ok = abs(yes_sum - 1.0) <= 0.05

    # ── Step 5: WEATHER_* internal label (PATCH-002 confidence rules) ─────────
    if not cli_result.get("ok") and not forecast_high:
        weather_label = "WEATHER_REJECT_DATA"
    elif not mutual_exclusivity_ok:
        weather_label = "WEATHER_REJECT_SETTLEMENT"
    elif observed_high is not None:
        # FINAL or PRELIMINARY CLI — binary mode, PATCH-003 gates the terminal label
        weather_label = "WEATHER_MODEL_READY"
    elif forecast_high is not None:
        # Pre-settlement — PATCH-002 Gaussian confidence rules
        if horizon_hours <= 24 and sigma_f < 4.5:
            weather_label = "WEATHER_MODEL_READY"
        elif horizon_hours <= 48:
            weather_label = "WEATHER_WATCH"
        else:
            weather_label = "WEATHER_SCOUT"
    else:
        weather_label = "WEATHER_REJECT_DATA"

    # ── Step 6: Bracket scoring ───────────────────────────────────────────────
    if observed_high is not None:
        # FINAL/PRELIMINARY CLI: binary scorer (exact known temp)
        brackets_scored = _score_weather_brackets_binary(observed_high, brackets)
        scoring_mode    = "binary_final_cli"
        model_high      = observed_high
        data_sources    = ["nws_cli"]
    elif forecast_high is not None:
        # Pre-settlement: Gaussian scorer (PATCH-002)
        brackets_scored = _score_weather_brackets_gaussian(forecast_high, sigma_f, brackets)
        scoring_mode    = "gaussian_forecast"
        model_high      = forecast_high
        data_sources    = [forecast_source]
    else:
        brackets_scored = _score_weather_brackets_gaussian(None, sigma_f, brackets)
        scoring_mode    = "no_data"
        model_high      = None
        data_sources    = []

    # Merge live orderbook prices into scored brackets (live yes_ask overrides yes_price)
    live_prices = kalshi_prices.get("prices_by_bracket", {})
    if live_prices:
        for b in brackets_scored:
            lp = live_prices.get(b.get("label"))
            if lp and lp.get("yes_ask") is not None:
                live_yes = float(lp["yes_ask"]) / 100.0 \
                    if float(lp["yes_ask"]) > 1.0 else float(lp["yes_ask"])
                # Recompute edge against live price
                mp = b.get("model_prob")
                if mp is not None:
                    b["live_yes_ask"]  = round(live_yes, 4)
                    b["live_raw_edge"] = round(mp - live_yes, 4)
                b["live_ticker"]   = lp.get("ticker")

    # ── Step 7: PATCH-003 price gate ─────────────────────────────────────────
    price_gate = _apply_weather_price_gate(
        effective_price_source,
        kalshi_prices.get("price_timestamp"),
        report_status,
        kalshi_prices,
    )

    # ── Step 8: Terminal label ────────────────────────────────────────────────
    terminal_label = _weather_terminal_label_v2(weather_label, brackets_scored, price_gate)

    # ── DST note ─────────────────────────────────────────────────────────────
    try:
        tz      = _zi.ZoneInfo(station["tz"])
        now_loc = _dt.datetime.now(tz)
        lst_dst_note = (
            f"DST active in {station['tz']} — NWS CLI uses LST window; "
            "validate forecast_timestamp alignment"
            if now_loc.dst() and now_loc.dst().total_seconds() > 0
            else f"Standard time active in {station['tz']}"
        )
    except Exception:
        lst_dst_note = "Could not determine DST status"

    # ── model_prob_sum (TF-WX-15) ─────────────────────────────────────────────
    mp_sum = round(sum(
        b["model_prob"] for b in brackets_scored if b.get("model_prob") is not None
    ), 4)

    # ── WEATHER_SCOUT auto-log (pre-settlement rows only) ─────────────────────
    # Only log gaussian_forecast mode — those are the calibration rows we need.
    # binary_final_cli rows are already settled; skip them so the Brier record
    # is not overwritten by a post-settlement re-evaluation call.
    if scoring_mode == "gaussian_forecast":
        _log_weather_scout_row(
            city          = city,
            scout_date    = date_str,
            forecast_high = forecast_high,
            sigma_f       = sigma_f,
            scoring_mode  = scoring_mode,
            report_status = report_status,
            weather_label = weather_label,
            terminal_label= terminal_label,
            price_source  = effective_price_source,
            model_high    = model_high,
            model_prob_sum= mp_sum,
            brackets_scored = brackets_scored,
        )

    return jsonify({
        # Identity
        "ok":                     True,
        "city":                   city,
        "series":                 station["series"],
        "station":                station["station"],
        "station_name":           station["name"],
        "date":                   date_str,
        # NWS data
        "observed_high":          observed_high,
        "report_status":          report_status,
        "revision_risk":          revision_risk,
        "forecast_high":          forecast_high,
        "forecast_source":        forecast_source,
        "model_high":             model_high,
        "data_sources":           data_sources,
        # PATCH-002 model
        "scoring_mode":           scoring_mode,
        "sigma_f":                sigma_f,               # TF-WX-16
        "forecast_horizon_hours": round(horizon_hours, 1),
        "weather_label":          weather_label,
        # Bracket scores
        "brackets_scored":        brackets_scored,
        "mutual_exclusivity_ok":  mutual_exclusivity_ok,
        "yes_price_sum":          round(yes_sum, 4),
        "model_prob_sum":         mp_sum,               # TF-WX-15
        # PATCH-003 gate
        "price_source":           effective_price_source,  # TF-WX-13
        "price_timestamp":        kalshi_prices.get("price_timestamp"),
        "market_status":          kalshi_prices.get("market_status"),
        "orderbook_source":       kalshi_prices.get("price_source"),
        "live_orderbook_checked": kalshi_prices.get("live_orderbook_checked", False),
        "price_age_minutes":      price_gate.get("price_age_minutes"),
        "can_trade":              price_gate["can_trade"],     # TF-WX-20
        "can_execute":            price_gate["can_execute"],
        "execution_rule":         price_gate["execution_rule"],
        "trade_block_reason":     price_gate.get("trade_block_reason"),
        # Terminal label
        "terminal_label":         terminal_label,
        # Convenience
        "lst_dst_note":           lst_dst_note,
        "settlement_warning":     (
            "NWS CLI data is PRELIMINARY — revisions before contract expiration "
            "count toward settlement; revisions after expiration do not."
            if revision_risk else None
        ),
        # Errors
        "cli_error":              cli_result.get("error") if not cli_result.get("ok") else None,
        "forecast_error":         fc_result.get("error") if not fc_result.get("forecast_high") else None,
        # Audit traceability (WOW-PATCH-001 requirement)
        "cli_product_id":         cli_result.get("product_id"),
        "cli_issuance_time":      cli_result.get("issuance_time"),
        "cli_source_url":         cli_result.get("source_url"),
        "cli_raw_text_excerpt":   (cli_result.get("raw_text") or "")[:800] or None,
    }), 200


# ── WOW / Kalshi Scan (Custom GPT entry point) ───────────────────────────────
_KALSHI_SCAN_VERSION  = "2026-06-29-mve-prefilter-v6"
_KALSHI_BUILD_TS      = "2026-06-28T00:00:00Z"   # updated on each deploy

# Multi-leg combo/slate market detector.
# These markets carry extra correlation risk and need a specialized calibrated
# model; without one they default to SCOUT so the GPT can request a probability.
#
# Design note: keep this CONSERVATIVE to avoid false positives.
#   - "MULTI" / "SERIES" in tickers often just means a multi-game match format
#     (esports BO3, MLB series) — those are still single-outcome contracts.
#   - Plain " and " in a title is ubiquitous in single-leg markets ("Will Nadal
#     and Djokovic meet?") so we don't use it alone as a signal.
#   - Only flag when there is an unambiguous multi-leg structure:
#       ticker-level: COMBO, PARLAY, SLATE, BUNDLE
#       title-level:  phrases that structurally require ≥2 independent events

# Standalone words / substrings that are definitive combo indicators in tickers
_COMBO_TICKER_TOKENS = frozenset([
    "COMBO", "PARLAY", "SLATE", "BUNDLE",
])

# Phrases that indicate ≥2 independent outcomes must all resolve.
# Each entry is checked as a substring of the lowercased title+subtitle.
_COMBO_TITLE_PHRASES = frozenset([
    "parlay",
    "combo",
    "slate",
    "bundle",
    "both teams to",
    "both players to",
    "both pitchers to",
    "both starters to",
    "both go over",
    "both go under",
    "both score over",
    "both score under",
    "both teams score",
    "to win and ",
    "and both ",
    "all three to",
    "all four to",
    "all five to",
    "at least two of",
    "at least three of",
    "any two of",
    "any three of",
])
# Minimum number of comma-separated leg tokens in the title to call it a multi-pick.
# Kalshi's KXMV multi-pick slate markets encode picks as "yes Team,yes Team,..." lists.
# Any ≥2 independent legs = combo (covers 2-leg SGP-style markets too).
_COMBO_MIN_COMMA_LEGS = 2


def _detect_market_type(m: dict) -> str:
    """Return 'combo' for genuine multi-leg / parlayed markets, else 'single'.

    Four signals, in order of confidence:

    0. KXMV market-collection prefix: KXMVESPORTSMULTIGAMEEXTENDED and
       KXMVECROSSCATEGORY are always multi-variant combo/cross-category slates.
    1. Ticker-level token: COMBO, PARLAY, SLATE, BUNDLE as a whole hyphen-segment.
    2. Title phrase match: explicit multi-leg language ("both teams to", "parlay", …).
    3. Comma-leg count: Kalshi KXMV multi-pick markets encode legs as a comma-
       separated list ("yes Dodgers,yes Phillies,…"). ≥ _COMBO_MIN_COMMA_LEGS (2)
       comma-separated tokens beginning with "yes " or "no " signal a multi-pick.
    """
    title_raw = (m.get("title", "") or "") + " " + (m.get("subtitle", "") or "")
    title_text = title_raw.lower()
    ticker_text = (" ".join([
        m.get("ticker", "") or "",
        m.get("event_ticker", "") or "",
        m.get("mve_collection_ticker", "") or "",
    ])).upper()

    # 0. KXMV multi-variant collection prefixes → always combo/cross_category
    # cross_category is a distinct type so the GPT can see it separately.
    # Both are non-single and filtered out when include_combo_markets=False.
    if "KXMVECROSSCATEGORY" in ticker_text:
        return "cross_category"
    if "KXMVESPORTSMULTIGAMEEXTENDED" in ticker_text:
        return "combo"

    # 1. Ticker tokens
    ticker_segments = set(ticker_text.replace("-", " ").split())
    if _COMBO_TICKER_TOKENS & ticker_segments:
        return "combo"

    # 2. Title phrases
    if any(phrase in title_text for phrase in _COMBO_TITLE_PHRASES):
        return "combo"

    # 3. Comma-separated multi-pick detection (≥2 yes/no legs)
    # Check subtitle alone first to avoid the title-prefix contamination bug:
    # when title="EventName" + subtitle="yes A,yes B", the concatenated
    # title_raw split produces "EventName yes A" as the first token which does
    # NOT start with "yes ", causing the first leg to be missed.
    _subtitle_only = (m.get("subtitle", "") or "")
    _sub_parts = [p.strip() for p in _subtitle_only.split(",")]
    _sub_picks = sum(
        1 for p in _sub_parts
        if p.lower().startswith("yes ") or p.lower().startswith("no ")
    )
    if _sub_picks >= _COMBO_MIN_COMMA_LEGS:
        return "combo"

    # Also check the full title_raw (handles cases where the whole title is the
    # comma-separated pick list with no separate subtitle prefix).
    parts = [p.strip() for p in title_raw.split(",")]
    pick_tokens = sum(
        1 for p in parts
        if p.lower().startswith("yes ") or p.lower().startswith("no ")
    )
    if pick_tokens >= _COMBO_MIN_COMMA_LEGS:
        return "combo"

    return "single"


@app.route("/wow/kalshi/debug-raw", methods=["GET"])
@require_api_key
def wow_kalshi_debug_raw():
    """
    Raw Kalshi inventory truth dump — no WOW gates, no classification.

    Fetches 200 open markets with NO category filter and reports how many
    have mve_collection_ticker == null (i.e. are NOT MVE combo products).

    Returns:
      total                 int   — markets returned by API
      mve_null_count        int   — markets with mve_collection_ticker == null
      mve_nonnull_count     int   — markets with mve_collection_ticker set
      mve_null_sample       list  — first 10 null-mve {ticker, title, subtitle, category}
      mve_nonnull_sample    list  — first 5 non-null {ticker, mve_collection_ticker}
      category_breakdown    dict  — {category: count} for the null-mve group
    """
    from kalshi_engine import kalshi_client
    try:
        limit = min(int(request.args.get("limit", 200)), 1000)
        raw = kalshi_client.search_markets(status="open", limit=limit)
        markets = raw.get("markets") or []

        null_mve  = [m for m in markets if not m.get("mve_collection_ticker")]
        nonnull_mve = [m for m in markets if m.get("mve_collection_ticker")]

        cat_breakdown = {}
        for m in null_mve:
            cat = m.get("category") or "unknown"
            cat_breakdown[cat] = cat_breakdown.get(cat, 0) + 1

        null_sample = [
            {
                "ticker":   m.get("ticker"),
                "title":    m.get("title") or m.get("event_title"),
                "subtitle": m.get("subtitle"),
                "category": m.get("category"),
            }
            for m in null_mve[:10]
        ]
        nonnull_sample = [
            {
                "ticker":              m.get("ticker"),
                "mve_collection_ticker": m.get("mve_collection_ticker"),
            }
            for m in nonnull_mve[:5]
        ]

        return jsonify({
            "ok":                True,
            "total":             len(markets),
            "mve_null_count":    len(null_mve),
            "mve_nonnull_count": len(nonnull_mve),
            "mve_null_sample":   null_sample,
            "mve_nonnull_sample": nonnull_sample,
            "category_breakdown": cat_breakdown,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/wow/kalshi/health", methods=["GET"])
def wow_kalshi_health():
    """
    One-call liveness check for the full Kalshi stack.

    Returns:
      flask_reachable          bool   — always True if this response arrives
      flask_pid                int    — OS pid of this worker
      flask_uptime_seconds     float  — seconds since Flask started
      kalshi_reachable         bool   — can we hit the Kalshi API?
      kalshi_open_total        int    — total open markets returned
      kalshi_mve_null_count    int    — non-combo markets open right now
      deploy_version           str    — _KALSHI_SCAN_VERSION string
      signal                   str    — "INVENTORY_READY" | "INVENTORY_EMPTY" | "KALSHI_UNREACHABLE"
    """
    from kalshi_engine import kalshi_client
    pid     = os.getpid()
    uptime  = round(time.time() - _APP_START_TIME, 1)

    try:
        raw     = kalshi_client.search_markets(status="open", limit=50)
        markets = raw.get("markets") or []
        null_ct = sum(1 for m in markets if not m.get("mve_collection_ticker"))
        signal  = "INVENTORY_READY" if null_ct > 0 else "INVENTORY_EMPTY"
        return jsonify({
            "flask_reachable":       True,
            "flask_pid":             pid,
            "flask_uptime_seconds":  uptime,
            "kalshi_reachable":      True,
            "kalshi_open_total":     len(markets),
            "kalshi_mve_null_count": null_ct,
            "deploy_version":        _KALSHI_SCAN_VERSION,
            "signal":                signal,
        })
    except Exception as e:
        return jsonify({
            "flask_reachable":      True,
            "flask_pid":            pid,
            "flask_uptime_seconds": uptime,
            "kalshi_reachable":     False,
            "kalshi_error":         str(e)[:200],
            "deploy_version":       _KALSHI_SCAN_VERSION,
            "signal":               "KALSHI_UNREACHABLE",
        }), 502


@app.route("/wow/kalshi/scan", methods=["POST", "OPTIONS"])
@app.route("/wow/kalshi/scan/", methods=["POST", "OPTIONS"])
@require_api_key
def wow_kalshi_scan():
    """
    Scan open Kalshi markets and classify each one through the WOW edge engine.

    This is the Custom GPT Action entry point.

    POST body (JSON):
      category             str    default "sports"
      sport                str    optional — free-text keyword filter on title/subtitle
      date                 str    optional — ISO date "YYYY-MM-DD" (future server filter)
      mode                 str    default "scan_only"  ("scan_only" | "evaluate_all")
      min_adjusted_edge    float  default 0.04
      max_markets          int    default 50 (max 100)
      model_probabilities  dict   ticker → model probability (0–1).
                                  Contracts not in this map:
                                    single-game → KALSHI_REJECT_UNCALIBRATED
                                    combo/slate → KALSHI_SCOUT
      include_raw_markets  bool   default false.  When true, response includes
                                  raw_markets: list of all scanned tickers + titles
                                  so the GPT can decide which to supply probabilities for.

    Returns:
      {
        markets_scanned, single_markets, combo_markets,
        playable_limit_only, final_approved, watch, scout,
        rejected_no_edge, rejected_fee_drag, rejected_thin_book,
        rejected_bad_rules, rejected_uncalibrated, data_unobtainable,
        candidates:    [ { ticker, event_title, contract_wording, market_type,
                           final_label, model_probability,
                           raw_edge, adjusted_edge,
                           fee_detail: { estimated_fee, spread_drag, slippage_drag,
                                         uncertainty_tax, total_drag },
                           entry_price, max_playable_price,
                           liquidity_grade, settlement_grade, settlement_risk,
                           market_bucket, blocking_reasons, warnings,
                           can_approve_bets: false } ],
        rejected:      [ same shape + reason: str ],
        raw_markets:   [ { ticker, event_ticker, title, subtitle, close_time,
                           market_type, has_model_prob } ]  # only when include_raw_markets
        execution_rule, request_echo
      }
    """
    # ── Kill switches ─────────────────────────────────────────────────────────
    DRY_RUN_ONLY        = True   # noqa: F841
    ALLOW_LIVE_TRADING  = False  # noqa: F841
    ALLOW_MARKET_ORDERS = False  # noqa: F841

    payload              = request.get_json(silent=True) or {}
    category             = payload.get("category", "sports")
    sport                = payload.get("sport")
    scan_date            = payload.get("date")          # ISO "YYYY-MM-DD"
    mode                 = payload.get("mode", "scan_only")
    min_adjusted_edge    = float(payload.get("min_adjusted_edge") or 0.04)
    max_markets          = min(int(payload.get("max_markets") or 50), 100)
    model_probabilities  = payload.get("model_probabilities") or {}   # ticker → float
    include_raw_markets  = bool(payload.get("include_raw_markets", False))
    # include_combo_markets: when False, combo/slate markets are silently skipped
    # market_type input: "single_game" → forces include_combo_markets=False
    _req_market_type     = payload.get("market_type", "all")   # "all"|"single_game"|"combo"
    include_combo_markets = bool(payload.get("include_combo_markets", True))
    if _req_market_type == "single_game":
        include_combo_markets = False
    elif _req_market_type == "combo":
        include_combo_markets = True   # will filter out singles below

    _stub_rejected = {
        "ticker":           None,
        "event_title":      "Kalshi scan unavailable",
        "contract_wording": "Backend route is live but Kalshi market data could not be retrieved.",
        "final_label":      "KALSHI_DATA_UNOBTAINABLE",
        "market_type":      "single",
        "reason":           "Scanner stub active. No live Kalshi rows retrieved.",
    }
    _echo = {
        "category":                     category,
        "sport":                        sport,
        "date":                         scan_date,
        "mode":                         mode,
        "min_adjusted_edge":            min_adjusted_edge,
        "max_markets":                  max_markets,
        "model_probabilities_supplied": len(model_probabilities),
        "include_raw_markets":          include_raw_markets,
        "include_combo_markets":        include_combo_markets,
        "market_type_filter":           _req_market_type,
    }

    try:
        from kalshi_engine import (
            kalshi_client,
            orderbook_normalizer,
            edge_engine,
            settlement_risk,
            market_buckets,
        )

        # ── Fetch open markets — large pool, filter down to max_markets ─────
        # Fetch 10× max_markets (min 100, max 250) so sport/date filters have
        # enough rows to work with before we slice to max_markets for evaluation.
        _RAW_FETCH_LIMIT = min(max(max_markets * 10, 100), 250)
        _kalshi_qp = {"category": category, "status": "open", "limit": _RAW_FETCH_LIMIT}
        raw = kalshi_client.search_markets(**_kalshi_qp)
        markets = raw.get("markets") or []

        # ── MVE pre-filter: use Kalshi's own mve_collection_ticker field ──────
        # Kalshi's GET /markets?category=sports returns KXMV multi-variant
        # collection markets (esports parlays, cross-category slates) even
        # though they aren't single-game sports markets.  Every KXMV market
        # carries a non-null mve_collection_ticker field in the raw API
        # response.  Drop them here before the classification loop so we never
        # pay the orderbook-fetch cost for markets we can't use.
        # When include_combo_markets=True we let them through for discovery.
        _n_raw = len(markets)
        _n_mve_dropped = 0
        if not include_combo_markets:
            _before_mve = len(markets)
            markets = [
                m for m in markets
                if not (m.get("mve_collection_ticker") or "").strip()
            ]
            _n_mve_dropped = _before_mve - len(markets)

        # ── Initialise filter_diagnostics — updated at each stage ────────────
        _diag = {
            "raw_fetched":              _n_raw,
            "dropped_by_mve_filter":    _n_mve_dropped,
            "after_mve_filter":         len(markets),
            "after_sport_filter":       len(markets),
            "after_date_filter":        len(markets),
            "after_combo_filter":       len(markets),
            "after_market_type_filter": len(markets),
            "returned":                 0,
            "skipped_sport":            0,
            "skipped_date":             0,
            "skipped_combo":            0,
            "skipped_market_type":      0,
            "kalshi_endpoint_used":     "GET /markets (search_markets)",
            "kalshi_query_params":      _kalshi_qp,
            "first_10_raw_tickers":     [m.get("ticker", "") for m in markets[:10]],
            "first_10_raw_titles":      [
                (m.get("title") or m.get("subtitle") or "")[:80]
                for m in markets[:10]
            ],
        }

        def _zero_resp(reason: str) -> dict:
            # Ensure GPT-required fields are always present even in early-exit paths
            _diag.setdefault("first_10_filtered_out_combo_tickers", [])
            _diag.setdefault("first_10_returned_tickers", [])
            _diag.setdefault("classifier_version", "kxmv-prefix+subtitle-cross-v5")
            _diag.setdefault("combo_by_prefix_count", _n_mve_dropped)
            _diag.setdefault("combo_by_subtitle_count", 0)
            _diag.setdefault("cross_category_count", 0)
            return {
                "kalshi_scan_version":              _KALSHI_SCAN_VERSION,
                "build_timestamp":                  _KALSHI_BUILD_TS,
                "terminal_label":                   "INPUT_EMPTY",
                "markets_scanned":                  0,
                "single_markets":                   0,
                "combo_markets":                    0,
                "cross_category_markets":           0,
                "skipped_type_filter":              0,
                "model_probability_testable_count": 0,
                "playable_limit_only":              0,
                "final_approved":                   0,
                "watch":                            0,
                "scout":                            0,
                "rejected_no_edge":                 0,
                "rejected_fee_drag":                0,
                "rejected_thin_book":               0,
                "rejected_bad_rules":               0,
                "rejected_uncalibrated":            0,
                "data_unobtainable":                0,
                "candidates":                       [],
                "rejected":                         [{
                    "ticker":           None,
                    "event_title":      "No markets matched filters",
                    "contract_wording": reason,
                    "final_label":      "INPUT_EMPTY",
                    "terminal_label":   "INPUT_EMPTY",
                    "market_type":      "n/a",
                    "reason":           reason,
                    "can_approve_bets": False,
                }],
                "filter_diagnostics":               _diag,
                "execution_rule":                   "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
                "request_echo":                     _echo,
            }

        if not markets:
            resp = _zero_resp(
                f"Kalshi returned 0 open markets for category={category!r}. "
                "Check that the category is valid (e.g. 'sports', 'financials')."
            )
            if include_raw_markets:
                resp["raw_markets"] = []
            return jsonify(resp), 200

        # ── Optional sport keyword filter ─────────────────────────────────────
        # Kalshi market titles use team/player names, not league abbreviations,
        # so we expand known sport abbreviations into their keyword sets.
        _SPORT_KEYWORD_MAP = {
            "mlb": {
                "runs scored", "runs", "dodgers", "yankees", "mets", "cubs",
                "red sox", "giants", "braves", "astros", "rangers", "phillies",
                "padres", "cardinals", "brewers", "pirates", "reds", "nationals",
                "marlins", "rockies", "diamondbacks", "athletics", "mariners",
                "angels", "white sox", "twins", "royals", "tigers", "guardians",
                "orioles", "rays", "blue jays", "milwaukee", "pittsburgh",
                "baltimore", "tampa bay", "toronto", "cincinnati", "cleveland",
                "detroit", "kansas city", "minnesota", "texas", "houston",
                "seattle", "oakland", "los angeles a", "chicago a", "boston",
                "miami", "colorado", "arizona", "san francisco", "san diego",
                "washington nationals", "atlanta", "philadelphia",
            },
            "nfl": {
                "touchdowns", "passing yards", "rushing yards", "field goal",
                "chiefs", "eagles", "cowboys", "patriots", "packers", "bears",
                "vikings", "lions", "buccaneers", "saints", "falcons",
                "panthers", "cardinals", "rams", "49ers", "seahawks",
                "broncos", "raiders", "chargers", "dolphins", "bills",
                "jets", "ravens", "steelers", "browns", "bengals",
                "titans", "jaguars", "colts", "texans", "commanders",
                "giants nfl", "new york giants", "new york jets",
            },
            "nba": {
                "points", "rebounds", "assists", "lakers", "celtics",
                "warriors", "bucks", "heat", "nets", "knicks", "bulls",
                "sixers", "nuggets", "suns", "clippers", "mavericks",
                "rockets", "spurs", "pelicans", "grizzlies", "jazz",
                "thunder", "blazers", "kings", "hornets", "hawks",
                "wizards", "pistons", "pacers", "cavaliers", "raptors",
                "magic", "timberwolves",
            },
            "nhl": {
                "goals", "saves", "maple leafs", "bruins", "rangers nhl",
                "penguins", "capitals", "blackhawks", "red wings",
                "flames", "canucks", "oilers", "avalanche", "blues",
                "wild", "predators", "jets nhl", "ducks", "sharks",
                "stars", "flyers", "hurricanes", "lightning", "panthers nhl",
                "senators", "sabres", "islanders", "devils", "coyotes",
                "golden knights", "kraken",
            },
            "nba playoffs": {"playoffs", "series", "conference"},
            "tennis": {
                "sets", "wimbledon", "us open", "french open", "australian open",
                "atp", "wta", "djokovic", "alcaraz", "sinner", "medvedev",
                "swiatek", "sabalenka",
            },
            "soccer": {
                "goals scored", "world cup", "premier league", "champions league",
                "la liga", "bundesliga", "serie a", "mls", "concacaf",
                "euros", "euro", "copa america",
            },
        }
        _before_sport = len(markets)
        if sport:
            kw = sport.lower()
            # Try the expanded keyword set first; fall back to literal substring
            expand_kws = _SPORT_KEYWORD_MAP.get(kw, {kw})
            def _sport_match(m: dict) -> bool:
                haystack = " ".join([
                    (m.get("title") or ""),
                    (m.get("subtitle") or ""),
                    (m.get("event_ticker") or ""),
                    (m.get("series_ticker") or ""),
                ]).lower()
                return any(ek in haystack for ek in expand_kws)
            markets = [m for m in markets if _sport_match(m)]
        _diag["skipped_sport"]      = _before_sport - len(markets)
        _diag["after_sport_filter"] = len(markets)

        # ── Optional date filter ───────────────────────────────────────────────
        # Keep markets whose close_time falls on or after scan_date (>= semantics).
        # Exact-match would silently drop everything because open markets always
        # close in the future.
        _before_date = len(markets)
        if scan_date:
            try:
                _target_date = scan_date.strip()[:10]  # "YYYY-MM-DD"
                _filtered = []
                for m in markets:
                    ct = m.get("close_time") or m.get("expiration_time") or ""
                    if ct:
                        ct_date = ct[:10]   # "YYYY-MM-DD" prefix
                        if ct_date >= _target_date:
                            _filtered.append(m)
                    else:
                        # No close_time metadata — keep the market (conservative)
                        _filtered.append(m)
                markets = _filtered
            except Exception:
                pass  # malformed date — skip filter silently
        _diag["skipped_date"]      = _before_date - len(markets)
        _diag["after_date_filter"] = len(markets)

        # Zero after filtering → return INPUT_EMPTY with diagnostics
        if not markets:
            _sport_note = (
                f" sport={sport!r} matched nothing — try omitting sport for discovery."
                if sport else ""
            )
            _date_note = (
                f" date={scan_date!r} (>= filter) removed all remaining markets."
                if scan_date and _diag["skipped_date"] == _before_date else ""
            )
            resp = _zero_resp(
                f"All markets were removed by filters.{_sport_note}{_date_note}"
            )
            if include_raw_markets:
                resp["raw_markets"] = []
            return jsonify(resp), 200

        # ── Classify market types before evaluation ───────────────────────────
        for m in markets:
            m["_market_type"] = _detect_market_type(m)

        _NON_SINGLE = ("combo", "cross_category")
        n_single = sum(1 for m in markets if m["_market_type"] == "single")
        n_combo  = sum(1 for m in markets if m["_market_type"] in _NON_SINGLE)
        n_cross  = sum(1 for m in markets if m["_market_type"] == "cross_category")

        # Snapshot ALL classified markets BEFORE the type filter strips combos.
        # raw_markets must include filtered-out combos so the GPT can see what
        # was scanned and confirm KXMV rows have is_combo_market=true.
        _all_classified = markets[:]

        _KXMV_PFX = ("KXMVESPORTSMULTIGAMEEXTENDED", "KXMVECROSSCATEGORY")
        _combo_by_prefix = sum(
            1 for m in markets if m["_market_type"] in _NON_SINGLE and any(
                pfx in (m.get("ticker") or "").upper() for pfx in _KXMV_PFX
            )
        )
        _diag["after_combo_filter"]         = len(markets)
        _diag["combo_by_prefix_count"]      = _combo_by_prefix
        _diag["combo_by_subtitle_count"]    = n_combo - _combo_by_prefix
        _diag["cross_category_count"]       = n_cross
        _diag["classifier_version"]         = "kxmv-prefix+subtitle-cross-v5"

        # ── market_type input filter: strip unwanted types before evaluation ──
        # include_combo_markets=False OR market_type="single_game" → singles only
        # market_type="combo"                                       → combos only
        # everything else                                           → all types
        _before_type = len(markets)
        n_skipped_type = 0
        if not include_combo_markets:
            markets = [m for m in markets if m["_market_type"] == "single"]
            n_skipped_type = _before_type - len(markets)
        elif _req_market_type == "combo":
            markets = [m for m in markets if m["_market_type"] in _NON_SINGLE]
            n_skipped_type = _before_type - len(markets)
        _diag["skipped_combo"]            = n_skipped_type
        _diag["skipped_market_type"]      = n_skipped_type
        _diag["after_market_type_filter"] = len(markets)
        _diag["first_10_filtered_out_combo_tickers"] = [
            m.get("ticker", "") for m in _all_classified
            if m["_market_type"] in _NON_SINGLE
        ][:10]
        _diag["first_10_returned_tickers"] = [
            m.get("ticker", "") for m in markets
        ][:10]

        # Zero after type filter — still emit raw_markets from _all_classified so
        # the GPT can confirm KXMV rows are correctly classified as combos even
        # though none pass the single_game filter.
        if not markets:
            resp = _zero_resp(
                f"All markets were removed by market_type filter "
                f"({_req_market_type!r} / include_combo_markets={include_combo_markets}). "
                f"Skipped {n_skipped_type} markets. "
                "Try market_type='all' and include_combo_markets=true for discovery."
            )
            resp["markets_scanned"]                  = n_single + n_combo
            resp["single_markets"]                   = n_single
            resp["combo_markets"]                    = n_combo
            resp["cross_category_markets"]           = n_cross
            resp["skipped_type_filter"]              = n_skipped_type
            resp["model_probability_testable_count"] = 0
            if include_raw_markets:
                resp["raw_markets"] = [
                    {
                        "ticker":          m.get("ticker", ""),
                        "event_ticker":    m.get("event_ticker", ""),
                        "series_ticker":   m.get("series_ticker", ""),
                        "title":           (m.get("title") or m.get("subtitle") or
                                            m.get("ticker", "")),
                        "subtitle":        m.get("subtitle", ""),
                        "close_time":      m.get("close_time", ""),
                        "market_type":     m["_market_type"],
                        "is_combo_market": m["_market_type"] in ("combo", "cross_category"),
                        "filtered_out_by": "market_type_filter",
                        "price_status":    "not_fetched",
                        "yes_price":       None,
                        "best_yes_bid":    None,
                        "best_yes_ask":    None,
                        "spread":          None,
                        "liquidity_grade": None,
                        "has_model_prob":  (
                            model_probabilities.get(m.get("ticker", "")) is not None
                        ),
                    }
                    for m in _all_classified
                ]
            return jsonify(resp), 200

        # ── Apply max_markets cap for evaluation (filters come first) ─────────
        _eval_markets = markets[:max_markets]
        _diag["returned"] = len(_eval_markets)

        # ── Build raw_markets from FULL filtered pool (not capped) ────────────
        # include_raw_markets shows all markets that survived filters so the GPT
        # can decide which tickers to supply model_probabilities for.
        def _cents_to_dec(v: int | float | None) -> float | None:
            return round(float(v) / 100.0, 4) if v is not None else None

        raw_markets_list = []
        if include_raw_markets:
            for m in _all_classified:   # ALL classified markets — includes filtered-out combos
                mtype = m["_market_type"]
                ticker = m.get("ticker") or ""
                # Exact + prefix model_prob lookup
                mp = model_probabilities.get(ticker)
                if mp is None:
                    tu = ticker.upper()
                    for k, v in model_probabilities.items():
                        if tu.startswith(k.upper()) or k.upper().startswith(tu):
                            mp = v
                            break
                # Snapshot prices from search_markets.
                # Standard markets: yes_bid / yes_ask / last_price in cents (0-100 int).
                # KXMV markets:     yes_bid_dollars / yes_ask_dollars / last_price_dollars
                #                   in dollars (0.0-1.0 float string); volume/OI in _fp fields.
                def _snap_float(cents_key: str, dollars_key: str) -> float | None:
                    v = m.get(cents_key)
                    if v is not None:
                        return _cents_to_dec(v)
                    d = m.get(dollars_key)
                    if d is not None:
                        try:
                            return round(float(d), 4)
                        except (ValueError, TypeError):
                            pass
                    return None

                _yb = _snap_float("yes_bid", "yes_bid_dollars")
                _ya = _snap_float("yes_ask", "yes_ask_dollars")
                _lp = _snap_float("last_price", "last_price_dollars")
                _nb = _snap_float("no_bid",  "no_bid_dollars")
                _na = _snap_float("no_ask",  "no_ask_dollars")

                # Volume / open-interest: try integer fields, then _fp string fields
                def _fp_float(int_key: str, fp_key: str) -> float | None:
                    v = m.get(int_key)
                    if v is not None:
                        return float(v)
                    d = m.get(fp_key)
                    if d is not None:
                        try:
                            return float(d)
                        except (ValueError, TypeError):
                            pass
                    return None

                _volume = _fp_float("volume", "volume_fp")
                _oi     = _fp_float("open_interest", "open_interest_fp")

                _yes_price = _lp
                _no_price  = round(1.0 - _lp, 4) if _lp is not None else None
                _spread    = round(_ya - _yb, 4) if (_ya is not None and _yb is not None) else None
                _has_snap  = (_lp is not None or _yb is not None or _ya is not None)
                # Distinguish: data fetched but all-zero liquidity vs truly missing
                _all_zero  = _has_snap and (_yb or 0.0) == 0.0 and (_ya or 0.0) == 0.0 and (_lp or 0.0) == 0.0
                _p_status  = ("zero_liquidity" if _all_zero
                              else "available"  if _has_snap
                              else "missing")
                raw_markets_list.append({
                    "ticker":          ticker,
                    "event_ticker":    m.get("event_ticker", ""),
                    "series_ticker":   m.get("series_ticker", ""),
                    "title":           m.get("title") or m.get("subtitle") or ticker,
                    "subtitle":        m.get("subtitle", ""),
                    "close_time":      m.get("close_time", ""),
                    "market_type":     mtype,
                    "is_combo_market": mtype in ("combo", "cross_category"),
                    "has_model_prob":  mp is not None,
                    # ── Prices (upgraded to orderbook after eval loop) ──────────
                    "yes_price":       _yes_price,
                    "no_price":        _no_price,
                    "best_yes_bid":    _yb,
                    "best_yes_ask":    _ya,
                    "best_no_bid":     _nb if _nb is not None else (round(1.0 - _ya, 4) if _ya is not None else None),
                    "best_no_ask":     _na if _na is not None else (round(1.0 - _yb, 4) if _yb is not None else None),
                    "spread":          _spread,
                    "volume":          _volume,
                    "open_interest":   _oi,
                    "liquidity_grade": None,   # set to orderbook grade post-eval
                    "price_source":    "market_snapshot" if _has_snap else "none",
                    "price_status":    _p_status,
                })

        # ── Counters ──────────────────────────────────────────────────────────
        counts = {
            "playable_limit_only":   0,
            "final_approved":        0,
            "watch":                 0,
            "scout":                 0,
            "rejected_no_edge":      0,
            "rejected_fee_drag":     0,
            "rejected_thin_book":    0,
            "rejected_bad_rules":    0,
            "rejected_uncalibrated": 0,
            "data_unobtainable":     0,
        }
        LABEL_MAP = {
            "KALSHI_FINAL_APPROVED":      "final_approved",
            "KALSHI_PLAYABLE_LIMIT_ONLY": "playable_limit_only",
            "KALSHI_WATCH":               "watch",
            "KALSHI_SCOUT":               "scout",
            "KALSHI_REJECT_NO_EDGE":      "rejected_no_edge",
            "KALSHI_REJECT_FEE_DRAG":     "rejected_fee_drag",
            "KALSHI_REJECT_THIN_BOOK":    "rejected_thin_book",
            "KALSHI_REJECT_BAD_RULES":    "rejected_bad_rules",
            "KALSHI_REJECT_UNCALIBRATED": "rejected_uncalibrated",
            "KALSHI_DATA_UNOBTAINABLE":   "data_unobtainable",
        }

        candidates = []
        rejected   = []
        _ob_cache: dict[str, dict] = {}   # ticker → norm_book (for raw_markets enrichment)

        for m in _eval_markets:
            ticker      = m.get("ticker") or ""
            title       = m.get("title") or m.get("subtitle") or ticker
            market_type = m["_market_type"]

            # ── Gate 1a: fetch orderbook ───────────────────────────────────────
            raw_book  = kalshi_client.safe_get_orderbook(ticker)
            _ob_err   = (raw_book.get("error") if raw_book else "orderbook fetch returned None")
            if not raw_book or raw_book.get("error"):
                # Try to extract HTTP status code from the error string
                _m = re.search(r"\b(4\d{2}|5\d{2})\b", str(_ob_err))
                _ob_http = int(_m.group(1)) if _m else None
                counts["data_unobtainable"] += 1
                rejected.append({
                    "ticker":              ticker,
                    "event_title":         title,
                    "contract_wording":    m.get("subtitle", ""),
                    "market_type":         market_type,
                    "is_combo_market":     market_type in ("combo", "cross_category"),
                    "final_label":         "KALSHI_DATA_UNOBTAINABLE",
                    "reason":              f"Orderbook fetch failed: {_ob_err}",
                    "price_status":        "missing",
                    "price_source":        "orderbook",
                    "orderbook_status":    "error",
                    "orderbook_http_status": _ob_http,
                    "price_parse_format":  "none",
                    "raw_price_keys_seen": [],
                    "price_error":         _ob_err,
                    "can_approve_bets":    False,
                })
                continue

            norm_book = orderbook_normalizer.normalize(raw_book, ticker=ticker)
            _ob_cache[ticker] = norm_book   # cache for raw_markets enrichment

            # ── Per-row price diagnostics ─────────────────────────────────────
            _PRICE_KEYS_DIAG = [
                "yes_bid", "yes_ask", "last_price", "volume", "open_interest",
                "yes_bid_dollars", "yes_ask_dollars", "last_price_dollars",
                "no_bid_dollars", "no_ask_dollars", "volume_fp", "open_interest_fp",
            ]
            _raw_price_keys = [k for k in _PRICE_KEYS_DIAG if m.get(k) is not None]
            _price_fmt = ("kxmv_dollars"    if m.get("yes_bid_dollars") is not None
                          else "standard_cents" if m.get("yes_bid")         is not None
                          else "none")
            _nb_bid  = norm_book.get("best_yes_bid")
            _nb_ask  = norm_book.get("best_yes_ask")
            _nb_mid  = norm_book.get("mid_price")
            _has_any = (_nb_bid is not None or _nb_ask is not None or _nb_mid is not None)
            # "available" = at least one side carries a real (non-zero / non-max) price
            _active  = (_has_any and
                        ((_nb_bid is not None and _nb_bid  > 0.0) or
                         (_nb_ask is not None and _nb_ask  < 1.0)))
            _row_price_status = ("available"       if _active
                                 else "zero_liquidity" if _has_any
                                 else "missing")
            _price_diag = {
                "price_status":          _row_price_status,
                "price_source":          "orderbook",
                "orderbook_status":      "success" if _active else "empty",
                "orderbook_http_status": 200,
                "price_parse_format":    _price_fmt,
                "raw_price_keys_seen":   _raw_price_keys,
                "price_error":           None,
            }

            # ── Gate 1b: price must be "available" to proceed ─────────────────
            # Missing bid/ask (zero_liquidity or missing) is a data failure,
            # not a calibration failure — label DATA_UNOBTAINABLE, not UNCALIBRATED.
            if _row_price_status != "available":
                counts["data_unobtainable"] += 1
                rejected.append({
                    "ticker":           ticker,
                    "event_title":      title,
                    "contract_wording": m.get("subtitle", ""),
                    "market_type":      market_type,
                    "is_combo_market":  market_type in ("combo", "cross_category"),
                    "final_label":      "KALSHI_DATA_UNOBTAINABLE",
                    "yes_price":        _nb_mid,
                    "best_yes_bid":     _nb_bid,
                    "best_yes_ask":     _nb_ask,
                    "spread":           norm_book.get("yes_spread"),
                    "liquidity_grade":  norm_book.get("liquidity_grade"),
                    "reason":           (
                        f"Missing contract price/orderbook: "
                        f"price_status={_row_price_status!r} "
                        f"(best_yes_bid={_nb_bid}, best_yes_ask={_nb_ask})"
                    ),
                    **_price_diag,
                    "can_approve_bets": False,
                })
                continue

            # ── Model probability lookup ──────────────────────────────────────
            model_prob = model_probabilities.get(ticker)
            if model_prob is None:
                ticker_upper = ticker.upper()
                for k, v in model_probabilities.items():
                    if ticker_upper.startswith(k.upper()) or k.upper().startswith(ticker_upper):
                        model_prob = v
                        break

            # ── Combo/slate fast-path: SCOUT when no calibrated probability ──
            # Applies to both combo and cross_category (both are multi-leg slates).
            if market_type in ("combo", "cross_category") and model_prob is None:
                counts["scout"] += 1
                rejected.append({
                    "ticker":            ticker,
                    "event_title":       title,
                    "contract_wording":  m.get("subtitle", ""),
                    "market_type":       market_type,
                    "is_combo_market":   True,
                    "final_label":       "KALSHI_SCOUT",
                    "model_probability": None,
                    "adjusted_edge":     None,
                    "raw_edge":          None,
                    "yes_price":         _nb_mid,
                    "best_yes_bid":      _nb_bid,
                    "best_yes_ask":      _nb_ask,
                    "entry_price":       _nb_ask,
                    "spread":            norm_book.get("yes_spread"),
                    "liquidity_grade":   norm_book.get("liquidity_grade"),
                    "reason":            (
                        "Multi-leg combo/slate market: requires a specialized calibrated "
                        "model_probability. Supply one in model_probabilities to evaluate."
                    ),
                    **_price_diag,
                    "can_approve_bets":  False,
                })
                continue

            # ── Gate 2: settlement grade F → KALSHI_REJECT_BAD_RULES ─────────
            sr = settlement_risk.grade_contract(
                title                = title,
                settlement_condition = m.get("subtitle"),
                resolution_source    = None,
                category             = category,
                contract_ticker      = ticker,
            )
            # Fire BAD_RULES on grade F OR settlement_risk REJECT
            # (hard-reject triggers can set settlement_risk=REJECT with non-F grade)
            if (sr["resolution_clarity_grade"] == "F" or
                    sr.get("settlement_risk") == "REJECT"):
                counts[LABEL_MAP.get("KALSHI_REJECT_BAD_RULES", "rejected_bad_rules")] += 1
                rejected.append({
                    "ticker":            ticker,
                    "event_title":       title,
                    "contract_wording":  m.get("subtitle", ""),
                    "market_type":       market_type,
                    "is_combo_market":   market_type in ("combo", "cross_category"),
                    "final_label":       "KALSHI_REJECT_BAD_RULES",
                    "model_probability": model_prob,
                    "yes_price":         _nb_mid,
                    "best_yes_bid":      _nb_bid,
                    "best_yes_ask":      _nb_ask,
                    "spread":            norm_book.get("yes_spread"),
                    "liquidity_grade":   norm_book.get("liquidity_grade"),
                    "settlement_grade":  sr["resolution_clarity_grade"],
                    "settlement_risk":   sr["settlement_risk"],
                    "reason":            (
                        "Settlement rejected: "
                        f"grade={sr['resolution_clarity_grade']} "
                        f"risk={sr.get('settlement_risk')} "
                        "(unresolvable or highly subjective contract)"
                    ),
                    **_price_diag,
                    "can_approve_bets":  False,
                })
                continue

            # ── Gate 3: liquidity grade F → KALSHI_REJECT_THIN_BOOK ──────────
            if norm_book.get("liquidity_grade") == "F":
                counts[LABEL_MAP.get("KALSHI_REJECT_THIN_BOOK", "rejected_thin_book")] += 1
                rejected.append({
                    "ticker":            ticker,
                    "event_title":       title,
                    "contract_wording":  m.get("subtitle", ""),
                    "market_type":       market_type,
                    "is_combo_market":   market_type in ("combo", "cross_category"),
                    "final_label":       "KALSHI_REJECT_THIN_BOOK",
                    "model_probability": model_prob,
                    "yes_price":         _nb_mid,
                    "best_yes_bid":      _nb_bid,
                    "best_yes_ask":      _nb_ask,
                    "spread":            norm_book.get("yes_spread"),
                    "liquidity_grade":   norm_book.get("liquidity_grade"),
                    "settlement_grade":  sr["resolution_clarity_grade"],
                    "settlement_risk":   sr["settlement_risk"],
                    "reason":            (
                        "Liquidity grade F: insufficient market depth "
                        f"(spread={norm_book.get('yes_spread')}, "
                        f"depth_within_2c={norm_book.get('depth_within_2c')})"
                    ),
                    **_price_diag,
                    "can_approve_bets":  False,
                })
                continue

            # ── Gate 4: no model probability → KALSHI_REJECT_UNCALIBRATED ────
            if model_prob is None:
                counts["rejected_uncalibrated"] += 1
                rejected.append({
                    "ticker":            ticker,
                    "event_title":       title,
                    "contract_wording":  m.get("subtitle", ""),
                    "market_type":       market_type,
                    "is_combo_market":   market_type in ("combo", "cross_category"),
                    "final_label":       "KALSHI_REJECT_UNCALIBRATED",
                    "model_probability": None,
                    "yes_price":         _nb_mid,
                    "best_yes_bid":      _nb_bid,
                    "best_yes_ask":      _nb_ask,
                    "spread":            norm_book.get("yes_spread"),
                    "liquidity_grade":   norm_book.get("liquidity_grade"),
                    "settlement_grade":  sr["resolution_clarity_grade"],
                    "settlement_risk":   sr["settlement_risk"],
                    "reason":            (
                        "No model probability supplied for this single-game contract. "
                        f"Supply model_probabilities[\"{ticker}\"] to evaluate edge."
                    ),
                    **_price_diag,
                    "can_approve_bets":  False,
                })
                continue

            # ── Gate 5: edge engine ───────────────────────────────────────────
            extra_warnings = []
            if market_type in ("combo", "cross_category") and model_prob is not None:
                extra_warnings.append(
                    "COMBO_MARKET: correlation risk not captured in single-leg edge model; "
                    "apply additional discount before acting."
                )

            ev = edge_engine.evaluate(
                model_probability = model_prob,
                normalized_book   = norm_book,
                category          = category,
                side              = "YES",
            )
            label = ev.get("label", "KALSHI_REJECT_UNCALIBRATED")

            # ── Bucket ────────────────────────────────────────────────────────
            bucket = market_buckets.classify(
                settlement_grade = sr["resolution_clarity_grade"],
                liquidity_grade  = norm_book.get("liquidity_grade", "F"),
                has_history      = False,
                category         = category,
                settlement_risk  = sr["settlement_risk"],
                adjusted_edge    = ev.get("adjusted_edge"),
            )

            counts[LABEL_MAP.get(label, "rejected_uncalibrated")] += 1

            fd = ev.get("fee_detail") or {}
            fee_breakdown = {
                "estimated_fee":   fd.get("estimated_fee"),
                "spread_drag":     fd.get("spread_drag"),
                "slippage_drag":   fd.get("slippage_drag"),
                "uncertainty_tax": fd.get("uncertainty_tax"),
                "total_drag":      fd.get("total_drag"),
            }

            row = {
                "ticker":             ticker,
                "event_title":        title,
                "contract_wording":   m.get("subtitle", ""),
                "market_type":        market_type,
                "is_combo_market":    market_type in ("combo", "cross_category"),
                "final_label":        label,
                "model_probability":  model_prob,
                "raw_edge":           ev.get("raw_edge"),
                "adjusted_edge":      ev.get("adjusted_edge"),
                "fee_detail":         fee_breakdown,
                "yes_price":          _nb_mid,
                "no_price":           round(1.0 - _nb_mid, 4) if _nb_mid is not None else None,
                "best_yes_bid":       _nb_bid,
                "best_yes_ask":       _nb_ask,
                "best_no_bid":        norm_book.get("best_no_bid"),
                "best_no_ask":        norm_book.get("best_no_ask"),
                "spread":             norm_book.get("yes_spread"),
                "entry_price":        _nb_ask,
                "max_playable_price": ev.get("max_playable_price"),
                "liquidity_grade":    norm_book.get("liquidity_grade"),
                **_price_diag,
                "settlement_grade":   sr["resolution_clarity_grade"],
                "settlement_risk":    sr["settlement_risk"],
                "market_bucket":      bucket.get("market_bucket"),
                "blocking_reasons":   (ev.get("blocking_reasons") or []) + (bucket.get("rationale") or []),
                "warnings":           (ev.get("warnings") or []) + extra_warnings,
                "can_approve_bets":   False,
            }

            adj         = ev.get("adjusted_edge")
            is_playable = label in ("KALSHI_FINAL_APPROVED", "KALSHI_PLAYABLE_LIMIT_ONLY")
            if is_playable and adj is not None and adj >= min_adjusted_edge:
                candidates.append(row)
            else:
                candidates_reason = "; ".join(row["blocking_reasons"]) if row["blocking_reasons"] else label
                rejected.append({**row, "reason": candidates_reason})

        # ── Enrich raw_markets with orderbook data for evaluated tickers ─────────
        # Upgrades market_snapshot → orderbook for tickers in _ob_cache.
        # For non-evaluated tickers the snapshot fields stay as-is.
        if include_raw_markets and _ob_cache:
            _raw_idx = {e["ticker"]: e for e in raw_markets_list}
            for tkr, nb in _ob_cache.items():
                entry = _raw_idx.get(tkr)
                if entry is None:
                    continue
                _yb2 = nb.get("best_yes_bid")
                _ya2 = nb.get("best_yes_ask")
                _mp2 = nb.get("mid_price")
                _has_ob = _yb2 is not None or _ya2 is not None

                # Always record that we tried the orderbook and the grade
                entry["price_source"]    = "orderbook"
                entry["liquidity_grade"] = nb.get("liquidity_grade")

                if _has_ob:
                    # Real orderbook levels — overwrite with full book data
                    _zero_ob = (_yb2 or 0.0) == 0.0 and (_ya2 or 0.0) == 0.0
                    entry["best_yes_bid"] = _yb2
                    entry["best_yes_ask"] = _ya2
                    entry["best_no_bid"]  = nb.get("best_no_bid")
                    entry["best_no_ask"]  = nb.get("best_no_ask")
                    entry["spread"]       = nb.get("yes_spread")
                    entry["yes_price"]    = _mp2
                    entry["no_price"]     = round(1.0 - _mp2, 4) if _mp2 is not None else None
                    entry["price_status"] = "zero_liquidity" if _zero_ob else "available"
                else:
                    # Empty orderbook — don't overwrite snapshot bid/ask values.
                    # Refine price_status to reflect what we actually have.
                    existing_status = entry.get("price_status", "missing")
                    if existing_status in ("zero_liquidity", "available"):
                        # Snapshot had values; orderbook confirms no live depth.
                        # Keep the snapshot numbers; mark as snapshot_only so the
                        # engine knows there's no live bid/ask spread to trade on.
                        entry["price_status"] = "snapshot_only"
                    else:
                        # No snapshot data either → truly no price info
                        entry["price_status"] = "missing"
                        entry["final_label_hint"] = "KALSHI_DATA_UNOBTAINABLE"

        # model_probability_testable_count: rows that cleared price+liquidity+settlement
        # and are NOT cross_category or combo — the only rows where supplying a
        # model_probability would unlock evaluation.  Must be > 0 before injecting
        # model probabilities.
        _testable = sum(
            1 for r in rejected
            if (r.get("final_label") == "KALSHI_REJECT_UNCALIBRATED" and
                not r.get("is_combo_market", False))
        ) + sum(
            1 for c in candidates
            if not c.get("is_combo_market", False)
        )

        resp = {
            "kalshi_scan_version":              _KALSHI_SCAN_VERSION,
            "build_timestamp":                  _KALSHI_BUILD_TS,
            "terminal_label":                   "SCAN_COMPLETE",
            "markets_scanned":                  n_single + n_combo,
            "single_markets":                   n_single,
            "combo_markets":                    n_combo,
            "cross_category_markets":           n_cross,
            "skipped_type_filter":              n_skipped_type,
            "model_probability_testable_count": _testable,
            **counts,
            "candidates":                       candidates,
            "rejected":                         rejected,
            "filter_diagnostics":               _diag,
            "execution_rule":                   "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
            "request_echo":                     _echo,
        }
        if include_raw_markets:
            resp["raw_markets"] = raw_markets_list
        return jsonify(resp), 200

    except Exception as exc:
        # Clean fallback — never return 500 to the GPT Action
        resp = {
            "kalshi_scan_version":   _KALSHI_SCAN_VERSION,
            "build_timestamp":       _KALSHI_BUILD_TS,
            "terminal_label":        "SCANNER_ERROR",
            "markets_scanned":       0,
            "single_markets":        0,
            "combo_markets":         0,
            "skipped_type_filter":   0,
            "playable_limit_only":   0,
            "final_approved":        0,
            "watch":                 0,
            "scout":                 0,
            "rejected_no_edge":      0,
            "rejected_fee_drag":     0,
            "rejected_thin_book":    0,
            "rejected_bad_rules":    0,
            "rejected_uncalibrated": 0,
            "data_unobtainable":     1,
            "candidates":            [],
            "rejected":              [{
                **_stub_rejected,
                "reason": f"Scanner error: {str(exc)[:300]}",
            }],
            "filter_diagnostics":    {
                "raw_fetched": 0, "after_sport_filter": 0,
                "after_date_filter": 0, "after_combo_filter": 0,
                "after_market_type_filter": 0, "returned": 0,
                "skipped_sport": 0, "skipped_date": 0,
                "skipped_combo": 0, "skipped_market_type": 0,
                "kalshi_endpoint_used": "n/a",
                "kalshi_query_params": {},
                "first_10_raw_tickers": [],
                "first_10_raw_titles": [],
                "exception": str(exc)[:300],
            },
            "execution_rule":        "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
            "request_echo":          _echo,
        }
        if include_raw_markets:
            resp["raw_markets"] = []
        return jsonify(resp), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 25643))
    app.run(host="0.0.0.0", port=port, debug=False)
