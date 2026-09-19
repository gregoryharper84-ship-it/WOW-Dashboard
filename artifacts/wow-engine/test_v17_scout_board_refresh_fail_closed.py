from pathlib import Path


def workflow() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "wow-v17-scout-board-refresh.yml"
    ).read_text()


def test_board_refresh_uses_short_lived_oidc_instead_of_database_secret():
    text = workflow()
    assert "WOW_SCOUT_DATABASE_URL" not in text
    assert "id-token: write" in text
    assert "WOW_SCOUT_PERSIST_URL" in text
    assert "scout_board_edge_materializer" in text


def test_board_refresh_cannot_report_success_when_brain_persist_did_not_succeed():
    text = workflow()
    assert "Fail closed when the upstream Scout brain persist did not succeed" in text
    assert "wow-v17-scout-brain-persist.yml/runs" in text
    assert 'if [ "$conclusion" != "success" ]; then' in text
    # The workflow_run path is already gated by the job-level conclusion check.
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "actions: read" in text


def test_materialization_is_unconditional_once_the_gates_pass():
    text = workflow()
    materialize = text.split("Materialize current boards", 1)[1]
    assert "scout_board_edge_materializer" in materialize
    assert "if:" not in materialize.split("run: |", 1)[0]


def test_research_only_governance_is_preserved():
    text = workflow()
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'test "$WOW_CAN_EXECUTE" = "false"' in text

