---
name: gate-engine slim response architecture
description: How /gate-engine/run response_mode=slim works and why; size budget for 179-row GPT slates.
---

## Rule
`response_mode=slim` uses a whitelist (`_SLIM_RUN_KEEP`) — all verbose ledgers are dropped.
`prop_ledger` is intentionally excluded from slim: `terminal_labels` provides per-row outcomes.
`terminal_labels` entries are compacted: 2 blockers max (trimmed to 80-char code prefix), no `line`/`direction` fields.
`invocation_audit` replaces `unique_theses` list with `unique_theses_count` int, same for `duplicate_groups`.

**Why:** The original `ResponseTooLargeError` from OpenAI GPT Actions happened because full 3-row responses were 46KB — extrapolating linearly, 179 rows would be ~2.7MB. With slim mode, 3-row → 2.9KB; 179-row estimate → ~65KB (well within OpenAI's ~100KB limit).

**How to apply:**
- Any new endpoint that the GPT calls with large batches should support `response_mode=slim`.
- The slim helpers are `_slim_run_result()`, `_slim_terminal_labels()`, `_slim_blocker()` in app.py.
- If adding fields to terminal_labels in full mode, evaluate whether slim mode needs them; default to excluding.

## invocation_audit: required contract (all 10 fields)
```
manifest_hash, required_skills, invoked_skills, missing_required_skills,
skill_verification_status, ceilings_applied, lowest_ceiling,
unique_theses, duplicate_groups, required_runtime_evidence_complete
```
- `ceilings_applied` must be `[{source, ceiling, reason}]` — NOT bare strings.
- `unique_theses` and `duplicate_groups` must be ARRAYS — not replaced by counts; counts are supplemental.
- `required_runtime_evidence_complete=True` when: manifest_hash non-empty + skill_verification_status=PASS + missing_required_skills=[]. DATA_CONTRACT_FAIL is ROW-LEVEL, not an audit failure — do NOT gate on dcf_count.

## validation_status values
- Evidence valid: `"VALID_RUNTIME_EVIDENCE"` + `strict_runtime_disposition="RUN_COMPLETE"` + `terminal_disposition="PLAY"|"NO_PLAY"` based on final_card.
- Evidence invalid (skills missing): `"INVALID"` + `strict_runtime_disposition="RUN_INVALID_REQUIRED_RUNTIME_EVIDENCE"` + `terminal_disposition="NO_PLAY"`.

## MANIFEST_GOVERNANCE_HASH in gate_engine_run
`MANIFEST_GOVERNANCE_HASH` is NOT imported at module level in app.py.
It is lazily imported inside `gate_engine_run()` alongside `determine_required_skills` and `resolve_lowest_ceiling`.
The import must include `MANIFEST_GOVERNANCE_HASH` explicitly or a NameError crashes the route.
