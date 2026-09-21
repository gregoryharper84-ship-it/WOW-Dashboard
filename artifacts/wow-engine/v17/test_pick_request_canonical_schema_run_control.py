from pathlib import Path


def test_live_host_documents_both_canonical_and_run_control_action_contracts():
    root = Path(__file__).resolve().parents[1]
    instructions = (root / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt").read_text(encoding="utf-8")
    companion = (root / "v17" / "openapi.wow-betting-engine.v17.run-control.yaml").read_text(encoding="utf-8")

    assert "Canonical Action schema: v17/openapi.wow-betting-engine.v17.yaml" in instructions
    assert "Run-control companion Action schema: v17/openapi.wow-betting-engine.v17.run-control.yaml" in instructions
    for operation in (
        "getWowV17PickRequestRunState",
        "runWowV17ResumablePickRequest",
        "closeWowV17PickRequestRun",
    ):
        assert operation in instructions
        assert operation in companion
    assert "can_execute=false" in instructions
