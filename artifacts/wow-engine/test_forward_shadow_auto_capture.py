from pathlib import Path


SQL_PATH = Path(__file__).with_name("forward_shadow_auto_capture.sql")


def _raw() -> str:
    return SQL_PATH.read_text().lower()


def _sql() -> str:
    return " ".join(_raw().split())


def _identity_sql() -> str:
    text = _raw()
    start = text.index("create or replace function public.wow_mlb_forward_pregame_identity")
    end = text.index("$function$;", start)
    return " ".join(text[start:end].split())


def test_identity_contains_only_stable_pregame_ids_and_schedule_fields():
    sql = _identity_sql()
    for token in (
        "gamepk",
        "gamedate",
        "officialdate",
        "gametype",
        "doubleheader",
        "gamenumber",
        "scheduledinnings",
        "hometeamid",
        "awayteamid",
        "venueid",
        "homestarterid",
        "awaystarterid",
    ):
        assert token in sql

    for forbidden in (
        "status",
        "score",
        "linescore",
        "abstractgamestate",
        "detailedstate",
        "hometeamname",
        "awayteamname",
        "venuename",
        "homestartername",
        "awaystartername",
    ):
        assert forbidden not in sql


def test_provenance_timestamp_is_taken_after_official_http_response():
    text = _raw()
    http_pos = text.index("v_resp := extensions.http_get")
    capture_pos = text.index("v_capture_at := clock_timestamp()", http_pos)
    future_filter_pos = text.index("(v_game->>'gamedate')::timestamptz <= v_capture_at", capture_pos)
    assert http_pos < capture_pos < future_filter_pos
    assert "v_capture_at timestamptz := clock_timestamp()" not in text


def test_collector_compares_only_future_games_and_serializes_capture():
    sql = _sql()
    assert "pg_advisory_xact_lock" in sql
    assert "hashtextextended" in sql
    assert "(v_game->>'gamedate')::timestamptz <= v_capture_at" in sql
    assert "prior.event_start_time > v_capture_at" in sql
    assert "unchanged_pregame_identity" in sql
    assert "captured_material_change" in sql


def test_raw_hash_is_audit_index_not_capture_identity_uniqueness():
    sql = _sql()
    assert "drop constraint if exists wow_mlb_forward_shadow_source_snapsho_slate_date_raw_sha256_key" in sql
    assert "create index if not exists idx_wow_mlb_forward_shadow_source_slate_raw_sha256" in sql
    assert "on conflict(slate_date,raw_sha256)" not in sql
    assert "returning snapshot_id into v_snapshot_id" in sql


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
