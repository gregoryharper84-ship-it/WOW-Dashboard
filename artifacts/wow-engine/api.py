"""
api.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2

FastAPI service exposing the endpoints named in the patch's host-registry
section (8B.5), for deployment on Render (compute_provider=RENDER) with
Supabase as database_provider.

This is a skeleton: it wires the ratified engine modules together and
enforces the governance gates, but per-sport conditional distribution
parameters (regime_model cohort counts, simulation stat samplers) must
be supplied from real fitted data before this can score live props —
consistent with 8B.1's prohibition on invented distribution shapes.

Step 3d review fix: previously /score-prop unconditionally 501'd even
once GOVERNED_PROBABILITY_CAPABILITY reached AVAILABLE, so Gate 11's
positive-path test (engine.py::score_prop_end_to_end, called directly)
could never actually prove the deployed endpoint works — "gates 1-10
could theoretically all pass while the scoring endpoint still returns
nothing usable." The handler below now actually calls the engine through
an injectable fitted-params provider seam: production ships with no
provider registered (so still correctly 501s — real per-sport data isn't
wired in), but a test can register a clearly-labeled synthetic/staging
provider via set_fitted_params_provider() and hit this real HTTP route
with FastAPI's TestClient to prove the endpoint-to-ledger path works,
without claiming real production distributions exist.

PRE_PRODUCTION_BLOCKER_API_AUTH -- RESOLVED: /score-prop, /score-event,
and /settle require Authorization: Bearer <WOW_ACTION_API_KEY> (see
_require_action_api_key below). This is application-layer auth only —
a caller (e.g. a Custom GPT Action) proves it holds WOW_ACTION_API_KEY;
it never sees the Supabase service-role credential, which stays backend-
only and is never part of this app's request/response schema. /health
and /governance stay public — they expose state, not a privileged
database mutation path.

/score-event MLB v1 is deliberately fail-closed. It validates an
authenticated full-game MLB outright-winner event contract, but until
the real fitted MLB game-win artifact and eligible event calibrator are
wired, it returns HTTP 409 with no numeric probability and performs no
persistence. It must never route team/event ML through /score-prop or
launder market-implied/manual estimates into governed model output.
"""
from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from regime_model import PrimaryRegime, CohortCounts, PitcherCounts
from simulation import RegimeConditionalParams, MIN_SIMULATION_DRAWS
from engine import score_prop_end_to_end
from ledger import insert_prediction, get_client

app = FastAPI(title="WOW External Governed Backend", version="1.1.0")

DEPLOYMENT_GATE_COUNT = 11  # see deployment_gate_tests.py; Gate 11 (real
                            # end-to-end positive path) was added per
                            # ChatGPT's 2026-08-26 code review.

GOVERNED_PROBABILITY_CAPABILITY = "UNAVAILABLE"  # Legacy constant, retained only
                                                  # for the /governance note text
                                                  # below. Runtime gating no longer
                                                  # trusts a hardcoded module flag
                                                  # an engineer could accidentally
                                                  # leave flipped -- it derives its
                                                  # answer from the live G01-G11
                                                  # ledger (wow_governed_deployment_
                                                  # state() in Supabase) via
                                                  # _governed_capability_provider
                                                  # below.


def _query_deployment_gate_state() -> Optional[dict]:
    """Live read of the G01-G11 deployment-gate ledger via the
    wow_governed_deployment_state() Supabase RPC. Returns None -- never an
    optimistic default -- if Supabase is unreachable/unconfigured; callers
    must treat that as UNAVAILABLE (missing required evidence fails closed,
    per the project's governance invariants)."""
    try:
        client = get_client()
        result = client.rpc("wow_governed_deployment_state", {}).execute()
        return result.data
    except Exception:
        return None


