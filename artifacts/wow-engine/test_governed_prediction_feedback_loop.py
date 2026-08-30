from pathlib import Path


MIGRATION = Path("migrations/20260830_governed_prediction_feedback_loop.sql")


def _sql() -> str:
    return MIGRATION.read_text()


def test_feedback_loop_uses_official_mlb_source_and_exact_identity():
    sql = _sql()
    assert "https://statsapi.mlb.com/api/v1/schedule" in sql
    assert "https://statsapi.mlb.com/api/v1.1/game/%s/feed/live" in sql
    assert "SCHEDULE_MATCH_NOT_UNIQUE" in sql
    assert "OFFICIAL_EVENT_ID_MISMATCH" in sql
    assert "PLAYER_IDENTITY_UNRESOLVED" in sql
    assert "OFFICIAL_TEAM_IDENTITY_MISMATCH" in sql


def test_prop_grader_preserves_hit_miss_push_and_void():
    sql = _sql()
    assert "PITCHER_STRIKEOUTS" in sql
    assert "VOID_NOT_STARTER" in sql
    assert "v_result := 'PUSH'" in sql
    assert "v_result := case when v_hit then 'HIT' else 'MISS' end" in sql
    assert "on conflict (prediction_id) do nothing" in sql


def test_event_grader_is_exact_full_game_outright_only():
    sql = _sql()
    assert "OUTRIGHT_WINNER" in sql
    assert "FULL_GAME_INCLUDING_EXTRA_INNINGS" in sql
    assert "on conflict (event_prediction_id) do nothing" in sql
    assert "v_state <> 'Final'" in sql


def test_outcomes_are_immutable_and_service_role_cannot_mutate_them():
    sql = _sql().lower()
    for table in ("wow_outcomes", "wow_event_outcomes", "wow_ncaaf_outcomes"):
        assert f"before update or delete on public.{table}" in sql
        assert f"revoke update, delete, truncate on public.{table} from service_role" in sql


def test_dispatcher_fails_closed_for_unwired_lanes_and_never_executes():
    sql = _sql()
    assert "wow_governed_auto_grade_predictions" in sql
    assert "CERTIFIED_OFFICIAL_OUTCOME_ADAPTER_NOT_WIRED" in sql
    assert "'can_execute',false" in sql
    assert "market order" not in sql.lower()
    assert "place wager" not in sql.lower()


def test_dispatcher_is_scheduled_once_for_primary_ledgers():
    sql = _sql()
    assert "wow-governed-primary-ledger-auto-grade" in sql
    assert "cron.unschedule" in sql
    assert "*/15 * * * *" in sql
