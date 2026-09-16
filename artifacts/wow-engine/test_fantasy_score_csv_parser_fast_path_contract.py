from pathlib import Path

SQL = (Path(__file__).resolve().parent / "migrations" / "20260916_fantasy_score_csv_parser_fast_path.sql").read_text()


def test_fast_path_preserves_full_parser_fallback():
    assert "v_work := replace(p_line, 'f_auto,q_auto', 'f_auto__WOW_CSV_COMMA__q_auto')" in SQL
    assert "if v_work !~ '\"[^\"]*,[^\"]*\"' then" in SQL
    assert "string_to_array(v_work, ',')" in SQL
    assert "regexp_matches(p_line" in SQL
    assert "'(?:^|,)(?:\"((?:[^\"]|\"\")*)\"|([^,]*))'" in SQL


def test_fast_path_restores_url_and_preserves_order():
    assert "with ordinality u(x, ord)" in SQL
    assert "array_agg" in SQL
    assert "order by ord" in SQL
    assert "'f_auto__WOW_CSV_COMMA__q_auto'" in SQL
    assert "'f_auto,q_auto'" in SQL
    assert "replace(trim(both '\"' from x), '\"\"', '\"')" in SQL


def test_parser_remains_service_role_only():
    lowered = SQL.lower()
    assert "revoke all on function public.wow_v17_csv_fields(text) from public, anon, authenticated" in lowered
    assert "grant execute on function public.wow_v17_csv_fields(text) to service_role" in lowered


def test_basketball_source_width_repair_is_guarded_and_idempotent():
    assert "array_length(a,1)=50" in SQL
    assert "array_length(a,1)=57" in SQL
    assert "FANTASY_SCORE_BASKETBALL_SCHEMA_GUARD_NOT_FOUND" in SQL
    assert "FANTASY_SCORE_BASKETBALL_SCHEMA_GUARD_PATCH_INCOMPLETE" in SQL
    assert "pg_get_functiondef" in SQL
    assert "execute v_patched" in SQL


def test_builder_authority_comment_remains_fail_closed():
    lowered = SQL.lower()
    assert "never grants publication, ranking, or execution authority" in lowered
