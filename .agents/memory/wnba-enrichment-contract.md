---
name: WNBA enrichment contract validator
description: gate_engine/wnba_enrichment_contract.py; WNBA_ENRICHMENT_TYPE_MISMATCH; game_log vs box_score_log type enforcement
---

## The rule

Two WNBA enrichment fields have different consumers and must never be mixed:

| Field | Type | Consumer |
|---|---|---|
| `game_log` | `list[number]` | L5/L10 ledger — flat numeric hit-rate |
| `box_score_log` | `list[dict]` | WNBA opportunity engine — role/minutes/usage |

When these are mixed (e.g. `game_log` contains dicts, or `box_score_log` contains numbers), the backend returns `WNBA_ENRICHMENT_TYPE_MISMATCH` (HTTP 422) rather than silently routing the wrong data.

## Module

`gate_engine/wnba_enrichment_contract.py`

Public API:
- `validate(enrichment) → (ok, error_code, detail)` — never raises
- `validate_or_raise(enrichment) → None` — raises ValueError on mismatch
- `mismatch_response(detail) → dict` — structured 422 body with remediation guidance
- `ERROR_CODE = "WNBA_ENRICHMENT_TYPE_MISMATCH"`

## Where it's wired

`app.py` → `analyze_and_score()` — runs when `body.get("enrichment")` is present in the request (GPT resubmission flow). Validates either flat or per-leg-keyed enrichment dicts. Returns 422 on mismatch before touching the pipeline.

## Why

Silent type mismatch was the failure mode: `game_log` with dicts would silently produce zero-length numeric list in L5/L10; `box_score_log` with numbers would silently produce zero qualifying games in WNBA opportunity engine. Both produce MODEL_QUALIFIED_HOLD with no diagnostic signal.

## How to apply

Any new endpoint that accepts submitted enrichment for WNBA legs must call `validate()` before passing enrichment to the pipeline.
