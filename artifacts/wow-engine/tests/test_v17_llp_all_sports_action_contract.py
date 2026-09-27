from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
SCHEMA = ROOT / "v17" / "openapi.llp-team-engine.v17.yaml"
EDITOR_INSTRUCTIONS = ROOT / "LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt"
AUTHORITY_INSTRUCTIONS = REPO_ROOT / "LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md"


def _schema():
    return yaml.safe_load(SCHEMA.read_text())


def test_llp_schema_exposes_governed_moneyline_full_slate_action():
    document = _schema()
    operation = document["paths"]["/v17/daily-snapshot-run"]["post"]
    assert operation["operationId"] == "runLlpV17FullSlate"
    assert operation["security"] == [{"actionBearer": []}]
    assert operation["x-openai-isConsequential"] is False

    request = document["components"]["schemas"]["LlpFullSlateRequest"]
    assert set(request["required"]) == {
        "requested_slate_date",
        "requested_timezone",
        "lanes",
        "max_props",
        "max_team_events",
        "response_mode",
    }
    assert request["properties"]["lanes"]["maxItems"] == 1
    assert request["properties"]["lanes"]["items"]["enum"] == ["MONEYLINE"]
    assert request["properties"]["max_props"]["enum"] == [0]
    assert request["properties"]["max_team_events"]["maximum"] == 12
    assert request["properties"]["response_mode"]["enum"] == ["FULL"]


def test_llp_schema_exposes_full_slate_row_detail_readback():
    operation = _schema()["paths"]["/v17/daily-snapshot-run/{run_id}/rows"]["get"]
    assert operation["operationId"] == "readLlpV17FullSlateRows"
    assert operation["security"] == [{"actionBearer": []}]
    assert operation["x-openai-isConsequential"] is False


def test_llp_editor_sources_require_scan_before_shortlist_and_budget_completion():
    for path in (AUTHORITY_INSTRUCTIONS, EDITOR_INSTRUCTIONS):
        text = path.read_text()
        assert "runLlpV17FullSlate" in text
        assert "MODEL_INVOCATION_BUDGET_REACHED" in text
        assert "scoreLlpV17TeamEvent" in text
        assert "partial shortlist" in text or "before ranking or shortlisting" in text


def test_authority_instruction_block_remains_pasteable():
    text = AUTHORITY_INSTRUCTIONS.read_text()
    pasteable = text.split("```", 2)[1]
    assert len(pasteable) < 8000
