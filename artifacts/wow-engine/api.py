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
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

from regime_model import PrimaryRegime, CohortCounts, PitcherCounts, dirichlet_multinomial_regime_probabilities
from simulation import MIN_SIMULATION_DRAWS
from market import MarketQuote, resolve_market_prior, blend_market_prior
from calibration import phase_a_shrinkage
from ledger import PredictionRow, determine_publishability, insert_prediction

app = FastAPI(title="WOW External Governed Backend", version="1.0.0")

GOVERNED_PROBABILITY_CAPABILITY = "UNAVAILABLE"  # flips to AVAILABLE only after
                                                  # the 10-point deployment gate
                                                  # passes against a live host —
                                                  # do not hardcode true here.


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
        "note": "governed_probability_capability stays UNAVAILABLE until all 10 deployment "
                "gate tests pass against this live deployment. Section 8A Manual Estimate "
                "Lane is the correct fallback until then.",
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


@app.post("/score-prop")
def score_prop(req: ScorePropRequest):
    if GOVERNED_PROBABILITY_CAPABILITY != "AVAILABLE":
        raise HTTPException(
            status_code=409,
            detail={
                "governed_probability_capability": "UNAVAILABLE",
                "governed_probability_status": "NOT_PRODUCED",
                "message": "This deployment has not cleared the 10-point deployment gate. "
                           "Use Section 8A Manual Estimate Lane instead.",
            },
        )
    # Real scoring requires fitted cohort/regime/simulation params supplied
    # from actual historical data — not implemented as a stub here, per
    # 8B.1's prohibition on invented distribution shapes.
    raise HTTPException(status_code=501, detail="Per-sport fitted parameters not yet wired in this deployment.")


@app.post("/settle")
def settle(prediction_id: str, official_result: str, actual_stat: float, hit: bool):
    from ledger import record_outcome
    return record_outcome(prediction_id, official_result=official_result, actual_stat=actual_stat, hit=hit)
