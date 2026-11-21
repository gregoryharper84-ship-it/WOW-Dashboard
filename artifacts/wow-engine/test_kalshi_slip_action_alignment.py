"""Cross-host research exchange is present only as authenticated V17 research Actions."""
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parent
ACTION=ROOT/"v17"/"openapi.wow-betting-engine.v17.yaml"
LLP=ROOT/"v17"/"openapi.llp-team-engine.v17.yaml"
GATEWAY=ROOT/"v17"/"supabase/functions/wow-llp-action-gateway/index.ts"

ROUTE="/v17/intelligence/kalshi-slip-exchange"
LLP_ROUTE="/functions/v1/wow-llp-action-gateway"+ROUTE


def test_both_host_action_schemas_contain_authenticated_two_way_exchange():
    for file, path, ids in (
        (ACTION, ROUTE, ("readWowV17KalshiSlipResearch","publishWowV17KalshiSlipResearch")),
        (LLP, LLP_ROUTE, ("readLlpV17KalshiSlipResearch","publishLlpV17KalshiSlipResearch")),
    ):
        schema=yaml.safe_load(file.read_text())
        ops=schema["paths"][path]
        assert set(ops)=={"get","post"}
        assert ops["get"]["operationId"]==ids[0]
        assert ops["post"]["operationId"]==ids[1]
        for method in ("get","post"):
            assert ops[method]["security"]==[{"actionBearer":[]}]
            assert ops[method]["responses"]["401"]
            assert ops[method]["responses"]["503"]
        body=ops["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert body["additionalProperties"] is False
        assert "calibrated_probability" not in body["properties"]
        assert "total_cost" not in body["properties"]
        assert body["properties"]["source_host"]["enum"]==["LLP_TEAM_BETTING_ENGINE","WOW_BETTING_ENGINE"]
        assert ops["post"]["x-openai-isConsequential"] is True
        assert ops["get"]["x-openai-isConsequential"] is False


def test_llp_gateway_has_explicit_allowed_get_post_regex_and_bearer_boundary():
    gateway=GATEWAY.read_text()
    snippet='pattern: /^\\/v17\\/intelligence\\/kalshi-slip-exchange$/, auth: true'
    assert gateway.count(snippet)==2
    assert '{ method: "GET", '+snippet in gateway
    assert '{ method: "POST", '+snippet in gateway
    assert 'authorization.startsWith("Bearer ")' in gateway
    assert "LLP_GATEWAY_PATH_NOT_ALLOWED" in gateway


def test_wow_single_schema_count_with_no_lost_actions():
    schema=yaml.safe_load(ACTION.read_text())
    ops=[node["operationId"] for path in schema["paths"].values()
         for node in path.values() if isinstance(node,dict) and "operationId" in node]
    assert len(ops)==31
    assert len(set(ops))==len(ops)
    for name in ("scoreWowPickRequest","submitWowV17NFLPickemBoard",
                 "scoreWowTeamEventRequest","scoreWowProp"):
        assert name in ops
