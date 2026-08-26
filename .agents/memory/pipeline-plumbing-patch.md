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

## Response counters — scoring_execution block
Added to every gate-engine/run response path:
- 200 success: `scoring_execution` with rows_received/normalized/rejected_schema/entering_pipeline/scored/qualified/rejected + failed_stage=None
- 500 BACKEND_PIPELINE_FAILURE: same block with rows_scored=0, failed_stage="pipeline_execution"
- 422 ContractError: rows_normalized=0, rows_rejected_schema=N, failed_stage="request_normalization"
Also added `scoring_completed=True` to 200 response.

_rows_received tracked before normalize loop; _rows_normalized/_rows_rejected_schema tracked after.
rows_qualified derived from final_card length first, then falls back to counting qualifying terminal_labels.

## failure_path.py blocker-on-invalid-enrichment
Non-dict enrichment (e.g. enrichment="RETRIEVED") now stamps:
- blocker: "DATA_CONTRACT_FAIL:failure_path:enrichment_schema_invalid:expected_object:received_str"
- terminal_label: DATA_CONTRACT_FAIL (if not already set)
- gate result: {primary_failure: "ENRICHMENT_SCHEMA_INVALID", can_execute: False, ...}
Returns early; never silently converts to {}.
**Why**: silent {} conversion would make a contract defect look like ordinary missing evidence.

## Test coverage (11 new tests in TestPipelinePlumbing)
multipart PNG accepted, local-path string rejected, data URL accepted,
failure_path string → 422, JSON object string → not 500,
error handler safety (can_execute in body),
normalize unit tests (ContractError field, JSON decode, list, None coercion, player string allowed),
multipart full extraction smoke (mocked Anthropic + pipeline),
valid MLB structured row reaches failure_path module without AttributeError,
failure_path non-dict enrichment emits blocker not silent coercion,
forced exception → full BACKEND_PIPELINE_FAILURE shape with request_id + scoring_execution counters.
