from pathlib import Path


def workflow() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "wow-v17-scout-board-refresh.yml"
    ).read_text()


def test_missing_database_credential_fails_closed_instead_of_reporting_success():
    text = workflow()
    assert "Fail closed when the database credential is absent" in text
    assert 'if [ -z "$WOW_SCOUT_DATABASE_URL" ]; then' in text
    # The skip-and-succeed path is what made an unpersisted run look green.
    assert "board refresh skipped fail-closed" not in text
    assert "if: env.WOW_SCOUT_DATABASE_URL == ''" not in text
    assert "if: env.WOW_SCOUT_DATABASE_URL != ''" not in text


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
    assert "scout_board_materializer.py" in materialize
    assert "if:" not in materialize.split("run: |", 1)[0]


def test_research_only_governance_is_preserved():
    text = workflow()
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'test "$WOW_CAN_EXECUTE" = "false"' in text
