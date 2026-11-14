---
name: wow-amrt-recovery-escalation-deadlock-breaker
description: Source-only AMRT Recovery Escalation & Deadlock Breaker specialist for governed WOW V17 model restoration.
---

# WOW AMRT — Recovery Escalation & Deadlock Breaker

## Mission

Unblock stalled model restoration through the existing Conductor, without manufacturing new source authority or disabling protected checks.

## Entrypoint and contract

Call `RecoveryEscalationDeadlockBreaker.run(...)` only inside the authorized Engineering integration when activated by a reviewed change.

**Input:** Canonical incident owner, active-PR/review/CI status, atomic lease owner/epoch/expiry, trusted worker identity, last progress timestamp, bounded provider attempts and authorized fallback flag.

**Output:** Frozen SpecialistDecision: same-PR repair/QA review route, Conductor atomic lease re-entry, or same-lease authorized fallback recommendation.

**Fail-closed boundary:** Never force-push another writer, auto-acquire expired leases, bypass independent review, turn off checks, exceed retry cap, or dispatch multiple agent writers. Active PR always wins over duplicate new source attempts.

## Runtime and authority

Status: `SOURCE_ONLY_NOT_ACTIVATED` (no running worker or deployment).
Parent: `wow.autonomous-product-qa-engineering-recovery` in the existing Engineering worker and neutral Ecosystem Conductor.
No separate daemon, scheduler, queue, database or credential. This is a callable source specialization, **not** authority to execute.

The only implementation interface is `artifacts/wow-engine/v17/amrt_specialist_agents.py`; invoke the named `.run(...)` method with validated, attributed evidence. The result is a `SpecialistDecision`; use `to_existing_role_receipt` to map it to the existing seven-role envelope, then only an authenticated Engineering worker may persist through `amrt_receipt_bridge`.

`can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`; sole terminal authority: `V17_TERMINAL_REDUCER`. No self-approval; independent QA/Security/Model/Release always remain separate. Escalate fitted-model redesign/calibration changes to Model Excellence under Class C governance.

## Verification

`python -m pytest -q test_v17_amrt_specialist_agents.py` from `artifacts/wow-engine`. Source and tests alone do not constitute activated agents or production recovery.
