from pathlib import Path

V17_DIR = Path(__file__).resolve().parent
SCHEMA = V17_DIR / "openapi.wow-betting-engine.v17.yaml"
INSTRUCTIONS = V17_DIR.parent / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"


def test_canonical_action_exposes_durable_daily_submit_and_status():
    text = SCHEMA.read_text(encoding="utf-8")
    assert text.count("operationId:") == 23
    assert "operationId: submitWowV17DailySnapshot" in text
    assert "operationId: getWowV17DailySnapshotRun" in text
    assert "schema: {$ref: '#/components/schemas/AsyncDailySubmitRequest'}" in text
    assert "x-openai-isConsequential: false" in text


def test_full_board_instructions_prefer_durable_daily_transport():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert "Full/current all-sport board: submitWowV17DailySnapshot" in text
    assert "poll getWowV17DailySnapshotRun" in text
    assert "Never start full-board work with synchronous runWowV17DailySnapshot" in text
    assert "can_execute=false" in text
