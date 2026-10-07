"""Source-policy controls for V17 Free-Core Independence.

This module is acquisition/control-plane only. It never creates, calibrates,
blends, ranks, or substitutes a sporting probability. Paid providers are
optional market/evidence enrichment and may be disabled completely without
changing fitted-model mathematics.

Activation is intentionally explicit because Free-Core routing is a Class B
change. Production keeps HYBRID semantics until governed promotion enables
WOW_V17_SOURCE_MODE=FREE_CORE.
"""
from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

MODE_HYBRID = "HYBRID"
MODE_FREE_CORE = "FREE_CORE"
VALID_MODES = frozenset({MODE_HYBRID, MODE_FREE_CORE})

STAGE_DISCOVERY = "DISCOVERY"
STAGE_INITIAL_ENRICHMENT = "INITIAL_ENRICHMENT"
STAGE_FINAL_REFRESH = "FINAL_REFRESH"
VALID_STAGES = frozenset(
    {STAGE_DISCOVERY, STAGE_INITIAL_ENRICHMENT, STAGE_FINAL_REFRESH}
)

_BUDGET_CONTEXT: ContextVar["PaidCallBudget | None"] = ContextVar(
    "wow_v17_paid_call_budget", default=None
)

BLOCK_FREE_CORE = "PAID_PROVIDER_DISABLED_FREE_CORE"
BLOCK_DISCOVERY = "PAID_PROVIDER_DISCOVERY_DISABLED"
BLOCK_PREFLIGHT = "PAID_PROVIDER_PREFLIGHT_REQUIRED"
BLOCK_BUDGET = "PAID_PROVIDER_BUDGET_EXHAUSTED"
BLOCK_RESERVED = "PAID_PROVIDER_BUDGET_RESERVED_FOR_FINAL_REFRESH"


