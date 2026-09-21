from pathlib import Path


ROOT = Path(__file__).resolve().parent
SQL = ROOT / "prop_fitted_model_registry.sql"


def test_candidate_rows_may_leave_exact_line_bounds_unset_but_certified_lifecycles_may_not():
    text = SQL.read_text(encoding="utf-8")
    assert "supported_line_min numeric," in text
    assert "supported_line_max numeric," in text
    assert "supported_line_min numeric not null" not in text
    assert "supported_line_max numeric not null" not in text
    assert "lifecycle_state = 'CANDIDATE'" in text
    assert "supported_line_min is null and supported_line_max is null" in text
    assert "lifecycle_state <> 'CANDIDATE'" in text
    assert text.count("supported_line_min is not null") >= 2
    assert text.count("supported_line_max is not null") >= 2
    assert "supported_line_min >= 0" in text
    assert "supported_line_max >= supported_line_min" in text
