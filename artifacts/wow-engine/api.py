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
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from regime_model import PrimaryRegime, CohortCounts, PitcherCounts
from simulation import RegimeConditionalParams, MIN_SIMULATION_DRAWS
from engine import score_prop_end_to_end
from ledger import insert_prediction

app = FastAPI(title="WOW External Governed Backend", version="1.0.0")

DEPLOYMENT_GATE_COUNT = 11  # see deployment_gate_tests.py; Gate 11 (real
                            # end-to-end positive path) was added per
                            # ChatGPT's 2026-08-26 code review.

GOVERNED_PROBABILITY_CAPABILITY = "UNAVAILABLE"  # flips to AVAILABLE only after
                                                  # all 11 deployment gate items
                                                  # pass against a live host —
                                                  # do not hardcode true here.


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
    return {
        "governed_probability_capability": GOVERNED_PROBABILITY_CAPABILITY,
        "governed_probability_status": "NOT_PRODUCED" if GOVERNED_PROBABILITY_CAPABILITY == "UNAVAILABLE" else "PRODUCED",
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
    scored_at: Optional[str] = None
    money_lane_status: str = "PAYOUT_UNRESOLVED"


@app.post("/score-prop")
def score_prop(req: ScorePropRequest):
    if GOVERNED_PROBABILITY_CAPABILITY != "AVAILABLE":
        raise HTTPException(
            status_code=409,
            detail={
                "governed_probability_capability": "UNAVAILABLE",
                "governed_probability_status": "NOT_PRODUCED",
                "message": f"This deployment has not cleared the {DEPLOYMENT_GATE_COUNT}-point "
                           f"deployment gate. Use Section 8A Manual Estimate Lane instead.",
            },
        )

    # Real scoring requires fitted cohort/regime/simulation params supplied
    # from actual historical data — not invented here, per 8B.1's
    # prohibition on invented distribution shapes.
    bundle = _fitted_params_provider(req.sport, req.stat_type)
    if bundle is None:
        raise HTTPException(
            status_code=501,
            detail="Per-sport fitted parameters not yet wired in this deployment.",
        )

    result = score_prop_end_to_end(
        event_id=req.event_id, event_start_time=req.event_start_time, sport=req.sport,
        stat_type=req.stat_type, line=req.line, direction=req.direction,
        source_snapshot_id=req.source_snapshot_id,
        cohort=bundle.cohort, pitcher=bundle.pitcher, regime_params=bundle.regime_params,
        resample_fn=bundle.resample_fn, n_eff=bundle.n_eff, seed=req.seed,
        candidate_direction=req.direction, scored_at=req.scored_at,
        parent_cohort=bundle.parent_cohort, settled_n_in_cohort=bundle.settled_n_in_cohort,
        money_lane_status=req.money_lane_status, draws=MIN_SIMULATION_DRAWS,
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


@app.post("/settle")
def settle(prediction_id: str, official_result: str, actual_stat: float, hit: bool):
    from ledger import record_outcome
    return record_outcome(prediction_id, official_result=official_result, actual_stat=actual_stat, hit=hit)
