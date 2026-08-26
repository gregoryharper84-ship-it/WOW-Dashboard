---
name: WOW Closeout Audit 2026-08-16
description: 8 audit items fixed in commit 51775e1; lessons from the HOLD→ACCEPT cycle.
---

## Rule
External GPT closeout audits require ALL of these before ACCEPT:
1. Exact row reconciliation (==, not >=)
2. Transactional (not best-effort) provenance fail-closed
3. Legacy counts separated from enforced counts
4. Behavioral test must not be skippable
5. Prob-ledger must cap BOTH FINAL_APPROVED and MONEY_QUALIFIED
6. NO_REGISTERED_MODEL must cap money labels (integration test required)
7. Enforcement status must be accurate (PARTIAL_OR_PENDING until tests pass)
8. Multi-worker smoke evidence required

## Key Gotchas
- `target_date` in run_pipeline() must be a `date` object, never a string (slate_validation calls .isoformat())
- `_rc_held = len(rows) - _rc_completed - _rc_rejected` ensures exact reconciliation by construction
- SAVEPOINT in psycopg2: execute("SAVEPOINT sp_name") on the cursor — shared transaction
- `validate_wx_terminal_label()` must be exported from the module (not just an internal function) so tests can call it without importing app.py
- Scan summary has TWO locations in app.py that set total_final_approved — both must be fixed

**Why:** External GPT held 9ecfea2 for these 8 specific issues; 51775e1 resolved all.
**How to apply:** Any future external GPT audit will check the same 8 categories. Address all before submitting.
