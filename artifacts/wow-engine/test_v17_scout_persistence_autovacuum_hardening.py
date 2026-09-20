from pathlib import Path


SQL = Path(__file__).parent / "v17/sql/20260920_scout_persistence_autovacuum_hardening.sql"


def test_scout_persistence_autovacuum_hardening_is_bounded_and_nonsemantic():
    text = SQL.read_text(encoding="utf-8").lower()
    assert "wow_scout.source_snapshots" in text
    assert "wow_scout.candidate_source_links" in text
    assert text.count("autovacuum_vacuum_scale_factor = 0.01") == 2
    assert text.count("autovacuum_vacuum_threshold = 1000") == 2
    assert "delete " not in text
    assert "truncate " not in text
    assert "drop " not in text
    assert "can_execute" not in text
    assert "probability" not in text
