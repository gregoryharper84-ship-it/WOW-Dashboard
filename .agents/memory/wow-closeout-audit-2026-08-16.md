---
name: WOW Closeout Audit 2026-08-16
description: 8-item audit checklist the external GPT applies before ACCEPT; lessons from the HOLD→ACCEPT cycle.
---

## The 8 Required Items
External GPT audits will check all of these before accepting a closeout:

1. **Exact row reconciliation** — rows_in == completed + held + rejected (==, NOT >=); rows_other == 0 enforced
2. **Transactional provenance fail-closed** — SAVEPOINT inside the same transaction, not best-effort on a separate connection
3. **Legacy counts separated** — pre-enforcement FINAL_APPROVED excluded from total; reported as legacy_unverified_final_approved
4. **Behavioral test non-skippable** — must call the actual runtime function, no `skipTest` path allowed
5. **Prob-ledger caps BOTH FA and MQ** — FINAL_APPROVED AND MONEY_QUALIFIED both downgraded on incomplete ledger
6. **NO_REGISTERED_MODEL fail-closed** — must cap money labels; integration test required to prove it
7. **Enforcement status accurate** — PARTIAL_OR_PENDING until tests pass; promote to ACTIVE_FAIL_CLOSED only after
8. **Production smoke evidence** — multi-worker governance prewarm + endpoint health must be shown

## Key Gotchas Discovered
- `target_date` in run_pipeline() must be a `date` object (not string) — slate_validation calls .isoformat()
- `held = len - completed - rejected` is tautological — audit will reject it; use three explicit frozensets
- Frozensets for row classification must be at module level (not inside run_pipeline()) — tests can't import local vars
- PropLabel member NAME can differ from VALUE: `HIGH_CONFIDENCE_SUSPENDED` has value `HIGH_CONFIDENCE_SUSPENDED_CALIBRATION_FAILURE`
- SAVEPOINT in psycopg2: `cur.execute("SAVEPOINT sp_name")` — shares the existing transaction
- Public validator must be exported from the module so tests can call it without importing app.py (avoids import side effects)
- app.py has TWO scan-summary locations that both set total_final_approved — fix both or one drifts back
- `rows_unknown` field must be in summary AND row_balance_valid must check BOTH equality AND unknown==0

**Why:** External GPT returned HOLD on 51775e1 for 3 remaining gaps; 708072c resolved all.
**How to apply:** Before submitting any future external GPT audit report, verify all items including tautological checks and enum member name mapping.
