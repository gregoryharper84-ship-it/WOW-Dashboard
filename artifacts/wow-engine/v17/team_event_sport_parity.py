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
    out["discovery_provider_event_id"] = getattr(event, "official_event_id", None)
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

        scan = daily.discovery.run_cross_sport_winner_scan(
            requested_slate_date=req.requested_slate_date,
            requested_timezone=req.requested_timezone,
            fetch_sport_events=feed,
            resolve_model=resolve_model,
            score_row=score,
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
    "install_cross_sport_discovery_evidence_handoff",
    "install_team_event_sport_parity",
    "parity_health",
]
