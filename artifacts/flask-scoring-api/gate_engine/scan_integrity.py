"""
Daily-scan discovery integrity contract.

This module is deliberately read-only: it observes discovery, inventory, and
evaluation facts after a scan and never changes a probability, terminal label,
or ranking threshold.  A lane is a coverage unit, not a candidate-quality
claim.  This keeps a quiet source/model omission visible even when no row
would have qualified.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import statistics
from typing import Any

from .market_family import CONTROLLING_SKILL, MarketFamily, Objective, ROUTE_TABLE
from .model_registry import lookup


COMPLETED = "COMPLETED"
NO_ACTIVE_EVENTS = "NO_ACTIVE_EVENTS"
NO_BOARD_INVENTORY = "NO_BOARD_INVENTORY"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
DATA_UNOBTAINABLE = "DATA_UNOBTAINABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"

VALID_OUTCOMES = frozenset({
    COMPLETED,
    NO_ACTIVE_EVENTS,
    NO_BOARD_INVENTORY,
    MODEL_UNAVAILABLE,
    DATA_UNOBTAINABLE,
    NOT_APPLICABLE,
})

PLAYER_PROP = MarketFamily.PLAYER_PROP
OUTRIGHT_WINNER = MarketFamily.OUTRIGHT_WINNER
MLB_UPSET_DISCOVERY = "MLB_UPSET_DISCOVERY"
MLB_1IP = "MLB_1IP"
WNBA_PRA = "WNBA_PRA"

_STAT_ALIASES = {
    "batter_hits": "HITS",
    "batter_home_runs": "HR",
    "batter_rbis": "RBI",
    "batter_strikeouts": "STRIKEOUTS",
    "batter_total_bases": "TOTAL_BASES",
    "pitcher_strikeouts": "STRIKEOUTS",
    "pitcher_outs": "OUTS",
    "pitcher_walks": "BB",
    "pitcher_earned_runs": "ER",
    "player_points": "POINTS",
    "player_rebounds": "REBOUNDS",
    "player_assists": "ASSISTS",
    "player_threes": "3PM",
    "player_steals": "STEALS",
    "player_blocks": "BLOCKS",
    "player_points_rebounds_assists": "PRA",
}


def _sport(value: Any) -> str:
    return str(value or "").strip().upper()


def _lane_id(sport: str, family: str) -> str:
    return f"{_sport(sport)}:{family}"


def _canonical_stat(prop: dict[str, Any]) -> str:
    raw = str(prop.get("stat_key") or prop.get("prop_type") or prop.get("prop") or "")
    compact = raw.strip().lower().replace(" ", "_").replace("-", "_")
    return _STAT_ALIASES.get(compact, raw.upper().replace(" ", "_"))


def _is_1ip(prop: dict[str, Any]) -> bool:
    raw = str(prop.get("stat_key") or prop.get("prop_type") or prop.get("prop") or "").lower()
    return "1ip" in raw or "first_inning" in raw or "first inning" in raw


def _is_pra(prop: dict[str, Any]) -> bool:
    return _canonical_stat(prop) in {"PRA", "PTS+REB+AST", "POINTS_REBOUNDS_ASSISTS"}


def _source_failed(observation: dict[str, Any]) -> bool:
    values = [
        observation.get("events_status"),
        observation.get("props_status"),
        observation.get("backup_status"),
    ]
    # A successful backup is a valid source observation; do not call a lane
    # unobtainable merely because the primary supplier was unavailable.
    if "AVAILABLE" in str(observation.get("backup_status") or "").upper():
        return False
    return any(
        "FAILED" in str(value).upper() or "PARTIAL" in str(value).upper()
        for value in values if value is not None
    )


def _route_available(family: str) -> tuple[bool, str | None]:
    if family == MLB_UPSET_DISCOVERY:
        family = OUTRIGHT_WINNER
    route = ROUTE_TABLE.get(family)
    if not route:
        return False, None
    skill = route.get("controlling_skill_id")
    return bool(skill and skill in CONTROLLING_SKILL.values()), skill


def specialist_availability(
    sport: str,
    family: str,
    inventory: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Read-only specialist registry view.

    It intentionally does not invent a fallback: an absent route or an
    unsupported model stays unavailable, even if another sport has a generic
    probability implementation.
    """
    routed_family = PLAYER_PROP if family in (MLB_1IP, WNBA_PRA) else family
    route_ok, skill = _route_available(routed_family)
    if not route_ok:
        return {
            "available": False,
            "controlling_skill_id": None,
            "reason": "CONTROLLING_SPECIALIST_UNAVAILABLE",
            "model_statuses": [],
        }

    if family in (OUTRIGHT_WINNER, MLB_UPSET_DISCOVERY):
        try:
            from .moneyline_probability import ModelStatus, get_model_for_sport
            model = get_model_for_sport(sport)
        except Exception:
            model = {"status": "UNAVAILABLE", "model_id": None}
            ModelStatus = type("_ModelStatus", (), {"UNAVAILABLE": "UNAVAILABLE"})
        if model.get("status") == ModelStatus.UNAVAILABLE:
            return {
                "available": False,
                "controlling_skill_id": skill,
                "reason": "MONEYLINE_MODEL_UNAVAILABLE",
                "model_statuses": ["UNAVAILABLE"],
            }
        return {
            "available": True,
            "controlling_skill_id": skill,
            "reason": None,
            "model_statuses": [model.get("status")],
        }

    entries = [lookup(sport, _canonical_stat(row), row.get("line")) for row in (inventory or [])]
    statuses = sorted({entry.get("status", "NO_REGISTERED_MODEL") for entry in entries})
    if entries and "NO_REGISTERED_MODEL" in statuses:
        return {
            "available": False,
            "controlling_skill_id": skill,
            "reason": (
                "NO_REGISTERED_MODEL"
                if len(statuses) == 1
                else "PARTIAL_MODEL_REGISTRY_COVERAGE"
            ),
            "model_statuses": statuses,
        }
    return {
        "available": True,
        "controlling_skill_id": skill,
        "reason": None,
        "model_statuses": statuses,
    }


