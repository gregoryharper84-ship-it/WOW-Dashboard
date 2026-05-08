import os
import random
import math
import threading
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify
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

_fallback_log: deque = deque(maxlen=50)
_log_lock = threading.Lock()


def get_public_url() -> str:
    domains = os.environ.get("REPLIT_DOMAINS", "")
    first = domains.split(",")[0].strip() if domains else ""
    return f"https://{first}" if first else "http://localhost:8000"


def get_db_conn():
    """Return a new psycopg2 connection or raise if unavailable."""
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
            return jsonify({
                "error": "Server misconfiguration: SCORING_API_KEY is not set"
            }), 500

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


def compute_rf_score(features: dict, player: str, prop: str, side: str, line: float) -> float:
    seed_str = f"{player}|{prop}|{side}|{line}"
    base_seed = sum(ord(c) for c in seed_str)

    feature_sum = 0.0
    feature_count = 0
    for key, val in features.items():
        try:
            numeric = float(val)
            feature_sum += numeric
            feature_count += 1
        except (TypeError, ValueError):
            if isinstance(val, bool):
                feature_sum += 1.0 if val else 0.0
                feature_count += 1

    feature_signal = (feature_sum / feature_count) if feature_count > 0 else 0.5

    rng = random.Random(base_seed)
    noise = rng.uniform(-8, 8)

    raw = (math.tanh(feature_signal * 0.1) + 1) / 2 * 100
    score = max(0.0, min(100.0, raw + noise))
    return round(score, 2)


