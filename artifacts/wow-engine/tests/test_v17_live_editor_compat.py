from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
INSTRUCTIONS = ROOT / "artifacts" / "wow-engine" / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"
KNOWLEDGE = ROOT / "artifacts" / "wow-engine" / "WOW_V17_GOVERNANCE_KNOWLEDGE.txt"
SCHEMA = ROOT / "artifacts" / "wow-engine" / "v17" / "openapi.wow-betting-engine.v17.yaml"


def test_live_gpt_instructions_fit_editor_limit_and_preserve_controls():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert len(text) <= 8000
    assert "WOW_V17_GOVERNANCE_KNOWLEDGE.txt" in text
    assert "can_execute=false" in text
    assert "V17_TERMINAL_REDUCER" in text
    assert "Never place, route, modify, approve, or cancel a wager/order" in text
    assert KNOWLEDGE.exists()


def test_action_operation_descriptions_fit_editor_limit():
    document = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            description = operation.get("description")
            if description is not None:
                assert len(description) <= 300, (path, method, len(description))


def test_action_schema_preserves_v17_boundary():
    document = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    assert document["servers"][0]["url"] == "https://wow-governed-probability-engine.onrender.com"
    paths = document["paths"]
    assert paths["/score-pick-request"]["post"]["operationId"] == "scoreWowV17PickRequest"
    assert paths["/score-team-event"]["post"]["operationId"] == "scoreWowV17TeamEventFromWowHost"
    assert paths["/v17/detailed-evidence-contract"]["get"]["operationId"] == "getWowV17DetailedEvidenceContract"
    assert document["components"]["securitySchemes"]["actionBearer"]["scheme"] == "bearer"
