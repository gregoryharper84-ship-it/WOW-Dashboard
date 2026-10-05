import json
from pathlib import Path

import yaml


HERE = Path(__file__).resolve().parents[1] / "v17"
SCHEMA = HERE / "openapi.llp-team-engine.v17.yaml"
GATEWAY_DIR = HERE / "vercel" / "llp-action-gateway"
VERCEL = GATEWAY_DIR / "vercel.json"
PACKAGE = GATEWAY_DIR / "package.json"
SOURCE = GATEWAY_DIR / "api" / "gateway.js"


def test_vercel_challenger_rewrites_exactly_the_canonical_llp_action_paths():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    config = json.loads(VERCEL.read_text(encoding="utf-8"))

    canonical = set(schema["paths"])
    rewrites = {item["source"] for item in config["rewrites"]}
    normalized = {
        path.replace(":run_id", "{run_id}")
        for path in rewrites
    }

    assert len(canonical) == 12
    assert normalized == canonical


def test_vercel_challenger_is_transport_only_and_fail_closed():
    source = SOURCE.read_text(encoding="utf-8")

    assert 'const TARGET = "https://wow-governed-probability-engine.onrender.com";' in source
    assert 'authorization.startsWith("Bearer ")' in source
    assert "headers.authorization = authorization" in source
    assert "LLP_VERCEL_GATEWAY_PATH_NOT_ALLOWED" in source
    assert "LLP_VERCEL_GATEWAY_METHOD_NOT_ALLOWED" in source
    assert "LLP_VERCEL_GATEWAY_AUTH_REQUIRED" in source
    assert "LLP_VERCEL_GATEWAY_UPSTREAM_TRANSPORT_FAILURE" in source
    assert "can_execute: false" in source
    assert 'redirect: "manual"' in source
    assert "x-wow-request-id" in source
    assert "x-request-id" in source
    assert "model_probability" not in source
    assert "calibrated_probability" not in source
    assert "sportsbook" not in source.lower()
    assert "market_prior" not in source


def test_vercel_challenger_does_not_embed_credentials():
    source = SOURCE.read_text(encoding="utf-8")
    config = VERCEL.read_text(encoding="utf-8")

    forbidden = [
        "WOW_ACTION_API_KEY=",
        "SUPABASE_SERVICE_ROLE_KEY=",
        "Bearer sb_",
        "sk-proj-",
    ]
    for marker in forbidden:
        assert marker not in source
        assert marker not in config


def test_vercel_challenger_declares_esm_runtime():
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    assert package["type"] == "module"
    assert package["engines"]["node"].startswith(">=")


def test_vercel_challenger_is_manual_only_and_cannot_gate_git_releases():
    config = json.loads(VERCEL.read_text(encoding="utf-8"))
    assert config["git"]["deploymentEnabled"] is False
