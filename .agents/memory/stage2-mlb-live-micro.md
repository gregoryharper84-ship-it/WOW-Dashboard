---
name: Stage 2 MLB live micro-market workstreams
description: Five reviewer-mandated workstreams for MLB live micro-market analysis and hard structural gate enforcement. Also covers the gunicorn post_fork schema_ready / settlement worker fix.
---

## Five Workstreams

### WS1 — Live micro-market module + endpoint
- `gate_engine/mlb_live_micro_market.py` — `analyze()` accepts 14 live game-state fields; returns opportunity_distribution, scoring_event_distribution, pitcher_k_distribution, raw_probability, calibrated_lower_bound, primary_failure_path, terminal_label.
- `POST /api/wow/mlb/live-micro/analyze` added to app.py (requires API key). Validates all 14 required fields; hitter_fantasy_score market types get a secondary `validate_market_support()` check before running.
- `can_execute = False`, `EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"` are module-level constants in every new module.

### WS2 — Hard structural gates (card_finalizer.py)
- `gate_engine/card_finalizer.py` — `run_hard_gates(rows)` runs 4 permanent gates unconditionally after `js_style_conversion.run_slip()`:
  1. `MAX_SAME_EVENT_LEGS = 2` — permanent, not freeze-only (replaces the old freeze-only check in slip_structure.py)
  2. `MAX_LIVE_MICRO_LEGS_PER_EVENT = 1`
  3. `REJECT_ALL_SAME_DIRECTION_CONCENTRATION` — blocks all-MORE or all-LESS cards with ≥3 legs
  4. `REQUIRE_LIVE_STATE_FOR_LIVE_MARKETS`
- `finalize_card(rows)` — weakest-leg finalizer runs after `_derive_four_lanes`; removes weakest leg when gap > 0.05; `SHRINK_CARD_WHEN_NO_REPLACEMENT = True` is unconditional.
- Both results attached to `run_pipeline()` return dict as `card_hard_gate_report` and `card_finalizer_report`.

### WS3 — Hitter fantasy score distribution
- `gate_engine/hitter_fantasy_score.py` — PrizePicks Fantasy Score (Baseball) scoring: Single=3, Double=6, Triple=9, HR=12, Run=3, RBI=3, SB=6, Walk=2 pts.
- `compute_fantasy_score_distribution(pa_remaining, rates, scoring)` — discrete event tree over PA, normal CDF P(FS >= threshold), std/variance.
- `compute_line_probability(line, direction, pa_remaining, rates)` — returns P_MORE, P_LESS, expected_fs.
- `validate_market_support(market_type)` — fails closed (returns supported=False) for any market not explicitly supported; callers must return REJECT_DATA_QUALITY not a fabricated probability.

### WS4 — Postmortem ledger new fields
- 12 new columns added via `ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS …` in `_ensure_llp_postmortem_schema`:
  market_phase, pregame_or_live, predicted_probability, calibrated_lower_bound, live_state_timestamp, same_event_count, directional_concentration, weakest_leg_rank, miss_margin, failure_category, process_pass, patch_would_reject.

### WS5 — Regression tests
- `gate_engine/tests/test_live_micro_regression.py` — 25 tests, all passing.
- 7 test classes: Pregame eligibility, SameEvent gate, HitterFantasyScore market support, Melton pitch-count cushion-risk, WeakestLeg finalizer, Directional concentration, LiveMicro module wiring.

## Key invariants
- `slip_structure.run_slip()` — same-event gate now runs OUTSIDE freeze block (permanent). `card_finalizer` enforces the same limit redundantly for defense-in-depth.
- Cushion-risk test at pitch_count=35 must use `inning=1, outs=0` (full 9 scope-outs) to get LOW; at `inning=4, outs=1` only 2 scope-outs remain → mean K ≈ 0.48 → HIGH is correct.
- All hitter fantasy score unsupported markets MUST fail closed via `validate_market_support()` before any probability calculation.

## Post-fork gunicorn fix (schema_ready: false / ticks: 0)

### Root causes
- `ensure_all_tables()` runs in the master's warmup daemon thread. Workers are forked before it completes → `_TABLES_READY = False` in every worker.
- `start_settlement_worker()` runs at module level in master. Workers inherit `_WORKER_STARTED = True` (the flag) but the daemon thread is dead. Health showed `started: true, ticks: 0` — a lie.

### Fixes applied
1. **`gunicorn_conf.py` post_fork hook** — three new blocks after the existing lock re-init:
   - Resets `llp_stage2_tables._TABLES_READY = False` and `_TABLES_LOCK = threading.Lock()`, then spawns a background thread calling `ensure_all_tables()` in the worker.
   - Resets `settlement_worker._WORKER_STARTED = False`, `_WORKER_LOCK = threading.Lock()`, zeroes all stat counters, then calls `start_settlement_worker()` — so each worker gets a real thread.
2. **`llp_stage2_tables.get_stage2_schema_health()`** — now lazily calls `ensure_all_tables()` before reading `_TABLES_READY`. Any health-endpoint hit self-heals even if the post_fork thread hasn't completed yet.
3. **`ensure_all_tables()`** — `except Exception: pass` replaced by `except Exception as exc: _TABLES_LAST_ERROR = str(exc)`. `get_stage2_schema_health()` now returns `last_error` so failures are visible.

### Verification
After restart: `/wow/stage2/health` returned `schema_ready: true`, `ticks: 1`, `started: true` in the dev server. Production gunicorn will now also pass because each forked worker bootstraps itself.

## GPT governance hash prohibition
- The stale hardcoded hash in the Custom GPT Instructions causes `RUN_INVALID_GOVERNANCE_MISMATCH` on every run.
- **Fix (user must do on OpenAI side):** Remove any hardcoded `Expected hash: ...` line from the Instructions. Use only the dynamic `getWowGovernanceStatus → governance_hash` flow.
- `gpt-instructions-stage2-block.md` now contains a "⚠ NEVER hardcode a governance hash" section with the correct pattern and the MISMATCH error remedy.

**Why:** Hardcoded hashes go stale on every backend patch. Dynamic lookup is the only sustainable pattern.

**How to apply:** Any new module that touches live market data must include can_execute=False + EXECUTION_RULE constants. Any new structural gate must go into card_finalizer.run_hard_gates(), not only into slip_structure.
