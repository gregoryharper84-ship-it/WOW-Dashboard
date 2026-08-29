from pathlib import Path


SQL_PATH = Path(__file__).with_name("forward_shadow_auto_capture.sql")


def _sql() -> str:
    return " ".join(SQL_PATH.read_text().split()).lower()


def _identity_sql() -> str:
    text = SQL_PATH.read_text().lower()
    start = text.index("create or replace function public.wow_mlb_forward_pregame_identity")
    end = text.index("$function$;", start)
    return " ".join(text[start:end].split())


def test_identity_contains_only_stable_pregame_fields():
    sql = _identity_sql()
    for token in (
        "gamepk",
        "gamedate",
        "officialdate",
        "gametype",
        "scheduledinnings",
        "hometeamid",
        "awayteamid",
        "venueid",
        "homestarterid",
        "awaystarterid",
    ):
        assert token in sql

    assert "status" not in sql
    assert "score" not in sql
    assert "linescore" not in sql
    assert "abstractgamestate" not in sql
    assert "detailedstate" not in sql


def test_collector_compares_only_future_games_and_serializes_capture():
    sql = _sql()
    assert "pg_advisory_xact_lock" in sql
    assert "hashtextextended" in sql
    assert "(v_game->>'gamedate')::timestamptz <= v_capture_at" in sql
    assert "prior.event_start_time > v_capture_at" in sql
    assert "unchanged_pregame_identity" in sql
    assert "captured_material_change" in sql


def test_collector_accumulates_row_count_with_valid_plpgsql_shape():
    sql = _sql()
    assert "v_row_count integer := 0" in sql
    assert "get diagnostics v_row_count = row_count" in sql
    assert "v_inserted_n := v_inserted_n + v_row_count" in sql
    assert "get diagnostics v_inserted_n = v_inserted_n + row_count" not in sql


def test_collector_remains_fail_closed():
    sql = _sql()
    assert "research_only,probability_publishable,can_execute" in sql
    assert "true,false,false" in sql
    assert "'probability_publishable',false" in sql
    assert "'can_execute',false" in sql
    assert "production_feature_ready" not in sql
    assert "governed_probability_capability" not in sql


def test_capture_is_staggered_ahead_of_hydration_and_grading():
    sql = _sql()
    assert "'wow-mlb-forward-shadow-auto-capture'" in sql
    assert "'2,17,32,47 * * * *'" in sql
    assert "select public.wow_mlb_forward_auto_capture_pregame();" in sql
