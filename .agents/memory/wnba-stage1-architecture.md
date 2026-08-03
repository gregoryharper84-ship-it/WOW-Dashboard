---
name: WNBA + Portfolio Stage 1 architecture
description: PATCH-WNBA-001 opportunity gate and PATCH-PORTFOLIO-001 cross-slip governor — files, wiring, and governance hash after Stage 1 merge.
---

## Patches added (Stage 1)
- `WOW-PATCH-WNBA-001-OPPORTUNITY-STABILITY-GATE` — precedence 90
- `WOW-PATCH-PORTFOLIO-001-CROSS-SLIP-EXPOSURE-GOVERNOR` — precedence 91

Both registered in `gate_engine/governance.py` `_PATCH_REGISTRY` (now 12 active patches total).

## Governance hashes after Stage 1
- `_GOVERNANCE_HASH` (governance.py): `9d3139add1e10603c4ca0fe889f3cae80dd3f3dc394d801d0e668652818ae431`
- `MANIFEST_GOVERNANCE_HASH` (wow_runtime_manifest.py): `af2259ac441286c92be0d22846e5386ef2d8d25f2d79a531f3dbdda0a49c94eb`

## Governance hashes after Stage 2A (current)
- `_GOVERNANCE_HASH` (governance.py): `f08bc5eb5e14b3019833fb16999c1cfa0e110a5e20fd03a34eef53850b3218b9`
- `MANIFEST_GOVERNANCE_HASH` (wow_runtime_manifest.py): `f0906aa5ba8a9cb84830c66fb10116fd3fccfbcb0a1a6bbfa659cee131c36285`
- Active patches: 13

**GPT must be updated to use the Stage 2A manifest hash.**

## New modules
- `gate_engine/wnba/opportunity_engine.py` — PATCH-WNBA-001
- `gate_engine/portfolio/cross_slip_exposure.py` — PATCH-PORTFOLIO-001 (`PortfolioExposureGovernor`)

## Pipeline wiring (pipeline.py)
1. After `status_role.run(row, ...)` → `_wnba_opp_gate.run(row, enrichment=enr)` (WNBA rows only)
   - Hard reject (WNBA_REJECT_UNSTABLE_OPPORTUNITY, WNBA_REJECT_ROTATION_VOLATILITY) → `continue`
   - Soft hold (WNBA_HOLD_ROLE_UNCERTAIN) → MODEL_QUALIFIED_HOLD ceiling, continues
2. After `ledger.check_and_register(row)` → `portfolio_governor.check_and_register(row)` (all rows, if governor injected)
   - `run_pipeline()` now accepts optional `portfolio_governor: _PortfolioGov | None` param

## app.py wiring (gate_engine_run)
- Imports: `_PortfolioExposureGovernor`, `_make_portfolio_governor`, `_wnba_opp_run`, `_wnba_opp_get_gate_status`, `_wnba_opp_log`
- Creates `_portfolio_gov = _make_portfolio_governor(session_id, conn_string)` then passes to `_ge_run_pipeline(portfolio_governor=_portfolio_gov)`

## New endpoints
- `POST /wow/wnba/opportunity-audit` — pre-screen WNBA rows for opportunity stability
- `POST /wow/session/exposure-audit` — pre-check cross-slip exposure for a candidate slip
- `/wow/patch-flags` enhanced with `wnba_opportunity_gate` + `cross_slip_governor` gate status

## New skill file
- `skills/wow-wnba-opportunity-scenario-and-exposure-governor-SKILL.md`
- Registered in `wow_runtime_manifest.py` as `wnba_opportunity_governor`

## Opportunity gate thresholds (configurable in module)
- `THRESH_OSS_GENERAL = 65` (non-PRA markets)
- `THRESH_OSS_PRA = 70` (PRA / composite)
- `THRESH_ROLE_CONF = 0.80`
- `THRESH_MIN_STAB = 60`
- `THRESH_ROT_VOLT_HARD = 80`
- `MIN_GAMES_REQUIRED = 3`

## Portfolio governor limits (configurable)
- `MAX_MKTFAMILY = 1` — one prop per (player, stat_family) per session; catches alternate lines
- `MAX_THESIS = 1` — one identical (player, stat_family, direction) per session
- Stage 1: in-memory only. Stage 2: promote to DB (session_thesis_exposure table)

## DB table (opportunity_audits)
- DDL in `opportunity_engine.py` (lazy-created)
- `log_opportunity_audit(row, session_id, research_run_id)` — call from settle endpoint

