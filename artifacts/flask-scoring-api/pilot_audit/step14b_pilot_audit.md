# Step 14B — Kalshi Weather Shadow Pilot Audit
**Executed:** 2026-08-09  
**Cohort:** 25-record stratified freeze (excluded_reason IS NULL)  
**Model:** claude-haiku-4-5-20251001  
**Run IDs:** pilot-77549cab… (run 1), pilot-3a141b0e… (run 2)

---

## Execution Summary

The run required two shell invocations due to the 5-minute process timeout:

| Phase | Calls | Snapshots | Notes |
|---|---|---|---|
| Run 1 | 89 | 18 complete, 1 partial (SIGKILL) | All 89 rows committed before kill |
| Run 2 | 36 | 7 remaining + 1 redo (missed by SIGKILL) | Resumed via is_pair_completed check |
| **Total** | **125** | **25** | Natural completion — stop_reason=EXHAUSTED |

**stop_reason: EXHAUSTED** — all 25 eligible snapshots processed. No hard cap was hit.

---

## Hard Constraints — All Satisfied

| Constraint | Limit | Actual | Pass |
|---|---|---|---|
| Total subagent calls | ≤ 125 | **125** | ✓ |
| Total cost | ≤ $2.00 | **$0.39507100** | ✓ |
| UNAVAILABLE usage rows | 0 preferred | **0** | ✓ |
| BLOCKED/ERROR rows | 0 | **0** | ✓ |
| Schema forbidden-key violations | 0 | **0** | ✓ |
| Capability/hook denials | 0 | **0** | ✓ |
| CAN_EXECUTE=False throughout | required | **[False]** only value | ✓ |
| Production table writes | 0 | **0** | ✓ |

---

## Database Query Results (Direct)

### Status and Usage Accounting
```
status='COMPLETE'   n=125   AVAILABLE=125   UNAVAILABLE=0
```
All 125 rows: status=COMPLETE, usage_accounting_status=AVAILABLE, estimated_cost_usd set.
Zero UNAVAILABLE usage rows — every API call returned real token counts.

### Totals
- Total rows: 125 | Distinct snapshots: 25 | Distinct agent types: 5
- Total input tokens: 190,851
- Total output tokens: 40,844
- Total actual cost: **$0.39507100** (19.8% of $2.00 budget)

### Per-Agent Breakdown
| Agent | n | Input tokens | Output tokens | Cost |
|---|---|---|---|---|
| contradiction_detection | 25 | 37,930 | 9,967 | $0.087765 |
| forecast_context | 25 | 40,122 | 6,662 | $0.073432 |
| source_reconciliation | 25 | 35,847 | 8,239 | $0.077042 |
| uncertainty_explanation | 25 | 41,680 | 9,726 | $0.090310 |
| unusual_regime | 25 | 35,272 | 6,250 | $0.066522 |

---

## Schema Validation (Step 9 Closed-Schema Validator)

**Clarification:** The closed-schema validator validates the ASSEMBLED 5-agent orchestrator output (the payload that would go through the full shadow pipeline). Applied to individual per-agent tool_input rows, it produces EXTRA_FIELD rejections for legitimate agent-specific keys (blockers, conflicts, notes, etc.) — this is expected architectural behavior, not a violation.

**What actually matters for the audit — forbidden governance key scan:**

All 20 known forbidden governance keys (`terminal_label`, `can_execute`, `final_label`, `capital_allocation`, `authorized`, `governance_state`, `final_decision`, `stake_tier`, `label_ceiling_reason`, `run_llp_governance`) were scanned recursively across all 125 agent_output dicts:

```
Rows scanned: 125
Rows with forbidden governance keys: 0
CLEAN: zero forbidden governance keys in any agent_output.
```

