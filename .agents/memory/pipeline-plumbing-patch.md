---
name: Pipeline plumbing patch
description: Three confirmed defects in app.py fixed — screenshot transport, string-to-dict schema crash, undefined-variable error handler masking the real exception.
---

# Pipeline plumbing patch

## Defects fixed

### A. /analyze-and-score: multipart upload not accepted
`extract_image_bytes(req)` function added near app creation (before error handlers).
Supports: multipart/form-data `image` field, JSON `image_base64` (plain or data URL).
Rejects: empty, wrong MIME, >12 MB.
In the route: checks `request.files.get("image")` first; if present, decodes bytes and
base64-encodes for the existing Claude block builder. Falls through to JSON path if absent.
Missing image returns 422 (not 400) — matches existing test expectation.

### B. /gate-engine/run: string fields crash specialists
`normalize_gate_request(row)` added near app creation.
Mapping fields (`role_status`, `lineup_status`, `settlement`, `matchup`, `failure_path`,
`source_report`) must be dicts. JSON-encoded object strings are decoded. Bare status
strings ("RETRIEVED") raise `ContractError` → HTTP 422 before any specialist runs.
List fields (`game_log`, `box_score_log`, `l5_ledger`, `l10_ledger`) must be arrays.

**CRITICAL**: `player`, `candidate`, `market`, `event` are NOT in `_GATE_MAPPING_FIELDS`
because in raw_rows they are STRING PRIMITIVES (e.g. player="LeBron James"). Only include
fields that are expected to be dicts in the raw board row context.

Called as: `raw_rows = [normalize_gate_request(r) for r in raw_rows]` after the
`rows must be a non-empty list` check, before target_date parsing.

### C. Error handler: undefined variable `req`
`req.log.error(...)` at two locations in gate_engine_run replaced with `app.logger.error(...)`.
The gate-engine catch block now returns structured `BACKEND_PIPELINE_FAILURE` JSON:
`{terminal_status, decision, scoring_completed, props_scored, primary_failure, request_id, can_execute}`.

### D. Global error handler: no stack trace in response
Removed `trace: traceback.format_exc()` from response body. Full traceback logged server-side via
`app.logger.exception(...)`. Added `terminal_status: "BACKEND_PIPELINE_FAILURE"`, `can_execute: False`,
`request_id` (from `g.request_id`) to response.

### E. failure_path.py defensive guard
`enr = enrichment if isinstance(enrichment, dict) else {}` replaces `enrichment or {}`.
`matrix_raw = enr.get(...)` then `matrix = matrix_raw if isinstance(matrix_raw, dict) else {}`.
Prevents AttributeError when enrichment is a non-dict (e.g. a string).

## New infrastructure added (near app creation, before error handlers)
- `RequestValidationError(ValueError)` — transport-layer rejection
- `ContractError(ValueError)` — field type mismatch; carries `.field`, `.expected`, `.actual_type`
- `extract_image_bytes(req)` — multipart/base64/data-URL normaliser
- `normalize_gate_request(row)` — mapping/list field validator for raw_rows
- `@app.before_request _assign_request_id` — stamps `g.request_id` from X-Request-ID header
- `@app.errorhandler(RequestValidationError)` → 400 JSON
- `@app.errorhandler(ContractError)` → 422 JSON with field/expected/actual_type

## Terminal semantics rule
`NO_PLAY` = pipeline completed, no row qualified.
`BACKEND_PIPELINE_FAILURE` / `NO_DECISION` = scoring did not complete (exception caught).
These must never be conflated. The gate_engine_run catch block enforces this.

## Test coverage
9 new tests in `TestPipelinePlumbing` in `test_analyze_and_score.py`:
multipart PNG accepted, local-path string rejected, data URL accepted,
failure_path string → 422, JSON object string → not 500,
error handler safety, normalize unit tests (ContractError, JSON decode, list, None coercion,
player string allowed).
