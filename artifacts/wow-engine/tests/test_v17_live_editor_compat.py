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
    encoded = text.encode("utf-8")
    assert len(text) <= 8000
    assert len(encoded) <= 7500
    assert "WOW_V17_GOVERNANCE_KNOWLEDGE.txt" in text
    assert "can_execute=false" in text
    assert "V17_TERMINAL_REDUCER" in text
    assert "Never place, route, modify, approve, or cancel a wager/order" in text
    assert "JSON number 0..1 or null only, never labels/text" in text
    assert "role_status object" in text
    assert "market_evidence is a sibling of evidence_families, never nested" in text
    assert "Unknown is not zero" in text
    assert "lookupWowV17PredictionReceipts" in text
    assert "display_authorized=true" in text
    assert "Canonical Action schema: v17/openapi.wow-betting-engine.v17.yaml" in text
    assert "openapi.custom-gpt.template.yaml" not in text
    assert "openapi.pick-request-action.yaml" not in text
    assert "LIVE_GPT_EDITOR_SYNC=VERIFIED" in text
    assert KNOWLEDGE.exists()


def test_directionless_best_side_expands_at_host_without_weakening_action_schema():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert "Best-side prop + exact line + no selected direction: expand MORE and LESS before `/score-pick-request`" in text
    assert "Final current-board publication requires refresh proving the chosen direction is offered" in text

    row = _schema()["components"]["schemas"]["PickRequestRow"]
    assert "direction" in set(row["required"])
    assert row["properties"]["direction"]["enum"] == ["MORE", "LESS"]


def test_live_gpt_large_prop_pools_chunk_and_recover_immutable_receipts():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert "LIVE_GPT interactive scoring must use <=4 directional rows per Action call" in text
    assert "continue chunk-by-chunk until every source row reconciles exactly once" in text
    assert "On an Action timeout/disconnect/ambiguous completion" in text
    assert "First call `lookupWowV17PredictionReceipts`" in text
    assert "Retry only still-unresolved rows" in text
    assert "Do not rank a partial pool as Full Model" in text

    batch = _schema()["components"]["schemas"]["PickRequestBatch"]
    assert batch["properties"]["rows"]["maxItems"] == 50
    response_mode = batch["properties"]["response_mode"]
    assert response_mode["enum"] == ["COMPACT", "FULL"]
    assert response_mode["default"] == "COMPACT"
    assert "response_mode=COMPACT" in text


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
    assert paths["/score-prop"]["post"]["operationId"] == "scoreWowProp"
    assert paths["/score-pick-request"]["post"]["operationId"] == "scoreWowPickRequest"
    assert paths["/score-team-event"]["post"]["operationId"] == "scoreWowV17TeamEventFromWowHost"
    assert paths["/v17/detailed-evidence-contract"]["get"]["operationId"] == "getWowV17DetailedEvidenceContract"
    assert paths["/v17/prediction-receipts/lookup"]["post"]["operationId"] == "lookupWowV17PredictionReceipts"
    assert document["components"]["securitySchemes"]["actionBearer"]["scheme"] == "bearer"


def test_live_editor_schema_exposes_full_board_diagnostics_with_bearer_auth():
    paths = _schema()["paths"]
    expected = {
        "/v17/capabilities": "getWowV17Capabilities",
        "/v17/market-health/rundown": "getWowV17RundownMarketHealth",
        "/v17/market-health/odds-api": "getWowV17OddsApiMarketHealth",
        "/v17/discovery/espn-compact": "getWowV17CompactEspnDiscovery",
    }
    for path, operation_id in expected.items():
        operation = paths[path]["get"]
        assert operation["operationId"] == operation_id
        assert operation["security"] == [{"actionBearer": []}]
        assert operation["x-openai-isConsequential"] is False

    espn_parameters = {
        parameter["name"]: parameter
        for parameter in paths["/v17/discovery/espn-compact"]["get"]["parameters"]
    }
    assert espn_parameters["page"]["schema"]["minimum"] == 1
    assert espn_parameters["page_size"]["schema"]["minimum"] == 1
    assert espn_parameters["page_size"]["schema"]["maximum"] == 250

    rundown_parameters = {
        parameter["name"]: parameter
        for parameter in paths["/v17/market-health/rundown"]["get"]["parameters"]
    }
    for parameters in (rundown_parameters, espn_parameters):
        assert parameters["date"]["required"] is False
        assert parameters["date"]["schema"] == {"type": "string"}


def test_prediction_receipt_openapi_requires_id_or_complete_exact_identity():
    schemas = _schema()["components"]["schemas"]
    batch = schemas["PredictionReceiptLookupBatch"]
    row = schemas["PredictionReceiptLookupRow"]
    assert batch["additionalProperties"] is False
    assert batch["required"] == ["rows"]
    assert row["additionalProperties"] is False
    assert row["anyOf"] == [
        {"required": ["prediction_id"]},
        {"required": ["event_id", "player", "stat_type", "line", "direction"]},
    ]
    assert row["properties"]["direction"]["anyOf"][0]["enum"] == ["MORE", "LESS"]


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
