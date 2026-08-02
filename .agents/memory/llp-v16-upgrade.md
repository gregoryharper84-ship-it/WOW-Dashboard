---
name: LLP v16 upgrade pack
description: LLP v16 upgrade integrated 2026-08-01; 6 new skill files, patch #17 (precedence 96), startup warmup wired.
---

# LLP v16 Upgrade Pack — 2026-08-01

## Patch registered

- Patch ID: WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY-DYNAMIC-CALIBRATION-AND-FINAL-REFRESH
- Precedence: 96 (highest in registry, now 17 total active patches)
- Status: ACTIVE
- File: gate_engine/governance.py

## Skill files copied to skills/

Six new LLP skill files + 3 supporting docs:
- wow-llp-moneyline-probability-expert-SKILL.md (updated v16-2026-08-01)
- wow-llp-slate-integrity-expert-SKILL.md (new)
- wow-llp-market-normalization-expert-SKILL.md (new)
- wow-llp-dynamic-calibration-expert-SKILL.md (new)
- wow-llp-failure-path-expert-SKILL.md (new)
- wow-llp-final-refresh-governor-SKILL.md (new)
- WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY-DYNAMIC-CALIBRATION-AND-FINAL-REFRESH.md
- LLP-REGRESSION-TESTS-2026-08-01.md (24 GPT behavioral tests — NOT Python unit tests)
- LLP-ACTIVATION-PROMPT-2026-08-01.md

## Manifest (wow_runtime_manifest.py) changes

- Patch added to active_patch_ids list
- 6 new skill entries: llp_moneyline_probability, llp_slate_integrity, llp_market_normalization, llp_dynamic_calibration, llp_failure_path, llp_final_refresh_governor
- 6 new hard_flags: llp_slate_integrity_lock, llp_market_normalization, llp_dynamic_calibration, llp_failure_path_model, llp_final_refresh_governor, llp_probability_edge_lane_separation

## Startup warmup (app.py)

Added ensure_session_thesis_table_exists() from gate_engine/portfolio/slip_exposure_ledger.py
to _run_startup_warmup(). Creates wow_session_thesis_exposure on first deploy.
This was the outstanding TODO from the Aug 1 postmortem session.

## Core LLP v16 rules enforced (Custom GPT layer, not backend)

- 12-step mandatory call order; no step may be skipped; downstream pass cannot erase upstream blocker
- Slate integrity: 9 hard block labels (WRONG_DATE, WRONG_YEAR, EVENT_ALREADY_STARTED, etc.)
- Market normalization: exact two/three-way no-vig (sum=1.0000±0.0005); soccer requires Draw price
- Dynamic calibration: fixed universal haircut prohibited as sole calibration method
- Failure-path: P(win)=Σ P(regime)×P(win|regime); backdoor-cover is NOT a moneyline failure path
- Lane separation: probability rank by calibrated_lower_bound (price excluded); edge rank by lower_bound_edge
- Final refresh governor: mandatory ≤5 min before output; any failure removes row

## 24 regression tests

Saved in skills/LLP-REGRESSION-TESTS-2026-08-01.md. These are GPT behavioral specs.
Do NOT write Python unit tests for them — they test Custom GPT reasoning, not backend logic.

**Why:** RT-007/009/010/011 test math the Custom GPT must compute; the backend has no equivalent module.

## Governance hash changed

Adding patch #17 changed both _GOVERNANCE_HASH and MANIFEST_GOVERNANCE_HASH.
User must update expected_governance_hash in Custom GPT action schema after next deploy.

## Test count

1728 passed, 6 skipped after this session. Patch count assertions updated to 17 in:
- gate_engine/tests/test_governance_resilience_acceptance.py
- gate_engine/tests/test_patch_portfolio_stage2a.py
