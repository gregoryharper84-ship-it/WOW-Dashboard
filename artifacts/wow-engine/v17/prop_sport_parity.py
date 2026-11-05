"""Universal sport-parity diagnostics for WOW V17 player props.

Equal treatment means every cataloged sport receives the same acquisition/model
accounting shape. It does not mean every sport has the same fitted-model or
autonomous-discovery capability. Missing capability stays visible and typed;
no sportsbook price, recent hit rate, or generic reasoning becomes a model.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from v17.prop_capability_manifest import (
    BUILD_REQUIRED,
    CANDIDATE_ONLY,
    CERTIFIED_PRODUCTION,
    DECLARED_PROP_LANES,
    SUPPORTED_HOLD_ONLY,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False
PROP_PARITY_VERSION = "WOW_V17_PROP_SPORT_PARITY_V1"

# Team/event and prop manifests now share the same canonical 13-sport roster.
# The request alias GOLF normalizes to PGA inside prop_capability_manifest.

# Automatic row hydration is already reviewed for these sports. This is not the
# same as autonomous board discovery: NFL/WNBA need a candidate row/player/event
# before their hydrator can run.
_AUTOMATIC_ROW_HYDRATION = {
    "MLB": "MLB_OFFICIAL_STATS_API",
    "NFL": "NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1",
    "WNBA": "WNBA_OFFICIAL_STATS_CDN_INJURY_V1",
}

# Daily owns these autonomous candidate producers (NBA/NHL deliberately absent:
# no governed evidence hydrator or certified specialist exists for them). Keeping this
# explicit prevents a missing producer from silently looking like an empty slate.
_DAILY_AUTONOMOUS_DISCOVERY = {
    "MLB": "MLB_OFFICIAL_PROBABLE_PITCHERS",
    # Discovery only: ESPN pregame scoreboard + QB/RB/WR/TE rosters feed the
    # governed NFL hydrator. Scoring/publication gates are unchanged.
    "NFL": "NFL_ESPN_PREGAME_SCOREBOARD_ROSTER",
}


def _manifest_sport(canonical_sport: str) -> str:
    return canonical_sport


def prop_sport_parity() -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        manifest_sport = _manifest_sport(sport)
        lanes = [
            capability
            for (lane_sport, _stat), capability in DECLARED_PROP_LANES.items()
            if lane_sport == manifest_sport
        ]
        counts = Counter(capability.lane_status for capability in lanes)
        certified = counts.get(CERTIFIED_PRODUCTION, 0)
        held = counts.get(SUPPORTED_HOLD_ONLY, 0)
        candidate = counts.get(CANDIDATE_ONLY, 0)
        build = counts.get(BUILD_REQUIRED, 0)
        output[sport] = {
            "contract_version": PROP_PARITY_VERSION,
            "sport": sport,
            "manifest_sport": manifest_sport,
            "cataloged": True,
            "discovery_visibility_required": True,
            "declared_lane_count": len(lanes),
            "certified_production_lane_count": certified,
            "supported_hold_lane_count": held,
            "candidate_lane_count": candidate,
            "build_required_lane_count": build,
            "automatic_row_hydration_supported": sport in _AUTOMATIC_ROW_HYDRATION,
            "automatic_row_hydration_provider": _AUTOMATIC_ROW_HYDRATION.get(sport),
            "daily_autonomous_discovery_supported": sport in _DAILY_AUTONOMOUS_DISCOVERY,
            "daily_autonomous_discovery_provider": _DAILY_AUTONOMOUS_DISCOVERY.get(sport),
            "daily_zero_rows_mean_no_slate": False,
            "unsupported_route_fallback_allowed": False,
            "market_probability_substitution_allowed": False,
            "generic_reasoning_substitution_allowed": False,
            "publication_is_exact_lane_scoped": True,
            "can_execute": False,
        }
    return output


def prop_sport_parity_summary() -> dict[str, Any]:
    rows = prop_sport_parity()
    return {
        "contract_version": PROP_PARITY_VERSION,
        "cataloged_sports": len(rows),
        "sports": rows,
        "automatic_row_hydration_sports": sorted(
            sport for sport, row in rows.items() if row["automatic_row_hydration_supported"]
        ),
        "daily_autonomous_discovery_sports": sorted(
            sport for sport, row in rows.items() if row["daily_autonomous_discovery_supported"]
        ),
        "sports_without_daily_autonomous_discovery": sorted(
            sport for sport, row in rows.items() if not row["daily_autonomous_discovery_supported"]
        ),
        "zero_candidates_is_not_capability_proof": True,
        "can_execute": False,
    }


def _norm_sport(value: Any) -> str:
    sport = str(value or "").strip().upper()
    return "PGA" if sport == "GOLF" else sport


def _scorer_executed(row: dict[str, Any]) -> bool:
    outcomes = (row.get("result") or {}).get("outcomes") or []
    if not outcomes:
        return False
    for outcome in outcomes:
        payload = outcome.get("payload") or {}
        if payload.get("route_preflight_blocked") is True:
            return False
        if payload.get("specialist_scoring_attempted") is False:
            return False
        if payload.get("code") == "PROP_SCORER_EXCEPTION":
            return False
    return True


def prop_sport_coverage_receipt(
    *,
    requested_sports: list[str] | None,
    rows: list[dict[str, Any]],
    acquisition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-sport PROPS coverage. Never infers coverage from the moneyline lane.

    ``cross_sport_discovery_audit`` belongs to the team-event/moneyline lane; a
    null there says nothing about prop coverage. Rows are only counted, never
    upgraded; unscored rows keep their terminal row_status.
    """
    parity = prop_sport_parity()
    prop_rows = [r for r in rows if r.get("lane") == "PROPS"]
    receipts = list((acquisition or {}).get("receipts") or [])
    sport_legs = {
        _norm_sport(leg.get("sport")): leg
        for leg in (acquisition or {}).get("sport_coverage") or []
        if isinstance(leg, dict) and leg.get("sport")
    }
    explicit_selection = bool(requested_sports)
    requested = [_norm_sport(s) for s in (requested_sports or [])] or sorted(parity)
    sports = list(dict.fromkeys(requested))
    seen = {_norm_sport((r.get("identity") or {}).get("sport")) for r in prop_rows}
    seen |= {_norm_sport(r.get("sport")) for r in receipts if r.get("sport")}
    seen |= set(sport_legs)
    sports += sorted(s for s in seen if s and s not in sports)
    per_sport: dict[str, Any] = {}
    for sport in sports:
        mine = [r for r in prop_rows if _norm_sport((r.get("identity") or {}).get("sport")) == sport]
        executed = [r for r in mine if _scorer_executed(r)]
        mine_receipts = [r for r in receipts if _norm_sport(r.get("sport")) == sport]
        excluded = [r for r in mine_receipts if r.get("write_status") not in (None, "SNAPSHOT_WRITE_SUCCEEDED")]
        sport_leg = sport_legs.get(sport) or {}
        attempted = sport_leg.get("attempted")
        # Independent acquisition counter versus sport-attributed receipt population.
        receipt_reconciled = attempted == len(mine_receipts) if type(attempted) is int and attempted >= 0 else None
        ceiling_skipped = sport_leg.get("status") == "NOT_ATTEMPTED_ROW_CEILING"
        cap = parity.get(sport)
        discovery = bool(cap and cap["daily_autonomous_discovery_supported"])
        unscored = len(mine) - len(executed)
        per_sport[sport] = {
            "sport": sport,
            "cataloged": cap is not None,
            "requested": sport in requested,
            "explicitly_requested": explicit_selection and sport in requested,
            "autonomous_discovery_supported": discovery,
            "autonomous_discovery_status": "ENABLED" if discovery else "NOT_IMPLEMENTED",
            "certified_production_lane_count": cap["certified_production_lane_count"] if cap else 0,
            "discovered_current_run": len(mine_receipts),
            "excluded": len(excluded),
            "already_captured_candidates": sport_leg.get("already_captured_candidates"),
            "discovery_attempt_status": sport_leg.get("status"),
            "unattempted_due_to_row_ceiling": sport_leg.get("unattempted_due_to_row_ceiling"),
            "canonical_rows": len(mine),
            "scored": len(executed),
            "unscored": unscored,
            "reconciled": receipt_reconciled,
            "reconciliation_scope": "ACQUISITION_ATTEMPTS_VS_ATTRIBUTED_RECEIPTS_ONLY",
            "coverage_status": (
                "ROWS_PRESENT" if mine else
                ("NOT_ATTEMPTED_ROW_CEILING" if ceiling_skipped else
                 ("DISCOVERY_DATA_UNOBTAINABLE" if sport_leg.get("status") == "DATA_UNOBTAINABLE" else
                  ("NO_ROWS_DISCOVERY_ENABLED" if discovery else "NO_ROWS_DISCOVERY_NOT_IMPLEMENTED")))
            ),
            "zero_rows_is_not_empty_slate_proof": True,
            "can_execute": False,
        }
    unattributed = [r for r in receipts if not r.get("sport")]
    return {
        "contract_version": PROP_PARITY_VERSION + "_COVERAGE_RECEIPT",
        "lane": "PROPS",
        "request_selection_scope": "EXPLICIT_SPORTS" if explicit_selection else "ALL_CATALOGED_DEFAULT",
        "scope_note": (
            "Prop-lane coverage only. cross_sport_discovery_audit is the moneyline/"
            "team-event lane; null there is not prop coverage."
        ),
        "per_sport": per_sport,
        "unattributed_acquisition_receipts": len(unattributed),
        "total_prop_rows": len(prop_rows),
        "all_reconciled": bool(sport_legs)
        and not unattributed
        and all(per_sport[sport]["reconciled"] is True for sport in sport_legs)
        and sum(v["canonical_rows"] for v in per_sport.values()) == len(prop_rows),
        "reconciliation_scope": "ACQUISITION_ATTEMPTS_VS_ATTRIBUTED_RECEIPTS_AND_ROW_SPORT_TOTALS",
        "canonical_persistence_readback_proven_here": False,
        "silent_sport_omission": False,
        "can_execute": False,
    }


__all__ = [
    "prop_sport_coverage_receipt",
    "CAN_EXECUTE",
    "PROP_PARITY_VERSION",
    "prop_sport_parity",
    "prop_sport_parity_summary",
]
