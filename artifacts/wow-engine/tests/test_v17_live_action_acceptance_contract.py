from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "v17" / "LIVE_GPT_ACTION_ACCEPTANCE.md"
EDITOR_ATTESTATION = ROOT / "v17" / "WOW_BETTING_ENGINE_EDITOR_ATTESTATION.md"
OPENAPI = ROOT / "v17" / "openapi.wow-betting-engine.v17.yaml"


def test_live_gpt_action_acceptance_contract_is_fail_closed():
    text = ACCEPTANCE.read_text(encoding="utf-8")

    assert "LIVE_GPT_ACTION_INVOCATION_BLOCKED" in text
    assert "EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED" in text
    assert "MODEL_UNAVAILABLE" in text
    assert "MODEL_SCORER_FAILED" in text
    assert "can_execute=false" in text
    assert "Only `verdict=PASS` permits `LIVE_GPT_EDITOR_SYNC=VERIFIED`" in text


def test_live_gpt_action_acceptance_requires_round_trip_actions():
    text = ACCEPTANCE.read_text(encoding="utf-8")

    assert "getWowV17BackendHealth" in text
    assert "scoreWowPickRequest" in text
    assert "lookupWowV17PredictionReceipts" in text
    assert "no more than 4 directional rows" in text
    assert "exact-once reconciliation" in text
    assert "Do not rank a partial pool" in text


def test_canonical_openapi_still_exposes_required_action_operation_ids():
    text = OPENAPI.read_text(encoding="utf-8")

    assert "operationId: getWowV17BackendHealth" in text
    assert "operationId: scoreWowPickRequest" in text
    assert "operationId: lookupWowV17PredictionReceipts" in text
    assert "can_execute is always false" in text


def test_editor_attestation_does_not_claim_unverified_live_acceptance():
    text = EDITOR_ATTESTATION.read_text(encoding="utf-8")

    assert "LIVE_ACTION_ACCEPTANCE_REQUIRED" in text
    assert "scoreWowPickRequest" in text
    assert "lookupWowV17PredictionReceipts" in text
    assert "can_execute=false" in text
