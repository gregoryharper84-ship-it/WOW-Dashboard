"""Offline Gemini cost/urgency routing contract; produces no API requests.

These are PRE-activation plans. An approved billing project is still mandatory,
and batch/cache adapters, durable expense limits, identity receipts and lifecycle
callbacks require separate engineering + SIRT acceptance before execution.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

CAN_EXECUTE = False
AUTHORITY = "EVIDENCE_ONLY"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
TASKS = frozenset({"code_review", "research_challenger", "sirt_challenger", "implementation_draft"})
URGENCY = frozenset({"INTERACTIVE", "BACKGROUND"})
COMPLEXITY = frozenset({"ROUTINE", "DEEP"})
CLASSIFICATIONS = frozenset({"PUBLIC", "SANITIZED"})

def plan_challenger(
    *, task: str, urgency: str, complexity: str, data_classification: str,
    paid_api_project_verified: bool, batch_requested: bool = False,
    explicit_cache_requested: bool = False,
) -> dict[str, object]:
    """Return a non-executable recommendation with explicit typed hold states."""
    if task not in TASKS or urgency not in URGENCY or complexity not in COMPLEXITY:
        raise ValueError("invalid_task_urgency_or_complexity")
    if data_classification not in CLASSIFICATIONS:
        raise ValueError("invalid_external_data_classification")
    model_tier = "FLASH_CANDIDATE" if complexity == "ROUTINE" else "PRO_CANDIDATE"
    result: dict[str, object] = {
        "provider": "gemini",
        "task": task,
        "model_tier": model_tier,
        "requested_transport": "BATCH" if batch_requested else "STANDARD",
        "requested_explicit_cache": explicit_cache_requested,
        "authority": AUTHORITY,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "status": "PLAN_ONLY",
        "reason": "REQUIRES_APPROVED_ACTIVATION_AND_BENCHMARK",
    }
    if not paid_api_project_verified:
        result.update(status="HOLD", reason="GEMINI_BILLING_UNVERIFIED")
    elif batch_requested and urgency != "BACKGROUND":
        result.update(status="HOLD", reason="GEMINI_BATCH_LATENCY_INCOMPATIBLE")
    elif batch_requested:
        result.update(status="HOLD", reason="GEMINI_BATCH_NOT_IMPLEMENTED")
    elif explicit_cache_requested:
        result.update(status="HOLD", reason="GEMINI_CACHE_NOT_IMPLEMENTED")
    return result


def forecast_usage_cost(
    *, uncached_input_tokens: int, cached_input_tokens: int,
    visible_output_tokens: int, thinking_tokens: int, cache_storage_token_hours: int,
    verified_price_card: dict[str, object],
) -> dict[str, object]:
    """Model-specific offline estimate, never a billing receipt or expenditure gate.

    Caller MUST supply a reviewed effective-date price card; no universal Gemini
    discount or 2026 forever-rate is baked in.
    """
    counts = (uncached_input_tokens, cached_input_tokens, visible_output_tokens,
              thinking_tokens, cache_storage_token_hours)
    if any(type(v) is not int or v < 0 for v in counts):
        raise ValueError("invalid_token_counts")
    card = verified_price_card
    if not isinstance(card, dict) or card.get("verified") is not True:
        raise ValueError("price_card_not_verified")
    for field in ("model_id", "pricing_mode", "effective_date", "source_url"):
        if not isinstance(card.get(field), str) or not card[field].strip():
            raise ValueError("price_card_metadata_missing")
    if card["pricing_mode"] not in {"STANDARD", "BATCH"}:
        raise ValueError("unsupported_pricing_mode")
    rate_names = ("uncached_input_per_million_usd", "cached_input_per_million_usd",
                  "output_including_thinking_per_million_usd",
                  "cache_storage_per_million_token_hours_usd")
    try:
        rates = [Decimal(str(card[name])) for name in rate_names]
        if any(not x.is_finite() or x < 0 for x in rates):
            raise ValueError("invalid_price_card_rates")
    except (KeyError, InvalidOperation, TypeError):
        raise ValueError("invalid_price_card_rates") from None
    counts_dec = [Decimal(x) for x in counts]
    total = (
        counts_dec[0] * rates[0]
        + counts_dec[1] * rates[1]
        + (counts_dec[2] + counts_dec[3]) * rates[2]
        + counts_dec[4] * rates[3]
    ) / Decimal(1_000_000)
    return {
        "model_id": card["model_id"],
        "pricing_mode": card["pricing_mode"],
        "effective_date": card["effective_date"],
        "source_url": card["source_url"],
        "estimated_cost_usd": str(total),
        "cost_status": "FORECAST_ONLY_NOT_BILLING_EVIDENCE",
        "can_execute": False,
    }