def _outcome(
    observation: dict[str, Any],
    inventory: list[dict[str, Any]],
    specialist: dict[str, Any],
    evaluated_rows: int,
    terminal_rows: int,
) -> str:
    if _source_failed(observation):
        return DATA_UNOBTAINABLE
    status_text = " ".join(str(observation.get(key) or "").upper() for key in (
        "events_status", "props_status", "backup_status"
    ))
    if "UNKNOWN SPORT" in status_text or "NO MARKETS DEFINED" in status_text:
        return NOT_APPLICABLE
    if int(observation.get("active_events", 0) or 0) <= 0:
        return NO_ACTIVE_EVENTS
    if not inventory:
        return NO_BOARD_INVENTORY
    if not specialist["available"]:
        return MODEL_UNAVAILABLE
    if evaluated_rows <= 0:
        return DATA_UNOBTAINABLE
    if terminal_rows != evaluated_rows or terminal_rows < len(inventory):
        return DATA_UNOBTAINABLE
    return COMPLETED


def _family_observation(
    observation: dict[str, Any],
    family: str,
) -> dict[str, Any]:
    lane = dict(observation)
    lane["active_events"] = int(
        (observation.get("active_events_by_family") or {}).get(
            family, observation.get("active_events", 0)
        ) or 0
    )
    family_source = (observation.get("source_by_family") or {}).get(family)
    if family_source:
        lane["events_status"] = family_source.get("events")
        lane["props_status"] = family_source.get("props")
        lane["backup_status"] = family_source.get("backup")
    return lane


