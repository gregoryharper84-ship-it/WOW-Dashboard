from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "build_gpt_editor_sync_packet.py"
SCHEMA = Path(__file__).resolve().parent / "openapi.wow-betting-engine.v17.yaml"
spec = importlib.util.spec_from_file_location("gpt_editor_sync_packet", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_packet_matches_canonical_repository_bytes_and_stays_fail_closed():
    packet, manifest = module.build_packet()
    instructions = module.INSTRUCTIONS.read_bytes()
    addendum = module.PRIZEPICKS_ADDENDUM.read_bytes()
    schema = module.ACTION_SCHEMA.read_bytes()

    assert packet == instructions.rstrip() + b"\n"
    assert manifest["canonical_instructions_sha256"] == hashlib.sha256(instructions).hexdigest()
    assert manifest["prizepicks_addendum_sha256"] == hashlib.sha256(addendum).hexdigest()
    assert manifest["action_schema_sha256"] == hashlib.sha256(schema).hexdigest()
    assert manifest["editor_instruction_packet_sha256"] == hashlib.sha256(packet).hexdigest()
    assert manifest["combined_editor_packet_sha256"] == hashlib.sha256(packet).hexdigest()
    assert manifest["editor_update_required"] is True
    assert manifest["live_editor_verified"] is False
    assert manifest["can_execute"] is False
    assert manifest["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert manifest["contract"] == "WOW_V17_GPT_EDITOR_SYNC_PACKET_V3"


def test_editor_packet_has_safe_utf8_margin_and_addendum_moves_to_knowledge():
    packet, manifest = module.build_packet()
    text = packet.decode("utf-8")
    addendum_text = module.PRIZEPICKS_ADDENDUM.read_text(encoding="utf-8")

    assert len(text) <= module.EDITOR_INSTRUCTION_CHAR_LIMIT == 8000
    assert len(packet) <= module.EDITOR_INSTRUCTION_BYTE_SAFETY_LIMIT == 7500
    assert manifest["editor_instruction_char_count"] <= manifest["editor_instruction_char_limit"] == 8000
    assert manifest["editor_instruction_byte_count"] <= manifest["editor_instruction_byte_safety_limit"] == 7500
    assert manifest["prizepicks_addendum_installation_surface"] == "KNOWLEDGE_FILE"
    assert manifest["prizepicks_knowledge_output_file"] == module.PRIZEPICKS_KNOWLEDGE_FILENAME
    assert "ATTACH_PRIZEPICKS_ADDENDUM_AS_KNOWLEDGE_FILE" in manifest["acceptance_required"]

    for token in module.REQUIRED_EDITOR_TOKENS:
        assert token in text
    for token in module.REQUIRED_PRIZEPICKS_TOKENS:
        assert token in addendum_text
        assert token in manifest["required_prizepicks_tokens"]

    assert "PRIZEPICKS BOARD-TO-SLIPS — V17 LIVE HOST ADDENDUM" not in text


def test_packet_contains_single_domain_action_contract_without_secrets():
    packet, manifest = module.build_packet()
    text = packet.decode("utf-8")
    schema_text = module.ACTION_SCHEMA.read_text(encoding="utf-8")

    for operation in module.REQUIRED_OPERATIONS:
        assert operation in schema_text
        assert operation in manifest["required_operations"]

    # Keep this dedicated sync workflow dependency-free. Full YAML/OpenAPI
    # validation runs in the protected backend regression suite.
    assert schema_text.count("operationId:") == module.REQUIRED_OPERATION_COUNT == 20
    assert manifest["action_operation_count"] == 20
    assert manifest["action_schema_installation_surface"] == "SINGLE_CUSTOM_ACTION_DOMAIN"
    assert manifest["action_schema_domain"] == "wow-governed-probability-engine.onrender.com"
    assert manifest["run_control_installation_surface"] == "MERGED_INTO_CANONICAL_ACTION_SCHEMA"
    assert "IMPORT_SINGLE_CANONICAL_ACTION_SCHEMA_WITH_20_OPERATIONS" in manifest["acceptance_required"]
    assert "FRESH_CHAT_SCORE_WOW_V17_SPREAD_FORWARD_SHADOW" in manifest["acceptance_required"]

    assert "WOW_ACTION_API_KEY=" not in text
    assert "Bearer sk-" not in text
    assert "API_KEY_TO_BEARER_EXISTING_WOW_ACTION_API_KEY__SECRET_NOT_INCLUDED" == manifest["authentication_contract"]


def test_spread_forward_shadow_action_is_closed_ncaaf_only_and_research_only():
    schema_text = SCHEMA.read_text(encoding="utf-8")

    route_start = schema_text.index("  /internal/v17/spread-forward-shadow:\n")
    route_end = schema_text.index("  /v17/prediction-receipts/lookup:\n", route_start)
    route = schema_text[route_start:route_end]

    assert "operationId: scoreWowV17SpreadForwardShadow" in route
    assert "x-openai-isConsequential: false" in route
    assert "Research-only NCAAF exact-line spread forward shadow" in route
    assert "security: [{actionBearer: []}]" in route
    assert "schema: {$ref: '#/components/schemas/SpreadForwardShadowRequest'}" in route

    request_start = schema_text.index("    SpreadForwardShadowRequest:\n")
    request_end = schema_text.index("    RecommendationBatch:\n", request_start)
    request = schema_text[request_start:request_end]

    assert "additionalProperties: false" in request
    assert (
        "required: [sport, event_id, event_start_time, home_team, away_team, home_spread, season]"
        in request
    )
    assert "sport: {type: string, enum: [NCAAF]}" in request
    assert "event_start_time: {type: string, format: date-time}" in request
    assert "home_spread: {type: number, exclusiveMinimum: -100, exclusiveMaximum: 100}" in request
    assert "season: {type: integer, minimum: 2000, maximum: 2100}" in request


def test_packet_is_deterministic_for_same_repository_content():
    packet_a, manifest_a = module.build_packet()
    packet_b, manifest_b = module.build_packet()
    assert packet_a == packet_b
    assert manifest_a == manifest_b
