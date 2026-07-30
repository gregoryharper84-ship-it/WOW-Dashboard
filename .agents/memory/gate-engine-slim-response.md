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

## invocation_audit: required fields for GPT
The GPT uses these fields to decide PLAY vs NO_PLAY:
- `required_runtime_evidence_complete` (bool) — false → NO_PLAY
- `required_skills` (list) — skills the engine expected
- `missing_required_skills` (list) — what was missing
- `skill_verification_status` (str) — PASS/PARTIAL/FAIL
- `manifest_hash` (str) — governance check

## MANIFEST_GOVERNANCE_HASH in gate_engine_run
`MANIFEST_GOVERNANCE_HASH` is NOT imported at module level in app.py.
It is lazily imported inside `gate_engine_run()` alongside `determine_required_skills` and `resolve_lowest_ceiling`.
The import must include `MANIFEST_GOVERNANCE_HASH` explicitly or a NameError crashes the route.
