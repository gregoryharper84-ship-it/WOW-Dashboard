# WOW V17 Web Intelligence Fallback V1 — Research-only build

**Purpose:** Continue auditable NBA research when the exact V17 specialist cannot publish a model probability. This is a read-only fallback, **not** a certified replacement model or auto-approval system.

## Production integration

On the accepted `api_ncaaf_acceptance:app` entrypoint, the new authenticated
`POST /v17/research/web-intelligence-fallback` route mounts when `WOW_V17_ACTIVE=1`.
It uses the existing `WOW_ACTION_API_KEY` Bearer dependency. It performs no network
requests, database writes, or background jobs; instead it ingests normalized
Scout/Research evidence. No new provider credential or infrastructure is required.

The caller must supply the official NBA event ID, future event start, exact market,
selection and line (line is null for moneyline), the actual upstream model failure
code, source-backed component observations, and optional exact market snapshots.

The evidence record specifies:
- `factor`: one of TEAM_STRENGTH (30%), INJURIES_LINEUPS (25%), OPPONENT_MATCHUP (20%), PACE_GAME_SCRIPT (15%), VENUE_FATIGUE (10%).
- `favorability`: 0..1 analyst/structured-research assessment for **that exact selection**; not a probability, not automatically extracted from news text.
- `source_group`: independent original publisher identifier, not each syndicated copy.
- `source_class`, `url` (HTTPS), `observed_at` (UTC offset required), `claim`, `confirmed`; optional `evidence_type`.

Source freshness/confirmation uses `v17.scout_source_policy`. Unsupported, stale,
future-dated, unconfirmed high-impact sources and sportsbook odds in sporting factors
are rejected. Latest same-publisher factor observation wins; independent disagreement
of at least 0.35 on a factor holds scoring. All five factors need support.

## Formula

`research_score = 50 + 100 * q * sum(weight_i * (factor_i - 0.5))`

Here `q = min(unique_source_groups / 5, 1) * mean(source_tier_quality)`.
This is a transparent heuristic for research triage only. The coefficient weights,
source quality factors and conflict cutoff are **unfitted design assumptions**,
not calibrated statistical evidence. Zero to 100 is **not** a win probability.

Current market odds, if present, produce only a snapshot's mathematical break-even
probability when event/selection/type/line match and timestamp is within 15 minutes.
Exact snapshot matching is **not** independent book verification; the result is
explicitly marked structurally verified only. Odds never affect research_score.

## Deliberately out of scope

- No live web-search or page-content attestation inside the scorer; existing
  Scouts/Research must acquire evidence and preserve source provenance.
- No published probability, calibrated lower bound, terminal approval,
  betting execution, card construction or market EV.
- No model status rewrite; original MODEL_UNAVAILABLE vs scorer/input/output failure
  remains visible.
- NBA only in V1. Other sports require their own reviewed feature profiles.
- No speculative game total/margin simulation or fitting without verified training
  data and out-of-time model evaluation.

For genuine probability fallback, fit an NBA margin/total distribution on historical
pregame features, chronologically replay, hold out later seasons, calibrate by exact
market, verify bounds and independently certify the new specialist. Only after
certification may its probability enter V17_TERMINAL_REDUCER.

## Local targeted test

`cd artifacts/wow-engine && pytest -q tests/test_v17_web_intelligence_fallback.py`

`can_execute=false`; `probability_publishable=false`.
