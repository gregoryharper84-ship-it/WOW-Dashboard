from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_prop_capability_auto_refresh_is_derived_from_artifact_lifecycle_changes():
    sql = (
        ROOT / "migrations" / "20260921_v17_prop_capability_auto_refresh.sql"
    ).read_text().lower()

    assert "wow_v17_refresh_prop_probability_capability()" in sql
    assert "after insert or update or delete" in sql
    assert "for each statement" in sql
    assert "wow_prop_fitted_model_artifacts" in sql
    assert "can_execute=false" in sql


def test_prop_capability_auto_refresh_does_not_promote_or_edit_artifacts():
    sql = (
        ROOT / "migrations" / "20260921_v17_prop_capability_auto_refresh.sql"
    ).read_text().lower()

    assert "update public.wow_prop_fitted_model_artifacts" not in sql
    assert "insert into public.wow_prop_fitted_model_artifacts" not in sql
    assert "delete from public.wow_prop_fitted_model_artifacts" not in sql