def _truthy(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(name: str, default: int, *, minimum: int = 0, maximum: int = 10000) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def source_mode() -> str:
    value = str(os.environ.get("WOW_V17_SOURCE_MODE", MODE_HYBRID)).strip().upper()
    return value if value in VALID_MODES else MODE_HYBRID


def free_core_enabled() -> bool:
    return source_mode() == MODE_FREE_CORE


def paid_discovery_fallback_enabled() -> bool:
    if free_core_enabled():
        return False
    return _truthy("WOW_V17_PAID_DISCOVERY_FALLBACK_ENABLED", True)


def provider_hierarchy() -> tuple[str, ...]:
    if free_core_enabled():
        return ("OFFICIAL_FREE", "DATA_UNOBTAINABLE")
    return (
        "OFFICIAL_FREE",
        "PAID_PRIMARY_OPTIONAL",
        "PAID_SECONDARY_OPTIONAL",
        "DATA_UNOBTAINABLE",
    )


@dataclass
class PaidCallBudget:
    """Per-scan optional-paid-call budget with a protected final-refresh reserve."""

    total: int
    final_refresh_reserve: int
    used: int = 0
    discovery_used: int = 0
    initial_enrichment_used: int = 0
    final_refresh_used: int = 0

    @classmethod
    def from_env(cls) -> "PaidCallBudget":
        if free_core_enabled():
            return cls(total=0, final_refresh_reserve=0)
        total = _bounded_int("WOW_V17_PAID_CALL_BUDGET_PER_SCAN", 20)
        reserve = _bounded_int("WOW_V17_PAID_FINAL_REFRESH_RESERVE", 5)
        reserve = min(reserve, total)
        return cls(total=total, final_refresh_reserve=reserve)

    @property
    def remaining(self) -> int:
        return max(self.total - self.used, 0)

    def check(
        self,
        stage: str,
        *,
        model_preflight_passed: bool = False,
    ) -> tuple[bool, str | None]:
        normalized = str(stage or "").strip().upper()
        if normalized not in VALID_STAGES:
            raise ValueError(f"UNSUPPORTED_PAID_PROVIDER_STAGE:{normalized}")
        if free_core_enabled():
            return False, BLOCK_FREE_CORE
        if normalized == STAGE_DISCOVERY and not paid_discovery_fallback_enabled():
            return False, BLOCK_DISCOVERY
        if normalized == STAGE_INITIAL_ENRICHMENT and not model_preflight_passed:
            return False, BLOCK_PREFLIGHT
        if self.remaining <= 0:
            return False, BLOCK_BUDGET
        if (
            normalized == STAGE_INITIAL_ENRICHMENT
            and self.remaining <= self.final_refresh_reserve
        ):
            return False, BLOCK_RESERVED
        return True, None

    def record_attempt(self, stage: str) -> None:
        normalized = str(stage or "").strip().upper()
        if normalized not in VALID_STAGES:
            raise ValueError(f"UNSUPPORTED_PAID_PROVIDER_STAGE:{normalized}")
        self.used += 1
        if normalized == STAGE_DISCOVERY:
            self.discovery_used += 1
        elif normalized == STAGE_INITIAL_ENRICHMENT:
            self.initial_enrichment_used += 1
        else:
            self.final_refresh_used += 1

    def receipt(self) -> dict[str, Any]:
        return {
            "source_mode": source_mode(),
            "paid_provider_dependency": "OPTIONAL",
            "paid_discovery_fallback_enabled": paid_discovery_fallback_enabled(),
            "provider_hierarchy": list(provider_hierarchy()),
            "paid_call_budget_total": self.total,
            "paid_call_budget_used": self.used,
            "paid_call_budget_remaining": self.remaining,
            "paid_final_refresh_reserve": self.final_refresh_reserve,
            "paid_discovery_calls_used": self.discovery_used,
            "paid_initial_enrichment_calls_used": self.initial_enrichment_used,
            "paid_final_refresh_calls_used": self.final_refresh_used,
            "probability_only_paid_market_calls_required": False,
            "market_probability_substitution_allowed": False,
            "generic_reasoning_probability_substitution_allowed": False,
            "terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": False,
        }



def begin_paid_budget_scope(
    budget: "PaidCallBudget | None" = None,
) -> Token:
    """Bind one paid-call budget to an entire governed run/slate."""
    return _BUDGET_CONTEXT.set(budget or PaidCallBudget.from_env())


def end_paid_budget_scope(token: Token) -> None:
    _BUDGET_CONTEXT.reset(token)


def current_paid_budget(*, create_if_missing: bool = False) -> "PaidCallBudget | None":
    budget = _BUDGET_CONTEXT.get()
    if budget is None and create_if_missing:
        budget = PaidCallBudget.from_env()
        _BUDGET_CONTEXT.set(budget)
    return budget


def check_paid_call(
    stage: str,
    *,
    model_preflight_passed: bool = False,
) -> tuple["PaidCallBudget", bool, str | None]:
    """Check one optional paid call against the shared run budget.

    A standalone caller gets its own bounded budget. A full slate should bind a
    scope first so all rows share the same reserve.
    """
    budget = current_paid_budget(create_if_missing=True)
    assert budget is not None
    allowed, blocker = budget.check(
        stage,
        model_preflight_passed=model_preflight_passed,
    )
    return budget, allowed, blocker


def record_paid_call(stage: str, budget: "PaidCallBudget | None" = None) -> None:
    target = budget or current_paid_budget(create_if_missing=True)
    assert target is not None
    target.record_attempt(stage)


def typed_row_degradation(
    *,
    provider: str,
    code: str,
    stage: str,
) -> dict[str, Any]:
    """Describe an optional-provider failure without manufacturing a global outage."""
    return {
        "scope": "ROW_OR_STAGE_ONLY",
        "provider": str(provider or "UNKNOWN").upper(),
        "stage": str(stage or "UNKNOWN").upper(),
        "code": str(code or "DATA_UNOBTAINABLE"),
        "global_slate_failure": False,
        "probability_substitution_allowed": False,
        "can_execute": False,
    }


__all__ = [
    "BLOCK_BUDGET",
    "BLOCK_DISCOVERY",
    "BLOCK_FREE_CORE",
    "BLOCK_PREFLIGHT",
    "BLOCK_RESERVED",
    "CAN_EXECUTE",
    "begin_paid_budget_scope",
    "check_paid_call",
    "current_paid_budget",
    "end_paid_budget_scope",
    "MODE_FREE_CORE",
    "MODE_HYBRID",
    "PaidCallBudget",
    "STAGE_DISCOVERY",
    "STAGE_FINAL_REFRESH",
    "STAGE_INITIAL_ENRICHMENT",
    "free_core_enabled",
    "paid_discovery_fallback_enabled",
    "provider_hierarchy",
    "record_paid_call",
    "source_mode",
    "typed_row_degradation",
]
