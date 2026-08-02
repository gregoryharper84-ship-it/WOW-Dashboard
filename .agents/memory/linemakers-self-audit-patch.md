---
name: Linemakers self-audit patch
description: Patch #18 — audit table, evidence manifest, reconciliation equation, unified calibration ledger, Gate 0 event-state mutex.
---

# Linemakers Presentation & Self-Audit Patch

**Patch ID:** `WOW-PATCH-2026-08-01-LINEMAKERS-PRESENTATION-AND-SELF-AUDIT`
**Precedence:** 97 (runs after all earlier patches)
**Patch count after this:** 18

## Key modules

- `kalshi_engine/scan_audit.py` — pure functions: `check_ticker_identity`, `build_candidate_audit_row`, `build_candidate_audit_table`, `build_evidence_manifest`, `run_second_pass_audit`, `build_reconciliation_equation`, `build_candidate_funnel_summary`
- `kalshi_engine/unified_calibration_ledger.py` — `wow_unified_calibration_ledger` table; `log_candidate()`, `settle_result()`, `get_ledger()`, `get_calibration_summary()`

## Gate 0 in sports_gate.py

Added Gate 0 (event-state mutex) before Gate 1. Any `event_status` in `_LIVE_EVENT_STATUSES` → `CATEGORY_DISABLED_OR_UNSUPPORTED / LIVE_MARKET_DISABLED` and short-circuits (no more gates run).

**Why:** Pregame probability is invalid after event start; pregame models must never score live markets.

**How to apply:** A candidate with `event_status` in `{"in_progress", "live", "started", "halftime", "suspended", "active", "inprogress"}` will always fail Gate 0. Missing event_status → "UNKNOWN" → passes Gate 0.

## Impact on existing tests

- `test_sports_gate_full_pass` counted exactly 9 gate verdicts; bumped to 10 after Gate 0 was added.
- Patch count tests bumped from 17 → 18 in `test_governance_resilience_acceptance.py` and `test_patch_portfolio_stage2a.py`.

## Reconciliation equation

```
rows_scanned = identity_failed + settlement_failed + event_state_failed
             + model_failed + price_failed + edge_failed
             + portfolio_failed + qualified
```

`event_state_failures` is a new counter (added alongside identity/settlement/model/etc.) — must be included when the caller provides an explicit `rows_scanned` override.

## app.py integration

- `_build_scan_audit_block()` helper defined immediately before the `@app.route("/wow/kalshi/category-scan")` decorator.
- `event_state_failures = 0` counter added to category-scan counter block.
- `event_status` field added to sports candidate dict from `m.get("status") or m.get("event_status") or "UNKNOWN"`.
- `unified_calibration_ledger.ensure_table()` called in `_run_startup_warmup()`.

## Ticker identity check

`check_ticker_identity()` compares `ticker`, `inventory_ticker`, `orderbook_ticker`. Mismatch → `CONTRACT_IDENTITY_UNVERIFIED` warning label — **never a block**. Warning surfaced in `candidate_audit_table[].contract_identity_warning` in the response JSON.

## Unified calibration ledger

Stores both QUALIFIED and REJECTED candidates so gate bias can be detected. `entry_type` distinguishes them. `rejection_reason` always set for REJECTED entries. All three lanes (KALSHI_WEATHER, KALSHI_SPORTS, SPORTS_LLP) write to the same table.
