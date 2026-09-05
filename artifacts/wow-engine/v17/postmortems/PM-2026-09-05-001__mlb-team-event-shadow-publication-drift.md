# PM-2026-09-05-001 — MLB Team/Event Shadow Publication Drift

## Status

MITIGATED

## Summary

A Sep. 4, 2026 Athletics @ Mariners forward-shadow MLB win-probability row was correctly persisted as research-only and `probability_publishable=false`, but the numeric Seattle probability was later surfaced conversationally as a strongest/#1 selection. The game result itself does not establish a model failure: the shadow model assigned Seattle approximately 56.38% and Oakland approximately 43.62%, so an Oakland win was a plausible modeled outcome. The incident is a publication-boundary/governance-promotion failure, not evidence that the Seattle sporting probability must be retrospectively changed.

## Impact

- User-visible impact: Seattle was presented with stronger official-selection semantics than the immutable backend row supported.
- Model/probability impact: No justified retrospective change to the sporting probability. The research shadow probability remains immutable evidence, not a governed published probability.
- Governance impact: A nonpublishable research artifact escaped into strongest/#1 presentation semantics.
- Data/persistence impact: No governed `wow_event_predictions` row was found for MLB event 823093. No fake postgame governed prediction will be backfilled.

## Detection

- Detected at: 2026-09-05 postmortem review.
- Detected by: user-submitted PrizePicks settlement screenshot plus direct Supabase forensic review.
- First known bad presentation: Sep. 4, 2026 user-facing ranking/card discussion containing Seattle as a strongest/#1 option.
- Last known good run/request: not established by this incident review.

## Evidence

Immutable backend evidence reviewed for official MLB event `823093`:

- Forward-shadow event id: `e7f3f662-f2ab-4c21-b4a5-f6cdc228c6c3`.
- Pregame matchup: Athletics @ Seattle Mariners.
- Probable starters after hydration: Kade Morris @ Logan Gilbert.
- Raw Seattle probability: `0.537659157929821`.
- Calibrated Seattle probability: `0.563781072435688`.
- Stored research lower bound: `0.563781072435688`.
- Projected runs: Seattle `4.96608731888535`, Athletics `4.46862052588852`.
- Shadow model: `MLB_V2C_SHARED_NB_2024_R1`.
- Shadow calibration: `LOGIT_INTERCEPT_POOLED_2022_2024`.
- Shadow row: `probability_publishable=false`.
- Initial score status: `SHADOW_SCORED_LINEUP_PENDING` with `LINEUP_NOT_CONFIRMED` plus calibration/production-readiness blockers.
- Confirmed-lineup score status: `SHADOW_SCORED_PREGAME`, still `probability_publishable=false` with calibration/production-readiness blockers.
- Confirmed lineup snapshot: `1ca630e1-ccbc-4052-8041-3953a9a393b6`, strict pregame provenance, Preview/Pre-Game, zero pitch events.
- The old shadow score reused the same home/away feature snapshot ids before and after lineup confirmation; therefore confirmation removed a blocker without changing that shadow feature vector.
- Prior Sep. 3 game context and three-day bullpen workload were present in the feature inputs; stale-series-game omission was ruled out.
- Official final: Athletics 7, Mariners 6.
- Observed favorite failure path: Logan Gilbert 4.2 IP, 7 ER, 8 H, 4 HR, 2 BB, 10 K, 99 pitches. Kade Morris allowed 5 ER in 2.2 IP. Seattle still scored six runs.

## Root Cause

Confirmed cause: presentation logic did not maintain a hard enough distinction between a numeric research/shadow sporting probability and an official V17 ranked/publishable selection. The backend row itself carried the correct nonpublishable semantics.

Contributing factor: the legacy forward-shadow scoring path treated lineup confirmation primarily as readiness state; its score snapshots reused the same team-level feature vectors before and after confirmed lineups. This path is research infrastructure and must not be promoted into official selection semantics.

Not a confirmed cause: Logan Gilbert's bad outing is not evidence by itself that the model was incorrectly calibrated. A 56.38% favorite can lose without process failure.

## V17 Classification

- BACKEND_RUNTIME: V17 runtime remained active; this incident does not establish a backend outage.
- MODEL_CAPABILITY: research shadow MLB probability completed, but official governed/publishable event probability was not established for this row.
- REPOSITORY_GOVERNANCE: publication hardening patch prepared on a dedicated branch; merge/deploy verification pending.
- LIVE_GPT_EDITOR_SYNC: not changed by this postmortem; any live editor synchronization remains a separate state.
- Terminal status: research/nonpublishable row should have remained unranked.
- `scoring_attempted`: true for the shadow model; governed official publication not proven.

## Controlling Lane / Specialist

Team/event winner route under `LLP_TEAM_BETTING_ENGINE`. Current certified MLB prospective specialist is `wow.mlb-game-win-probability-expert`. Shadow/Research artifacts are evidence only and may not substitute for the certified governed publication package.

## Failure Semantics

Do not rewrite this incident as `MODEL_UNAVAILABLE`: a research shadow model produced a numeric probability. The defect is `PROCESS_FAIL_GOVERNANCE_PROMOTION` / official-publication semantics, while the settled selection outcome is a plausible `MODEL_MISS` under the research probability.

## Remediation

- Engineering fix ID(s): `FIX-2026-09-05-001`.
- Temporary mitigation: treat every `probability_publishable=false`, `rank_eligible=false`, `SHADOW_SCORED_*`, or `PASS_RESEARCH_BOUND` team/event row as research/diagnostic only.
- Permanent fix: add a fail-closed official team/event publication guard and bind the ML Winners skill to it. Official MLB ranking additionally requires certified strict bounds, confirmed lineup/model-input fingerprinting, explicit numeric favorite failure paths, calibration health, and V17 terminal-reducer receipt.

## Verification

- Regression test(s): `v17/test_team_event_official_publication_guard.py` includes an incident-style Seattle shadow fixture.
- Acceptance test(s): valid certified-style package can pass; research shadow, point-equals-lower-bound, missing failure paths, missing lineup fingerprint, missing reducer receipt, or `can_execute=true` cannot pass.
- Production verification: pending PR merge/deploy and live acceptance run.
- Verified commit/deploy: pending.

## Prevention / Follow-up

- Official leaderboard/card builders must consume only rows that pass the publication guard.
- Research/held probabilities may be displayed only in a clearly labeled diagnostic section and never assigned official rank.
- Do not backfill a governed pregame probability after settlement.
- Do not alter Seattle-specific or Logan-Gilbert-specific model weights from this single outcome.
- Track calibration across a real forward sample rather than learning from one binary miss.

## Closure

- Closed at: pending.
- Closed by: pending production verification.
- Final status: MITIGATED; code/test patch under review.
- Linked engineering fix(es): `FIX-2026-09-05-001`.

---

V17 safety invariant: `can_execute=false`. This record cannot authorize, route, modify, approve, or cancel a wager/order.
