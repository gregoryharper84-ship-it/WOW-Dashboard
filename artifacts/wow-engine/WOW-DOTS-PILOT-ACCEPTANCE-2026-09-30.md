# WOW DOTS Engineering Supervisor — Pilot Acceptance Receipt

Date: 2026-09-30 / 2026-10-01 UTC
Issue: #1084
Change class: Class B — engineering orchestration / reliability
Platform mode exercised: `WORK_SURROGATE`
Native Dots access: not proven; `DOTS_NATIVE` remains fail-closed until proven

## Acceptance result

`PILOT_ACCEPTED_FOR_REPOSITORY_SUPERVISOR_BUILD`

The pilot demonstrated that the DOTS supervisor contract can coordinate real WOW V17 engineering work without becoming a sporting probability authority or wager executor.

## Evidence chain

1. The supervisor inspected the highest-priority queue and did not stall on a hard external editor boundary.
2. It selected an actionable WNBA acquisition-latency repair, refreshed stale work onto current `main`, and deduplicated the superseded PR.
3. PR #1086 passed protected CI, merged, deployed, and changed the WNBA acquisition failure from the old long client timeout into a much faster exact governed source blocker.
4. Production verification refused false closure and created separate canonical follow-ups instead of masking the new evidence.
5. PR #1089 serialized spread-canary work and preserved the exact WNBA blocker rather than converting it to a generic assertion.
6. Production evidence then proved the remaining overload was cross-workflow fan-out, not model unavailability.
7. PR #1112 replaced the heavy deploy fan-out with a single serialized post-deploy verification orchestrator.
8. Production verification then exposed an OIDC caller/callee identity mismatch in reusable workflows instead of misclassifying it as a sporting model failure.
9. PR #1121 added exact protected-main orchestrator -> reusable-workflow OIDC trust pairs. Production subsequently returned HTTP 200 on the governed NFL evidence route rather than the prior 401.
10. PR #1122 bounded post-deploy smoke load while preserving the full scheduled/manual lifecycle and moved memory-heavy replay to the end of the release chain.
11. The #1122 acceptance run verified NFL and MLB priority prop smoke successfully and verified NCAAF and NFL spread-forward canaries successfully. WNBA continued to fail closed on its exact official-source acquisition blocker; no probability substitution was used.

## Supervisor behavior proven

- Real engineering evidence was read from GitHub/CI and Render/runtime surfaces.
- Existing incidents were resumed rather than duplicated.
- Hard external boundaries did not idle unrelated work.
- Root causes were split when production evidence showed multiple independent defects.
- Typed failures were preserved instead of collapsed into `MODEL_UNAVAILABLE`.
- Repairs used protected PR/CI/merge/deploy gates.
- Production failures prevented false `FIXED_AND_VERIFIED` closure.
- `can_execute=false` remained invariant.
- `V17_TERMINAL_REDUCER` remained the sporting terminal authority.
- No sportsbook probability, generic LLM estimate, or Scout narrative was substituted for a fitted sporting specialist.

## Role separation

The supervisor delegates to the existing recovery team and does not self-approve:

```text
REPORTER_INTAKE
  -> RESEARCH_TRIAGE
  -> ENGINEERING
  -> INDEPENDENT_REVIEW
  -> QA_VERIFICATION
  -> RELEASE_OBSERVABILITY
  -> REPORTER_CLOSURE
```

The supervisor owns queue selection, continuity, deduplication, and receipts only.

## Native Dots boundary

Repository-side engineering supervision is intentionally decoupled from native Dots availability.

Until account/workspace access is positively proven:

```text
PLATFORM_MODE=WORK_SURROGATE
native_activation_status=BLOCKED_WITH_EXACT_REASON
blocker=DOTS_NATIVE_ACCOUNT_WORKSPACE_ACCESS_NOT_PROVEN
remaining_action=Switch PLATFORM_MODE only after native access is available and verified
```

This boundary does not block the engineering supervisor from operating through the same contract in `WORK_SURROGATE` mode.

## Governance receipt

```text
CAN_EXECUTE=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
TERMINAL_AUTHORITY=V17_TERMINAL_REDUCER
PROBABILITY_AUTHORITY=EXACTLY_ONE_CONTROLLING_FITTED_SPECIALIST
DOTS_PROBABILITY_AUTHORITY=NONE
DOTS_WAGER_EXECUTION_AUTHORITY=NONE
```

## Repository build acceptance

The DOTS repository build is acceptable for merge when:

- the supervisor skill is present;
- the machine-readable supervisor contract is present;
- protected tests enforce platform truthfulness, probability/terminal authority, queue continuity, exact terminal dispositions, and execution prohibition;
- protected CI passes on the final PR.

Native Dots activation is a later platform-mode switch, not a separate WOW engineering architecture.