def _coverage_row(
    sport: str,
    family: str,
    observation: dict[str, Any],
    inventory: list[dict[str, Any]],
    evaluated_rows: int,
) -> dict[str, Any]:
    lane_observation = _family_observation(observation, family)
    # These are active-slate lanes, not board-selected lanes.  A source that
    # returns general props while silently omitting their dedicated inventory
    # is an acquisition failure, never evidence that the lane did not exist.
    # A source may explicitly declare the lane not applicable, which retains
    # the normal NOT_APPLICABLE outcome path.
    if (
        family in (MLB_1IP, WNBA_PRA)
        and int(lane_observation.get("active_events", 0) or 0) > 0
        and not inventory
        and not (observation.get("source_by_family") or {}).get(family)
    ):
        lane_observation["props_status"] = (
            "FAILED: REQUIRED_ACTIVE_LANE_INVENTORY_MISSING"
        )
    specialist = specialist_availability(sport, family, inventory)
    is_primary_prop_lane = family == PLAYER_PROP
    terminal_by_family = observation.get("terminal_by_family") or {}
    legacy_terminal_count = observation.get(
        "terminal_outcomes", observation.get("evaluated_rows", 0)
    )
    qualifiers_by_family = observation.get("qualifiers_by_family") or {}
    provisional_by_family = observation.get("provisional_by_family") or {}
    terminal_rows = int(
        terminal_by_family.get(
            family,
            legacy_terminal_count if is_primary_prop_lane else 0,
        ) or 0
    )
    qualifier_count = int(
        qualifiers_by_family.get(
            family,
            observation.get("qualifier_count", 0) if is_primary_prop_lane else 0,
        ) or 0
    )
    provisional_count = int(
        provisional_by_family.get(
            family,
            observation.get("provisional_refreshes", 0) if is_primary_prop_lane else 0,
        ) or 0
    )
    unresolved_count = max(len(inventory) - terminal_rows, 0)
    outcome = _outcome(
        lane_observation, inventory, specialist, evaluated_rows, terminal_rows
    )
    return {
        "lane_id": _lane_id(sport, family),
        "sport": _sport(sport),
        "market_family": family,
        "coverage_outcome": outcome,
        "active_event_count": int(lane_observation.get("active_events", 0) or 0),
        "received_inventory_count": len(inventory),
        "evaluated_row_count": int(evaluated_rows or 0),
        "terminal_outcome_count": terminal_rows,
        "unresolved_inventory_count": unresolved_count,
        "qualifier_count": qualifier_count,
        "provisional_refresh_count": provisional_count,
        "targeted_refresh_count": provisional_count + unresolved_count,
        "controlling_skill_id": specialist["controlling_skill_id"],
        "specialist_available": specialist["available"],
        "specialist_reason": specialist["reason"],
        "model_statuses": specialist["model_statuses"],
        "source_status": {
            "events": lane_observation.get("events_status"),
            "props": lane_observation.get("props_status"),
            "backup": lane_observation.get("backup_status"),
        },
        "candidate_quality": "NOT_EVALUATED" if evaluated_rows == 0 else "EVALUATED_SEPARATELY",
        "can_execute": False,
    }


def _expected_families(sport: str, observation: dict[str, Any]) -> list[str]:
    families = [PLAYER_PROP]
    if _sport(sport) == "MLB" and int(observation.get("active_events", 0) or 0) > 0:
        families.extend([OUTRIGHT_WINNER, MLB_UPSET_DISCOVERY, MLB_1IP])
    if _sport(sport) == "WNBA" and int(observation.get("active_events", 0) or 0) > 0:
        families.append(WNBA_PRA)
    return list(dict.fromkeys(families))


