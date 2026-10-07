"""Universal sport-parity contract for V17 team/event orchestration.

Every cataloged sport is treated by the same orchestration stages:
DISCOVERY -> IDENTITY -> EVIDENCE/HYDRATION -> EXACT SPORT MODEL -> CALIBRATION
-> GOVERNANCE -> TERMINAL REDUCTION.

The sporting model and evidence sources remain sport-specific. This module never
creates capability, never substitutes market probability/generic reasoning, and
never upgrades rank/publication. Missing capability/evidence stays typed and
visible. can_execute is always false.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Mapping

from fastapi import HTTPException

from v17.team_event_capability_manifest import (
    EXPECTED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
    normalize_team_event_identity,
)
from v17.team_event_governance_profiles import TEAM_EVENT_GOVERNANCE_PROFILES
from v17.team_event_model_development_manifest import TEAM_EVENT_MODEL_DEVELOPMENT

CAN_EXECUTE = False
PARITY_CONTRACT_VERSION = "V17_TEAM_EVENT_SPORT_PARITY_V1"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

# These identify where canonical sporting evidence is owned. Different sports
# legitimately have different evidence sources; equality means every sport has
# an explicit owner/state and the same fail-closed handoff rules.
_HYDRATION_OWNER = {
    "MLB": "SERVER_CANONICAL_MLB_LEDGER",
    "NFL": "NFL_SPORT_SPECIFIC_PUBLICATION_CHAIN",
    "WNBA": "SPORT_SPECIFIC_EVIDENCE_CONTRACT",
    "NHL": "SPORT_SPECIFIC_EVIDENCE_CONTRACT",
    "SOCCER": "SPORT_SPECIFIC_EVIDENCE_CONTRACT",
    "TENNIS": "SPORT_SPECIFIC_EVIDENCE_CONTRACT",
    "MMA": "SPORT_SPECIFIC_EVIDENCE_CONTRACT",
    "NBA": "MODEL_DEVELOPMENT_LANE",
    "NCAAF": "MODEL_DEVELOPMENT_LANE",
    "NCAAB": "MODEL_DEVELOPMENT_LANE",
    "PGA": "MODEL_DEVELOPMENT_LANE",
    "BOXING": "MODEL_DEVELOPMENT_LANE",
    "CRICKET": "MODEL_DEVELOPMENT_LANE",
}

# Discovery providers may already carry certified sporting fields in a nested
# evidence object. Preserve only fields the exact sport bridge declares/uses;
# do not infer or manufacture anything from prices.
_GENERIC_EVIDENCE_KEYS = {
    "calibration_artifact",
    "status_freshness_hours",
    "sample_size",
    "effective_sample_n",
    "games_sampled",
    "matches_sampled",
    "home_win_pct",
    "away_win_pct",
    "home_elo",
    "away_elo",
    "home_goalie_sv_pct",
    "away_goalie_sv_pct",
    "home_pp_pct",
    "away_pk_pct",
    "home_xg_per_game",
    "away_xg_per_game",
    "fight_history",
    "surface",
    "participant_status",
    "starting_xi_status",
    "goalie_status",
    "expected_starters_rotation",
    "injury_report",
    "rest_back_to_back",
    "rest_travel",
    "competition_rules",
    "home_draw_away_outcome_space",
    "weight_class",
    "scheduled_rounds",
    "weigh_in_status",
    "no_contest_draw_outcome_space",
    "retirement_settlement_rules",
    "tournament_round",
}


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def build_discovery_evidence(event: Any, registration: Any | None = None) -> dict[str, Any]:
    """Preserve provider-supplied sporting evidence without inventing inputs."""
    raw = dict(getattr(event, "raw", None) or {})
    nested: dict[str, Any] = {}
    for key in ("sport_specific_evidence", "evidence", "model_inputs"):
        value = raw.get(key)
        if isinstance(value, Mapping):
            nested.update(dict(value))

    sport = str(getattr(event, "sport", "") or "").upper()
    allowed = set(TEAM_EVENT_INPUT_CONTRACTS.get(sport, ())) | set(_GENERIC_EVIDENCE_KEYS)
    if registration is not None:
        allowed.update(getattr(registration, "required_inputs", ()) or ())

    out: dict[str, Any] = {}
    for key in sorted(allowed):
        value = nested.get(key)
        if not _present(value):
            value = raw.get(key)
        if _present(value):
            out[key] = value

    # Provenance only; these fields never satisfy a fitted sporting input by
    # themselves and never become an "official" league event id.
    out["discovery_provider"] = getattr(event, "provider", None) or getattr(event, "source", None)
    out["discovery_provider_sport_id"] = getattr(event, "provider_sport_id", None)
    out["discovery_regime"] = getattr(event, "regime", None)
    out["discovery_provider_event_id"] = (
        raw.get("provider_event_id")
        or raw.get("event_id")
        or raw.get("id")
        or getattr(event, "official_event_id", None)
    )
    out["market_probability_used_as_model"] = False
    out["generic_reasoning_used_as_model"] = False
    out["can_execute"] = False
    return out


def _state_for(sport: str, bridge_health: Mapping[str, Any] | None = None) -> dict[str, Any]:
    normalized = str(sport or "").upper()
    health = dict(bridge_health or {})
    profile = TEAM_EVENT_GOVERNANCE_PROFILES.get(normalized)
    development = TEAM_EVENT_MODEL_DEVELOPMENT.get(normalized)
    registered = bool(health.get("registered"))
    certification = str(health.get("certification_status") or "UNAVAILABLE")
    scorer = bool(health.get("scorer_resolvable"))
    model_ready = bool(registered and scorer and certification == "CERTIFIED")
    return {
        "contract_version": PARITY_CONTRACT_VERSION,
        "sport": normalized,
        "cataloged": normalized in EXPECTED_TEAM_EVENT_SPORTS,
        "discovery_required": True,
        "canonical_identity_required": True,
        "hydration_owner": _HYDRATION_OWNER.get(normalized, "UNASSIGNED"),
        "required_input_contract": list(TEAM_EVENT_INPUT_CONTRACTS.get(normalized, ())),
        "governance_profile_installed": profile is not None,
        "bridge_registered": registered,
        "scorer_resolvable": scorer,
        "certification_status": certification,
        "model_capability_ready": model_ready,
        "development_status": development.status if development else "UNMAPPED",
        "publication_is_row_scoped": True,
        "rank_metric": "GOVERNED_CALIBRATED_LOWER_BOUND_WHERE_LANE_CONTRACT_REQUIRES",
        "market_probability_substitution_allowed": False,
        "generic_reasoning_substitution_allowed": False,
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def parity_health(bridge_health: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return one complete, same-shaped status row for every cataloged sport."""
    return {
        sport: _state_for(sport, bridge_health.get(sport) or {})
        for sport in EXPECTED_TEAM_EVENT_SPORTS
    }


