# NFL Pick'em authentic weekly scorecard — Class B integration contract

Issue #1530. Prerequisites: Class A audit PR #1522, independent SIRT #1521,
protected merge and verified deployment. This document and accompanying
non-serving code are **not** evidence of those prerequisites.

## Source-of-truth dependency boundary

Inputs must come from **three independently evidenced source families**:

1. Official scheduled NFL regular-season events for exactly one season/week:
   official event IDs, home/away, kickoff, frozen manifest/source receipt,
   source content checksum, schedule revision, postponement/cancellation rules.
   Freeze before earliest applicable kickoff; never rewrite in hindsight.
2. Original fitted NFL moneyline-specialist **immutable** prediction receipts
   captured before each game: source snapshot, model artifact/version, both
   calibrated sporting probabilities, original terminal, selected side, model
   timestamp and latest known material update. Never reconstruct missing
   predictions from market odds, favored teams, generic LLMs or postgame data.
3. Independently sourced official finals, final-state/source proof, settlement
   receipt/timestamp, and append-only revisions. A newer official revision may
   invalidate a previously settled winner; old grades must not survive silently.

## Stage 1 built in this draft — passive evidence reconciler

Source: artifacts/wow-engine/v17/nfl_pickem_weekly_evidence.py

reconcile_weekly_evidence validates manifest identity/count, explicit
scheduled/postponed/cancelled states, missing/extra/duplicated event IDs and
picks, finalized settlements, revision uniqueness/continuity and timestamp
ordering. It calls existing audit_governed_pickem_week only after exact event
set reconciliation, never supplies model probabilities.

- Incomplete/unsafe evidence => EVIDENCE_BLOCKED_NO_PUBLICATION, typed
  blockers and original full-week denominator; structurally malformed evidence
  raises WeeklyEvidenceError.
- Complete **supplied** evidence => AUDIT_CANDIDATE_UNVERIFIED_NO_PUBLICATION,
  with deterministic content-bound candidate ID, canonical audit result and
  settlement revision counts. Changing a prediction, final, source snapshot
  or manifest frozen clock changes the candidate identity.
- **In every state:** publication_allowed=false,
  source_authenticity_verified=false, durable_persistence_verified=false,
  scheduled_delivery_verified=false and can_execute=false.
- This pure stage neither fetches trustworthy outside evidence, independently
  authenticates a source, creates permanent ledgers, schedules, sends email nor
  proves a deployed endpoint. Merely writing the string 'official' is not proof.

The production policy stays MAX_EXPECTED_CORRECT with exactly one fitted NFL
probability specialist; no ownership/popularity or bettor equity logic. The
Poisson-binomial full-slate distribution is descriptive only and assumes
independent games; never promise 90–100% future prediction accuracy.

## Read-only Supabase findings — 2026-10-08

The connected wow-engine-validation project has existing source candidates:

- wow_nfl_event_predictions: 389 rows in the observed table; Oct 11 has 50
  predictions over 13 distinct event IDs. Multiple prediction revisions do
  not authorize selecting a postkickoff best-looking pick.
- wow_v17_nfl_pickem_async_runs: eleven completed board-run records, but
  these are not official independently settled delivery-grade report receipts.
- wow_v17_daily_run_row_detail: broad audit/prediction row details.
- wow_nfl_training_games: Week 3 and Week 4 contain sixteen scored games each,
  but independent official result attribution, versioned settlement receipts
  and complete original Pick'em predictions remain unproven.
- wow_event_outcomes: a read-only join to wow_event_predictions on NFL sport
  returned zero linked NFL outcomes. This is a **ledger-coverage gap**, not a
  claim there were no real NFL results.

No evidence-backed external official settlement adapter, versioned immutable
weekly final ledger, or unattended delivered weekly report has been certified.

## Remaining Class B implementation and verification

1. Official manifest capture adapter: source identity, retrieval receipts,
   checksum, official canonical IDs, signed-off freezing, typed reschedule/void.