def _lane_inventory(
    family: str,
    inventory: list[dict[str, Any]],
    observation: dict[str, Any],
) -> list[dict[str, Any]]:
    explicit = (observation.get("inventory_by_family") or {}).get(family)
    if explicit is not None:
        return list(explicit)
    if family == MLB_1IP:
        return [row for row in inventory if _is_1ip(row)]
    if family == WNBA_PRA:
        return [row for row in inventory if _is_pra(row)]
    if family in (OUTRIGHT_WINNER, MLB_UPSET_DISCOVERY):
        return []
    if family == PLAYER_PROP:
        return [
            row for row in inventory
            if not (_sport(row.get("sport")) == "MLB" and _is_1ip(row))
            and not (_sport(row.get("sport")) == "WNBA" and _is_pra(row))
        ]
    return list(inventory)


def _lane_evaluated(family: str, observation: dict[str, Any]) -> int:
    by_family = observation.get("evaluated_by_family") or {}
    if family in by_family:
        return int(by_family.get(family, 0) or 0)
    if family == PLAYER_PROP:
        return int(observation.get("evaluated_rows", 0) or 0)
    return 0


def _calibration_cohorts(coverage: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach existing calibration-health facts by sport without changing them."""
    try:
        from .calibration_health import get_health_summary
        sport_health = (get_health_summary() or {}).get("by_sport", {})
    except Exception:
        sport_health = {}
    return {
        row["lane_id"]: {
            "sport": row["sport"],
            "coverage_outcome": row["coverage_outcome"],
            "health": deepcopy(sport_health.get(row["sport"], {
                "grade": "DATA_GAP",
                "reason": "NO_LANE_SPECIFIC_CALIBRATION_RECORDS",
            })),
        }
        for row in coverage
    }


def build_scan_integrity_report(
    requested_sports: list[str],
    sport_observations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Build one exact-once coverage record for every expected scan lane.

    `sport_observations` comes from the scanner's actual acquisition flow.
    The report does not treat uploaded boards as a discovery source and does
    not judge candidate quality.  Source failures and duplicate/missing lanes
    are a fail-closed run-integrity result; no-active-event and no-inventory
    outcomes are complete observations, not omissions.
    """
    coverage: list[dict[str, Any]] = []
    for requested in requested_sports:
        sport = _sport(requested)
        observation = dict(sport_observations.get(sport) or sport_observations.get(requested) or {})
        observation.setdefault("inventory", [])
        for family in _expected_families(sport, observation):
            inventory = _lane_inventory(family, observation["inventory"], observation)
            coverage.append(_coverage_row(
                sport, family, observation, inventory, _lane_evaluated(family, observation)
            ))

    lane_counts = Counter(row["lane_id"] for row in coverage)
    duplicate_lanes = sorted(lane for lane, count in lane_counts.items() if count != 1)
    count_mismatches = sorted(
        row["lane_id"] for row in coverage
        if row["evaluated_row_count"] > row["received_inventory_count"]
        or row["terminal_outcome_count"] != row["evaluated_row_count"]
        or row["qualifier_count"] > row["terminal_outcome_count"]
        or (
            row["coverage_outcome"] == COMPLETED
            and row["unresolved_inventory_count"] != 0
        )
    )
    unavailable_sources = [
        row["lane_id"] for row in coverage
        if row["coverage_outcome"] == DATA_UNOBTAINABLE
    ]
    unavailable_specialists = [
        row["lane_id"] for row in coverage
        if row["coverage_outcome"] == MODEL_UNAVAILABLE
    ]
    zero_qualifier_lanes = [
        row["lane_id"] for row in coverage
        if row["coverage_outcome"] == COMPLETED and row["qualifier_count"] == 0
    ]
    provisional_refreshes = [
        row["lane_id"] for row in coverage if row["targeted_refresh_count"] > 0
    ]
    unresolved_inventory = [
        row["lane_id"] for row in coverage if row["unresolved_inventory_count"] > 0
    ]
    reconciliation_ok = (
        not duplicate_lanes
        and not count_mismatches
        and not unavailable_sources
        and not unavailable_specialists
    )
    return {
        "coverage_matrix": coverage,
        "source_availability": {
            row["lane_id"]: deepcopy(row["source_status"]) for row in coverage
        },
        "reconciliation": {
            "expected_lane_count": len(coverage),
            "received_lane_count": len(coverage),
            "received_inventory_count": sum(row["received_inventory_count"] for row in coverage),
            "evaluated_row_count": sum(row["evaluated_row_count"] for row in coverage),
            "terminal_outcome_count": sum(row["terminal_outcome_count"] for row in coverage),
            "unresolved_inventory_count": sum(
                row["unresolved_inventory_count"] for row in coverage
            ),
            "qualifier_count": sum(row["qualifier_count"] for row in coverage),
            "provisional_refresh_count": sum(row["provisional_refresh_count"] for row in coverage),
            "targeted_refresh_count": sum(row["targeted_refresh_count"] for row in coverage),
            "exact_once": not duplicate_lanes,
            "duplicate_or_mismatched_lanes": sorted(set(duplicate_lanes + count_mismatches)),
            "row_count_mismatch_lanes": count_mismatches,
            "unavailable_source_lanes": unavailable_sources,
            "unavailable_specialist_lanes": unavailable_specialists,
            "unresolved_inventory_lanes": unresolved_inventory,
            "zero_qualifier_lanes": zero_qualifier_lanes,
            "provisional_refresh_lanes": provisional_refreshes,
            "integrity_valid": reconciliation_ok,
            "public_label": "DEGRADED_ENGINE_RUN" if not reconciliation_ok else None,
            "run_integrity_result": "COMPLETE" if reconciliation_ok else "RUN_INTEGRITY_FAILURE",
        },
        "calibration_health_by_lane": _calibration_cohorts(coverage),
        "can_execute": False,
        "dry_run_only": True,
    }


def _board_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _sport(row.get("sport")),
        str(row.get("player") or row.get("team") or "").strip().lower(),
        str(row.get("prop_type") or row.get("prop") or row.get("market_type") or "").strip().lower(),
        str(row.get("direction") or row.get("side") or "").strip().upper(),
    )


