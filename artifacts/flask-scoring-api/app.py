import os
import random
import math
import threading
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

app = Flask(__name__)
CORS(app, origins="*", allow_headers=["Content-Type", "Authorization", "X-API-Key"])

DISCLAIMER = (
    "SUPPORT LAYER ONLY — This score is a statistical signal for informational "
    "analysis purposes. It cannot and does not approve, authorize, or recommend "
    "any bet or wager. All decisions remain solely with the user."
)

VALID_WINDOWS = {"L5": 5, "L10": 10}

SIDE_MAP = {
    "over":  "MORE",
    "more":  "MORE",
    "under": "LESS",
    "less":  "LESS",
}
# SQL fragments that match both aliases per normalized side
SIDE_SQL = {
    "MORE": ("(side ILIKE 'over' OR side ILIKE 'more')", []),
    "LESS": ("(side ILIKE 'under' OR side ILIKE 'less')", []),
}

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


def build_filter_clause(player=None, sport=None, prop=None, side=None, since=None):
    """
    Returns (conditions, params) for a WHERE clause.
    Apply order: since → player/sport/prop → side.
    `side` must already be normalized to 'MORE' or 'LESS'.
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
        "label": row.get("label", "Support Layer Only")
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_rf_score(features: dict, player: str, prop: str, side: str, line: float) -> float:
    seed_str = f"{player}|{prop}|{side}|{line}"
    base_seed = sum(ord(c) for c in seed_str)

    feature_sum, feature_count = 0.0, 0
    for val in features.values():
        try:
            feature_sum += float(val)
            feature_count += 1
        except (TypeError, ValueError):
            if isinstance(val, bool):
                feature_sum += 1.0 if val else 0.0
                feature_count += 1

    feature_signal = (feature_sum / feature_count) if feature_count > 0 else 0.5
    rng = random.Random(base_seed)
    noise = rng.uniform(-8, 8)
    raw = (math.tanh(feature_signal * 0.1) + 1) / 2 * 100
    return round(max(0.0, min(100.0, raw + noise)), 2)


def persist_request(player, sport, prop, side, line, score, label, game_date=None):
    from datetime import date as _date
    if game_date is None:
        game_date = _date.today().isoformat()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "player": player, "sport": sport, "prop": prop,
        "side": side, "line": line, "score": score, "label": label,
        "game_date": game_date,
    }
    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scoring_requests "
                    "(timestamp, player, sport, prop, side, line, score, label, game_date) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (entry["timestamp"], player, sport, prop, side, line, score, label, game_date)
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
              since=None, window_n=None, limit=50):
    """
    Query recent scoring records.
    Window (L5/L10) overrides the limit when set.
    """
    conn = get_db_conn()
    conditions, params = build_filter_clause(player, sport, prop, side, since)

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
                since=None, window_n=None, top_limit=10):
    """
    Aggregate stats. When window_n is set, all aggregates operate on the
    latest N filtered records via a CTE.
    `side` must be 'MORE', 'LESS', or None (already normalized).
    """
    conn = get_db_conn()
    conditions, params = build_filter_clause(player, sport, prop, side, since)
    cte_sql, cte_params, source, agg_where = build_query_source(
        conditions, params, window_n
    )
    top_n = max(1, min(int(top_limit), 100))

    over_where  = _append_where(agg_where, "(side ILIKE 'over' OR side ILIKE 'more')")
    under_where = _append_where(agg_where, "(side ILIKE 'under' OR side ILIKE 'less')")

    with conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # Overview: count, avg, max, min + over/under split
            cur.execute(
                f"{cte_sql} "
                f"SELECT COUNT(*) AS total, "
                f"ROUND(AVG(score)::numeric,2) AS avg_score, "
                f"ROUND(MAX(score)::numeric,2) AS max_score, "
                f"ROUND(MIN(score)::numeric,2) AS min_score, "
                f"COUNT(*) FILTER (WHERE side ILIKE 'over' OR side ILIKE 'more') AS over_count, "
                f"COUNT(*) FILTER (WHERE side ILIKE 'under' OR side ILIKE 'less') AS under_count, "
                f"ROUND(AVG(score) FILTER (WHERE side ILIKE 'over' OR side ILIKE 'more')::numeric, 2) AS over_avg, "
                f"ROUND(AVG(score) FILTER (WHERE side ILIKE 'under' OR side ILIKE 'less')::numeric, 2) AS under_avg "
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
                      window_n=10, limit=10, today=False):
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
        player=None, sport=sport, prop=prop, side=side, since=since
    )
    if today:
        conditions.append("game_date = CURRENT_DATE")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # ROW_NUMBER partitions per (player, sport, prop, side) combo, ordered
    # most-recent-first, so rn=1 is the latest record for that combo.
    sql = f"""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY player, sport, prop, side
                       ORDER BY timestamp DESC
                   ) AS rn
            FROM scoring_requests
            {where}
        ),
        windowed AS (
            SELECT * FROM ranked WHERE rn <= %s
        )
        SELECT
            player,
            sport,
            prop,
            side,
            COUNT(*)                                         AS record_count,
            ROUND(AVG(score)::numeric, 2)                   AS average_score,
            ROUND(MAX(score)::numeric, 2)                   AS max_score,
            ROUND(MIN(score)::numeric, 2)                   AS min_score,
            MAX(CASE WHEN rn = 1 THEN score END)            AS latest_score,
            MAX(CASE WHEN rn = 1 THEN line  END)            AS latest_line,
            MAX(timestamp)                                   AS latest_timestamp,
            JSON_AGG(score ORDER BY timestamp ASC)          AS scores
        FROM windowed
        GROUP BY player, sport, prop, side
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
    over/more → MORE, under/less → LESS. Raises ValueError on unknown values.
    """
    if not raw:
        return None
    normalized = SIDE_MAP.get(raw.strip().lower())
    if not normalized:
        raise ValueError(
            f"Invalid side '{raw}'. Accepted: over, more, under, less"
        )
    return normalized


def parse_common_filters():
    """
    Parse shared query params for /stats and /request-log.
    Returns a dict of parsed values or raises ValueError.
    Filter application order: since → player/sport/prop → side → window.
    """
    window_label, window_n = parse_window(request.args.get("window", ""))
    since_dt   = parse_since(request.args.get("since", ""))
    side_norm  = normalize_side(request.args.get("side", ""))
    return {
        "player":       request.args.get("player", "").strip() or None,
        "sport":        request.args.get("sport",  "").strip() or None,
        "prop":         request.args.get("prop",   "").strip() or None,
        "side":         side_norm,
        "since":        since_dt,
        "window_label": window_label,
        "window_n":     window_n,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
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
    raw_side = str(data["side"]).strip().upper()
    side = "MORE" if raw_side in ("MORE", "OVER") else "LESS"

    try:
        line = float(data["line"])
    except (TypeError, ValueError):
        return jsonify({"error": "'line' must be a numeric value"}), 422

    # Accept any extra keys as features (GPT analysis fields)
    reserved = set(required_fields + ["features", "game_date"])
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

    score = compute_rf_score(features, player, prop, side, line)
    label = "Support Layer Only"
    persist_request(player, sport, prop, side, line, score, label, game_date=game_date)

    if score >= 80:
        signal = "Strong Signal — high confidence edge"
    elif score >= 65:
        signal = "Solid Signal — moderate edge"
    elif score >= 50:
        signal = "Neutral Signal — slight lean"
    else:
        signal = "Weak Signal — no clear edge"

    return jsonify({
        "player": player,
        "sport": sport,
        "prop": prop,
        "side": side,
        "line": line,
        "wow_score": score,
        "signal": signal,
        "score_range": "0-100",
        "saved_to_lobby": True,
        "disclaimer": DISCLAIMER,
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
    side   = str(data["side"])
    features = data.get("features", {})

    try:
        line = float(data["line"])
    except (TypeError, ValueError):
        return jsonify({"error": "'line' must be a numeric value"}), 422

    if not isinstance(features, dict):
        return jsonify({"error": "'features' must be a JSON object (key-value pairs)"}), 422

    score = compute_rf_score(features, player, prop, side, line)
    label = "Support Layer Only"
    persist_request(player, sport, prop, side, line, score, label)

    return jsonify({
        "label": label,
        "score": score,
        "score_range": "0-100",
        "input": {
            "player": player, "sport": sport, "prop": prop,
            "side": side, "line": line,
            "features_received": len(features)
        },
        "disclaimer": DISCLAIMER,
        "can_approve_bets": False
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
            window_n=f["window_n"], limit=limit
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
            "since": f["since"].isoformat() if f["since"] else None
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
            window_n=f["window_n"], top_limit=top_limit
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
        entries = fetch_leaderboard(
            sport=sport, prop=prop, side=side_norm, since=since_dt,
            window_n=window_n, limit=limit, today=today_flag
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
            "since": since_dt.isoformat() if since_dt else None
        },
        "leaderboard": entries
    })


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


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path:
        full = os.path.join(_STATIC_DIR, path)
        if os.path.isfile(full):
            return send_from_directory(_STATIC_DIR, path)
    index = os.path.join(_STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return send_from_directory(_STATIC_DIR, "index.html")
    return jsonify({"service": "WOW Scoring API", "status": "ok", "version": "1.0.0"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 25643))
    app.run(host="0.0.0.0", port=port, debug=False)
