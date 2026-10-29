# WOW V17 — NCAAF genuine schedule-before-kickoff capture audit

October 29, 2026. Supabase validation project `wow-engine-validation`.

The earlier CFBD `/games/players` corpus (205,144 settled stat observations) was backfilled on October 29, 2026, and **does not establish historical pregame player-form feature availability**. A separate CFBD `/games` source history does include actual earlier schedule-version captures, including events still upcoming.

## Independently verified 2026 schedule-source provenance

One year-scoped read of `public.wow_ncaaf_source_snapshots` contained 40,848 game occurrences across 65 source snapshots; 19,915 event-snapshot occurrences were taken before their **then-captured** kickoff time, representing 703 distinct official event IDs. Source capture dates began September 15, 2026.

The new private `wow_prop_history.ncaaf_pregame_schedule_research_v1` view selects **one earliest captured pregame version per event**, preserving immutable source-snapshot ID, SHA-256 digest, observed time, original kickoff, teams and eventual settled-game comparison.

| Earliest pregame schedule capture | Official event IDs | Before its captured kickoff | Before settled kickoff where known |
| --- | ---: | ---: | ---: |
| Exact match against settled game | 226 | 226 | 226 |
| Kickoff changed by settlement | 159 | 159 | 159 |
| Not yet in settled training-game ledger | 318 | 318 | Unknown pending settlement |
| **Total** | **703** | **703** | **385 verified against settled kickoff** |

There were zero team-matchup changes in those 385 reconciled settled events, zero captures after their known settled kickoff, and zero probability, model-training or execution claims.

**Caution:** For 318 not-yet-settled event IDs, capture precedes the recorded kickoff, but there is no final game ledger to confirm the eventual kickoff. The 159 changed-start events retain the earlier source timestamp and captured kickoff; do not rewrite the historic observation to claim a different start time.

## Scope and access

This supplies **genuine before-kickoff schedule and event-identity provenance only**, not live player statistics, injuries, usage, routes, targets, sportsbook quotes or an individually fitted probability package. `captured_schedule_source_provenance_verified=true` is **not** `player_prop_feature_pit_verified`, which stays FALSE. `model_training_eligible`, `probability_publishable` and `can_execute` are also FALSE.

The view is `security_invoker`, RLS-backed and service-role SELECT only. The normal consumer cannot access it; it has no write authority and does not create an executable pick.

Source: `artifacts/wow-engine/ncaaf_pregame_schedule_research_v1.sql`. Supabase validation migration `wow_ncaaf_actual_pregame_schedule_receipts_20261029` was successfully applied and independently queried.

## Next evidence

Attach schedule IDs and captured kickoff versions to each *newly captured* approved pregame player context, with time-stamped game/team identity, lineup and injury/role evidence, observed before the **actual eventual** event start. Keep retrospective boxscores strictly as postgame labels. Any official probability publication remains subject to independent calibration and `V17_TERMINAL_REDUCER`; `can_execute=false`.
