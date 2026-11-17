# WOW V17 — NFL Genuine Pregame Challenger Receipts (Design)

**Owner:** Existing LLP_TEAM_BETTING_ENGINE NFL fitted team-event lane. **Context:** Project Hub #2076, PR #2705 retrospective only, PR #2700 scenario bridge, preexisting #1342 challenger ledger, and protected V17 terminal policy.

## Problem / verified cause

The November 17 retrospective used prior-game data correctly but materialized many holdout features only after kickoff. The existing `wow_nfl_ml_challenger_forward_shadow` table had one row and zero pregame grades. There was no automated challenger capture or two-provider settlement. Therefore the retrospective improvement (108 matched games, Brier delta -0.00365, week-cluster CI [-0.0163,+0.0084]) is NOT a validated production-model lift.

## Chosen approach

**Smallest reuse of existing infrastructure.** Keep the 2021–24 L2 fitted logistic and 2025 Platt-calibration experiment fixed, and use the canonical NFL P2 prior-feature builder for upcoming games. Persist a genuine selected-side research forecast to the **existing** challenger-forward table in the first eligible pregame capture, alongside immutable schedule/feature/model hashes and the time-correct incumbent prediction receipt if available. GitHub Actions runs twice daily during the NFL season on protected main only. The existing outcome ledger is reconciled with independent ESPN final event evidence before first/only grade. Do NOT treat ESPN+NFLverse as league-official certification.

## Non-goals

No duplicate production team-event model, no auto-promote, no user Pick'em submission, no slip recommendations, no real-time odds or bets, and no alteration of LLP or the terminal reducer. This work does not fit scenario-conditional player volume distributions; the merged opponent bridge remains separately research-only.

## Technical contract

1. **Source:** `wow_nfl_source_snapshots` latest captured `SCHEDULES` raw gzip, decompressed original CSV SHA-256 matching captured digest, freshness <=96h. Unfinished game, canonical ID/teams/kickoff, kickoff in (now,now+36h], exact USA Eastern timezone. Already scored fixtures are ignored.
2. **Historical model:** train only 2021–24 source-receipted prior-feature rows; calibrate only 2025. Never use 2026 results in training, calibration, feature normalization, lower bound, or model selection. Retrospective sources are training evidence, not proof of historical pregame capture. Research local Wilson and block bootstrap outputs clearly **uncertified**.
3. **Target features:** reuse champion's `build_prediction_feature_row` against stored completed-game summaries; compare home/away current-season prior-game counts to canonical schedule. Reject stale/missing model, matchup identity, source history or forecast time.
4. **Immutability:** DB trigger reassigns `prediction_created_at` and `created_at` from server time; refuses postkickoff insert, FORECAST mutation, deletion and multiple grades. Existing service-role-only grant remains. `grade_source_manifest` may be populated only once with an ESPN+NFLverse exact score/identity consensus and computed Brier/log-loss.
5. **Independent grading:** ESPN final + stored nflverse result must agree on canonical teams, start time and scores; preserve ESPN raw body digest and nflverse source SHA. Ties, retirements, missing results, source conflicts or 3rd-party failures remain ungraded. A matched incumbent comparison uses only its **stored pregame receipt**, not a later champion forecast.
6. **Operational safety:** workflow PRs only test, never access credentials/write. Only `schedule`/manual dispatch on protected `main` can insert/grade. No secrets in logs/artifacts. Failures report actual zero capture/held counts rather than false success. Schedule uses no paid provider, two lightweight seasonal sweeps/day.

## Acceptance criteria

- Local and hosted unit tests plus disposable PostgreSQL trigger integrity tests pass at exact PR head.
- Required GitHub native checks pass; changes only through protected main, no force/bypass.
- Live Supabase migration installed and verified, workflow deployed; latest GitHub scheduled run has nonsecret results.
- At least one **real, server-created prekickoff** forecast exists for a future event; after settlement, official-provider consensus grade persisted; sufficient paired forward grades and independent calibration review before any model promotion.

**Residual limitation:** A strong scientific promotion claim needs far more than one weekend, and prospective source/provider rights and availability are externally dependent. Schedule or GitHub outages may delay capture; never backfill predictions after kickoff.
