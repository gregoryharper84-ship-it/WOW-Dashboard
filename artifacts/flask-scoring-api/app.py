import os
import random
import math
from flask import Flask, request, jsonify

app = Flask(__name__)

DISCLAIMER = (
    "SUPPORT LAYER ONLY — This score is a statistical signal for informational "
    "analysis purposes. It cannot and does not approve, authorize, or recommend "
    "any bet or wager. All decisions remain solely with the user."
)


def compute_rf_score(features: dict, player: str, prop: str, side: str, line: float) -> float:
    """
    Lightweight simulated random-forest-style scorer.
    Combines numeric features with deterministic seeding so the same
    inputs always return the same score, while still distributing
    realistically across 0-100.
    """
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
def health():
    return jsonify({
        "status": "ok",
        "service": "WOW Sports Prop Scoring API",
        "version": "1.0.0",
        "label": "Support Layer Only",
        "disclaimer": DISCLAIMER
    })


@app.route("/random-forest-score", methods=["POST"])
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
        "score_range": "0–100",
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
