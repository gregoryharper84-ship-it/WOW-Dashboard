# LLP Three-Way Selection Disposition Board

## Status

Implementation design for issue #2076. This is a read-only presentation and evidence-reconciliation layer over an existing Daily MONEYLINE run.

## Problem

The Daily runner already discovers cross-sport events, invokes the registered team-event scoring route, and persists row detail. The LLP readback currently returns one page at a time and deliberately reports board_complete=false. There is no single reconciled NFL/NBA/NHL board that gives every event exactly one of the three user-facing dispositions.

The board must not confuse an external research lean with a fitted model probability or a model-certified selection.

## Goals

1. Produce one complete, paginated-and-reconciled readback for all NFL, NBA, and NHL MONEYLINE rows in a Daily run.
2. Assign exactly one event-level disposition: QUALIFIED, PROVISIONAL, or HELD.
3. Require independent event-prediction ledger verification before QUALIFIED.
4. Allow PROVISIONAL only when a real model artifact exists but V17 certification is incomplete, and independent pregame research supports an explicit side.
5. Keep all external research separate from fitted probabilities, calibration, lower bounds, publication eligibility, and terminal authority.
6. Preserve unresolved or malformed supported-sport events as HELD rows rather than dropping them.
7. Keep the board read-only, authenticated, fail-closed, and can_execute=false.

## Non-goals

- No new model, model training, calibration, probability generation, or scorer routing.
- No change to V17_TERMINAL_REDUCER or any terminal decision.
- No changes to NFL Pick'em entries or the shared Pick'em sheet.
- No wagers, order placement, or automatic submission.
- No Supabase schema migration, production write, or deployment.
- The board does not itself prove the official league schedule is complete; it proves coverage against the persisted Daily run manifest and discovery reconciliation. The calling GPT must separately compare the official slate before claiming official-slate completeness.

## Architecture

### 1. Daily run manifest

Before persisting Daily row details, Daily adds a compact LLP_DAILY_RUN_MANIFEST_V1 object to daily_request_context. It records:

- Expected persisted row count.
- Expected row indexes and counts for NFL, NBA, and NHL MONEYLINE rows.
- Per-sport discovered event counts.
- Cross-sport row reconciliation status.
- Acquisition-detail persistence and board-completeness status.
- Missing-feed and source-blocker lists.
- Daily row reconciliation summary.

The manifest is repeated in each row detail through the existing JSON request context. No database schema change is required. The board compares all pages and manifests; it does not trust a single page or a client-supplied expected count.

### 2. Read-only full-board endpoint

Add authenticated POST /v17/llp-team-intelligence/{run_id}/board. The request contains optional external_research contexts. The endpoint:

1. Reads every Daily row-detail page, bounded to 500 rows.
2. Reads the server-persisted run manifest.
3. Projects each page through the existing LLP readback projection. If the installed projection supports a database argument, pass the same read-only database client so the independent event-prediction ledger verifier is used.
4. Retains every supported-sport row. If the existing page projector cannot form an event identity, add a HELD placeholder using the row index and manifest sport.
5. Reconciles page count, row indexes, per-sport counts, discovery reconciliation, acquisition-detail persistence, duplicate identities, and evaluation-result presence.
6. Adds one selection_disposition to every supported event.
7. Returns the complete board or a typed incomplete board. It never writes, re-scores, or upgrades terminal state.

The endpoint is read-only despite using POST: the body supplies research annotations for this response only. It is not persisted and is not sent to the fitted scorer.

### 3. Disposition rules

#### QUALIFIED

All of the following are required:

- Canonical event identity is exact and the event is still pregame.
- The existing LLP projection reports a governed winner receipt.
- source_diagnostics.event_prediction_receipt_verified is exactly true, produced by independent ledger verification.
- The model class is CERTIFIED_FITTED.
- Model and calibration versions, artifact IDs, and SHA-256 hashes are present.
- Calibrated probability, lower bound, and upper bound are present, finite, ordered, and within (0, 1).
- An immutable source/prediction receipt ID is present.
- Source snapshot and model timestamps are timezone-aware, pregame, and correctly ordered.
- probability_publishable and rank_eligible are true; can_execute is false.
- No duplicate identity, ledger mismatch, or material external research newer than the model timestamp exists.

If the ledger-verification field is absent, false, or not independently produced, the event cannot be QUALIFIED. This deliberately keeps main fail-closed until the independent verifier integration in PR #2487 is merged.

#### PROVISIONAL

All of the following are required:

- Canonical event identity is exact and the event is still pregame.
- A model evaluation result, model version, model artifact ID, and valid model artifact SHA-256 exist. A missing artifact is a hard HELD condition.
- The model is not a fully verified certified fitted receipt, or valid newer material research makes the existing model receipt stale for the current information set.
- No hard blocker exists: unresolved identity, insufficient model inputs, model unavailable, scorer failure, invalid model output, explicit governance failure, ledger mismatch/unavailability, or event already started.
- External research explicitly selects the home or away participant.
- At least two independent HTTPS source domains support that same side. At least two supporting sources must be non-market sources. Market context alone cannot qualify a provisional side.
- All source observations and the research capture timestamp are before kickoff and not in the future.
- The research identity exactly matches the Daily event (sport, event ID, teams, and start time).
- No source explicitly contradicts the selected side and no material contradictory factor is supplied.

PROVISIONAL never carries a fitted probability, lower bound, market-implied probability, or model confidence. It sets probability_publishable=false, rank_eligible=false, and can_execute=false. It carries source URLs, domains, source types, timestamps, concise summaries, and model_recheck_required where appropriate.

The backend validates research metadata and identity but does not fetch or independently verify source content. The caller must actually retrieve and cite the sources. The response states source_content_verified_by_backend=false.

#### HELD

Every other event is HELD. Hard reasons include:

- Insufficient inputs or missing specialist evaluation.
- Unresolved or mismatched event identity.
- Missing model artifact or artifact hash.
- Model unavailable, scorer failed, or invalid model output.
- Explicit governance or ledger verification failure.
- Stale, mismatched, contradictory, or insufficient external research.
- Missing run manifest, missing row pages, duplicate rows/identities, or incomplete discovery/acquisition reconciliation.

HELD has no selected side and no probability fields.

### 4. Board completeness

board_complete is independent of the number of QUALIFIED or PROVISIONAL events. It is true only when:

- All expected Daily rows are retrieved within the bounded page limit.
- Row indexes are unique and reconcile to the manifest.
- NFL/NBA/NHL row indexes and counts reconcile to the manifest.
- Cross-sport row reconciliation, acquisition-detail reconciliation, and acquisition board completeness are PASS.
- No configured feed is missing and no relevant source blocker remains.
- Every supported event row has a typed evaluation result.

If any condition fails, return board_complete=false with typed board_blockers. Partial event dispositions may still be returned, but the response must not be described as a complete slate. This is completeness against the Daily run manifest, not proof that the upstream provider discovered every official game.

## Safety invariants

- Existing scorer output and market evidence are never overwritten.
- Research context is never passed to the fitted specialist.
- Research-only values never become model probabilities.
- Only the independent ledger verifier can support QUALIFIED.
- The board never writes to Supabase and never mutates a saved row.
- V17_TERMINAL_REDUCER remains the sole terminal authority.
- terminal_decision_generated=false and can_execute=false in every board response.
- The existing readback feature flag remains the opt-in gate.
- PR #2487 remains untouched; this feature consumes its existing readback interface and fails closed until its ledger verification is available.
