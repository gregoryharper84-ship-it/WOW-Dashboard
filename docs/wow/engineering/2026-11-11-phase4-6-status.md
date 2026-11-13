# Phases 4–6 status — 2026-11-11 (`can_execute=false`; no promotion, deployment or flag change)

## Phase 4 — NBA points: `BLOCKED_WITH_EXACT_REASON` (not certified)
Registry (validation project): NBA POINTS/REBOUNDS/ASSISTS are `CANDIDATE`, family `NBA_PLAYER_PROP_EMPIRICAL_RESIDUAL_JOINT_V1`, 18179 training rows, `probability_publishable=false`; POINTS checksum prefix `ee0620690bcd`. Candidate builder (`v17/nba_scalar_candidate_registry.py`) carries typed blockers:
- `NBA_SCALAR_EXACT_LINE_CALIBRATION_MISSING`
- `NBA_SCALAR_FORWARD_COHORT_NOT_CERTIFIED`
- `NBA_SCALAR_CANONICAL_ACTION_CANARY_MISSING`
Being a fitted joint model does not certify the points marginal. Next: strictly-chronological replay of the points marginal, exact-line calibration, forward cohort (not fabricated).

## Phase 5 — NHL SOG: `BLOCKED_WITH_EXACT_REASON`
`NHL_SOG_REAL_MULTI_SEASON_CORPUS_REQUIRED` stands. `api-web.nhle.com` and `statsapi.web.nhl.com` are unreachable from this sandbox (proxy), so no corpus was acquired and none was manufactured. Next: owner-approved source-rights decision plus a reachable, provenance-pinned multi-season source; existing pipelines then run unchanged.

## Phase 6 — NFL receptions: `NOT_STARTED` (no model, no registry row)
No NFL RECEPTIONS artifact exists. nflverse weekly CSVs (already hash-pinned) carry receptions/targets, so a chronological train 2021–23 / calibrate 2024 / holdout 2025 build is feasible. It is a Class C change (new discrete distribution, calibration) and needs its own challenger PR with replay and governed review; it must not reuse receiving-yard coefficients.