## Test file
- `gate_engine/tests/test_patch_wnba_portfolio_stage1.py` — 32 passed, 6 skipped (Stage 2 stubs)

## Pre-existing test fixes applied in same commit
- `test_patch_count_is_9` → renamed to `test_patch_count_is_12` (governance.py already had 10 patches; test was stale)
- `test_clean_candidate_passes` (llp_governance): added `model_timestamp` to `_approved_candidate()` fixture (validates_material_staleness requires it)
- `test_valid_ledger_passes` / `test_narrative_component_blocked` (prob_ledger): added Stage 2 schema fields to `_ledger()` fixture (`_validate_stage2_schema` requires them)

## Why portfolio governor is NOT WNBA-only
Spec says cross-slip applies to all sports (max_alternate_lines_same_distribution=1 is sport-agnostic). Only the opportunity gate is WNBA-specific.

## Stage 2A — DB-backed portfolio persistence (implemented)
- `gate_engine/portfolio/pg_portfolio_governor.py` — `PgPortfolioGovernor` class
- Two new tables: `wow_portfolio_dedup` (dedup sentinel + SELECT FOR UPDATE lock target), `wow_portfolio_exposure_log` (audit log)
- `make_portfolio_governor()` returns `PgPortfolioGovernor` when DATABASE_URL present, `PortfolioExposureGovernor` (in-memory) as fallback
- `gate_engine_run()` now passes `research_run_id` + `slate_date=target_date` into governor
- Rollover: slate_date filtering — prior-date rows invisible to dedup check, no purge step needed
- Fail-closed: DB error → `SESSION_LEDGER_UNAVAILABLE` blocks run
- New label: `RUN_INVALID_SESSION_ID_MISSING` (fires if session_id empty, pre-DB)
- New endpoint: `GET /wow/session/exposure-inspect?session_id=X&slate_date=Y` — read-only snapshot
- Startup warmup calls `_ensure_portfolio_tables()` to auto-create both tables
- 25 new tests (test_patch_portfolio_stage2a.py), all mocked — no live DB required

**Key: dedup sentinel key format is `mktf:{mktfamily_key}` and `thesis:{thesis_key}` — both stored in the same wow_portfolio_dedup table.**

## Direction-inclusive mktfamily_key (patch applied July 31)
`mktfamily_key = player|stat|direction` — direction IS included.
- MORE + LESS on the same player+stat → **different keys → both allowed** (opposing directions are independent research queries)
- MORE 19.5 + MORE 22.5 on same player+stat → **same key → REJECT_CROSS_SLIP_CONCENTRATION** (alternate lines, same distribution)
- REJECT_CROSS_SLIP_CONCENTRATION blocker tag now reads `alternate_line_same_direction` (was `alternate_line_or_same_distribution`)

## ODDS API key resolution (fixed July 31)
`services/odds_api.py` `_get()` now resolves: `ODDS_API_PAID_KEY` → `ODDS_API_FREE_KEY` → `ODDS_API_KEY` (legacy). Was reading only `ODDS_API_KEY` which is absent in Replit secrets → always "invalid ODDS_API_KEY". Module-level `ODDS_API_KEY` constant kept for back-compat only.

## WNBA enrichment key split (applied Aug 3 — permanent)
`opportunity_engine.py` and `l5_l10_ledger.py` both previously read `enrichment["game_log"]` but needed incompatible formats:
- `l5_l10_ledger.py` expects `game_log` = plain list of numbers (unchanged)
- `opportunity_engine.py` expects per-game dicts with MIN, stat, FGA, USG

Fix: `opportunity_engine.py` now reads `enrichment["box_score_log"]` (list of per-game dicts).
`l5_l10_ledger.py` / `pipeline.py` still use `game_log` as plain numbers — untouched.
`test_patch_wnba_portfolio_stage1.py` updated: all enrichment dicts use `"box_score_log"` key.
**Valid USG keys accepted by opportunity_engine:** `USG`, `USG%`, `usg_pct`, `usage_rate`, `Usage%` (case-sensitive).

## Stage 2B remaining work (not implemented)
- Component/shot distribution engine (`gate_engine/wnba/shot_volume_model.py`)
- Scenario survival engine (`gate_engine/scenarios/wnba_scenarios.py`)
- DB-backed cross-request portfolio persistence (`session_thesis_exposure` table)
- Settlement endpoint + calibration ledger
- Endpoints: `/wow/wnba/distribution-model`, `/wow/wnba/scenario-simulate`, `/wow/slip/full-audit`, `/wow/settle/prop`, `/wow/research-run/{id}`
