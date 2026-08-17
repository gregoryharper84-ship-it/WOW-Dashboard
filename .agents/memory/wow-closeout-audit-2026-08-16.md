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
- `_rc_held = len(rows) - _rc_completed - _rc_rejected` gives exact reconciliation by construction
- SAVEPOINT in psycopg2: `cur.execute("SAVEPOINT sp_name")` — shares the existing transaction
- Public validator must be exported from the module so tests can call it without importing app.py (avoids import side effects)
- app.py has TWO scan-summary locations that both set total_final_approved — fix both or one drifts back

**Why:** External GPT returned HOLD on 9ecfea2 for these 8 specific deficiencies; 51775e1 resolved all.
**How to apply:** Before submitting any future external GPT audit report, verify all 8 categories are addressed.
