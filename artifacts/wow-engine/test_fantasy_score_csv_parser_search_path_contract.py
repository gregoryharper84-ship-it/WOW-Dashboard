from pathlib import Path

SQL = (Path(__file__).resolve().parent / "migrations" / "20260916_fantasy_score_csv_parser_search_path.sql").read_text().lower()


def test_parser_search_path_is_pinned():
    assert "alter function public.wow_v17_csv_fields(text)" in SQL
    assert "set search_path = pg_catalog" in SQL


def test_parser_execution_stays_service_role_only():
    assert "revoke all on function public.wow_v17_csv_fields(text) from public, anon, authenticated" in SQL
    assert "grant execute on function public.wow_v17_csv_fields(text) to service_role" in SQL


def test_search_path_hardening_does_not_claim_new_authority():
    assert "no parsing/model/lifecycle behavior changes" in SQL
    assert "publication/execution semantics unchanged" in SQL
