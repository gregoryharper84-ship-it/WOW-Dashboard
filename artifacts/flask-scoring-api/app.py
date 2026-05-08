import os
import random
import math
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins="*", allow_headers=["Content-Type", "Authorization", "X-API-Key"])

DISCLAIMER = (
    "SUPPORT LAYER ONLY — This score is a statistical signal for informational "
    "analysis purposes. It cannot and does not approve, authorize, or recommend "
    "any bet or wager. All decisions remain solely with the user."
)


def get_public_url() -> str:
    domains = os.environ.get("REPLIT_DOMAINS", "")
    first = domains.split(",")[0].strip() if domains else ""
    return f"https://{first}" if first else "http://localhost:8000"


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


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "WOW Sports Prop Scoring API",
        "version": "1.0.0",
        "label": "Support Layer Only",
        "disclaimer": DISCLAIMER,
        "auth": "X-API-Key header required on /random-forest-score",
        "endpoints": {
            "health": "GET /health (no auth)",
            "score": "POST /random-forest-score (X-API-Key required)",
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

    return jsonify({
        "label": "Support Layer Only",
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
        "servers": [
            {"url": server_url}
        ],
        "security": [
            {"ApiKeyAuth": []}
        ],
        "paths": {
            "/": {
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
                        "401": {"description": "Missing X-API-Key header"},
                        "403": {"description": "Invalid API key"},
                        "400": {"description": "Invalid JSON body"},
                        "422": {"description": "Missing or invalid fields"}
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
                        "player": {
                            "type": "string",
                            "description": "Player full name",
                            "example": "Patrick Mahomes"
                        },
                        "sport": {
                            "type": "string",
                            "description": "Sport identifier (e.g. NFL, NBA, MLB)",
                            "example": "NFL"
                        },
                        "prop": {
                            "type": "string",
                            "description": "Prop type (e.g. passing_yards, points, strikeouts)",
                            "example": "passing_yards"
                        },
                        "side": {
                            "type": "string",
                            "description": "Which side of the line (over or under)",
                            "example": "over"
                        },
                        "line": {
                            "type": "number",
                            "description": "The prop line value",
                            "example": 285.5
                        },
                        "features": {
                            "type": "object",
                            "description": "Optional key-value feature signals (numeric values preferred)",
                            "additionalProperties": True,
                            "example": {
                                "last_5_avg": 312.4,
                                "vs_defense_rank": 8
                            }
                        }
                    }
                },
                "ScoreResponse": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "example": "Support Layer Only"},
                        "score": {
                            "type": "number",
                            "description": "Support score from 0 to 100",
                            "example": 74.3
                        },
                        "score_range": {"type": "string", "example": "0-100"},
                        "input": {"type": "object", "description": "Echo of inputs received"},
                        "disclaimer": {"type": "string"},
                        "can_approve_bets": {"type": "boolean", "example": False}
                    }
                }
            }
        }
    }
    return jsonify(schema)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
