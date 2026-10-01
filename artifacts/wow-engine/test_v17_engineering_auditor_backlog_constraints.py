from pathlib import Path

from v17.engineering_auditor import severity_from_labels


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "artifacts"
    / "wow-engine"
    / "migrations"
    / "20261001231000_align_engineering_auditor_backlog_constraints.sql"
)
AUDITOR = ROOT / "artifacts" / "wow-engine" / "v17" / "engineering_auditor.py"


def _normalized_migration() -> str:
    return " ".join(MIGRATION.read_text().split())


def test_auditor_can_emit_p4_and_migration_accepts_it() -> None:
    assert severity_from_labels(["P4"]) == "P4"
    sql = _normalized_migration()
    assert "priority in ('P0', 'P1', 'P2', 'P3', 'P4')" in sql


def test_auditor_backlog_source_matches_migration_constraint() -> None:
    auditor_text = AUDITOR.read_text()
    assert '"source": "ENGINEERING_AUDITOR"' in auditor_text
    sql = _normalized_migration()
    assert "source in ('engineering', 'pm', 'ENGINEERING_AUDITOR')" in sql


def test_alignment_is_additive_and_does_not_touch_probability_or_execution_authority() -> None:
    sql = _normalized_migration().lower()
    assert "alter table public.wow_engineering_backlog" in sql
    assert "drop table" not in sql
    assert "model_probability" not in sql
    assert "can_execute" not in sql
    assert "v17_terminal_reducer" not in sql
