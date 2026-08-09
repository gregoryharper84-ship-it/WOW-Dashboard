# Kalshi Weather Shadow Pilot — Status Tracker

**Pilot status: VALIDATED_COMPLETE**  
**Final ruling (Step 15, ChatGPT): APPROVED_CLOSED**  
**Tracker last updated: 2026-08-09 (Step 16 closure)**

---

## Authority constants — permanent throughout pilot and after

| Constant | Value |
|---|---|
| `CAN_EXECUTE` | `False` |
| `PRODUCTION_AUTHORITY` | `False` |
| `USER_OUTPUT_AUTHORITY` | `False` |
| `CAPITAL_ALLOCATION` | `False` |

These are class-level constants in `gate_engine/kalshi_wx_shadow_client.py` (lines 21–23, 110–112, 127–129) guarded by runtime assertion checks. Never changed during the pilot. No production orders were placed. No capital was allocated.

---

## Shadow flags — permanent throughout pilot and after

| Flag | State |
|---|---|
| `SHADOW_RESEARCH_API_ENABLED` | `(not set)` in environment — resolves to `False` |
| `KALSHI_WX_SHADOW_AGENT_ENABLED` | `false` |

`SHADOW_RESEARCH_API_ENABLED` was passed only as inline subprocess env during the pilot runner shell invocations and was never persisted to Replit env vars. Naturally absent after subprocess exit.

---

## Total real spend

| Phase | Run ID(s) | Rows | Cost |
|---|---|---|---|
| Step 14B pilot | pilot-77549cab… (89 rows) + pilot-3a141b0e… (36 rows) | 125 | $0.395071 |
| Step 14C canary | canary-14c-3b00e8ca… | 5 | $0.015899 |
| **Total** | | **130** | **$0.410970** |

All 130 rows have `model='claude-haiku-4-5-20251001'`, `model IS NULL = 0` (Step 14D backfill). Zero production or calibration ledger writes across all runs.

---

## Known pilot limitations

1. **All ceilings KALSHI_WATCH.** `KalshiWxShadowResearchClient.research()` is inert (`CAN_EXECUTE=False`), so the `forecast_context` subagent fails and the orchestrator produces `status=BLOCKED` with conservative `ceiling=KALSHI_WATCH` for every snapshot. This is correct behavior — not a schema or enforcement failure.

2. **BLOCKED + SHADOW_PASS is not a contradiction.** A blocked payload with `ceiling=KALSHI_WATCH` (a `CEILING_CAPABLE_LABELS` member) and `advisory_only=True` correctly passes the closed-schema validator. The safety boundary worked; the schema validation worked; no production authority was exercised.

3. **No live market outcome comparison.** The pilot ran against a frozen 25-snapshot cohort (stratified, `excluded_reason IS NULL`). No settlement verification against live Kalshi outcomes was performed.

4. **PROVISIONAL calibration throughout.** Without live research client execution, calibration status on all outputs is `PROVISIONAL`. No `CALIBRATED` labels were produced.

5. **External data sources partially unavailable.** BBRef and similar external hosts are blocked from the Replit container. NWS primary was used for all weather sourcing.

6. **Step 14B model column was NULL until Step 14D backfill.** The backfill was executed once after Step 14D and confirmed: 130 rows updated, 0 remaining NULL.

---

## Step history — one entry per step

| Step | Title | Status | Notes |
|---|---|---|---|
| 1–3 | Shadow pilot infrastructure — `CapabilityBoundary`, schema validator, shadow orchestrator, `KalshiWxShadowResearchClient` scaffold, capture module, durable Postgres queue | **COMPLETE** | Covered by memory files: `kalshi-wx-shadow-stages-123`, `kalshi-wx-shadow-durable-queue`, `kalshi-wx-shadow-nonblocking` |
| 7 | Shadow registry taxonomy — 3 namespaces, `kalshi_wx_terminal_labels.py`, `CEILING_CAPABLE_LABELS` | **COMPLETE** | `kalshi-wx-shadow-registry` memory |
| 9 | Closed-schema validator — `kalshi_wx_shadow_schema.py`, pure Python, forbidden-key scan, `SHADOW_PASS` singleton, 51 tests | **COMPLETE** | `kalshi-wx-shadow-schema` memory |
| 10 | Shadow agent scaffold — `kalshi_wx_shadow_agent.py`, TEST-ONLY direct completion helper, validator-invariant enforced, 6 tests | **COMPLETE** | `kalshi-wx-shadow-agent` memory |
| 10D | Shadow capture wiring — `kalshi_wx_shadow_capture.py`, lazy-import patch targets, `UNAVAILABLE` sentinel, durable queue insertion | **COMPLETE** | `kalshi-wx-shadow-capture` memory |
| 12.5A | Durable Postgres-backed queue — `kalshi_wx_shadow_snapshot_queue` table, synchronous INSERT on request thread, no threads/orchestrator in capture path | **COMPLETE** | `kalshi-wx-shadow-durable-queue` memory |
| 14A | Pre-pilot preparation — snapshot cohort freeze (25 records, stratified, `excluded_reason IS NULL`), runner scaffold, Gate A / Gate B dual-gate auth, `kalshi_wx_shadow_results` table, usage accounting | **COMPLETE** | |
| 14B | 25-snapshot live pilot run — 125 subagent calls, 2 shell invocations (SIGKILL + resume), `stop_reason=EXHAUSTED`, $0.395071 | **APPROVED** | `pilot_audit/step14b_pilot_audit.md`; 0 BLOCKED rows, 0 forbidden-key violations, 0 production writes |
| 14C | Native schema repair + canary — per-subagent validator module (`kalshi_wx_shadow_native_schema.py`), canonical assembler wired into runner, canary run (5 rows, `canary-14c-3b00e8ca…`), $0.015899 | **APPROVED_CLOSED** | `step14c-native-schema-repair` memory; 60 new tests; `final_decision`/`stake_tier`/`is_playable` gap closed |
| 14D | Audit-hardening — Fix 1: model identity via `_sa_mod._MODEL` + 130-row backfill; Fix 2: outer enforcement choke point in `run_pilot` for all paths (real + mock); 14 new tests | **APPROVED_CLOSED** | `step14d-audit-hardening` memory; 4305 pass, 9 pre-existing failures unchanged |
| 15 | ChatGPT final ruling | **APPROVED_CLOSED** | User record: `"STEP_15=READY_FOR_CHATGPT_FINAL_RULING"` → ruling received: `APPROVED_CLOSED` |
| 16 | Closure documentation — status tracker, Step 16 record, memory update, no code/logic/flag/model/runtime changes | **COMPLETE** | `pilot_audit/step16_closure.md`; this file |
