"""Fair, bounded cross-sport discovery for V17 Daily winner scans.

The canonical discovery function historically used one shared wall-clock budget
for the entire sport catalog. A slow first family could consume that budget and
cause every later family to terminate as DISCOVERY_BUDGET_EXHAUSTED without a
single provider attempt. That is bounded, but it is not equal treatment.

This repair preserves bounded acquisition while giving each configured family
its own soft budget. A separate hard total budget still caps the complete scan.
No probability, model, calibration, ranking, market-value, or execution logic is
changed here. can_execute remains false.
"""
from __future__ import annotations

import os
from time import monotonic
from typing import Any, Callable, Iterable, Mapping

CAN_EXECUTE = False
DEFAULT_TOTAL_BUDGET_MAX_SECONDS = 300.0


def total_discovery_budget_seconds(*, family_budget: float, family_count: int) -> float:
    """Return the hard wall-clock cap for one full catalog sweep.

    The default gives every family two soft-budget equivalents of runway to
    absorb one slow synchronous provider call while still capping a full scan at
    five minutes. Operators can lower or raise this bounded cap without changing
    sporting model behavior.
    """
    default = max(30.0, min(DEFAULT_TOTAL_BUDGET_MAX_SECONDS, family_budget * max(1, family_count) * 2.0))
    raw = os.environ.get("WOW_CROSS_SPORT_DISCOVERY_TOTAL_BUDGET_SECONDS")
    if raw is None:
        return default
    try:
        configured = float(raw)
    except ValueError:
        return default
    return max(1.0, min(configured, DEFAULT_TOTAL_BUDGET_MAX_SECONDS))


def _append_total_budget_hold(discovery: Any, inventory: Any, family: str, *, family_budget: float, total_budget: float) -> None:
    inventory.acquisition_audit.append(
        {
            "family": family,
            "provider": discovery.registry.PROVIDER,
            "provider_sport_ids_attempted": [],
            "request_status": discovery.DISCOVERY_BUDGET_EXHAUSTED,
            "events_returned": 0,
            "blocker_if_any": discovery.DISCOVERY_BUDGET_EXHAUSTED,
            "budget_seconds": family_budget,
            "total_budget_seconds": total_budget,
            "budget_scope": "TOTAL_HARD_CAP",
            "can_execute": False,
        }
    )
    inventory.source_blockers.append(
        {
            "scope": "family",
            "sport": family,
            "sport_key": None,
            "status": discovery.DISCOVERY_BUDGET_EXHAUSTED,
            "budget_seconds": family_budget,
            "total_budget_seconds": total_budget,
            "budget_scope": "TOTAL_HARD_CAP",
        }
    )


def discover_winner_slate_fair(
    *,
    requested_slate_date: str,
    requested_timezone: str,
    fetch_sport_events: Callable[..., Iterable[Mapping[str, Any]]],
    supported_sports: Iterable[str] | None = None,
    discovery_targets: Mapping[str, tuple[Any, ...]] | None = None,
    include_regime_variants: bool = False,
    now: Any = None,
    budget_seconds: float | None = None,
    _discovery_module: Any = None,
    _original: Callable[..., Any] | None = None,
) -> Any:
    """Run the canonical discovery logic one family at a time with fair budgets."""
    if _discovery_module is None:
        from v17 import cross_sport_winner_discovery as discovery
    else:
        discovery = _discovery_module

    original = _original or getattr(discovery, "_v17_fair_discovery_original", discovery.discover_winner_slate)
    families = tuple(supported_sports or discovery.SUPPORTED_DISCOVERY_SPORTS)
    family_budget = float(discovery.discovery_budget_seconds() if budget_seconds is None else budget_seconds)
    family_budget = max(0.5, min(family_budget, 120.0))
    total_budget = total_discovery_budget_seconds(family_budget=family_budget, family_count=len(families))
    total_deadline = monotonic() + total_budget

    inventory = discovery.DiscoveryInventory(
        requested_slate_date=requested_slate_date,
        requested_timezone=requested_timezone,
    )

    for family in families:
        if monotonic() >= total_deadline:
            inventory.sports_queried.append(family)
            _append_total_budget_hold(
                discovery,
                inventory,
                family,
                family_budget=family_budget,
                total_budget=total_budget,
            )
            continue

        family_deadline = monotonic() + family_budget

        def guarded_fetch(requested_family: str, target: Any = None):
            if monotonic() >= family_deadline:
                raise discovery.DiscoveryFeedError(discovery.DISCOVERY_BUDGET_EXHAUSTED)
            return fetch_sport_events(requested_family, target)

        child_targets = None
        if discovery_targets is not None:
            child_targets = {family: tuple(discovery_targets.get(family) or ())}

        child = original(
            requested_slate_date=requested_slate_date,
            requested_timezone=requested_timezone,
            fetch_sport_events=guarded_fetch,
            supported_sports=(family,),
            discovery_targets=child_targets,
            include_regime_variants=include_regime_variants,
            now=now,
            budget_seconds=family_budget,
        )

        budget_hit = False
        for blocker in child.source_blockers:
            if blocker.get("reason_code") == discovery.DISCOVERY_BUDGET_EXHAUSTED:
                blocker["status"] = discovery.DISCOVERY_BUDGET_EXHAUSTED
                blocker["budget_scope"] = "FAMILY_SOFT_CAP"
                blocker["budget_seconds"] = family_budget
                budget_hit = True

        if budget_hit:
            for audit in child.acquisition_audit:
                if audit.get("family") != family:
                    continue
                audit["partial_discovery"] = bool(audit.get("events_returned"))
                audit["budget_scope"] = "FAMILY_SOFT_CAP"
                audit["budget_seconds"] = family_budget
                audit["blocker_if_any"] = discovery.DISCOVERY_BUDGET_EXHAUSTED
                if not audit.get("events_returned"):
                    audit["request_status"] = discovery.DISCOVERY_BUDGET_EXHAUSTED

        inventory.events.extend(child.events)
        inventory.sports_queried.extend(child.sports_queried)
        inventory.sports_with_events.extend(child.sports_with_events)
        inventory.source_blockers.extend(child.source_blockers)
        inventory.acquisition_audit.extend(child.acquisition_audit)

    return inventory


def install_cross_sport_discovery_fairness() -> bool:
    """Install the fairness wrapper idempotently on the canonical module."""
    from v17 import cross_sport_winner_discovery as discovery

    if getattr(discovery, "_v17_cross_sport_discovery_fairness_installed", False):
        return True

    original = discovery.discover_winner_slate
    discovery._v17_fair_discovery_original = original

    def wrapped(**kwargs: Any):
        return discover_winner_slate_fair(
            _discovery_module=discovery,
            _original=original,
            **kwargs,
        )

    discovery.discover_winner_slate = wrapped
    discovery._v17_cross_sport_discovery_fairness_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "DEFAULT_TOTAL_BUDGET_MAX_SECONDS",
    "discover_winner_slate_fair",
    "install_cross_sport_discovery_fairness",
    "total_discovery_budget_seconds",
]