2. Immutable prediction adapter: reuse originals in canonical NFL prediction
   store and row-details; verify all provenance, version/hash, pregame timestamps
   and source terminals. Prevent selection among hindsight revisions.
3. Final settlement adapter: independently verify official results and append
   revisions, with authoritative identity and unresolved tie/void rules.
4. Durable append-only ledgers: manifest, prediction reference, settlement
   revision, audit, delivery and acknowledgement. Design service-only schema
   with authorization and RLS review. Unique season/week/revision hash and
   atomic receipt/lease handling must make retries idempotent; ambiguous
   delivery acknowledgement means DELIVERY_UNVERIFIED, not resend blindly.
5. Schedule after the **verified last official final**, not an assumed fixed
   Monday clock. Preserve unscheduled/manual runs and pending-final retries.
6. Publish only after full event reconciliation, genuine model and final
   evidence, committed immutable audit, actual delivery and readback receipts;
   include counts, missing/held rows, model uncertainty, Brier, log loss and
   distribution caveats, never output false complete n/N statistics.
7. Tests: 16/16, 15/16, 9/16, bye week, postpone, cancel, late changes,
   duplicate/empty/extra prediction, duplicate and revised finals, source
   outage, retries, crash/restart, immutable readback, two unattended cycles.
8. Protected merge with fresh exact-head CI, independent SIRT/QA,
   deployed Render revision and live readback; preserve terminal authority,
   can_execute=false and zero wagers.

Stage 1 does not constitute user-visible automatic weekly reporting. No model
promotion, market substitution, protected-branch override or terminal closure.


## Precise existing persistence mapping (read-only preflight)

The existing production Pick'em board async runs store a completed result_payload
containing the board's picks, blocked rows, expected_game_count, schedule
snapshot ID and source_receipt_persistence. Three 2026-10-01 completed
16-event boards had exactly 16 distinct prediction IDs and no late
model timestamps compared with the earliest kickoff. There were also partial
boards and additional complete versions. **None alone identifies the user's
irrevocably frozen submitted card.** A new adapter must require an original
trusted selection-lock receipt rather than cherry-pick among hindsight runs.

Board pick rows expose immutable_model_timestamp, source_prediction_id and
source_snapshot_id, but the inspected pick payload keys did **not** include
latest_material_update_at. The official weekly adapter must hydrate verified
material-change timestamps from the original model/source evidence ledger or
explicitly classify freshness UNKNOWN. It must not silently assume that an
omitted timestamp proves no late news. The new non-serving evidence reconciler
now performs its own strict material-update guard where that timestamp is
present, independently of still-unmerged Class A PR #1522.

This is why a complete-looking 16/16 board, a successful backend workflow or
synthetic regression tests are **not** a real published weekly accuracy report.


## Stage 2 built in draft — source-byte-verified candidate manifest

Source: artifacts/wow-engine/v17/nfl_pickem_weekly_manifest.py

freeze_nflverse_week_candidate accepts a previously preserved NFLVerse
SCHEDULES snapshot and the original compressed CSV bytes, without fetching
new network data or writing any database state. It verifies permitted dataset
identity, original SHA-256 of decompressed bytes, bounded 50-MB expansion
**during** decompression, snapshot capture clock before freeze, freeze clock
strictly before the earliest weekly kickoff, exact REG season/week membership,
canonical game IDs, distinct home/away teams, and exactly the expected number
of games. Even a scored zero must stop a purported pregame freeze. The resulting
deterministic FROZEN manifest is compatible with the stage-1 reconciler.

This is a **candidate** from a source the existing engine already archives,
not a league-authoritative final settlement, not independent NFL.com proof,
and not evidence that the manifest was durably frozen in a database. The
output explicitly says source_authenticity_verified=false and
publication_allowed=false. The official-data cross-check, immutable
write/verification, monitoring of reschedules/cancellations, and secure
service-only evidence adapter remain mandatory before activation. No
NCAAF/MLB/prop serving code or fitted sporting model changed.
