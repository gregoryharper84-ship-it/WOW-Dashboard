from __future__ import annotations

from v17 import daily_snapshot_runtime as daily
from v17 import free_core_source_policy as policy
from v17 import llp_rundown_market_bridge as bridge


def test_daily_run_owns_one_paid_budget_scope_and_resets(monkeypatch):
    seen = {}

    def impl(req, *, db, market_api, event_api):
        budget = policy.current_paid_budget()
        assert budget is not None
        seen["budget_id"] = id(budget)
        budget.record_attempt(policy.STAGE_DISCOVERY)
        return {"budget_used": budget.used, "can_execute": False}

    monkeypatch.setattr(daily, "_run_daily_snapshot_impl", impl)
    assert policy.current_paid_budget() is None
    out = daily.run_daily_snapshot(object(), db=object(), market_api=object(), event_api=object())
    assert out["budget_used"] == 1
    assert policy.current_paid_budget() is None


def test_standalone_market_context_owns_local_budget_and_resets(monkeypatch):
    seen = {}

    def impl(req, *, opener=None):
        budget = policy.current_paid_budget()
        assert budget is not None
        seen["budget"] = budget
        budget.record_attempt(policy.STAGE_INITIAL_ENRICHMENT)
        return {"status": "MARKET_DATA_UNOBTAINABLE", "can_execute": False}

    monkeypatch.setattr(bridge, "_resolve_rundown_market_context_impl", impl)
    assert policy.current_paid_budget() is None
    out = bridge.resolve_rundown_market_context(object())
    assert out["status"] == "MARKET_DATA_UNOBTAINABLE"
    assert seen["budget"].used == 1
    assert policy.current_paid_budget() is None


def test_market_context_reuses_existing_daily_budget(monkeypatch):
    outer = policy.PaidCallBudget(total=5, final_refresh_reserve=2)
    token = policy.begin_paid_budget_scope(outer)
    try:
        def impl(req, *, opener=None):
            assert policy.current_paid_budget() is outer
            outer.record_attempt(policy.STAGE_INITIAL_ENRICHMENT)
            return {"status": "MARKET_DATA_UNOBTAINABLE", "can_execute": False}

        monkeypatch.setattr(bridge, "_resolve_rundown_market_context_impl", impl)
        bridge.resolve_rundown_market_context(object())
        assert outer.used == 1
        assert policy.current_paid_budget() is outer
    finally:
        policy.end_paid_budget_scope(token)

    assert policy.current_paid_budget() is None
