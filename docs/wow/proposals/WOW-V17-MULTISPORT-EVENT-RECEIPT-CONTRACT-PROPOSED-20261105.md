# WOW V17 — PROPOSED Multisport Team-Event Prediction Receipt Contract v0.1

**Status: PROPOSED / NOT APPROVED / NOT DEPLOYABLE.** Owner: Engineering/AMRT (implementation); independent acceptance: SIRT + Independent QA/Release; product acceptance: WOW Betting Intelligence; terminal authority: V17_TERMINAL_REDUCER. Ticket: P0 #2473; evidence #1128. `can_execute=false` throughout.

## 0. Evidence and incompatibility

Freebuff's real-PostgreSQL characterization branch `freebuff/ae0ffa56-2473-ledger-schema` at `157769502e0e15d49455fa626e0bd2075853e89e` confirms that the existing `wow_event_predictions` DDL is **MLB-only**: its checks require `sport=MLB`, `league=MLB`, MLB controlling specialist, and `FULL_GAME_INCLUDING_EXTRA_INNINGS`. Non-MLB rows fail SQLSTATE 23514. LLP #2473 at `5819fc26720530d8248d073574ae6863080dc047` reads `wow_event_predictions` for **NFL/NBA/NHL**, accepting only the full-game NFL/NBA inclusive-overtime and NHL inclusive-overtime/shootout settlement bases. Hence there is no possible positive authenticated LLP receipt through that table under the current contract. The connected validation schema independently carries those MLB-only constraints, with a separate NFL specialist table; **no claim about actual Render production DB identity follows without authenticated binding verification.**

**Do not:** widen/drop MLB checks, relabel MLB receipts, insert fake UUIDs into an MLB ledger, infer winner from price, treat a shadow NBA research receipt as publishable, copy historical specialist rows en masse, or mark a green local test as independent acceptance.

## 1. Normative target, pending governance approval

Adopt an **additional append-only full-game team-event publication receipt ledger** (illustrative name `public.wow_v17_multisport_event_prediction_receipts`) for `NFL`, `NBA`, and `NHL` only. This is a **new terminal-certified ledger**, not an adapter that silently fabricates a canonical row from pre-existing or research-only specialist tables. The original MLB `wow_event_predictions` remains unchanged and fully available to its controlling specialist. Sport-specific prediction tables remain independently authoritative for their own numerical model outputs.

This proposal's SQL **does not grant any writer or reader role permissions**. The official producer, reducer verification, event identity resolver, versioned readback, roles/grants, migration sequencing, telemetry and rollback must be implemented, reviewed and proven in separately attributable Engineering changes before release. A table populated by a superuser with matching fields alone is **not** proof that a fitted model or reducer generated those fields.

## 2. Exact receipt envelope (v1)

Every candidate is identified by **sport + immutable official event ID + verified two-team identities + game-start timestamp + market family + settlement basis**:

| Field | Evidence requirement |
| --- | --- |
| `sport`, `official_event_id`, `event_start_time`, `home_team`, `away_team` | Authoritative current pregame fixture, same event on Action/producer/ledger/readback; no string-name crosswalk guesses |
| `market_family`, `settlement_basis` | `OUTRIGHT_WINNER`; NFL/NBA `FULL_GAME_INCLUDING_OVERTIME`, NHL `FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT`; not regulation-only |
| `selected_participant` | Literal one of the two saved participants; never infer selected side from highest probability or odds |
| `source_snapshot_id`, `source_snapshot_timestamp` | Exact, immutable and source-pinned pregame evidence; no retrospective substitution |
| `score_snapshot_id`, `model_artifact_id/version/sha256`, `calibration_artifact_id/version/sha256`, `model_timestamp` | Exact fitted specialist and independently accepted calibrator/bounds proven for this event; separate source UUID is not a receipt |
| `calibrated_home/away_probability`, `calibrated_home/away_lower_bound`, `calibrated_home/away_upper_bound` | PostgreSQL `numeric`; finite, strictly interior, ordered per side, home/away points sum to 1 within existing declared tolerance; no float comparison as authority |
| `locked_at`, `terminal_reduced_at`, `reducer_receipt_id/sha256`, `terminal_authority`, `terminal_label` | Authentic score→lock→V17_TERMINAL_REDUCER receipt, independently re-read/verified by privileged admission adapter, not a caller's booleans or a reconstructed label |
| `event_prediction_id`, `created_at` | Database-assigned UUID and insertion time. Application must omit both; a trigger resets them to DB-generated values on insert. This proves DB assignment, **not** model/reducer authenticity without the producer/admission chain |
| `rank_eligible`, `probability_publishable`, `can_execute` | Final accepted receipts only: true/true/false. No place for research-only rows; unqualified predictions remain in their existing specialist holding lanes |

### Chronology (new ledger is single-insert *after* reducer)

`source_snapshot_timestamp <= model_timestamp <= locked_at <= terminal_reduced_at <= created_at < event_start_time`.

This **differs deliberately** from legacy #2473's `created_at <= locked_at` model, which represented a previously inserted/then-reduced mutable historical record. The new envelope is a **single immutable, post-reducer insertion**, so the new adapter needs a versioned verifier with these exact rules. Do not modify legacy MLB chronology to make this pass. Server clocks and source snapshots must be independently trusted; use exact UTC instants.

