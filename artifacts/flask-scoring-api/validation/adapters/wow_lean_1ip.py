"""
validation/adapters/wow_lean_1ip.py

WOW_LEAN_1IP adapter — reads existing production model outputs WITHOUT
changing any production code, gates, or endpoints.

This adapter calls:
  1. gate_engine.mlb.savant_1ip_ledger.build_1ip_ledger()   (read-only)
  2. gate_engine.mlb.ip1_event_tree.simulate_1ip()           (read-only)
  3. gate_engine.mlb.savant_1ip_ledger.compute_pitches_per_batter_dist()

It does NOT call app.py, any Flask endpoint, or any live scoring route.
All production invariants (can_execute=False, ceiling=MODEL_QUALIFIED_HOLD)
remain untouched in the production modules.

Output is a PredictionRecord, not a live scoring decision.

Adapter model version string matches the production patch that introduced
the event-tree activation: WOW-PATCH-2026-08-17-1IP-PRODUCTION-HYDRATION.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from validation.schema.prediction_record import PredictionRecord

ADAPTER_ID      = "WOW_LEAN_1IP"
MODEL_VERSION   = "1ip_monte_carlo_event_tree_v1"
ADAPTER_VERSION = "WOW-PATCH-2026-08-17-1IP-PRODUCTION-HYDRATION"


def predict(
    *,
    pitcher_name: str,
    pitcher_mlbam_id: int,
    opponent: str,
    game_date: str,          # ISO YYYY-MM-DD (board date; must be ≤ today for real data)
    line: float,
    direction: str,
    season: Optional[str] = None,
    n_trials: int = 25_000,
    _frozen_at: Optional[str] = None,   # injectable for tests
) -> dict:
    """
    Run the WOW_LEAN_1IP adapter for a single pitcher/prop.

    Returns
    -------
    dict with:
      prediction_record  PredictionRecord instance (or None if model unavailable)
      ledger             Raw savant ledger dict for inspection
      simulation         Raw simulate_1ip output dict (or None)
      error              str describing failure (or None)
      adapter_id         ADAPTER_ID
    """
    from gate_engine.mlb.savant_1ip_ledger import (
        build_1ip_ledger,
        compute_pitches_per_batter_dist,
    )
    from gate_engine.mlb.ip1_event_tree import simulate_1ip

    direction = direction.upper()
    season    = season or game_date[:4]
    error     = None
    sim       = None
    prob      = None
    uncertainty = None

    # ── 1. Fetch Savant first-inning ledger ───────────────────────────────
    try:
        ledger = build_1ip_ledger(
            pitcher_mlbam_id,
            season,
            game_date,
            line=float(line),
            side=direction,
        )
    except Exception as exc:
        return {
            "prediction_record": None,
            "ledger":            None,
            "simulation":        None,
            "error":             f"savant_ledger_exception:{exc!s:.150}",
            "adapter_id":        ADAPTER_ID,
        }

    if ledger.get("error"):
        return {
            "prediction_record": None,
            "ledger":            ledger,
            "simulation":        None,
            "error":             f"savant_ledger_error:{ledger['error']!s:.150}",
            "adapter_id":        ADAPTER_ID,
        }

    bf_dist = ledger.get("bf_distribution") or {}
    if (bf_dist.get("n") or 0) == 0:
        return {
            "prediction_record": None,
            "ledger":            ledger,
            "simulation":        None,
            "error":             "bf_distribution_empty:no_verified_savant_data",
            "adapter_id":        ADAPTER_ID,
        }

    # ── 2. Derive pitches-per-batter distribution ─────────────────────────
    ppb_dist = compute_pitches_per_batter_dist(ledger.get("ledger_rows") or [])

    # ── 3. Simulate ───────────────────────────────────────────────────────
    try:
        sim = simulate_1ip(
            bf_distribution         = bf_dist,
            pitches_per_batter_dist = ppb_dist,
            line_value              = float(line),
            side                    = direction,
            n_trials                = n_trials,
        )
    except Exception as exc:
        error = f"simulation_exception:{exc!s:.150}"
    else:
        raw = sim.get("raw_less") if direction == "LESS" else sim.get("raw_more")
        if raw is not None and 0.01 <= float(raw) <= 0.99:
            prob = round(float(raw), 4)
            # Uncertainty proxy: |raw_more - raw_less| / 2 (distance from boundary)
            raw_other = sim.get("raw_more") if direction == "LESS" else sim.get("raw_less")
            if raw_other is not None:
                uncertainty = round(abs(float(raw) - float(raw_other)) / 2, 4)
        else:
            error = f"simulation_degenerate:raw={raw}"

    # ── 4. Build feature snapshot ─────────────────────────────────────────
    features = {
        "bf_distribution":             bf_dist,
        "pitches_per_batter_dist":     ppb_dist,
        "l10_hit_rate":                ledger.get("l10_hit_rate"),
        "l5_hit_rate":                 ledger.get("l5_hit_rate"),
        "data_coverage":               ledger.get("data_coverage"),
        "fetch_method":                ledger.get("fetch_method"),
    }

    provenance = {
        "source":         ledger.get("source"),
        "fetch_method":   ledger.get("fetch_method"),
        "bf_n":           bf_dist.get("n"),
        "ppb_n":          ppb_dist.get("n"),
        "board_date":     game_date,
        "season":         season,
        "pitcher_id":     pitcher_mlbam_id,
        "adapter_id":     ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "n_trials":       n_trials,
        "error":          error,
    }

    pred = PredictionRecord.create(
        game_date          = game_date,
        pitcher_name       = pitcher_name,
        pitcher_mlbam_id   = pitcher_mlbam_id,
        opponent           = opponent,
        line               = float(line),
        direction          = direction,
        model_probability  = prob,
        model_uncertainty  = uncertainty,
        features           = features,
        model_version      = MODEL_VERSION,
        data_provenance    = provenance,
        _frozen_at         = _frozen_at,
    )

    return {
        "prediction_record": pred,
        "ledger":            ledger,
        "simulation":        sim,
        "error":             error,
        "adapter_id":        ADAPTER_ID,
    }
