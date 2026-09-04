from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
INSTRUCTIONS = ROOT / "artifacts" / "wow-engine" / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"
KNOWLEDGE = ROOT / "artifacts" / "wow-engine" / "WOW_V17_GOVERNANCE_KNOWLEDGE.txt"
SCHEMA = ROOT / "artifacts" / "wow-engine" / "v17" / "openapi.wow-betting-engine.v17.yaml"


def _schema():
    return yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))


def test_live_gpt_instructions_fit_editor_limit_and_preserve_controls():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert len(text) <= 8000
    assert "WOW_V17_GOVERNANCE_KNOWLEDGE.txt" in text
    assert "can_execute=false" in text
    assert "V17_TERMINAL_REDUCER" in text
    assert "Never place, route, modify, approve, or cancel a wager/order" in text
    assert "JSON null, never text" in text
    assert "role_status must be a JSON object" in text
    assert KNOWLEDGE.exists()


def test_action_operation_descriptions_fit_editor_limit():
    document = _schema()
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            description = operation.get("description")
            if description is not None:
                assert len(description) <= 300, (path, method, len(description))


def test_action_schema_preserves_v17_boundary():
    document = _schema()
    assert document["servers"][0]["url"] == "https://wow-governed-probability-engine.onrender.com"
    paths = document["paths"]
    assert paths["/score-pick-request"]["post"]["operationId"] == "scoreWowV17PickRequest"
    assert paths["/score-team-event"]["post"]["operationId"] == "scoreWowV17TeamEventFromWowHost"
    assert paths["/v17/detailed-evidence-contract"]["get"]["operationId"] == "getWowV17DetailedEvidenceContract"
    assert document["components"]["securitySchemes"]["actionBearer"]["scheme"] == "bearer"


def test_detailed_evidence_openapi_matches_runtime_required_families():
    schemas = _schema()["components"]["schemas"]
    envelope = schemas["DetailedEvidenceEnvelope"]
    assert envelope["properties"]["evidence_families"] == {"$ref": "#/components/schemas/DetailedEvidenceFamilies"}
    assert envelope["properties"]["market_evidence"] == {"$ref": "#/components/schemas/DetailedMarketEvidence"}

    families = schemas["DetailedEvidenceFamilies"]
    assert families["additionalProperties"] is False
    assert families["required"] == [
        "recent_form",
        "head_to_head",
        "player_performance",
        "lineup_availability_depth",
        "tactical_style",
        "match_context_stakes",
        "environment",
        "officiating",
        "schedule_fatigue_travel",
        "advanced_statistics",
    ]
    for name in families["required"]:
        assert families["properties"][name] == {"$ref": "#/components/schemas/DetailedEvidenceFamily"}


def test_detailed_evidence_openapi_matches_runtime_value_types():
    schemas = _schema()["components"]["schemas"]
    family = schemas["DetailedEvidenceFamily"]
    assert family["required"] == ["status"]
    assert family["properties"]["status"]["enum"] == [
        "AVAILABLE", "PARTIAL", "UNAVAILABLE", "NOT_APPLICABLE"
    ]
    for field in ("data_quality", "certainty"):
        spec = family["properties"][field]
        assert spec["type"] == ["number", "null"]
        assert spec["minimum"] == 0
        assert spec["maximum"] == 1

    item = schemas["DetailedEvidenceItem"]
    assert item["required"] == ["name", "feature_status", "source", "as_of"]
    assert item["properties"]["feature_status"]["enum"] == [
        "MODEL_INPUT",
        "REGIME_INPUT",
        "CALIBRATION_INPUT",
        "MARKET_EVIDENCE",
        "EVIDENCE_ONLY",
    ]
    for field in ("data_quality", "certainty"):
        spec = item["properties"][field]
        assert spec["type"] == ["number", "null"]
        assert spec["minimum"] == 0
        assert spec["maximum"] == 1

    market = schemas["DetailedMarketEvidence"]
    assert market["required"] == ["market_state"]
    assert market["properties"]["market_state"]["enum"] == [
        "EXACT_LINE", "ADJACENT_LINE", "NO_MARKET"
    ]