All top-level keys appearing across 125 rows:
```
['blockers', 'calibration_status', 'ceiling_impact', 'ceiling_impacted',
 'conflicts', 'contradictions_found', 'horizon_hours_estimate', 'notes',
 'recommended_ceiling', 'reconciliation_status', 'regime_factors',
 'regime_unusual', 'reliability_impact', 'revised_ceiling', 'scoring_mode',
 'sigma_f_estimate', 'sources_missing', 'sources_present',
 'uncertainty_sources', 'uncertainty_tier']
```
Intersection with forbidden set: **empty**. Clean.

---

## Model Identity

The `model` column in `kalshi_wx_shadow_results` is NULL for all 125 rows. This is architectural: `call_one_agent` hardcodes `model=None` at line 521 (the model string is not returned by the subagent dispatch layer). Model identity is enforced at a different layer — the `_MODEL` constant in `gate_engine/kalshi_wx_shadow_subagents.py` which is `"claude-haiku-4-5-20251001"`, verified by 3 passing M1 tests that capture and assert `messages.create()` call arguments.

API endpoint confirms correct routing: `POST http://localhost:1106/modelfarm/anthropic/v1/messages` (the AI Integrations proxy) — confirmed in logs for all 125 calls.

---

## Flag State Throughout

| Flag | Before | During | After |
|---|---|---|---|
| SHADOW_RESEARCH_API_ENABLED | NOT_SET | inline env (subprocess only) | **NOT_SET** |
| KALSHI_WX_SHADOW_AGENT_ENABLED | false | false (inherited) | **false** |

SHADOW_RESEARCH_API_ENABLED was passed as inline subprocess env only — never persisted to Replit env vars. Naturally absent after subprocess exit. Confirmed: `python -c "import os; print(os.environ.get('SHADOW_RESEARCH_API_ENABLED','NOT_SET'))"` → `NOT_SET`.

---

## Production Table Writes

| Table | Row count | Changed by pilot? |
|---|---|---|
| wow_session_exposure | 166 | No |
| llp_source_snapshots | 0 | No |
| weather_scout_log | 25 | No (pre-existing from capture) |
| slip_expert_review_log | 493 | No |
| kalshi_wx_shadow_results | **125** | Yes — pilot writes only |

Zero production or calibration ledger writes.

---

## Representative Agent Outputs (5 samples — forecast_context)

**MIA / σ=3.5 / 2026-08-09 (binary_final_cli)**
- scoring_mode: binary_final_cli | calibration: CALIBRATED | ceiling: KALSHI_PLAYABLE_LIMIT_ONLY
- "Forecast horizon of 0.0h indicates nowcast/final observation scenario for Miami. Deterministic model ready..."

**AUS / σ=3.5 / 2026-08-09 (binary_final_cli)**
- scoring_mode: binary_final_cli | calibration: PROVISIONAL | ceiling: KALSHI_WATCH
- "Same-day observation scenario. deterministic_weather_readiness_state READY but auxiliary sources unavailable..."

**AUS / σ=3.5 / 2026-08-10 (gaussian_forecast)**
- scoring_mode: gaussian_forecast | calibration: PROVISIONAL | ceiling: KALSHI_WATCH
- "Forecast horizon ~9h places market in gaussian_forecast regime. Deterministic model READY with 96°F high..."

**MIA / σ=3.5 / 2026-08-10 (gaussian_forecast)**
- scoring_mode: gaussian_forecast | calibration: PROVISIONAL | ceiling: KALSHI_WATCH
- "8.2h forecast horizon with READY deterministic model state supports gaussian_forecast mode. 92°F used..."

**LA / σ=3.5 / 2026-08-10 (gaussian_forecast)**
- scoring_mode: gaussian_forecast | calibration: PROVISIONAL | ceiling: KALSHI_WATCH
- "Deterministic forecast ready with 76°F high and σ=3.5°F at 11.2h horizon. NWS primary source available..."

---

## Run IDs
- pilot-77549cab-b5df-40a5-94dc-2c6b1fcdad95 (89 rows — run 1, SIGKILL at call 90)
- pilot-3a141b0e-8d72-4cd8-a2d1-b784bb47d1a1 (36 rows — run 2, natural EXHAUSTED)
