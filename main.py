from flask import Flask, request, jsonify
import os

app = Flask(__name__)

API_KEY = os.getenv("SCORING_API_KEY")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "WOW Scoring API"
    })

@app.route("/random-forest-score", methods=["POST"])
def score():
    key = request.headers.get("X-API-Key")

    if key != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()

    score = 70

    if data.get("features", {}).get("l10_hit_rate", 0) >= 0.7:
        score += 10

    return jsonify({
        "score": score,
        "label": "Support Layer Only"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
    