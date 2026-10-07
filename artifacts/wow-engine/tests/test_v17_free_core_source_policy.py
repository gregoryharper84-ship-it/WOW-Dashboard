from __future__ import annotations

from v17 import free_core_source_policy as policy


def test_free_core_disables_all_paid_provider_calls(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    budget = policy.PaidCallBudget.from_env()

    assert budget.total == 0
    assert policy.provider_hierarchy() == ("OFFICIAL_FREE", "DATA_UNOBTAINABLE")
    for stage in (
        policy.STAGE_DISCOVERY,
        policy.STAGE_INITIAL_ENRICHMENT,
        policy.STAGE_FINAL_REFRESH,
    ):
        allowed, code = budget.check(stage, model_preflight_passed=True)
        assert allowed is False
        assert code == policy.BLOCK_FREE_CORE


def test_hybrid_requires_model_preflight_before_initial_paid_enrichment(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "HYBRID")
    monkeypatch.setenv("WOW_V17_PAID_CALL_BUDGET_PER_SCAN", "10")
    monkeypatch.setenv("WOW_V17_PAID_FINAL_REFRESH_RESERVE", "3")
    budget = policy.PaidCallBudget.from_env()

    assert budget.check(
        policy.STAGE_INITIAL_ENRICHMENT,
        model_preflight_passed=False,
    ) == (False, policy.BLOCK_PREFLIGHT)
    assert budget.check(
        policy.STAGE_INITIAL_ENRICHMENT,
        model_preflight_passed=True,
    ) == (True, None)


def test_final_refresh_reserve_cannot_be_spent_by_initial_enrichment(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "HYBRID")
    monkeypatch.setenv("WOW_V17_PAID_CALL_BUDGET_PER_SCAN", "4")
    monkeypatch.setenv("WOW_V17_PAID_FINAL_REFRESH_RESERVE", "2")
    budget = policy.PaidCallBudget.from_env()

    for _ in range(2):
        assert budget.check(
            policy.STAGE_INITIAL_ENRICHMENT,
            model_preflight_passed=True,
        ) == (True, None)
        budget.record_attempt(policy.STAGE_INITIAL_ENRICHMENT)

    assert budget.remaining == 2
    assert budget.check(
        policy.STAGE_INITIAL_ENRICHMENT,
        model_preflight_passed=True,
    ) == (False, policy.BLOCK_RESERVED)

    assert budget.check(policy.STAGE_FINAL_REFRESH) == (True, None)
    budget.record_attempt(policy.STAGE_FINAL_REFRESH)
    assert budget.check(policy.STAGE_FINAL_REFRESH) == (True, None)


def test_paid_discovery_fallback_is_activation_controlled(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "HYBRID")
    monkeypatch.setenv("WOW_V17_PAID_DISCOVERY_FALLBACK_ENABLED", "false")
    budget = policy.PaidCallBudget.from_env()
    assert budget.check(policy.STAGE_DISCOVERY) == (False, policy.BLOCK_DISCOVERY)

    monkeypatch.setenv("WOW_V17_PAID_DISCOVERY_FALLBACK_ENABLED", "true")
    budget = policy.PaidCallBudget.from_env()
    assert budget.check(policy.STAGE_DISCOVERY) == (True, None)


def test_provider_failure_is_row_scoped_not_global():
    out = policy.typed_row_degradation(
        provider="RUNDOWN",
        code="RUNDOWN_QUOTA_EXHAUSTED",
        stage=policy.STAGE_INITIAL_ENRICHMENT,
    )
    assert out["scope"] == "ROW_OR_STAGE_ONLY"
    assert out["global_slate_failure"] is False
    assert out["probability_substitution_allowed"] is False
    assert out["can_execute"] is False



def test_paid_budget_scope_is_shared_then_reset(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "HYBRID")
    budget = policy.PaidCallBudget(total=3, final_refresh_reserve=1)
    token = policy.begin_paid_budget_scope(budget)
    try:
        current = policy.current_paid_budget()
        assert current is budget
        checked, allowed, blocker = policy.check_paid_call(
            policy.STAGE_INITIAL_ENRICHMENT,
            model_preflight_passed=True,
        )
        assert checked is budget
        assert allowed is True
        assert blocker is None
        policy.record_paid_call(policy.STAGE_INITIAL_ENRICHMENT, checked)
        assert policy.current_paid_budget().used == 1
    finally:
        policy.end_paid_budget_scope(token)

    assert policy.current_paid_budget() is None
