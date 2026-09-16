from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_STATUS = ROOT / "artifacts" / "wow-engine" / "V17_PRODUCTION_STATUS.md"
EDITOR_SYNC = ROOT / "artifacts" / "wow-engine" / "V17_CUSTOM_GPT_EDITOR_SYNC.md"
WOW_ATTESTATION = ROOT / "artifacts" / "wow-engine" / "v17" / "WOW_BETTING_ENGINE_EDITOR_ATTESTATION.md"
WOW_INSTRUCTIONS = ROOT / "artifacts" / "wow-engine" / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"
WOW_SCHEMA = ROOT / "artifacts" / "wow-engine" / "v17" / "openapi.wow-betting-engine.v17.yaml"


def test_live_editor_sync_status_is_not_contradictory():
    production = PRODUCTION_STATUS.read_text(encoding="utf-8")
    sync = EDITOR_SYNC.read_text(encoding="utf-8")
    attestation = WOW_ATTESTATION.read_text(encoding="utf-8")

    assert "LIVE_EDITOR_SYNC_PENDING" in sync
    assert "LIVE_EDITOR_SYNC_PENDING" in production
    assert "LIVE_EDITOR_SYNC_PENDING" in attestation
    assert "LIVE_EDITOR_SYNC_VERIFIED" not in production


def test_pending_sync_preserves_action_canary_contract():
    instructions = WOW_INSTRUCTIONS.read_text(encoding="utf-8")
    attestation = WOW_ATTESTATION.read_text(encoding="utf-8")
    schema = yaml.safe_load(WOW_SCHEMA.read_text(encoding="utf-8"))

    operation = schema["paths"]["/score-pick-request"]["post"]
    assert operation["operationId"] == "scoreWowV17PickRequest"
    assert schema["servers"][0]["url"] == "https://wow-governed-probability-engine.onrender.com"
    assert schema["components"]["securitySchemes"]["actionBearer"]["scheme"] == "bearer"

    assert "scoreWowV17PickRequest" in instructions
    assert "LIVE_GPT_ACTION_INVOCATION_BLOCKED" in instructions
    assert "scoring_attempted=true" in instructions
    assert "scoreWowV17PickRequest exposed after reload" in attestation
    assert "canonical row-level Action receipt" in attestation
    assert "manual research reconstruction" in attestation
    assert "can_execute = false" in attestation
