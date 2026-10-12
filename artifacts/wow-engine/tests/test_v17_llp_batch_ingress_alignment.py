"""Canonical two-gateway + two-Action-schema alignment for the LLP batch ingress.

Scope is additive Class B transport. The server retains fitted model and
terminal authority; this test does not certify any sporting model.
"""
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1] / "v17"
BATCH = "/score-team-event-request"
LLP_PREFIX = "/functions/v1/wow-llp-action-gateway"


def _spec(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_batch_present_in_both_action_contracts_and_canonical_server():
    wow = _spec("openapi.wow-betting-engine.v17.yaml")
    llp = _spec("openapi.llp-team-engine.v17.yaml")
    operations = [
        wow["paths"][BATCH]["post"],
        llp["paths"][LLP_PREFIX + BATCH]["post"],
    ]
    assert operations[0]["operationId"] == "scoreWowTeamEventRequest"
    assert operations[1]["operationId"] == "scoreLlpV17TeamEventBatch"
    for spec, op in zip((wow, llp), operations):
        assert op["security"] == [{"actionBearer": []}]
        assert op["x-openai-isConsequential"] is False
        assert op["requestBody"]["required"] is True
        assert op["requestBody"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/TeamEventRequestBatch"
        batch = spec["components"]["schemas"]["TeamEventRequestBatch"]
        assert batch["additionalProperties"] is False
        assert batch["required"] == ["rows"]
        rows = batch["properties"]["rows"]
        assert (rows["minItems"], rows["maxItems"]) == (1, 100)
        assert rows["items"]["$ref"] == "#/components/schemas/TeamEventRequestRow"


def test_batch_schema_matches_backend_pydantic_and_fails_closed():
    wow = _spec("openapi.wow-betting-engine.v17.yaml")
    llp = _spec("openapi.llp-team-engine.v17.yaml")
    for spec in (wow, llp):
        row = spec["components"]["schemas"]["TeamEventRequestRow"]
        assert row["additionalProperties"] is False
        assert set(row["required"]) == {
            "research_run_id", "objective_lane", "sport", "league",
            "event_key", "event_state", "event_date", "timezone",
            "price_required_for_objective",
        }
        assert set(row["properties"]["objective_lane"]["enum"]) == {
            "OUTRIGHT_WIN_PROBABILITY", "UPSET_PROBABILITY", "MARKET_EDGE",
        }
        assert row["properties"]["event_state"]["enum"] == ["PREGAME"]
        assert row["properties"]["price_required_for_objective"]["type"] == "boolean"
        assert row["properties"]["market_input"]["type"] == ["object", "null"]
    backend = (ROOT.parent / "team_event_request_runtime.py").read_text(encoding="utf-8")
    assert "class TeamEventRequestRow(BaseModel)" in backend
    assert "rows: list[TeamEventRequestRow] = Field(min_length=1, max_length=100)" in backend
    assert "@app.post(\"/score-team-event-request\"" in backend


def test_vercel_and_supabase_gateway_allow_only_authenticated_post():
    vercel = (ROOT / "vercel/llp-action-gateway/api/gateway.js").read_text(encoding="utf-8")
    edge = (ROOT / "supabase/functions/wow-llp-action-gateway/index.ts").read_text(encoding="utf-8")
    config = json.loads((ROOT / "vercel/llp-action-gateway/vercel.json").read_text(encoding="utf-8"))
    assert '[\"/score-team-event-request\", { method: \"POST\", auth: true }]' in vercel
    assert '{ method: \"POST\", pattern: /^\\/score-team-event-request$/, auth: true }' in edge
    routes = [item for item in config["rewrites"] if item["source"] == LLP_PREFIX + BATCH]
    assert len(routes) == 1
    assert routes[0]["destination"] == "/api/gateway?__route=%2Fscore-team-event-request"
    assert config["git"]["deploymentEnabled"] is False
    assert 'redirect: "manual"' in vercel and 'authorization.startsWith("Bearer ")' in vercel
    assert "const ROUTES: Route[] = [" in edge
    assert "can_execute: false" in vercel
    for text in (vercel, edge):
        assert "sk-proj-" not in text
        assert "SUPABASE_SERVICE_ROLE_KEY=" not in text


def test_existing_single_event_and_hold_routes_unchanged():
    wow = _spec("openapi.wow-betting-engine.v17.yaml")
    llp = _spec("openapi.llp-team-engine.v17.yaml")
    assert "/score-team-event" in wow["paths"]
    assert LLP_PREFIX + "/score-team-event" in llp["paths"]
    assert "/internal/v17/spread-forward-shadow" in wow["paths"]
    assert LLP_PREFIX + "/internal/v17/spread-forward-shadow" in llp["paths"]
