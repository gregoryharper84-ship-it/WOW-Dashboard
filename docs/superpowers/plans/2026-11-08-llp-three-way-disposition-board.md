# Implementation Plan: LLP Three-Way Selection Disposition Board

BASE: db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c
SPEC: docs/superpowers/specs/2026-11-08-llp-three-way-disposition-board-design.md
PARENT: issue #2076

## Global constraints

- Work on chatgpt/llp-three-way-disposition-board-20261108 only.
- Do not modify PR #2487 or its readback-owned files. Consume the current projection interface and require its independently generated event_prediction_receipt_verified=true field before QUALIFIED.
- No scoring/model changes, no database migrations, no production writes, no deployment, no merge, no Pick'em sheet edits, no wagers, and no auto-submission.
- Preserve V17_TERMINAL_REDUCER as sole terminal authority and can_execute=false.
- The board is complete against the Daily run manifest only; never claim official schedule completeness from this endpoint alone.
- External research is caller-supplied, metadata-validated, not fetched by the API, and never a fitted probability.
- The current environment has no repository checkout and outbound git clone failed. Use the isolated GitHub feature branch and hosted PR CI as the verification surface; record this limitation.

## Task 1 — Three-state disposition classifier

Files:
- New artifacts/wow-engine/v17/llp_selection_disposition.py
- New artifacts/wow-engine/test_llp_selection_disposition.py

Steps:
1. Write behavior tests for QUALIFIED, PROVISIONAL, and HELD before implementation.
2. Run the targeted test and observe the expected red failure while the classifier is absent.
3. Implement strict Pydantic research-source/context contracts and a pure classifier.
4. Ensure QUALIFIED requires the independent ledger verification flag, certified fitted class, exact artifact/calibration provenance, calibrated bounds, immutable receipt, pregame timestamps, and false execution.
5. Ensure PROVISIONAL requires an existing model artifact/hash, exact pregame identity, two independent non-market sources supporting the same side, and no hard blocker. Omit all probability fields.
6. Ensure missing artifact, insufficient inputs, unresolved identity, model unavailable, scorer failure, governance/ledger mismatch, or research conflict produces HELD.
7. Run the targeted classifier tests and the existing LLP readback regression.

Expected:
- All classifier tests pass.
- No provisional object contains calibrated_probability, lower_bound, model_probability, or model_confidence.
- No path returns can_execute=true.

## Task 2 — Daily manifest and full-board aggregation

Files:
- Modify artifacts/wow-engine/v17/daily_snapshot_runtime.py
- New artifacts/wow-engine/v17/llp_team_intelligence_board.py
- New artifacts/wow-engine/test_llp_team_intelligence_board.py

Steps:
1. Add a pure Daily run manifest builder and persist its compact result in daily_request_context before row-detail persistence.
2. Test the manifest using literal row-index/count fixtures.
3. Implement bounded page retrieval (25 rows per page, 500-row ceiling) and read-only projection through the existing LLP readback.
4. Reconcile all row indexes, per-sport expected counts, discovery and acquisition-detail reconciliation, missing feeds, duplicate event IDs, and specialist evaluation-result presence.
5. Preserve malformed/unresolved NFL/NBA/NHL rows as HELD placeholders rather than dropping them.
6. Apply the disposition classifier to every supported event.
7. Add tests for multi-page completion, gaps, duplicate identities, missing manifest, discovery incompleteness, unresolved identity, and false execution.

Expected:
- board_complete=true only when the full Daily manifest and discovery/acquisition evidence reconcile.
- Every supported-sport event row appears exactly once and has one disposition.
- A row with no identity is retained as HELD.
- The board does not write or re-score.

## Task 3 — Authenticated API and gateway wiring

Files:
- Modify artifacts/wow-engine/v17/daily_snapshot_runtime.py
- Modify artifacts/wow-engine/v17/openapi.llp-team-engine.v17.yaml
- Modify artifacts/wow-engine/v17/supabase/functions/wow-llp-action-gateway/index.ts
- Modify artifacts/wow-engine/v17/vercel/llp-action-gateway/api/gateway.js
- Modify artifacts/wow-engine/v17/vercel/llp-action-gateway/vercel.json
- Modify artifacts/wow-engine/tests/test_v17_llp_batch_ingress_alignment.py

Steps:
1. Install the board route only when WOW_LLP_TEAM_INTELLIGENCE_READBACK_ENABLED=1.
2. Add authenticated POST /v17/llp-team-intelligence/{run_id}/board with x-openai-isConsequential=false and strict additionalProperties=false schemas.
3. Add the route to both gateway allowlists and the Vercel rewrite/normalization.
4. Add contract tests for OpenAPI, authentication, method, route forwarding, and can_execute=false.
5. Verify existing single-event, batch, Daily, readback, and NFL Pick'em routes remain unchanged.

Expected:
- The new action is authenticated and read-only.
- Both gateways forward the same path and body.
- Existing action contracts remain intact.

## Task 4 — Operating instructions and end-to-end verification

Files:
- Modify artifacts/wow-engine/LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt
- All changed tests and docs.

Steps:
1. Add the operational sequence: run Daily full slate, request an empty-research board to obtain every event, gather current external sources, then request the board with research contexts.
2. Require a separate official-schedule comparison before claiming official-slate completeness.
3. Require citations for every PROVISIONAL research side and prohibit numeric model probabilities on provisional/held rows.
4. Run the targeted test suite and full wow-engine-verify / wow-verify checks on the final PR head.
5. Inspect exact-head diff, workflow jobs, and all changed files. Record any unavailable check explicitly.
6. Request independent review; do not merge or deploy.

Expected:
- The final board has one disposition per NFL/NBA/NHL event and explicit completeness/reason fields.
- The PR remains open and unmerged pending independent acceptance and protected release.