def _board_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _board_key(row),
        str(row.get("event_id") or row.get("game") or "").strip().lower(),
        row.get("line"),
        str(row.get("promo_id") or row.get("promo") or "").strip().lower(),
    )


def correlate_board_delta(
    current_rows: list[dict[str, Any]] | None,
    previous_rows: list[dict[str, Any]] | None,
    prior_evidence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Compare optional board intake without using it to choose discovered sports.

    Evidence is reusable only for an unchanged board signature with an explicit
    `still_valid` prior-evidence marker.  The scanner remains responsible for
    independent cross-sport discovery.
    """
    current = list(current_rows or [])
    previous = list(previous_rows or [])
    prior_evidence = prior_evidence or {}
    current_by_key = {_board_key(row): row for row in current}
    previous_by_key = {_board_key(row): row for row in previous}
    added, removed, moved, promo_changed, unchanged, reused, refresh = [], [], [], [], [], [], []
    reused_evidence: list[dict[str, Any]] = []

    for key, row in current_by_key.items():
        old = previous_by_key.get(key)
        key_text = "|".join(key)
        if old is None:
            added.append(key_text)
            refresh.append(key_text)
            continue
        if _board_signature(row) == _board_signature(old):
            unchanged.append(key_text)
            evidence = prior_evidence.get(key_text) or {}
            if evidence.get("still_valid") is True:
                reused.append(key_text)
                reused_evidence.append({
                    "board_key": key_text,
                    "event_hydration": deepcopy(evidence.get("event_hydration")),
                    "model_evidence": deepcopy(evidence.get("model_evidence")),
                    "valid_until": evidence.get("valid_until"),
                })
            else:
                refresh.append(key_text)
            continue
        if str(row.get("promo_id") or row.get("promo") or "") != str(old.get("promo_id") or old.get("promo") or ""):
            promo_changed.append(key_text)
        else:
            moved.append(key_text)
        refresh.append(key_text)

    for key in previous_by_key:
        if key not in current_by_key:
            removed.append("|".join(key))

    return {
        "board_enriches_discovery_only": True,
        "added": sorted(added),
        "removed": sorted(removed),
        "moved": sorted(moved),
        "promo_changed": sorted(promo_changed),
        "unchanged": sorted(unchanged),
        "unchanged_reused": sorted(reused),
        "reused_evidence": sorted(reused_evidence, key=lambda item: item["board_key"]),
        "targeted_refresh": sorted(set(refresh)),
        "current_board_count": len(current),
        "previous_board_count": len(previous),
        "can_execute": False,
    }


def build_objective_separation(
    rows: list[dict[str, Any]],
    top_n: int = 10,
) -> dict[str, Any]:
    """
    Keep sporting probability, market, settlement, money/EV, and portfolio
    evidence in distinct output blocks.

    Only rows already carrying an explicit calibrated lower bound are sent to
    the existing cross-sport ranker.  Missing market/money/settlement evidence
    never hides a completed sporting probability.  Rows without a CLB remain
    visible as provisional refresh requests rather than being ranked by a
    substitute score.
    """
    probability_rows = [
        row for row in rows
        if row.get("calibrated_probability_lower_bound") is not None
        or row.get("lower_bound") is not None
        or ((row.get("gates") or {}).get("wnba_generative") or {}).get("cal_lower_bound") is not None
        or ((row.get("gates") or {}).get("tennis_total_games") or {}).get("cal_lower_bound") is not None
    ]
    provisional_rows = [row for row in rows if row not in probability_rows]

    if probability_rows:
        from .cross_sport_ranker import rank
        ranked = rank(probability_rows, top_n=top_n).to_dict()
    else:
        ranked = {
            "highest_hit_probability": [],
            "highest_calibrated_prob": [],
            "best_edge": [],
            "best_multi_leg": [],
            "summary": {
                "n_eligible": 0,
                "n_eliminated_weak": 0,
                "n_total_input": 0,
                "sports_covered": [],
            },
            "ranker_version": "cross_sport_ranker_v1.0",
            "can_execute": False,
            "requires_human_confirmation": True,
        }

    def _identity(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "row_id": row.get("row_id"),
            "sport": row.get("sport"),
            "player": row.get("player") or row.get("player_name"),
            "stat_key": row.get("stat_key") or row.get("prop") or row.get("prop_type"),
            "line": row.get("line"),
            "side": row.get("side") or row.get("direction"),
        }

    return {
        "ranking_basis": "calibrated_lower_bound",
        "ranked_probability_results": ranked,
        "completed_probability_row_count": len(probability_rows),
        "provisional_probability_rows": [
            {**_identity(row), "refresh_reason": "CALIBRATED_LOWER_BOUND_REQUIRED"}
            for row in provisional_rows[:50]
        ],
        "provisional_probability_row_count": len(provisional_rows),
        "market_evidence": [
            {
                **_identity(row),
                "market_probability": row.get("market_probability") or row.get("no_vig_probability"),
                "pure_edge": row.get("pure_edge") or row.get("adjusted_edge"),
            }
            for row in rows
            if row.get("market_probability") is not None
            or row.get("no_vig_probability") is not None
        ][:50],
        "settlement_evidence": [
            {**_identity(row), "settlement": deepcopy(row.get("settlement"))}
            for row in rows if row.get("settlement") is not None
        ][:50],
        "money_ev_evidence": [
            {**_identity(row), "money_ev": deepcopy(row.get("money_ev") or row.get("ev"))}
            for row in rows if row.get("money_ev") is not None or row.get("ev") is not None
        ][:50],
        "portfolio_evidence": [
            {**_identity(row), "portfolio": deepcopy(row.get("portfolio"))}
            for row in rows if row.get("portfolio") is not None
        ][:50],
        "can_execute": False,
        "dry_run_only": True,
    }