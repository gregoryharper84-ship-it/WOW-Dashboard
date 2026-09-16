from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "migrations" / "20260916_mlb_fantasy_score_candidate_builder.sql"
PATCH = ROOT / "migrations" / "20260916_fix_mlb_fantasy_score_player_mean_count.sql"


def test_followup_patch_replaces_postgres_incompatible_jsonb_object_length():
    base = BASE.read_text()
    patch = PATCH.read_text()
    assert "jsonb_object_length(v_player_means)" in base
    assert "pg_get_functiondef" in patch
    assert "jsonb_object_keys(v_player_means)" in patch
    assert "replace(v_def, v_old, v_new)" in patch


def test_followup_patch_reasserts_non_execution_and_service_role_only_invocation():
    patch = PATCH.read_text().lower()
    assert "revoke all on function public.wow_v17_build_mlb_fantasy_score_candidates" in patch
    assert "from anon, authenticated" in patch
    assert "grant execute on function public.wow_v17_build_mlb_fantasy_score_candidates" in patch
    assert "to service_role" in patch
    assert "never grants publication, ranking, or execution authority" in patch