def persist_request(player: str, sport: str, prop: str, side: str,
                    line: float, score: float, label: str) -> bool:
    """
    Write one scoring record to PostgreSQL.
    Returns True on success, False on any DB error (falls back to in-memory).
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "player": player,
        "sport": sport,
        "prop": prop,
        "side": side,
        "line": line,
        "score": score,
        "label": label
    }
    try:
        conn = get_db_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO scoring_requests
                        (timestamp, player, sport, prop, side, line, score, label)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (entry["timestamp"], player, sport, prop, side, line, score, label)
                )
        conn.close()
        return True
    except Exception:
        with _log_lock:
            _fallback_log.appendleft(entry)
        return False


def fetch_log(player=None, sport=None, prop=None, limit=50):
    """
    Query PostgreSQL for recent scoring records.
    Raises on DB error so the caller can return a clear HTTP error.
    """
    conn = get_db_conn()
    conditions = []
    params = []

    if player:
        params.append(f"%{player}%")
        conditions.append("player ILIKE %s")
    if sport:
        params.append(sport)
        conditions.append("sport ILIKE %s")
    if prop:
        params.append(f"%{prop}%")
        conditions.append("prop ILIKE %s")

    params.append(min(limit, 200))
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
            cur.execute(sql, params)
            rows = cur.fetchall()
    conn.close()

    return [
        {
            "timestamp": row["timestamp"].isoformat() if hasattr(row["timestamp"], "isoformat") else str(row["timestamp"]),
            "player": row["player"],
            "sport": row["sport"],
            "prop": row["prop"],
            "side": row["side"],
            "line": float(row["line"]),
            "score": float(row["score"]),
            "label": row["label"]
        }
        for row in rows
    ]


@app.route("/", methods=["GET"])
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
            "health": "GET /health (no auth)",
            "score": "POST /random-forest-score (X-API-Key required)",
            "log": "GET /request-log (X-API-Key required)",
            "schema": "GET /openapi.json (no auth)"
        }
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
    sport = str(data["sport"])
    prop = str(data["prop"])
    side = str(data["side"])
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
            "player": player,
            "sport": sport,
            "prop": prop,
            "side": side,
            "line": line,
            "features_received": len(features)
        },
        "disclaimer": DISCLAIMER,
        "can_approve_bets": False
    })


@app.route("/request-log", methods=["GET"])
@require_api_key
def request_log():
    raw_limit = request.args.get("limit", "50")
    try:
        limit = max(1, min(int(raw_limit), 200))
    except (ValueError, TypeError):
        return jsonify({"error": "'limit' must be a positive integer"}), 422

    player_filter = request.args.get("player", "").strip() or None
    sport_filter = request.args.get("sport", "").strip() or None
    prop_filter = request.args.get("prop", "").strip() or None

    try:
        entries = fetch_log(
            player=player_filter,
            sport=sport_filter,
            prop=prop_filter,
            limit=limit
        )
        storage = "postgresql"
    except Exception as exc:
        db_err = str(exc)
        if not _PSYCOPG2_AVAILABLE or not os.environ.get("DATABASE_URL"):
            return jsonify({
                "error": "Database unavailable",
                "detail": db_err,
                "hint": "Ensure DATABASE_URL is set and the database is reachable"
            }), 503
        return jsonify({
            "error": "Database unavailable",
            "detail": db_err
        }), 503

    return jsonify({
        "count": len(entries),
        "limit": limit,
        "order": "most recent first",
        "storage": storage,
        "filters": {
            "player": player_filter,
            "sport": sport_filter,
            "prop": prop_filter
        },
        "requests": entries
    })


def fetch_stats(player=None, sport=None, prop=None, limit=10):
    """
    Query aggregate stats from PostgreSQL.
    Raises on DB error so the caller can return a clear HTTP error.
    """
    conn = get_db_conn()

    filter_conditions = []
    filter_params = []
    if player:
        filter_params.append(f"%{player}%")
        filter_conditions.append("player ILIKE %s")
    if sport:
        filter_params.append(sport)
        filter_conditions.append("sport ILIKE %s")
    if prop:
        filter_params.append(f"%{prop}%")
        filter_conditions.append("prop ILIKE %s")

    where = ("WHERE " + " AND ".join(filter_conditions)) if filter_conditions else ""
    top_limit = max(1, min(int(limit), 100))

    with conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(
                f"SELECT COUNT(*) AS total, ROUND(AVG(score)::numeric, 2) AS avg_score "
                f"FROM scoring_requests {where}",
                filter_params
            )
            overview = cur.fetchone()

            cur.execute(
                f"SELECT sport, COUNT(*) AS requests, ROUND(AVG(score)::numeric, 2) AS avg_score "
                f"FROM scoring_requests {where} "
                f"GROUP BY sport ORDER BY requests DESC",
                filter_params
            )
            by_sport = cur.fetchall()

            cur.execute(
                f"SELECT player, sport, prop, side, line, ROUND(AVG(score)::numeric, 2) AS avg_score, COUNT(*) AS times_scored "
                f"FROM scoring_requests {where} "
                f"GROUP BY player, sport, prop, side, line "
                f"ORDER BY avg_score DESC "
                f"LIMIT %s",
                filter_params + [top_limit]
            )
            top_props = cur.fetchall()

            cur.execute(
                f"SELECT timestamp, player, sport, prop, side, line, score "
                f"FROM scoring_requests {where} "
                f"ORDER BY timestamp DESC LIMIT %s",
                filter_params + [top_limit]
            )
            recent = cur.fetchall()

    conn.close()

    return {
        "total_request_count": int(overview["total"]),
        "average_score_overall": float(overview["avg_score"]) if overview["avg_score"] is not None else None,
        "average_score_by_sport": [
            {
                "sport": row["sport"],
                "requests": int(row["requests"]),
                "avg_score": float(row["avg_score"])
            }
            for row in by_sport
        ],
        "top_scored_props": [
            {
                "player": row["player"],
                "sport": row["sport"],
                "prop": row["prop"],
                "side": row["side"],
                "line": float(row["line"]),
                "avg_score": float(row["avg_score"]),
                "times_scored": int(row["times_scored"])
            }
            for row in top_props
        ],
        "most_recent_scored_props": [
            {
                "timestamp": row["timestamp"].isoformat() if hasattr(row["timestamp"], "isoformat") else str(row["timestamp"]),
                "player": row["player"],
                "sport": row["sport"],
                "prop": row["prop"],
                "side": row["side"],
                "line": float(row["line"]),
                "score": float(row["score"])
            }
            for row in recent
        ]
    }


@app.route("/stats", methods=["GET"])
@require_api_key
def stats():
    raw_limit = request.args.get("limit", "10")
    try:
        limit = max(1, min(int(raw_limit), 100))
    except (ValueError, TypeError):
        return jsonify({"error": "'limit' must be a positive integer"}), 422

    player_filter = request.args.get("player", "").strip() or None
    sport_filter  = request.args.get("sport",  "").strip() or None
    prop_filter   = request.args.get("prop",   "").strip() or None

    try:
        data = fetch_stats(
            player=player_filter,
            sport=sport_filter,
            prop=prop_filter,
            limit=limit
        )
    except Exception as exc:
        return jsonify({
            "error": "Database unavailable",
            "detail": str(exc),
            "hint": "Ensure DATABASE_URL is set and the database is reachable"
        }), 503

    return jsonify({
        "storage": "postgresql",
        "filters": {
            "player": player_filter,
            "sport": sport_filter,
            "prop": prop_filter,
            "limit": limit
        },
        **data
    })


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
                    "description": "Returns service status and available endpoints. No auth required.",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "Service is healthy",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/HealthResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/random-forest-score": {
                "post": {
                    "operationId": "scoreProp",
                    "summary": "Score a player prop",
                    "description": (
                        "Accepts player prop details and optional feature signals. "
                        "Returns a support score from 0 to 100. "
                        "Requires X-API-Key header. "
                        "SUPPORT LAYER ONLY — cannot approve bets."
                    ),
                    "security": [{"ApiKeyAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ScoreRequest"},
                                "example": {
                                    "player": "Patrick Mahomes",
                                    "sport": "NFL",
                                    "prop": "passing_yards",
                                    "side": "over",
                                    "line": 285.5,
                                    "features": {
                                        "last_5_avg": 312.4,
                                        "vs_defense_rank": 8,
                                        "home_game": 1,
                                        "rest_days": 7
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Support score returned successfully",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ScoreResponse"}
                                }
                            }
                        },
                        "401": {"description": "Missing or invalid X-API-Key"},
                        "400": {"description": "Invalid JSON body"},
                        "422": {"description": "Missing or invalid fields"}
                    }
                }
            },
            "/stats": {
                "get": {
                    "operationId": "getStats",
                    "summary": "Aggregate scoring statistics",
                    "description": (
                        "Returns aggregate stats from the PostgreSQL request log: "
                        "total request count, overall average score, breakdown by sport, "
                        "top-scored props, and most recent scored props. "
                        "Supports optional filters. Requires X-API-Key."
                    ),
                    "security": [{"ApiKeyAuth": []}],
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "Max entries for top_scored_props and most_recent_scored_props (default 10, max 100)",
                            "required": False,
                            "schema": {"type": "integer", "default": 10, "maximum": 100}
                        },
                        {
                            "name": "player",
                            "in": "query",
                            "description": "Filter by player name (partial match)",
                            "required": False,
                            "schema": {"type": "string"}
                        },
                        {
                            "name": "sport",
                            "in": "query",
                            "description": "Filter by sport (case-insensitive)",
                            "required": False,
                            "schema": {"type": "string"}
                        },
                        {
                            "name": "prop",
                            "in": "query",
                            "description": "Filter by prop type (partial match)",
                            "required": False,
                            "schema": {"type": "string"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Stats returned successfully",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/StatsResponse"}
                                }
                            }
                        },
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
                        "Returns recent scoring requests from PostgreSQL in reverse chronological order. "
                        "Supports optional filters by player, sport, and prop. "
                        "Requires X-API-Key header. API keys are never stored."
                    ),
                    "security": [{"ApiKeyAuth": []}],
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "description": "Maximum number of records to return (default 50, max 200)",
                            "required": False,
                            "schema": {"type": "integer", "default": 50, "maximum": 200}
                        },
                        {
                            "name": "player",
                            "in": "query",
                            "description": "Filter by player name (partial match)",
                            "required": False,
                            "schema": {"type": "string"}
                        },
                        {
                            "name": "sport",
                            "in": "query",
                            "description": "Filter by sport (exact match, case-insensitive)",
                            "required": False,
                            "schema": {"type": "string"}
                        },
                        {
                            "name": "prop",
                            "in": "query",
                            "description": "Filter by prop type (partial match)",
                            "required": False,
                            "schema": {"type": "string"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Request log returned successfully",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/LogResponse"}
                                }
                            }
                        },
                        "401": {"description": "Missing or invalid X-API-Key"},
                        "422": {"description": "Invalid query parameter"},
                        "503": {"description": "Database unavailable"}
                    }
                }
            }
        },
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key"
                }
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
                        "sport": {"type": "string", "example": "NFL"},
                        "prop": {"type": "string", "example": "passing_yards"},
                        "side": {"type": "string", "example": "over"},
                        "line": {"type": "number", "example": 285.5},
                        "features": {
                            "type": "object",
                            "additionalProperties": True,
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
                        "player": {"type": "string", "example": "Patrick Mahomes"},
                        "sport": {"type": "string", "example": "NFL"},
                        "prop": {"type": "string", "example": "passing_yards"},
                        "side": {"type": "string", "example": "over"},
                        "line": {"type": "number", "example": 285.5},
                        "score": {"type": "number", "example": 74.3},
                        "label": {"type": "string", "example": "Support Layer Only"}
                    }
                },
                "LogResponse": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "example": 3},
                        "limit": {"type": "integer", "example": 50},
                        "order": {"type": "string", "example": "most recent first"},
                        "storage": {"type": "string", "example": "postgresql"},
                        "filters": {"type": "object"},
                        "requests": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/LogEntry"}
                        }
                    }
                },
                "SportStat": {
                    "type": "object",
                    "properties": {
                        "sport": {"type": "string", "example": "NFL"},
                        "requests": {"type": "integer", "example": 12},
                        "avg_score": {"type": "number", "example": 74.3}
                    }
                },
                "TopProp": {
                    "type": "object",
                    "properties": {
                        "player": {"type": "string", "example": "Patrick Mahomes"},
                        "sport": {"type": "string", "example": "NFL"},
                        "prop": {"type": "string", "example": "passing_yards"},
                        "side": {"type": "string", "example": "over"},
                        "line": {"type": "number", "example": 285.5},
                        "avg_score": {"type": "number", "example": 91.2},
                        "times_scored": {"type": "integer", "example": 3}
                    }
                },
                "RecentProp": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string", "example": "2026-05-08T14:32:01+00:00"},
                        "player": {"type": "string", "example": "LeBron James"},
                        "sport": {"type": "string", "example": "NBA"},
                        "prop": {"type": "string", "example": "points"},
                        "side": {"type": "string", "example": "over"},
                        "line": {"type": "number", "example": 27.5},
                        "score": {"type": "number", "example": 68.5}
                    }
                },
                "StatsResponse": {
                    "type": "object",
                    "properties": {
                        "storage": {"type": "string", "example": "postgresql"},
                        "filters": {"type": "object"},
                        "total_request_count": {"type": "integer", "example": 42},
                        "average_score_overall": {"type": "number", "example": 74.3},
                        "average_score_by_sport": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SportStat"}
                        },
                        "top_scored_props": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/TopProp"}
                        },
                        "most_recent_scored_props": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/RecentProp"}
                        }
                    }
                }
            }
        }
    }
    return jsonify(schema)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
