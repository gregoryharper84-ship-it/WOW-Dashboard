# Production acceptance — PM-2026-09-14-001 secondary verify (2026-11-10)

`can_execute=false` · `V17_TERMINAL_REDUCER` · no paid market retries

## Live runtime

| Probe | Result |
| --- | --- |
| Web service | `srv-da7sa9gu01pc73brt80g` (`wow-governed-probability-engine`) |
| Worker | `srv-dapkbc0473hc73c1978g` |
| Live deploy (web+worker) | `dep-dbpin52vcj2c73d9c1g0` / sibling worker deploy on SHA `e7384b9d2fed3256681b25715516dff2e97d3233` (later tip `2f535a248` after #2553) |
| Safety deploy still observed earlier same day | `dep-dbphku67bikc73dkl5rg` SHA `98a56157a5916c6d25ee1c51bab3d183448e23c8` (#2554 availability) |
| `/health/live` | HTTP 200 `{status:ok, can_execute:false}` |
| `/governance` | `can_execute=false`, `global_terminal_authority=V17_TERMINAL_REDUCER`, `deployment_gates.status=PASS` |
| `wow_rapid.py verify-production` | `PRODUCTION_READ_ONLY_VERIFIED` |

## Immutable predictions / reducer receipts

- `wow_predictions`: 4709 rows; newest `2026-11-09T15:38:05Z` (predates safety deploy window).
- `wow_nfl_event_predictions`: 1741 rows; `can_execute` never true; all rows currently stamp `model_probability_publishable=true` while champion publication remains held — exact #2512 false-green provenance defect still present in stored history.
- Newest Daily async run: `v17-daily-async-92c5c484-...` completed `2026-11-09T15:35:36Z` with balanced reconciliation (`12` in / `1` completed / `11` rejected). No Daily result after the #2554 safety deploy — post-safety scoring run is **absent**, not failed.
- Newest Daily row-detail cohort carries `terminal_reduction` on every sampled row with `terminal_upgraded_from_rejection=false` and `can_execute=false`.

## Secondary item disposition (no paid retries)

| Item | Status | Evidence |
| --- | --- | --- |
| `1IP_CAPABILITY_MANIFEST_DRIFT` | **FIXED_VERIFIED** | Live `/v17/governance-detail` declares `MLB / 1ST_INNING_PITCHES_THROWN` as `SUPPORTED_HOLD_ONLY`, `route_active=true`, `declared_skill_status=TEST_ONLY`, `publication_allowed=false`, `exact_line_support_policy=EXACT_CERTIFIED_LINES_ONLY_REJECT_OOD`. Strikeouts remain `CERTIFIED_PRODUCTION`. |
| `INFRASTRUCTURE_BLOCKED_SEMANTICS_SUSPECT` | **FIXED_VERIFIED** | 418 production PROPS rows with `terminal_cause=MODEL_JUDGMENT` and `infrastructure_blocked=false` while concurrent market blockers (`MARKET_DATA_UNAVAILABLE`, `PAYOUT_UNRESOLVED`, `SETTLEMENT_RULE_UNRESOLVED`) remain listed separately. |
| `ZERO_ROW_REASON_MISCLASSIFIED` | **DEPLOYED_NOT_OBSERVED** | Zero production Daily runs with `zero_row_reason=REQUESTED_ROW_LIMIT_ZERO`. Unit acceptance suite still green (`48` passed including zero-row typing). |
| `SCORING_ATTEMPTED_SEMANTICS_AMBIGUOUS` | **DEPLOYED_NOT_OBSERVED** | Compact Daily row-detail persistence does not currently store `action_invocation_attempted` / `specialist_scoring_attempted` (0 matching rows). Unit receipt-split tests remain green. Needs one owner Action/receipt readback or a free `max_props=0` Daily that persists those fields. |

Regression suite re-run for secondary contracts (local `.venv`):  
`test_receipt_semantics_split.py`, `test_daily_response_contract_acceptance.py`, `test_prop_capability_manifest.py`, `test_daily_terminal_reduction.py` → **48 passed**.

## Parallel lane status captured during this verification

### NFL

- Forward grades: **94 / 100** required (`INSUFFICIENT_FORWARD_EVIDENCE`, `DO_NOT_CERTIFY_YET`, blocker `FORWARD_GRADED_N_94_LT_100`).
- Open restorative PRs refreshed onto protected main for exact-head re-check: #2552 (aged schedule recheck), #2512 (publishability provenance).
- Legitimate forward grading continues via `/internal/v17/nfl-forward-shadow` (OIDC). This session could not invoke that path (no workflow dispatch / OIDC from the agent).

### NBA / NHL model capability (not infrastructure-only)

- Live governance: NBA `MODEL_UNAVAILABLE` / unregistered; NHL registered but `model_artifact_present=false` / `REGISTERED_NOT_READY`.
- Persistence: NBA/WNBA fitted artifact + calibrator rows exist (`promoted=false`, `active=false` for all 114 NBA model artifacts). Certification table empty. Confirms need for current-pregame hydration + publication bridge (#2472 family) rather than more plumbing.
- Restorative PRs: #2509 NHL fitted specialist refreshed onto main (tests 30 passed locally); #2472 NBA remains CONFLICTING and is the canonical restorative path.

## Closure gate

`PM-2026-09-14-001` remains `DEPLOYED_PENDING_VERIFY` until the two remaining secondary observations land **or** Reporter explicitly scopes them out with a typed residual. Safety/availability deployment is independently real; model certification / pick production is not restored.

```text
can_execute = false
terminal_authority = V17_TERMINAL_REDUCER
post_safety_daily_scoring_run = ABSENT
```
