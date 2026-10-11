"""Offline policy tests: no network/billing/dispatch."""
from v17.gemini_cost_routing import plan_challenger, forecast_usage_cost
import pytest


def kwargs(**overrides):
    defaults = {
        "task": "research_challenger", "urgency": "BACKGROUND", "complexity": "ROUTINE",
        "data_classification": "PUBLIC", "paid_api_project_verified": True,
    }
    defaults.update(overrides)
    return defaults


def test_standard_routes_flash_candidate_without_execution():
    result = plan_challenger(**kwargs())
    assert result["status"] == "PLAN_ONLY"
    assert result["requested_transport"] == "STANDARD"
    assert result["model_tier"] == "FLASH_CANDIDATE"
    assert result["can_execute"] is False


def test_deep_analysis_pro_candidate_not_automatic_payment():
    result = plan_challenger(**kwargs(complexity="DEEP"))
    assert result["model_tier"] == "PRO_CANDIDATE"
    assert result["status"] == "PLAN_ONLY"


def test_batch_planned_but_no_async_backend_fabricated():
    result = plan_challenger(**kwargs(batch_requested=True))
    assert result["status"] == "HOLD" and result["reason"] == "GEMINI_BATCH_NOT_IMPLEMENTED"


def test_interactive_batch_forbidden():
    result = plan_challenger(**kwargs(urgency="INTERACTIVE", batch_requested=True))
    assert result["reason"] == "GEMINI_BATCH_LATENCY_INCOMPATIBLE"


def test_explicit_cache_requires_real_adapter_and_privacy_review():
    result = plan_challenger(**kwargs(explicit_cache_requested=True))
    assert result["reason"] == "GEMINI_CACHE_NOT_IMPLEMENTED"


def test_unpaid_tier_forbidden_even_for_public_input():
    result = plan_challenger(**kwargs(paid_api_project_verified=False))
    assert result["reason"] == "GEMINI_BILLING_UNVERIFIED"


@pytest.mark.parametrize("bad", [
    {"task": "sporting_probability"},
    {"data_classification": "PRIVATE"},
    {"complexity": "UNDEFINED"},
    {"urgency": "URGENT_NOW"},
])
def test_invalid_scope_cannot_route(bad):
    with pytest.raises(ValueError):
        plan_challenger(**kwargs(**bad))


def card(mode="STANDARD", **overrides):
    c = {
        "verified": True, "model_id": "test-model-only", "pricing_mode": mode,
        "effective_date": "2026-10-10", "source_url": "https://example.invalid/reviewed-pricing",
        "uncached_input_per_million_usd": "1",
        "cached_input_per_million_usd": "0.1",
        "output_including_thinking_per_million_usd": "5",
        "cache_storage_per_million_token_hours_usd": "2",
    }
    c.update(overrides)
    return c


def estimate(**overrides):
    return forecast_usage_cost(
        uncached_input_tokens=100000,
        cached_input_tokens=50000,
        visible_output_tokens=1000,
        thinking_tokens=3000,
        cache_storage_token_hours=1000000,
        verified_price_card=card(**overrides),
    )


def test_estimate_counts_thinking_and_cache_storage_separately():
    result = estimate()
    # 0.1 + 0.005 + 0.020 + 2 = 2.125 USD
    assert result["estimated_cost_usd"] == "2.125"
    assert result["cost_status"] == "FORECAST_ONLY_NOT_BILLING_EVIDENCE"


def test_batch_requires_its_own_reviewed_price_card_not_automatic_halving():
    assert estimate(mode="BATCH")["estimated_cost_usd"] == "2.125"


@pytest.mark.parametrize("bad", [
    {"verified": False},
    {"effective_date": ""},
    {"output_including_thinking_per_million_usd": "-1"},
    {"uncached_input_per_million_usd": "not-a-number"},
])
def test_invalid_or_unverified_rate_card_rejected(bad):
    with pytest.raises(ValueError):
        estimate(**bad)


def test_negative_thinking_count_rejected():
    with pytest.raises(ValueError):
        forecast_usage_cost(
            uncached_input_tokens=1, cached_input_tokens=0,
            visible_output_tokens=1, thinking_tokens=-1,
            cache_storage_token_hours=0, verified_price_card=card())