def _annotate_payload(req: Any, payload: dict[str, Any], health: Mapping[str, Any]) -> dict[str, Any]:
    sport = normalize_team_event_identity(
        getattr(req, "sport", ""), getattr(req, "league", None)
    )
    out = dict(payload)
    out["sport_parity_contract"] = _state_for(sport, health.get(sport) or {})
    out["global_terminal_authority"] = out.get("global_terminal_authority") or TERMINAL_AUTHORITY
    out["rank_eligible"] = out.get("rank_eligible") is True
    out["probability_publishable"] = out.get("probability_publishable") is True
    out["can_execute"] = False
    return out


def install_team_event_sport_parity() -> dict[str, Any]:
    """Install one final parity wrapper after all sport bridges/governance."""
    import v17.team_event_bridge_runtime as bridges
    import v17.team_event_request_runtime as base_runtime

    if getattr(bridges, "_v17_team_event_sport_parity_installed", False):
        return {
            "status": "ALREADY_INSTALLED",
            "sport_parity": parity_health(bridges.team_event_bridge_health()),
            "can_execute": False,
        }

    original_score = bridges.score_registered_team_event_request
    original_health = bridges.team_event_bridge_health

    def parity_score(
        req: Any,
        *,
        event_api: Any,
        canonical_hydration_required: bool = False,
    ) -> dict[str, Any]:
        health = original_health()
        try:
            result = original_score(
                req,
                event_api=event_api,
                canonical_hydration_required=canonical_hydration_required,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            raise HTTPException(
                status_code=exc.status_code,
                detail=_annotate_payload(req, detail, health),
                headers=exc.headers,
            ) from exc
        return _annotate_payload(req, result, health)

    def parity_bridge_health() -> dict[str, dict[str, Any]]:
        base = original_health()
        parity = parity_health(base)
        output: dict[str, dict[str, Any]] = {}
        for sport in EXPECTED_TEAM_EVENT_SPORTS:
            output[sport] = {
                **dict(base.get(sport) or {}),
                "sport_parity_contract": parity[sport],
                "probability_publishable_scope": "ROW_NOT_GLOBAL_CAPABILITY",
                "can_execute": False,
            }
        for sport, value in base.items():
            if sport not in output:
                output[sport] = {**dict(value), "can_execute": False}
        return output

    bridges.score_registered_team_event_request = parity_score
    bridges.team_event_bridge_health = parity_bridge_health
    base_runtime.score_team_event_request = parity_score
    bridges._v17_team_event_sport_parity_original_score = original_score
    bridges._v17_team_event_sport_parity_original_health = original_health
    bridges._v17_team_event_sport_parity_installed = True
    bridges._install_health_overlay()

    return {
        "status": "INSTALLED",
        "sport_parity": parity_health(original_health()),
        "all_cataloged_sports_accounted_for": True,
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }



def canonicalize_nfl_discovery_identity(
    event: Any,
    *,
    req: Any,
    event_api: Any,
    settlement_basis: str,
) -> Any:
    """Resolve an NFL provider alias through the canonical nflverse ledger.

    Provider ids are evidence aliases only. The event's official id changes only
    after the existing NFL specialist proves a unique canonical schedule match.
    """
    if str(getattr(event, "sport", "") or "").upper() != "NFL":
        return event

    raw = dict(getattr(event, "raw", None) or {})
    provider_event_id = str(
        getattr(event, "official_event_id", None)
        or raw.get("provider_event_id")
        or raw.get("event_id")
        or raw.get("id")
        or ""
    ).strip()
    get_client = getattr(event_api, "get_client", None)
    db = get_client() if callable(get_client) else None
    if not provider_event_id or db is None:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = (
            "NFL_PROVIDER_EVENT_ID_MISSING"
            if not provider_event_id
            else "NFL_EVENT_LEDGER_CLIENT_UNAVAILABLE"
        )
        return replace(event, raw=raw)

    from v17.nfl_team_event_specialist import resolve_nfl_team_event_evidence

    probe = SimpleNamespace(
        official_event_id=provider_event_id,
        home_team=event.home_team,
        away_team=event.away_team,
        requested_slate_date=req.requested_slate_date,
        requested_timezone=req.requested_timezone,
        event_start_time_utc=event.commence_time_utc,
        settlement_basis=settlement_basis,
    )
    try:
        resolution = resolve_nfl_team_event_evidence(probe, db=db)
    except Exception as exc:  # noqa: BLE001 - preserve typed identity hold
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = str(
            getattr(exc, "code", None) or type(exc).__name__
        )
        return replace(event, raw=raw)

    if resolution.get("ok") is not True:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = str(
            resolution.get("code") or "NFL_CANONICAL_IDENTITY_UNRESOLVED"
        )
        return replace(event, raw=raw)

    raw["provider_event_id"] = provider_event_id
    raw["canonical_identity_status"] = "CANONICAL_RESOLVED"
    raw["canonical_identity_source"] = "CANONICAL_NFLVERSE_LEDGER"
    raw["canonical_identity_resolution"] = resolution.get("identity_resolution")
    raw["canonical_source_snapshot_id"] = resolution.get(
        "canonical_source_snapshot_id"
    )
    return replace(
        event,
        official_event_id=str(resolution["canonical_event_id"]),
        raw=raw,
    )

def canonicalize_mlb_discovery_identity(
    event: Any,
    *,
    req: Any,
    event_api: Any,
) -> Any:
    """Resolve an MLB provider alias through the canonical MLB event ledger.

    ESPN/Rundown ids remain aliases. The discovery row receives a canonical
    official_event_id only after the existing MLB resolver proves one exact
    participants/start/slate match.
    """
    if str(getattr(event, "sport", "") or "").upper() != "MLB":
        return event

    raw = dict(getattr(event, "raw", None) or {})
    provider_event_id = str(
        getattr(event, "official_event_id", None)
        or raw.get("provider_event_id")
        or raw.get("event_id")
        or raw.get("id")
        or ""
    ).strip()
    if not provider_event_id:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = "MLB_PROVIDER_EVENT_ID_MISSING"
        return replace(event, raw=raw)

    from v17.mlb_team_event_hydration import resolve_mlb_team_event_evidence

    probe = SimpleNamespace(
        official_event_id=provider_event_id,
        requested_slate_date=req.requested_slate_date,
        requested_timezone=req.requested_timezone,
        event_start_time_utc=event.commence_time_utc,
        home_team=event.home_team,
        away_team=event.away_team,
        source_snapshot_id=f"discovery:{event.provider or event.sport_key}:{provider_event_id}",
        latest_material_update_timestamp=None,
        sport_specific_evidence={},
    )
    try:
        resolution = resolve_mlb_team_event_evidence(probe, event_api=event_api)
    except Exception as exc:  # noqa: BLE001 - preserve typed identity hold
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = str(
            getattr(exc, "code", None) or type(exc).__name__
        )
        return replace(event, raw=raw)

    if resolution.get("ok") is not True:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = str(
            resolution.get("code") or "MLB_CANONICAL_IDENTITY_UNRESOLVED"
        )
        return replace(event, raw=raw)

    canonical_event_id = str(
        resolution.get("canonical_official_event_id") or ""
    ).strip()
    if not canonical_event_id:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = "MLB_CANONICAL_EVENT_ID_MISSING"
        return replace(event, raw=raw)

    raw["provider_event_id"] = provider_event_id
    raw["canonical_identity_status"] = "CANONICAL_RESOLVED"
    raw["canonical_identity_source"] = "CANONICAL_MLB_LEDGER"
    raw["canonical_identity_resolution"] = resolution.get(
        "canonical_identity_resolution"
    )
    raw["canonical_source_snapshot_id"] = resolution.get(
        "canonical_source_snapshot_id"
    )
    raw["canonical_snapshot_timestamp"] = resolution.get(
        "canonical_snapshot_timestamp"
    )
    return replace(event, official_event_id=canonical_event_id, raw=raw)





def canonicalize_ncaaf_discovery_identity(event: Any) -> Any:
    """Resolve free ESPN discovery through WOW's established CFBD event identity."""
    if str(getattr(event, "sport", "") or "").upper() != "NCAAF":
        return event

    raw = dict(getattr(event, "raw", None) or {})
    provider_event_id = str(
        raw.get("provider_event_id")
        or raw.get("_wow_secondary_event_id")
        or ""
    ).strip()

    from v17.ncaaf_event_identity import resolve_ncaaf_current_event_identity

    try:
        resolution = resolve_ncaaf_current_event_identity(
            event_start_time=str(event.commence_time_utc),
            home_team=str(event.home_team),
            away_team=str(event.away_team),
        )
    except Exception as exc:  # noqa: BLE001 - preserve typed canonical hold
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = str(
            getattr(exc, "code", None) or type(exc).__name__
        )
        return replace(event, official_event_id=None, raw=raw)

    canonical_event_id = str(resolution.get("event_id") or "").strip()
    if not canonical_event_id:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = "NCAAF_CANONICAL_EVENT_ID_MISSING"
        return replace(event, official_event_id=None, raw=raw)

    if provider_event_id:
        raw["provider_event_id"] = provider_event_id
    raw["canonical_identity_status"] = "CANONICAL_RESOLVED"
    raw["canonical_identity_source"] = str(
        resolution.get("identity_provider") or "CFBD:/games"
    )
    raw["canonical_identity_resolution"] = str(
        resolution.get("identity_resolution")
        or "CFBD_EXACT_PARTICIPANTS_START_MATCH"
    )
    raw["canonical_identity_market_features_used"] = False
    return replace(event, official_event_id=canonical_event_id, raw=raw)


def canonicalize_nhl_discovery_identity(event: Any) -> Any:
    """Resolve a free discovery NHL alias through the first-party NHL schedule."""
    if str(getattr(event, "sport", "") or "").upper() != "NHL":
        return event

    raw = dict(getattr(event, "raw", None) or {})
    provider_event_id = str(
        raw.get("provider_event_id")
        or raw.get("_wow_secondary_event_id")
        or ""
    ).strip()

    from v17.nhl_event_identity import resolve_nhl_current_event_identity

    try:
        resolution = resolve_nhl_current_event_identity(
            event_start_time=str(event.commence_time_utc),
            home_team=str(event.home_team),
            away_team=str(event.away_team),
        )
    except Exception as exc:  # noqa: BLE001 - preserve typed canonical hold
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = str(
            getattr(exc, "code", None) or type(exc).__name__
        )
        return replace(event, official_event_id=None, raw=raw)

    canonical_event_id = str(resolution.get("event_id") or "").strip()
    if not canonical_event_id:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = "NHL_CANONICAL_EVENT_ID_MISSING"
        return replace(event, official_event_id=None, raw=raw)

    if provider_event_id:
        raw["provider_event_id"] = provider_event_id
    raw["canonical_identity_status"] = "CANONICAL_RESOLVED"
    raw["canonical_identity_source"] = str(
        resolution.get("identity_provider") or "NHL_PUBLIC_WEB_API"
    )
    raw["canonical_identity_resolution"] = "OFFICIAL_NHL_SCHEDULE_EXACT_MATCH"
    raw["canonical_identity_verified_at"] = resolution.get("identity_verified_at")
    raw["canonical_identity_market_features_used"] = False
    return replace(event, official_event_id=canonical_event_id, raw=raw)


def canonicalize_wnba_discovery_identity(event: Any) -> Any:
    """Resolve an ESPN WNBA discovery alias through the official WNBA schedule.

    ESPN event/team ids remain aliases. The event receives a canonical WNBA
    event id only after the existing reconciler proves one exact league-owned
    schedule match by event alias, teams and start time.
    """
    if str(getattr(event, "sport", "") or "").upper() != "WNBA":
        return event

    raw = dict(getattr(event, "raw", None) or {})
    provider_event_id = str(
        raw.get("provider_event_id")
        or raw.get("_wow_secondary_event_id")
        or getattr(event, "official_event_id", None)
        or ""
    ).strip()
    home_alias = str(raw.get("_wow_secondary_home_team_id") or "").strip()
    away_alias = str(raw.get("_wow_secondary_away_team_id") or "").strip()

    if not provider_event_id:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = "WNBA_PROVIDER_EVENT_ID_MISSING"
        return replace(event, raw=raw)
    if not home_alias or not away_alias:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = "WNBA_PROVIDER_TEAM_ALIAS_MISSING"
        return replace(event, raw=raw)

    event_alias = (
        provider_event_id
        if provider_event_id.startswith("espn-")
        else f"espn-{provider_event_id}"
    )
    home_team_id = home_alias if home_alias.startswith("espn-") else f"espn-{home_alias}"
    away_team_id = away_alias if away_alias.startswith("espn-") else f"espn-{away_alias}"

    from v17.wnba_spread_event_identity import resolve_wnba_current_event_identity

    try:
        resolution = resolve_wnba_current_event_identity(
            event_id=event_alias,
            event_start_time=str(event.commence_time_utc),
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )
    except Exception as exc:  # noqa: BLE001 - preserve typed identity hold
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = str(
            getattr(exc, "code", None) or type(exc).__name__
        )
        return replace(event, raw=raw)

    canonical_event_id = str(resolution.get("event_id") or "").strip()
    if not canonical_event_id:
        raw["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
        raw["canonical_identity_blocker"] = "WNBA_CANONICAL_EVENT_ID_MISSING"
        return replace(event, raw=raw)

    raw["provider_event_id"] = provider_event_id
    raw["canonical_identity_status"] = "CANONICAL_RESOLVED"
    raw["canonical_identity_source"] = str(
        resolution.get("identity_provider") or "WNBA_OFFICIAL_SCHEDULE_API"
    )
    raw["canonical_identity_alias_provider"] = str(
        resolution.get("identity_alias_provider") or "ESPN_SCOREBOARD"
    )
    raw["canonical_identity_resolution"] = "OFFICIAL_WNBA_SCHEDULE_EXACT_MATCH"
    raw["canonical_identity_verified_at"] = resolution.get("identity_verified_at")
    return replace(event, official_event_id=canonical_event_id, raw=raw)


def cross_sport_model_coverage(rows: Any, *, requested_model_budget: int | None = None) -> dict[str, Any]:
    """Separate discovery accounting from identity/model-routing coverage."""
    from v17 import cross_sport_winner_discovery as discovery

    discovered = [row for row in (rows or []) if isinstance(row, Mapping)]
    non_candidates = {
        discovery.WRONG_DATE,
        discovery.STARTED_OR_FINAL,
        discovery.CANCELLED_OR_POSTPONED,
    }
    candidates = [row for row in discovered if row.get("bucket") not in non_candidates]
    identity_unresolved = sum(
        1 for row in candidates if row.get("bucket") == discovery.IDENTITY_UNRESOLVED
    )
    model_routed = sum(
        1 for row in candidates if row.get("bucket") in discovery.MODEL_BUCKETS
    )
    model_invoked = 0
    for row in candidates:
        detail = row.get("detail") if isinstance(row.get("detail"), Mapping) else {}
        if row.get("model_invoked") is True or detail.get("model_invoked") is True:
            model_invoked += 1

    candidate_count = len(candidates)
    if candidate_count == 0:
        routing_status = "NO_ELIGIBLE_PREGAME_ROWS"
    elif model_routed == candidate_count:
        routing_status = "FULL_MODEL_ROUTING"
    elif model_routed == 0:
        routing_status = "NO_MODEL_ROUTING"
    else:
        routing_status = "PARTIAL_MODEL_ROUTING"

    return {
        "discovered_rows": len(discovered),
        "pregame_candidate_rows": candidate_count,
        "identity_resolved_pregame_rows": max(candidate_count - identity_unresolved, 0),
        "identity_unresolved_rows": identity_unresolved,
        "model_routed_rows": model_routed,
        "model_invoked_rows": model_invoked,
        "model_routing_coverage_status": routing_status,
        "requested_model_invocation_budget": requested_model_budget,
        "max_team_events_semantics": "MODEL_INVOCATION_BUDGET_NOT_DISCOVERY_ROW_CAP",
        "discovery_rows_retained_for_reconciliation": True,
        "can_execute": False,
    }


def install_cross_sport_discovery_evidence_handoff() -> bool:
    """Give every discovered registered sport the same evidence handoff chance.

    This replaces only the Daily cross-sport orchestration wrapper. The exact
    sport bridge remains responsible for canonical hydration, model inputs,
    calibration, and publication. Provider evidence is passed through only when
    explicitly present; nothing is synthesized from odds.
    """
    from v17 import daily_snapshot_runtime as daily

    if getattr(daily, "_v17_cross_sport_discovery_evidence_handoff_installed", False):
        return True

    def repaired_cross_sport_moneyline_rows(
        req: Any,
        *,
        run_id: str,
        event_api: Any,
        covered_event_ids: set[str],
        fetch_sport_events: Any = None,
    ):
        if not daily.discovery_feed.enabled():
            return None

        feed = fetch_sport_events
        if feed is None:
            feed = daily.discovery_feed.union_feed(
                daily.discovery_feed.odds_proxy_feed(),
                daily.discovery_feed.rundown_board_feed(
                    slate_date=req.requested_slate_date
                ),
            )

        def canonicalize_identity(event: Any) -> Any:
            sport = str(getattr(event, "sport", "") or "").upper()
            if sport == "MLB":
                return canonicalize_mlb_discovery_identity(
                    event,
                    req=req,
                    event_api=event_api,
                )
            if sport == "NFL":
                return canonicalize_nfl_discovery_identity(
                    event,
                    req=req,
                    event_api=event_api,
                    settlement_basis=daily._settlement_basis("NFL"),
                )
            if sport == "WNBA":
                return canonicalize_wnba_discovery_identity(event)
            if sport == "NCAAF":
                return canonicalize_ncaaf_discovery_identity(event)
            if sport == "NHL":
                return canonicalize_nhl_discovery_identity(event)
            return event

        def resolve_model(event: Any) -> Any:
            return daily.bridge_runtime.TEAM_EVENT_BRIDGES.get(event.sport)

        def score(event: Any, model: Any) -> dict[str, Any]:
            if str(event.official_event_id or "") in covered_event_ids:
                return {
                    "code": "ALREADY_SCORED_IN_CANONICAL_LANE",
                    "probability_publishable": False,
                    "rank_eligible": False,
                    "can_execute": False,
                }
            evidence = build_discovery_evidence(event, model)
            try:
                request = daily.TeamEventRequest(
                    requester_host_identity="WOW_BETTING_ENGINE",
                    research_run_id=run_id,
                    requested_slate_date=req.requested_slate_date,
                    requested_timezone=req.requested_timezone,
                    candidate_family="OUTRIGHT_WINNER",
                    decision_intent="BEST_SIDE",
                    event_key=event.event_key,
                    official_event_id=str(event.official_event_id),
                    event_start_time_utc=str(event.commence_time_utc),
                    sport=event.sport,
                    league=event.league or event.sport,
                    settlement_basis=daily._settlement_basis(event.sport),
                    home_team=str(event.home_team),
                    away_team=str(event.away_team),
                    source_snapshot_id=(
                        f"discovery:{event.provider or event.sport_key}:"
                        f"{event.official_event_id}"
                    ),
                    sport_specific_evidence=evidence,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "code": "MODEL_INPUTS_INSUFFICIENT",
                    "blockers": ["TEAM_EVENT_REQUEST_CONTRACT_INVALID"],
                    "error_type": type(exc).__name__,
                    "sport_parity_contract": {
                        "contract_version": PARITY_CONTRACT_VERSION,
                        "sport": event.sport,
                        "can_execute": False,
                    },
                    "probability_publishable": False,
                    "rank_eligible": False,
                    "can_execute": False,
                }
            try:
                return daily.score_team_event_request(
                    request,
                    event_api=event_api,
                    canonical_hydration_required=True,
                )
            except HTTPException as exc:
                return daily._detail(exc)
            except Exception as exc:  # noqa: BLE001
                return {
                    "code": "MODEL_SCORER_FAILED",
                    "error_type": type(exc).__name__,
                    "model_invoked": True,
                    "probability_publishable": False,
                    "rank_eligible": False,
                    "can_execute": False,
                }

        scan = dict(daily.discovery.run_cross_sport_winner_scan(
            requested_slate_date=req.requested_slate_date,
            requested_timezone=req.requested_timezone,
            fetch_sport_events=feed,
            resolve_model=resolve_model,
            score_row=score,
            canonicalize_identity=canonicalize_identity,
        ))
        scan["model_coverage"] = cross_sport_model_coverage(
            scan.get("rows"),
            requested_model_budget=int(getattr(req, "max_team_events", 0) or 0),
        )
        rows = [
            daily._terminal_row(
                "MONEYLINE",
                {
                    key: row.get(key)
                    for key in (
                        "official_event_id",
                        "commence_time_utc",
                        "home_team",
                        "away_team",
                        "sport",
                    )
                },
                row,
                daily.assert_no_terminal_upgrade(
                    daily.reduce_row_terminal(
                        [
                            "COMPLETED"
                            if row.get("bucket") == daily.discovery.MODEL_COMPLETED
                            else "HELD"
                        ]
                    )
                ),
            )
            for row in scan["rows"]
            if str(row.get("official_event_id") or "") not in covered_event_ids
        ]
        return rows, scan

    daily._cross_sport_moneyline_rows = repaired_cross_sport_moneyline_rows
    daily._v17_cross_sport_discovery_evidence_handoff_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "PARITY_CONTRACT_VERSION",
    "build_discovery_evidence",
    "canonicalize_mlb_discovery_identity",
    "canonicalize_nfl_discovery_identity",
    "canonicalize_ncaaf_discovery_identity",
    "canonicalize_nhl_discovery_identity",
    "canonicalize_wnba_discovery_identity",
    "cross_sport_model_coverage",
    "install_cross_sport_discovery_evidence_handoff",
    "install_team_event_sport_parity",
    "parity_health",
]
