from pathlib import Path


MIGRATION = Path(__file__).parent / "migrations" / "20260830_mlb_event_fitted_model_registry.sql"


def test_registry_is_separate_and_non_executable():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "wow_mlb_event_fitted_model_artifacts" in sql
    assert "WOW_MLB_EVENT_FITTED_MODEL_V1" in sql
    assert "wow_mlb_research_model_artifacts" in sql
    assert "can_execute boolean not null default false" in sql
    assert "check (can_execute = false)" in sql
    assert "probability_publishable boolean not null default false" in sql
    assert "check (probability_publishable = false)" in sql


def test_certified_lifecycle_requires_calibrator_and_certification():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "calibrator_id uuid references public.wow_calibrators(calibrator_id)" in sql
    assert "certification_id text" in sql
    assert "PROSPECTIVE_CERTIFIED" in sql
    assert "CHAMPION" in sql
    assert "active = true" in sql
    assert "promoted = true" in sql
    assert "calibrator_id is not null" in sql
    assert "certification_id is not null" in sql
    assert "promoted_at is not null" in sql


def test_lookup_fails_closed_and_never_promotes_capability():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "wow_mlb_event_certified_model_artifact" in sql
    assert "MLB_EVENT_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND" in sql
    assert "MLB_EVENT_CERTIFIED_MODEL_ARTIFACT_READY" in sql
    assert "probability_publishable', false" in sql
    assert "can_execute', false" in sql
    assert "update public.wow_runtime_capabilities" not in sql.lower()
    assert "insert into public.wow_mlb_event_fitted_model_artifacts" not in sql.lower()
