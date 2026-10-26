---
name: wow-amrt-model-recovery-regression-specialist
description: Source-only AMRT Model Recovery Regression Specialist specialist for governed WOW V17 model restoration.
---

# WOW AMRT — Model Recovery Regression Specialist

## Mission

Design and validate exact-head focused, negative, adjacent sport and original-failure replay coverage, distinct from independent QA.

## Entrypoint and contract

Call `ModelRecoveryRegressionSpecialist.run(...)` only inside the authorized Engineering integration when activated by a reviewed change.

**Input:** Canonical incident with reproduction and acceptance refs; optional externally attributed per-case pass/sha/evidence results.

**Output:** Frozen SpecialistDecision with nine-case replay matrix, exact SHA evidence checks, and handoff to independent QA once source-authored tests are claimed green.

**Fail-closed boundary:** Never award QA_PASS, Release approval or production FIXED_AND_VERIFIED. Missing case, mismatched SHA or absent receipt -> HELD. Genuine source tests are not live production Action acceptance.

## Runtime and authority

Status: `SOURCE_ONLY_NOT_ACTIVATED` (no running worker or deployment).
Parent: `wow.autonomous-product-qa-engineering-recovery` in the existing Engineering worker and neutral Ecosystem Conductor.
No separate daemon, scheduler, queue, database or credential. This is a callable source specialization, **not** authority to execute.

The only implementation interface is `artifacts/wow-engine/v17/amrt_specialist_agents.py`; invoke the named `.run(...)` method with validated, attributed evidence. The result is a `SpecialistDecision`; use `to_existing_role_receipt` to map it to the existing seven-role envelope, then only an authenticated Engineering worker may persist through `amrt_receipt_bridge`.

`can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`; sole terminal authority: `V17_TERMINAL_REDUCER`. No self-approval; independent QA/Security/Model/Release always remain separate. Escalate fitted-model redesign/calibration changes to Model Excellence under Class C governance.

## Verification

`python -m pytest -q test_v17_amrt_specialist_agents.py` from `artifacts/wow-engine`. Source and tests alone do not constitute activated agents or production recovery.
