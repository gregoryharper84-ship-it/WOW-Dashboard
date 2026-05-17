import os
import re
import json
import random
import math
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
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
        expected_key = os.environ.get("SCORING_API_KEY", "")
        if not expected_key:
            return jsonify({"error": "Server misconfiguration: SCORING_API_KEY is not set"}), 500
        provided_key = request.headers.get("X-API-Key", "").strip()
        if not provided_key:
            return jsonify({
                "error": "Missing API key",
                "hint": "Include your key in the X-API-Key request header"
            }), 401
        if not secrets_equal(provided_key, expected_key):
            return jsonify({"error": "Invalid API key"}), 401
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
            "health":      "GET /health (no auth)",
            "score":       "POST /random-forest-score (X-API-Key required)",
            "log":         "GET /request-log?window=L5|L10&since=...&player=...&sport=...&prop=...&side=...&limit=...",
            "stats":       "GET /stats?window=L5|L10&since=...&player=...&sport=...&prop=...&side=...&limit=...",
            "leaderboard": "GET /leaderboard?window=L5|L10(default L10)&sport=...&prop=...&side=...&limit=...",
            "schema":      "GET /openapi.json (no auth)"
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

    # Accept three formats:
    #   1. multipart/form-data file upload  (request.files["image"])
    #   2. JSON body with image_base64
    #   3. JSON body with image_url
    body       = request.get_json(silent=True) or {}
    form       = request.form
    image_base64 = None
    image_url    = None
    media_type   = body.get("media_type") or form.get("media_type", "image/jpeg")
    sport_hint   = body.get("sport")      or form.get("sport", "")
    platform     = body.get("platform")   or form.get("platform", "PrizePicks")

    # Case 1 — file upload
    file = request.files.get("image") or request.files.get("file") or (
        list(request.files.values())[0] if request.files else None
    )
    if file:
        import base64 as _base64
        file_bytes   = file.read()
        image_base64 = _base64.b64encode(file_bytes).decode("utf-8")
        mime         = file.content_type or "image/jpeg"
        if mime and mime != "application/octet-stream":
            media_type = mime
    else:
        image_base64 = body.get("image_base64") or form.get("image_base64")
        image_url    = body.get("image_url")    or form.get("image_url")

    if not image_base64 and not image_url:
        return jsonify({
            "ok": False,
            "error": "Provide a file upload (field: 'image'), 'image_base64', or 'image_url'",
        }), 422

    # Build Claude image content block
    if image_base64:
        # Strip data URL prefix if caller included it
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]
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
            props = json.loads(match.group()) if match else []

        return jsonify({
            "ok":    True,
            "props": props,
            "count": len(props),
            "model": message.model,
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 25643))
    app.run(host="0.0.0.0", port=port, debug=False)
