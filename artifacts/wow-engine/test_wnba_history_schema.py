from pathlib import Path


def test_raw_history_schema_is_non_executable_and_role_unresolved():
    sql = Path(__file__).with_name("wnba_history_schema.sql").read_text().lower()
    assert "create table if not exists public.wow_wnba_player_game_logs" in sql
    assert "alter table public.wow_wnba_player_game_logs enable row level security" in sql
    assert "can_execute boolean not null default false" in sql
    assert "check (can_execute = false)" in sql
    assert "role_evidence_status text not null default 'unresolved'" in sql
    assert "training_materialization_status text not null default 'blocked_role_evidence'" in sql

    table_body = sql.split("create table if not exists public.wow_wnba_player_game_logs (", 1)[1].split(");", 1)[0]
    column_names = {
        line.strip().split()[0]
        for line in table_body.splitlines()
        if line.strip() and not line.strip().startswith(("constraint", "--")) and line.startswith("    ")
    }
    assert "starter" not in column_names
    assert "probability" not in column_names
    assert "model_family" not in column_names
