from pathlib import Path

import yaml


def test_live_host_uses_single_domain_canonical_action_with_run_control_merged():
    root = Path(__file__).resolve().parents[1]
    instructions = (root / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt").read_text(encoding="utf-8")
    primary = yaml.safe_load((root / "v17" / "openapi.wow-betting-engine.v17.yaml").read_text(encoding="utf-8"))
    companion = yaml.safe_load((root / "v17" / "openapi.wow-betting-engine.v17.run-control.yaml").read_text(encoding="utf-8"))

    assert "Canonical Action schema: v17/openapi.wow-betting-engine.v17.yaml" in instructions
    assert "Run-control operations are merged into the canonical Action schema" in instructions
    assert primary["servers"][0]["url"] == companion["servers"][0]["url"]

    primary_ops = {
        operation["operationId"]
        for methods in primary["paths"].values()
        for operation in methods.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert len(primary_ops) == 20

    for operation in (
        "getWowV17PickRequestRunState",
        "runWowV17ResumablePickRequest",
        "closeWowV17PickRequestRun",
    ):
        assert operation in instructions
        assert operation in primary_ops
        assert operation in str(companion)

    assert "scoreWowV17SpreadForwardShadow" in primary_ops
    spread = primary["paths"]["/internal/v17/spread-forward-shadow"]["post"]
    assert spread["operationId"] == "scoreWowV17SpreadForwardShadow"
    assert spread["security"] == [{"actionBearer": []}]
    assert spread["x-openai-isConsequential"] is False
    assert primary["components"]["schemas"]["SpreadForwardShadowRequest"]["properties"]["sport"]["enum"] == ["NCAAF"]

    assert "can_execute=false" in instructions
