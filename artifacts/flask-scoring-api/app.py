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
        # Try X-API-Key header first, then fall back to Authorization: Bearer <key>
        provided_key = request.headers.get("X-API-Key", "").strip()
        if not provided_key:
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                provided_key = auth[7:].strip()
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
    if not api_key:
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
        return jsonify({"ok": False, "error": "api-sports request timed out"}), 504
    except _req.exceptions.RequestException as e:
        return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
    except Exception as e:
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
    if not api_key:
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
        return jsonify({"ok": False, "error": "api-sports request timed out"}), 504
    except _req.exceptions.RequestException as e:
        return jsonify({"ok": False, "error": f"Network error: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 25643))
    app.run(host="0.0.0.0", port=port, debug=False)
