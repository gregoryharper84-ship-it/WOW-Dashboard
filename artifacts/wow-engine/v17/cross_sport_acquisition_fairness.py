"""Fair cross-sport acquisition opportunity for V17 winner discovery.

The parity repair made every cataloged sport visible, but the original discovery
loop used one serial wall-clock budget for the entire slate. A slow first family
could therefore consume the budget and leave later configured sports unattempted.
That is accounting parity without acquisition-opportunity parity.

This repair keeps provider/model ownership unchanged and changes only acquisition
orchestration:
- every configured sport family gets at least one real provider attempt;
- the discovery budget is isolated per family, so MLB latency cannot starve NFL,
  NBA, WNBA, NCAAF, or any later family;
- multi-target families remain bounded after their guaranteed first attempt;
- partial family coverage is explicit and typed, never presented as a complete
  empty slate;
- no market probability, model probability, ranking, terminal authority, or
  execution posture is changed. ``can_execute`` remains false.
"""
from __future__ import annotations

from time import monotonic
from typing import Any, Callable, Iterable, Mapping

CAN_EXECUTE = False
FAIRNESS_CONTRACT_VERSION = "V17_CROSS_SPORT_ACQUISITION_FAIRNESS_V1"


def fair_discover_winner_slate(
    *,
    requested_slate_date: str,
    requested_timezone: str,
    fetch_sport_events: Callable[..., Iterable[Mapping[str, Any]]],
    supported_sports: Iterable[str] | None = None,
    discovery_targets: Mapping[str, tuple[Any, ...]] | None = None,
    include_regime_variants: bool = False,
    now: Any = None,
    budget_seconds: float | None = None,
):
    """Discover a slate without allowing one family to starve later families.

    ``budget_seconds`` is a per-family expansion budget. The first configured
    target for each family is always attempted, even when that first call itself
    consumes the nominal budget. This is intentional: a budget may bound extra
    acquisition work, but it may not erase another sport's opportunity to be
    looked at.
    """
    from v17 import cross_sport_winner_discovery as discovery
    from v17 import rundown_sport_registry as registry

    families = tuple(
        str(family).upper()
        for family in (
            discovery.SUPPORTED_DISCOVERY_SPORTS
            if supported_sports is None
            else supported_sports
        )
    )
    targets = dict(
        discovery_targets
        if discovery_targets is not None
        else discovery.default_discovery_targets(
            families,
            include_regime_variants=include_regime_variants,
        )
    )
    raw_budget = (
        discovery.discovery_budget_seconds()
        if budget_seconds is None
        else float(budget_seconds)
    )
    family_budget = max(0.0, raw_budget)
    inventory = discovery.DiscoveryInventory(
        requested_slate_date=requested_slate_date,
        requested_timezone=requested_timezone,
    )
    for family in families:
        family_targets = tuple(targets.get(family) or ())
        if family_targets:
            inventory.expected_acquisition_targets.extend(
                {"family": family, "target_key": target.target_key}
                for target in family_targets
            )
        else:
            inventory.expected_acquisition_targets.append(
                {
                    "family": family,
                    "target_key": discovery.NO_CONFIGURED_DISCOVERY_FEED,
                }
            )

    for family in families:
        inventory.sports_queried.append(family)
        family_targets = tuple(targets.get(family) or ())

        if not family_targets:
            inventory.acquisition_audit.append(
                {
                    "family": family,
                    "provider": registry.PROVIDER,
                    "provider_sport_ids_attempted": [],
                    "request_status": discovery.NO_CONFIGURED_DISCOVERY_FEED,
                    "events_returned": 0,
                    "blocker_if_any": discovery.NO_CONFIGURED_DISCOVERY_FEED,
                    "configured_target_count": 0,
                    "targets_remaining_unattempted": 0,
                    "first_pass_attempted": False,
                    "acquisition_opportunity_guaranteed": False,
                    "coverage_complete": False,
                    "fairness_contract_version": FAIRNESS_CONTRACT_VERSION,
                    "can_execute": False,
                }
            )
            inventory.source_blockers.append(
                {
                    "scope": "family",
                    "sport": family,
                    "sport_key": None,
                    "status": discovery.NO_CONFIGURED_DISCOVERY_FEED,
                }
            )
            inventory.acquisition_details.append(
                {
                    "family": family,
                    "target_key": discovery.NO_CONFIGURED_DISCOVERY_FEED,
                    "provider": registry.PROVIDER,
                    "league": None,
                    "regime": None,
                    "provider_sport_id": None,
                    "sport_key": None,
                    "final_state": discovery.NO_CONFIGURED_DISCOVERY_FEED,
                    "provider_status": discovery.PROVIDER_NOT_ATTEMPTED,
                    "fallback_status": discovery.FALLBACK_NOT_APPLICABLE,
                    "exhaustion_status": discovery.NO_CONFIGURED_PATH,
                    "events_returned": 0,
                    "duplicate_rows_suppressed": 0,
                    "blocker_code": discovery.NO_CONFIGURED_DISCOVERY_FEED,
                    "can_execute": False,
                }
            )
            continue

        attempted: list[Any] = []
        returned = 0
        succeeded = False
        failures: list[str] = []
        seen_identities: set[tuple[str, str, str]] = set()
        duplicates = 0
        family_deadline = monotonic() + family_budget
        budget_exhausted = False

        for target_index, target in enumerate(family_targets):
            # The first configured target is unconditional. Later targets are
            # bounded by this family's own budget, never another family's time.
            if target_index > 0 and monotonic() >= family_deadline:
                budget_exhausted = True
                break

            target_id = (
                target.sport_id
                if getattr(target, "sport_id", None) is not None
                else getattr(target, "sport_key", None)
            )
            attempted.append(target_id)
            try:
                fetched = fetch_sport_events(family, target)
                if isinstance(fetched, discovery.AcquisitionFeedResult):
                    rows = list(fetched.rows)
                    provider_status = fetched.provider_status
                    fallback_status = fetched.fallback_status
                    exhaustion_status = fetched.exhaustion_status
                    blocker_code = fetched.blocker_code
                else:
                    rows = list(fetched or ())
                    provider_status = discovery.PROVIDER_SUCCEEDED
                    fallback_status = discovery.FALLBACK_NOT_APPLICABLE
                    exhaustion_status = discovery.PATHS_NOT_EXHAUSTED
                    blocker_code = None
            except discovery.DiscoveryFeedError as exc:
                failures.append(exc.code)
                acquisition = exc.acquisition
                failure_status = discovery.classify_acquisition_failure(exc.code)
                inventory.source_blockers.append(
                    {
                        "scope": "target",
                        "sport": family,
                        **target.as_dict(),
                        "status": failure_status,
                        "reason_code": exc.code,
                    }
                )
                inventory.acquisition_details.append(
                    {
                        **target.as_dict(),
                        "final_state": failure_status,
                        "provider_status": (
                            acquisition.provider_status
                            if acquisition is not None
                            else discovery.PROVIDER_FAILED
                        ),
                        "fallback_status": (
                            acquisition.fallback_status
                            if acquisition is not None
                            else discovery.FALLBACK_NOT_APPLICABLE
                        ),
                        "exhaustion_status": (
                            acquisition.exhaustion_status
                            if acquisition is not None
                            else discovery.PROVIDER_PATHS_EXHAUSTED
                        ),
                        "events_returned": 0,
                        "duplicate_rows_suppressed": 0,
                        "blocker_code": exc.code,
                        "can_execute": False,
                    }
                )
                continue
            except Exception as exc:  # noqa: BLE001 - provider failure is evidence
                failures.append(type(exc).__name__)
                inventory.source_blockers.append(
                    {
                        "scope": "target",
                        "sport": family,
                        **target.as_dict(),
                        "status": discovery.PROVIDER_REQUEST_FAILED,
                        "error_type": type(exc).__name__,
                    }
                )
                inventory.acquisition_details.append(
                    {
                        **target.as_dict(),
                        "final_state": discovery.PROVIDER_REQUEST_FAILED,
                        "provider_status": discovery.PROVIDER_FAILED,
                        "fallback_status": discovery.FALLBACK_NOT_APPLICABLE,
                        "exhaustion_status": discovery.PROVIDER_PATHS_EXHAUSTED,
                        "events_returned": 0,
                        "duplicate_rows_suppressed": 0,
                        "blocker_code": type(exc).__name__,
                        "can_execute": False,
                    }
                )
                continue

            succeeded = True
            target_returned = 0
            target_duplicates = 0
            for raw in rows:
                if not isinstance(raw, Mapping):
                    continue
                event = discovery.normalize_discovered_event(
                    raw,
                    sport=family,
                    sport_key=target.label,
                    source="DISCOVERY_FEED",
                    now=now,
                    target=target,
                )
                identity = discovery._dedupe_identity(event)
                if identity in seen_identities:
                    duplicates += 1
                    target_duplicates += 1
                    continue
                seen_identities.add(identity)
                inventory.events.append(event)
                returned += 1
                target_returned += 1
            inventory.acquisition_details.append(
                {
                    **target.as_dict(),
                    "final_state": (
                        discovery.EVENTS_RETURNED
                        if target_returned
                        else discovery.NO_EVENTS_RETURNED
                    ),
                    "provider_status": provider_status,
                    "fallback_status": fallback_status,
                    "exhaustion_status": exhaustion_status,
                    "events_returned": target_returned,
                    "duplicate_rows_suppressed": target_duplicates,
                    "blocker_code": blocker_code,
                    "can_execute": False,
                }
            )

        remaining = max(len(family_targets) - len(attempted), 0)
        coverage_complete = remaining == 0
        if budget_exhausted and remaining:
            inventory.source_blockers.append(
                {
                    "scope": "family",
                    "sport": family,
                    "sport_key": None,
                    "status": discovery.DISCOVERY_BUDGET_EXHAUSTED,
                    "budget_seconds": family_budget,
                    "configured_target_count": len(family_targets),
                    "targets_attempted": len(attempted),
                    "targets_remaining_unattempted": remaining,
                    "first_pass_attempted": bool(attempted),
                    "fairness_contract_version": FAIRNESS_CONTRACT_VERSION,
                }
            )
            for target in family_targets[len(attempted):]:
                inventory.acquisition_details.append(
                    {
                        **target.as_dict(),
                        "final_state": discovery.DISCOVERY_BUDGET_EXHAUSTED,
                        "provider_status": discovery.PROVIDER_NOT_ATTEMPTED,
                        "fallback_status": discovery.FALLBACK_NOT_ATTEMPTED,
                        "exhaustion_status": discovery.DISCOVERY_BUDGET_EXHAUSTED,
                        "events_returned": 0,
                        "duplicate_rows_suppressed": 0,
                        "blocker_code": discovery.DISCOVERY_BUDGET_EXHAUSTED,
                        "can_execute": False,
                    }
                )

        if returned:
            status = discovery.EVENTS_RETURNED
            blocker = None
            inventory.sports_with_events.append(family)
        elif succeeded:
            status = discovery.NO_EVENTS_RETURNED
            blocker = None
        else:
            status = discovery.classify_acquisition_failure(
                failures[0] if failures else None
            )
            blocker = failures[0] if failures else status

        # Partial multi-target coverage must remain visible even if an earlier
        # target returned events. We preserve the primary request status while
        # exposing the incomplete coverage as a typed blocker.
        if blocker is None and not coverage_complete:
            blocker = discovery.DISCOVERY_BUDGET_EXHAUSTED

        inventory.acquisition_audit.append(
            {
                "family": family,
                "provider": registry.PROVIDER,
                "provider_sport_ids_attempted": attempted,
                "request_status": status,
                "events_returned": returned,
                "duplicate_rows_suppressed": duplicates,
                "blocker_if_any": blocker,
                "configured_target_count": len(family_targets),
                "targets_remaining_unattempted": remaining,
                "first_pass_attempted": bool(attempted),
                "acquisition_opportunity_guaranteed": bool(attempted),
                "coverage_complete": coverage_complete,
                "family_budget_seconds": family_budget,
                "fairness_contract_version": FAIRNESS_CONTRACT_VERSION,
                "can_execute": False,
            }
        )

    return inventory


def install_cross_sport_acquisition_fairness() -> dict[str, Any]:
    """Install the fair discovery function as the production discovery owner."""
    from v17 import cross_sport_winner_discovery as discovery

    if getattr(discovery, "_v17_cross_sport_acquisition_fairness_installed", False):
        return {
            "status": "ALREADY_INSTALLED",
            "fairness_contract_version": FAIRNESS_CONTRACT_VERSION,
            "can_execute": False,
        }

    discovery._v17_cross_sport_acquisition_fairness_original = (
        discovery.discover_winner_slate
    )
    discovery.discover_winner_slate = fair_discover_winner_slate
    discovery._v17_cross_sport_acquisition_fairness_installed = True
    return {
        "status": "INSTALLED",
        "fairness_contract_version": FAIRNESS_CONTRACT_VERSION,
        "first_configured_target_guaranteed": True,
        "budget_scope": "PER_FAMILY_AFTER_GUARANTEED_FIRST_ATTEMPT",
        "terminal_authority_unchanged": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "FAIRNESS_CONTRACT_VERSION",
    "fair_discover_winner_slate",
    "install_cross_sport_acquisition_fairness",
]