### Only one allowed producer

The inserted record must be created by a narrowly privileged, authenticated **producer→terminal-admission** service after it has independently obtained the actual fitted specialist output and the exact immutable V17 terminal reducer receipt. The admission layer must verify the reducer output (including `status=PASS`, `terminal_label=FINAL_APPROVED`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`), model/calibrator/canonical event/settlement/selected side/freshness and receipt SHA before INSERT. The illustrative SQL CHECKs and a self-asserted reducer ID **cannot substitute** for that verification. Direct app/client table INSERT remains forbidden.

**Idempotency:** one saved row per `(sport, official_event_id, score_snapshot_id)` and reducer receipt ID. On duplicate attempt, re-read exact durable fields and return the same DB UUID only on full equality; divergent duplicates are typed `RECEIPT_MISMATCH` holds, never UPDATE/DELETE/overwrite. Different genuine pre-lock score revisions may be retained separately with explicit supersession via the official terminal reducer, not arbitrary latest-row selection.

## 3. Readback routing — protect independent owners

- MLB: route only to the existing `wow_event_predictions` verifier; keep its schema and specialist intact.
- NFL/NBA/NHL: #2473's **versioned** verifier reads the new ledger by the authentic DB UUID returned on the producer→terminal admission response, plus exact event/sport/side/model/settlement/timestamps/numeric match and current terminal state. Do not assume a pre-existing NFL specialist `score_snapshot_id` equals the newly assigned ledger `event_prediction_id`.
- Missing UUID → existing `LLP_EVENT_PREDICTION_ID_UNVERIFIED`; absent/unavailable DB → existing lookup hold; wrong or duplicated row → existing mismatch/not-exactly-one hold; mismatched numeric → existing probability-mismatch hold. Register any *new* codes in `artifacts/wow-engine/docs/failure_codes.md` in the same future source PR.
- An Action failure is never `MODEL_UNAVAILABLE`. A valid specialist model score with missing canonical receipt is still a **receipt/readback infrastructure or provenance HOLD**, not a fabricated qualified winner.
- Scout may display only verified controlling specialist numbers; it cannot mint probability or a terminal label. Keep Freebuff's separate Scout numeric/terminal protections independent of this path.

## 4. Proposed migration isolation

Draft artifact: `docs/wow/proposals/20261105_v17_multisport_event_receipt_DRAFT.sql`.

It is placed **outside** `artifacts/wow-engine/migrations/` on purpose: it must not be picked up by any automated migration runner. The draft creates an additive new table and protective checks, DB-generated identity/time, immutable UPDATE/DELETE denial, RLS and **no GRANTs** to app/anon/authenticated/service_role. It has **no producer procedure**, no readback route change, no model registration and no seeded/backfilled rows. It must NOT be executed on production before the independent approval/replay/rollback path is accepted.

Promotion to the real migrations folder requires an authorized Engineering PR and explicitly documented production project identity, migration order and down-plan. Do not run DB DDL from this proposal.

## 5. Test matrix / release acceptance

**Freebuff (implementation + reproducible evidence):** use an ephemeral *real PostgreSQL* cluster and versioned schema migrations, not an in-memory fake schema. Keep the existing characterization proof until it is replaced with positive/negative tests for the approved contract. Show:
1. Original MLB-only ledger still rejects NFL/NBA/NHL (23514) and accepts its legitimate MLB rows.
2. New ledger rejects wrong sport, settlement basis, duplicate participant, selected side, invalid/rounded numeric or bounds, mismatched complement and pre/postlock/order chronology.
3. DB-generated UUID and created_at cannot be client-controlled; direct insert privileges denied to app roles; RLS enabled; UPDATE/DELETE always rejected even for privileged migrator test.
4. Authenticated *positive* NFL/NBA/NHL admission from real controlling scorer + certified artifact + genuine independently bound V17 reducer receipt; exact UUID is returned and persisted, no forged side or market price. Include mixed-family wrong route negatives.
5. Repeated exact intake idempotently returns original ID; same snapshot with altered field is a typed conflict; two prelock revisions reconcile without leak/double publication.
6. Reject bogus reducer SHA, stale scorer, absent model artifact/calibrator, started/voided/postponed event, wrong official ID, post-model material update without rerun.
7. Exact `#2473` production reader/adaptor returns publishable only for matching immutable row and approved terminal disposition; does not conflate NBA shadow research. NFL Pick'em unaffected.
8. Full source/regression suite and exact-head hosted CI, independent SIRT semantic review, authenticated QA/Release and protected merge. No false green due to skipped pgserver/psycopg.
9. Authorized production binding / migration / permissions verification; identical Render web+worker deployed revision; authenticated Action→terminal→ledger→LLP readback and **two real canary cycles** (not shadow fixtures).

**Ownership / status:** Freebuff writes producer/adapter and tests. AMRT/Engineering owns migration, protected merge and Render deployment. SIRT independently challenges. WOW Betting Intelligence owns acceptance criteria, not approval. No production schema modifications, fitted probabilities, promotion, wagers or release authority from this proposal. `WORKING_NOT_COMPLETE`, `can_execute=false`, terminal owner `V17_TERMINAL_REDUCER`.