def _query_calibration_health() -> Optional[dict]:
    """Live read of the most recently assessed MLB V2D Calibration Health
    row, so /governance can report it honestly instead of omitting it."""
    try:
        client = get_client()
        result = (
            client.table("wow_mlb_v2d_calibration_health")
            .select("*")
            .order("assessed_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception:
        return None


def _default_governed_capability_provider() -> str:
    gate_state = _query_deployment_gate_state()
    if not gate_state:
        return "UNAVAILABLE"
    return gate_state.get("governed_probability_capability", "UNAVAILABLE")


GovernedCapabilityProvider = Callable[[], str]
_governed_capability_provider: GovernedCapabilityProvider = _default_governed_capability_provider


def set_governed_capability_provider(provider: GovernedCapabilityProvider) -> None:
    """Test/staging seam, mirroring set_fitted_params_provider/set_persist_fn
    below. Production leaves this unset, so every request queries the live
    gate ledger fresh -- gate status can only ever go stale in the direction
    of UNAVAILABLE (an unreachable ledger), never the reverse."""
    global _governed_capability_provider
    _governed_capability_provider = provider


def _default_controlling_specialist_provider(sport: str, prop_type: str) -> Optional[dict]:
    """Live read of the wow_controlling_specialist() Supabase RPC -- the
    single source of truth for which specialist governs a (sport, prop_type)
    pair, including the MLB first-inning-pitch-count routing invariant
    (wow.mlb-first-inning-pitch-count-expert, >=25,000 event-tree
    simulations). Returns None if unreachable; callers must fail closed
    rather than assume a default/generic specialist."""
    try:
        client = get_client()
        result = client.rpc(
            "wow_controlling_specialist", {"p_sport": sport, "p_prop_type": prop_type}
        ).execute()
        return result.data
    except Exception:
        return None


ControllingSpecialistProvider = Callable[[str, str], Optional[dict]]
_controlling_specialist_provider: ControllingSpecialistProvider = _default_controlling_specialist_provider


def set_controlling_specialist_provider(provider: ControllingSpecialistProvider) -> None:
    """Test/staging seam, mirroring the other _xxx_provider seams here."""
    global _controlling_specialist_provider
    _controlling_specialist_provider = provider


@dataclass
class FittedParamsBundle:
    """Real (or, in a test, clearly-labeled synthetic/staging) fitted
    inputs for one (sport, stat_type). Per 8B.1, this module never
    invents these itself — see set_fitted_params_provider()."""
    cohort: CohortCounts
    pitcher: PitcherCounts
    regime_params: dict[PrimaryRegime, RegimeConditionalParams]
    resample_fn: Callable
    n_eff: float
    parent_cohort: Optional[str] = None
    settled_n_in_cohort: int = 0


FittedParamsProvider = Callable[[str, str], Optional[FittedParamsBundle]]


def _no_fitted_params(sport: str, stat_type: str) -> Optional[FittedParamsBundle]:
    return None


_fitted_params_provider: FittedParamsProvider = _no_fitted_params
_persist_fn: Callable[..., dict] = insert_prediction


def set_fitted_params_provider(provider: FittedParamsProvider) -> None:
    """Test/staging seam. No provider is registered by default — leave
    unset in production until real per-sport historical fits are ready
    (see README "Per-sport fitted parameters")."""
    global _fitted_params_provider
    _fitted_params_provider = provider


def set_persist_fn(fn: Callable[..., dict]) -> None:
    """Test seam so /score-prop can be exercised without a live Supabase
    instance (gates 1/8 remain untestable in this sandbox — see README)."""
    global _persist_fn
    _persist_fn = fn


def _require_action_api_key(authorization: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency guarding every route that can mutate the
    service-role-backed store (/score-prop, /score-event, /settle).
    Callers authenticate with ``Authorization: Bearer <WOW_ACTION_API_KEY>``
    — an application-layer credential distinct from, and never exchanged
    for, the Supabase service-role key, which stays backend-only.

    Fails closed: if WOW_ACTION_API_KEY is not configured in this
    deployment's environment, every protected request is rejected with
    401 rather than silently admitted. The supplied token is compared
    with a constant-time comparison and is never logged or echoed.
    """
    configured_key = os.environ.get("WOW_ACTION_API_KEY")
    if not configured_key:
        raise HTTPException(status_code=401, detail="This deployment is not configured for authenticated access.")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    supplied_key = authorization[len("Bearer "):]
    if not secrets.compare_digest(supplied_key, configured_key):
        raise HTTPException(status_code=401, detail="Invalid credential.")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "host_type": "EXTERNAL_GOVERNED_BACKEND",
        "compute_provider": "RENDER",
        "database_provider": "SUPABASE",
        "batch_provider": "COLAB",
        "deployment_tier": "FREE",
    }


@app.get("/governance")
def governance():
    """Reports the actual currently-evidenced governed state -- never an
    optimistic default. Both the deployment-gate ledger and Calibration
    Health are read live on every call; an unreachable gate ledger reports
    UNAVAILABLE/NOT_PRODUCED rather than silently omitting the field or
    assuming a prior good state."""
    gate_state = _query_deployment_gate_state()
    calibration_health = _query_calibration_health()

    if gate_state:
        capability = gate_state.get("governed_probability_capability", "UNAVAILABLE")
        governed_status = gate_state.get("governed_probability_status", "NOT_PRODUCED")
        deployment_gates = gate_state.get("deployment_gates", [])
    else:
        capability = "UNAVAILABLE"
        governed_status = "NOT_PRODUCED"
        deployment_gates = "GATE_LEDGER_UNREACHABLE"

    return {
        "governed_probability_capability": capability,
        "governed_probability_status": governed_status,
        "probability_publishable": False,
        "can_execute": False,
        "deployment_gates": deployment_gates,
        "calibration_health": calibration_health,
        "patch_id": "WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE",
        "patch_revision": "v2",
        "note": f"governed_probability_capability stays UNAVAILABLE until all "
                f"{DEPLOYMENT_GATE_COUNT} deployment gate items pass against this live "
                f"deployment. Section 8A Manual Estimate Lane is the correct fallback "
                f"until then.",
    }


class ScorePropRequest(BaseModel):
    event_id: str
    event_start_time: str
    sport: str
    player: Optional[str] = None
    stat_type: str
    line: float
    direction: str
    source_snapshot_id: str
    seed: int = 0
    money_lane_status: str = "PAYOUT_UNRESOLVED"
    # Step 3d BLOCKER-02 fix: scored_at is deliberately NOT a request field.
    # An ordinary caller must not be able to supply or backdate the
    # governed scoring timestamp -- /score-prop always uses the server
    # clock (see below). Deterministic validation/backtesting callers use
    # the separate, controlled path: calling engine.score_prop_end_to_end()
    # directly with an explicit scored_at (see deployment_gate_tests.py).


class EventMarketPrior(BaseModel):
    """Optional current two-way no-vig MLB market prior.

    It is context/prior evidence only. The event route never promotes it
    into a model probability when the fitted MLB model is unavailable.
    """
    model_config = ConfigDict(extra="forbid")

    home_probability: float
    away_probability: float
    timestamp: str
    quality: Optional[str] = None
    source: Optional[str] = None


class ScoreEventRequest(BaseModel):
    """MLB full-game outright-winner identity/evidence contract.

    Backend-owned model/calibration/publication fields are intentionally
    absent so clients cannot inject a probability and have it re-labeled
    as governed model output.
    """
    model_config = ConfigDict(extra="forbid")

    research_run_id: str
    requested_slate_date: str
    requested_timezone: str
    scan_stage: str

    event_key: str
    official_event_id: str
    event_start_time_utc: str

    sport: str
    league: str
    market_family: str
    settlement_basis: str

    home_team: str
    away_team: str
    venue: str

    home_starting_pitcher: str
    away_starting_pitcher: str
    home_starter_status: str
    away_starter_status: str
    home_lineup_status: str
    away_lineup_status: str

    latest_material_update_timestamp: Optional[str] = None
    source_snapshot_id: str
    market_prior: Optional[EventMarketPrior] = None


def _parse_aware_timestamp(value: str) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed


def _score_event_contract_errors(req: ScoreEventRequest) -> list[str]:
    """Validate the v1 event contract without scoring or persistence."""
    errors: list[str] = []

    required_text = {
        "research_run_id": req.research_run_id,
        "requested_timezone": req.requested_timezone,
        "event_key": req.event_key,
        "official_event_id": req.official_event_id,
        "league": req.league,
        "home_team": req.home_team,
        "away_team": req.away_team,
        "venue": req.venue,
        "home_starting_pitcher": req.home_starting_pitcher,
        "away_starting_pitcher": req.away_starting_pitcher,
        "home_starter_status": req.home_starter_status,
        "away_starter_status": req.away_starter_status,
        "home_lineup_status": req.home_lineup_status,
        "away_lineup_status": req.away_lineup_status,
        "source_snapshot_id": req.source_snapshot_id,
    }
    for field_name, value in required_text.items():
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field_name} missing or empty")

    if req.sport != "MLB":
        errors.append("sport must be MLB for /score-event v1")
    if req.market_family != "OUTRIGHT_WINNER":
        errors.append("market_family must be OUTRIGHT_WINNER")
    if req.settlement_basis != "FULL_GAME_INCLUDING_EXTRA_INNINGS":
        errors.append("settlement_basis must be FULL_GAME_INCLUDING_EXTRA_INNINGS")
    if req.scan_stage != "PREGAME":
        errors.append("scan_stage must be PREGAME")
    if req.league != "MLB":
        errors.append("league must be MLB for /score-event v1")
    if req.home_team.strip() and req.home_team.strip().casefold() == req.away_team.strip().casefold():
        errors.append("home_team and away_team must differ")

    try:
        date.fromisoformat(req.requested_slate_date)
    except (TypeError, ValueError):
        errors.append("requested_slate_date must be YYYY-MM-DD")

    event_start = _parse_aware_timestamp(req.event_start_time_utc)
    if event_start is None:
        errors.append("event_start_time_utc must be an ISO 8601 timestamp with timezone")
    elif event_start.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        errors.append("event has already started; /score-event v1 is pregame only")

    if req.latest_material_update_timestamp is not None:
        if _parse_aware_timestamp(req.latest_material_update_timestamp) is None:
            errors.append("latest_material_update_timestamp must be an ISO 8601 timestamp with timezone")

    try:
        uuid.UUID(req.source_snapshot_id)
    except (ValueError, TypeError, AttributeError):
        errors.append("source_snapshot_id must be a UUID")

    if req.market_prior is not None:
        hp = req.market_prior.home_probability
        ap = req.market_prior.away_probability
        if not (0 < hp < 1) or not (0 < ap < 1):
            errors.append("market_prior probabilities must each satisfy 0<p<1")
        elif abs((hp + ap) - 1.0) > 1e-6:
            errors.append("market_prior home+away probabilities must normalize to 1")
        if _parse_aware_timestamp(req.market_prior.timestamp) is None:
            errors.append("market_prior.timestamp must be an ISO 8601 timestamp with timezone")

    return errors


@app.post("/score-prop", dependencies=[Depends(_require_action_api_key)])
def score_prop(req: ScorePropRequest):
    if _governed_capability_provider() != "AVAILABLE":
        raise HTTPException(
            status_code=409,
            detail={
                "governed_probability_capability": "UNAVAILABLE",
                "governed_probability_status": "NOT_PRODUCED",
                "message": f"This deployment has not cleared the {DEPLOYMENT_GATE_COUNT}-point "
                           f"deployment gate. Use Section 8A Manual Estimate Lane instead.",
            },
        )

    # Specialist routing (R3 / MLB 1IP invariant): determine the
    # controlling specialist for this (sport, stat_type) from the governed
    # routing ledger before scoring anything. A prop with no routed
    # specialist must never be scored by generic reasoning, trends, or
    # market intuition -- it is refused, not silently downgraded.
    specialist = _controlling_specialist_provider(req.sport, req.stat_type)
    if specialist is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SPECIALIST_ROUTING_UNAVAILABLE",
                "message": "Could not reach the controlling-specialist routing ledger.",
            },
        )
    if specialist.get("controlling_specialist") == "MODEL_UNAVAILABLE":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MODEL_UNAVAILABLE",
                "probability_publishable": False,
                "sport": specialist.get("sport"),
                "canonical_prop_type": specialist.get("canonical_prop_type"),
                "message": "No controlling specialist is routed for this (sport, prop_type). "
                           "Refusing to substitute generic reasoning, trends, or market "
                           "intuition for a governed model.",
            },
        )
    # The routed specialist may require more simulations than the generic
    # 8B.2 floor (e.g. the MLB 1IP contract's 25,000-simulation minimum) --
    # never fewer than MIN_SIMULATION_DRAWS, which stays the absolute floor.
    draws = max(MIN_SIMULATION_DRAWS, specialist.get("min_event_tree_simulations") or 0)

    # Real scoring requires fitted cohort/regime/simulation params supplied
    # from actual historical data — not invented here, per 8B.1's
    # prohibition on invented distribution shapes.
    bundle = _fitted_params_provider(req.sport, req.stat_type)
    if bundle is None:
        raise HTTPException(
            status_code=501,
            detail="Per-sport fitted parameters not yet wired in this deployment.",
        )

    # Server-generated UTC scoring time -- see ScorePropRequest note above.
    scored_at = datetime.now(timezone.utc).isoformat()

    result = score_prop_end_to_end(
        event_id=req.event_id, event_start_time=req.event_start_time, sport=req.sport,
        stat_type=req.stat_type, line=req.line, direction=req.direction,
        source_snapshot_id=req.source_snapshot_id,
        cohort=bundle.cohort, pitcher=bundle.pitcher, regime_params=bundle.regime_params,
        resample_fn=bundle.resample_fn, n_eff=bundle.n_eff, seed=req.seed,
        candidate_direction=req.direction, scored_at=scored_at,
        parent_cohort=bundle.parent_cohort, settled_n_in_cohort=bundle.settled_n_in_cohort,
        money_lane_status=req.money_lane_status, draws=draws,
    )

    if not result.row.probability_publishable:
        raise HTTPException(
            status_code=422,
            detail={
                "probability_publishable": False,
                "data_gaps": result.row.data_gaps,
                "error": result.error,
            },
        )

    return _persist_fn(result.row)


@app.post("/score-event", dependencies=[Depends(_require_action_api_key)])
def score_event(req: ScoreEventRequest):
    """Validate one MLB full-game ML event, then fail closed until fitted.

    V1 intentionally has no scoring/persistence positive path. This route
    proves identity/auth/routing separation without turning an absent
    model artifact into an invented probability.
    """
    errors = _score_event_contract_errors(req)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVENT_CONTRACT_INVALID",
                "probability_publishable": False,
                "errors": errors,
                "can_execute": False,
            },
        )

    raise HTTPException(
        status_code=409,
        detail={
            "ok": False,
            "code": "GOVERNED_EVENT_MODEL_UNAVAILABLE",
            "sport": "MLB",
            "market_family": "OUTRIGHT_WINNER",
            "controlling_specialist": "wow.mlb-game-win-probability-expert",
            "governed_probability_capability": "UNAVAILABLE",
            "governed_probability_status": "NOT_PRODUCED",
            "probability_publishable": False,
            "fallback": "SECTION_8A_MANUAL_ESTIMATE_LANE",
            "blockers": [
                "MLB_FITTED_MODEL_ARTIFACT_UNAVAILABLE",
                "MLB_EVENT_CALIBRATOR_UNAVAILABLE",
            ],
            "can_execute": False,
        },
    )


@app.post("/settle", dependencies=[Depends(_require_action_api_key)])
def settle(prediction_id: str, official_result: str, actual_stat: float, hit: bool):
    # Step 3d BLOCKER-01 corollary: without a recorded settlement_timestamp,
    # calibrator_store.load_historical_calibration_rows() fails this row
    # closed (excludes it) rather than risk using event_start_time as a
    # stand-in availability marker. Server-generated, for the same reason
    # /score-prop's scored_at is -- an ordinary caller must not backdate
    # when a result became knowable.
    from ledger import record_outcome
    return record_outcome(
        prediction_id, official_result=official_result, actual_stat=actual_stat, hit=hit,
        settlement_timestamp=datetime.now(timezone.utc).isoformat(),
    )


# Agent Runtime V1 (Phase 1): durable run ledger, polling, and health-check
# endpoints. Imported last so its routes attach to `app` after every route
# above is already registered — api_g11.py and api_prod.py both copy `app`'s
# routes wholesale except the specific paths they each override
# (/score-event, /governance, /score-prop), so anything agent_runtime_api
# registers propagates through both wrapper layers without either changing.
import agent_runtime_api  # noqa: E402,F401
