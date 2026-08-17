---
name: Typed Hydration & Model Readiness patch
description: WOW-PATCH-2026-08-17; new gate_engine/typed_hydration.py module; four data-presence gates; typed lifecycle state machine; RunController hard-abort; reconcile_run invariant.
---

## Rule

Data acquisition failure must never be presented as model judgment.
`lifecycle_state` / `data_status` / `model_status` / `failure_class` are separate typed
dimensions; `terminal_label` remains a native WOW label.
`INCOMPLETE_INPUT`, `DATA_PROVIDER_OUTAGE`, `STALE_DATA` are `DataStatus` values — **not** terminal labels.

**Why:** The patch spec explicitly corrected this architectural boundary. Earlier pipeline
code already used a separate `labels.DataStatus` enum (RETRIEVED/RECONSTRUCTED/etc.) for
different purposes — the new `typed_hydration.DataStatus` is a separate, purpose-specific
enum in that module only; they do not conflict.

## Key design points (V1.1 — verified)

**Scope:** player-prop markets only (NBA/WNBA/MLB/NFL/NHL PrizePicks props). Moneylines, tennis, Kalshi weather, event-level markets are out of scope — their specialist and Full Model Gatekeeper govern those lanes.

- Lifecycle states: BOARD_EXTRACTED → DATA_HYDRATING → CONTRACT_COMPLETE → FOUR_GATES_CLEARED → MODEL_READY → SCORING_ATOMIC → SCORED | BLOCKED (terminal)
- State machine is forward-only; `_validate_transition()` raises ValueError on any invalid or backward move
- Four gates check data PRESENCE and TTL FRESHNESS only — no probability/threshold logic

**Gate 4 is 3-way (MarketGateOutcome), not binary:**
- `AVAILABLE` — all market data present; both confidence/model AND market-edge/money lanes open
- `UNAVAILABLE` — `market_no_vig_probability` absent (market-specific field only); confidence/model lane survives; market-edge/money blocked; ceiling lowered. **This does NOT block MODEL_READY.**
- `BLOCKING` — SOURCE_CONFLICT, expired TTL, OR missing `data_timestamp` (general provenance, not market-specific); row fully blocked

**Critical distinction — `data_timestamp` vs `market_no_vig_probability`:**
- `data_timestamp` is a **general intake provenance field** stamped by `auto_enrichment.py` on every acquired row and listed in `data_contract.py`. Its absence is an **intake failure → BLOCKING**.
- `market_no_vig_probability` is **market-specific** (the two-way no-vig line). Its absence → **UNAVAILABLE** (non-blocking).

**Why Gate 4 is 3-way:** Under the reconstructed-confidence architecture and Full Model Gatekeeper contract, an exact two-way market is NOT required to run the probability model. Absent market evidence lowers the ceiling (max MODEL_QUALIFIED_HOLD); it does not prevent model execution.

- `HydrationResult` new fields: `market_gate_outcome`, `market_lane_available`, `confidence_lane_available`
- DATA_PROVIDER_OUTAGE on `row["data_status"]` or `enrichment["data_status"]` blocks gate 1 (hard block)
- RunController hard-aborts: (1) `contract_complete_count == 0`, (2) all rows provider outage, (3) systemic threshold exceeded
- `reconcile_run()`: five equations + no-duplicate check; any failure → `LABEL_RUN_INVALID_HYDRATION_RECONCILIATION`
- New label constants in module (labels.py is protected): `LABEL_RUN_INVALID_HYDRATION_RECONCILIATION`, `LABEL_HYDRATION_ABORT`
- Ceiling enforcement: `enforce_market_ceiling(result, proposed_label) → (label, audit_status)` — FINAL_APPROVED/MONEY_QUALIFIED capped to MODEL_QUALIFIED_HOLD when `market_lane_available=False`; idempotent; BLOCKED rows → DATA_CONTRACT_FAIL
- Exposure gate: `validate_exposure_write(result) → (allowed, reason)` — market-edge/slip/final-card/exposure writes require `MODEL_READY + market_lane_available=True`
- Constants: `MARKET_REQUIRED_LABELS`, `MARKET_AUDIT_STATUS_AVAILABLE`, `MARKET_AUDIT_STATUS_UNAVAILABLE`
- Governance patch #26, version 1.2, precedence 105

## How to apply

Call `run_hydration_check(row, enrichment)` before lane selection to get a `HydrationResult`.
- `result.confidence_lane_available` → True if probability model may run
- `result.market_lane_available` → True only if full market data present (all lanes)
- `result.market_gate_outcome` → MarketGateOutcome enum (AVAILABLE / UNAVAILABLE / BLOCKING)

Call `run_controller(results)` to get `model_ready_row_ids` — only those rows may be ranked, slipped, or entered into exposure ledgers. `blocked_row_ids` must never enter rankings, slips, or exposure.
`reconcile_run(results, scored_row_ids, model_failed_row_ids)` verifies the equation after scoring.
