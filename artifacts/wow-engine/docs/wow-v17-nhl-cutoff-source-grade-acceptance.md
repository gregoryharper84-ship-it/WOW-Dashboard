# WOW V17 NHL: final identity, source snapshot and forward grade

Incident: #2020. Engineering/AMRT implementation. SIRT independent challenge.
Independent Verification proves deployed artifacts and genuine receipts.
Status: WORKING_NOT_COMPLETE until protected merge, actual stored-source
readback, calibrated pregame score, official settlement and chronological grade.

## Cutoff-time pregame revalidation
The fitted NHL scorer now invokes `verify_nhl_identity_at_scoring` immediately
before its existing fitted candidate scoring step **after** source/history/model
prerequisites are validated. It independently re-reads the official NHL public
club schedule and requires the exact 10-digit event ID, exact home and away,
**identical official start** (not a loose 30-minute provider-discovery match),
game type 2, FUT/PRE game state, current verification time and matched-game
SHA-256. Changed/stale/started/postponed fixtures produce native input holds:
`NHL_CUTOFF_*`, `NHL_EVENT_NOT_PREGAME` or the original
`NHL_OFFICIAL_SOURCE_*`. A fetch failure is never `MODEL_UNAVAILABLE`.

This adds one official schedule request for a candidate that has already
passed upstream validation and is about to be scored. No additional
polling/worker/paid provider. A successful result is a **runtime freshness
check**, not proof that a player/goalie feature changed the fitted number or
that the full source bytes were stored. The scorer package retains the
`cutoff_identity_receipt`, separate from model probability and settlement.

Requires the matched-game `identity_match_sha256` resolver contract of
PR #2821 to be merged and independently accepted first. A draft branch's
passing tests cannot substitute for release.

## First-party gamecenter source persistence
The existing gamecenter capture accepts optional server-owned
`snapshot_write(raw_bytes, metadata)` and `snapshot_read(snapshot_id)`
callbacks. It refuses to assert durable identity unless **both** callbacks
exist, the store returns a nonempty ID, and readback bytes match the exact
first-party response and SHA-256. No automatic source write occurs: the
authenticated runtime must explicitly install a rights-approved durable store
and prove readback/owner authorization. Bad readback or partial write returns
typed `NHL_SOURCE_SNAPSHOT_READBACK_FAILED`; absent store retains
`source_snapshot_id=null`. Starter goalie remains unconfirmed regardless of
source persistence: it requires independent authorized confirmation.

This is a **storage adapter contract**, not a deployed archive. The
persisted source must have legal reuse permission, expiry/retention policy,
readback evidence and run/incident receipt. No database migration or
public table/grant is introduced here.

## Official settlement and forward grading
`grade_nhl_pregame_forecast` consumes one separately retrieved immutable
forecast receipt and one append-only official `wow_d1_source_events` V2
settlement (not an inferred postgame forecast). Requires exact 10-digit
canonical NHL event ID, team and selected-side match, positive immutable
prediction ID, exact fitted model version and artifact/feature hashes, source
snapshot ID, forecast timestamp **strictly before official start**, source
retrieval **after** start, official FINAL/OFF score and independently verifiable
V2 envelope hash. It grades exact selected-side Brier and log loss.
`summarize_nhl_forward_grade_cohort` conserves unique events and immutable
forecasts, rejects mixing model versions, reports cohort n, mean forecast,
observed rate, calibration bias, average lower bound, empirical reliability
and thin-sample warning.

Both return **research only**. They do not independently prove the referenced
forecast was actually retrieved from a protected immutable writer, nor do they
write forward grades, backfill predictions, certify calibration, or change
terminal rank/publication. The acceptance owner must wire this read-only join
to actual governed DB rows, persist its grading receipt under existing
governed lifecycle, and have SIRT replay chronological cutoffs.

## Required independent operational proof
1. Protected exact-head test and review, plus deployed backend SHA/Action parity.
2. Actual upcoming NHL candidate revalidated at scoring time; saved exact
   official ID, start, participant, source SHA and typed mismatch control.
3. Authorized gamecenter raw payload stored and independently read back with
   actual `source_snapshot_id`; material input freshness/goalie confirmed
   separately by appropriate sources.
4. An actual pre-lock immutable fitted-specialist prediction receipt with
   calibrated lower bound, artifact version and source/feature IDs.
5. Official settled V2 source event matched to that **same** original forecast
   and a persisted independent grade, followed by sufficient prospective
   cohort metrics and separate independent calibration acceptance.
6. V17_TERMINAL_REDUCER is the only terminal authority; `can_execute=false`
   everywhere. No paid tool or automatic wager is authorized.
