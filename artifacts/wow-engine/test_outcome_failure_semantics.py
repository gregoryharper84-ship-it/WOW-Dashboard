from pathlib import Path


MIGRATION = Path("migrations/20260831_outcome_failure_semantics.sql")


def _sql() -> str:
    return MIGRATION.read_text()


def test_miss_cannot_promote_predicted_failure_path_to_observed_cause():
    sql = _sql()
    lowered = sql.lower()
    assert "new.failure_category := 'MODEL_MISS'" in sql
    assert "primary_failure_path" in lowered
    assert "new.failure_category := new.primary_failure_path" not in lowered
    assert "new.failure_category := p.primary_failure_path" not in lowered


def test_hit_and_push_do_not_receive_failure_categories():
    sql = _sql()
    assert "in ('HIT','PUSH')" in sql
    assert "new.failure_category := null" in sql


def test_normalization_is_insert_only_and_preserves_immutable_outcome_model():
    sql = _sql().lower()
    assert "before insert on public.wow_outcomes" in sql
    assert "before update" not in sql
    assert "before delete" not in sql


def test_semantics_hardening_never_executes_orders():
    sql = _sql().lower()
    for forbidden_callable in ("place_bet(", "place_wager(", "market_order(", "submit_order(", "cancel_order("):
        assert forbidden_callable not in sql
