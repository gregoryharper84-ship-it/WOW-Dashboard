# Production acceptance — PM-2026-09-14-001 secondary verify (2026-11-10)

`can_execute=false` · `V17_TERMINAL_REDUCER` · no paid market retries

## Live runtime

| Probe | Result |
| --- | --- |
| Web service | `srv-da7sa9gu01pc73brt80g` (`wow-governed-probability-engine`) |
| Worker | `srv-dapkbc0473hc73c1978g` |
| Live deploy (web+worker) | `dep-dbpin52vcj2c73d9c1g0` / worker sibling `dep-dbpin52vcj2c73d9c3b0` on SHA `e7384b9d2fed3256681b25715516dff2e97d3233` (#2557) |
| Safety deploy ancestor (deactivated, still in live ancestry) | `dep-dbphku67bikc73dkl5rg` SHA `98a56157a5916c6d25ee1c51bab3d183448e23c8` (#2554 availability); compare `98a56157…e7384b9d` = `behind_by=0`, `ahead_by=6` |
| Protected `main` vs live | `main` includes merged #2552 (`e82af6a9…`) and is ahead of the live SHA; Render has not yet redeployed that tip — lag does not remove the safety ancestor from what is serving |
| `/health/live` | HTTP 200 `{status:ok, can_execute:false}` |
| `/governance` | `can_execute=false`, `global_terminal_authority=V17_TERMINAL_REDUCER`, `deployment_gates.status=PASS` |
| `wow_rapid.py verify-production` | `PRODUCTION_READ_ONLY_VERIFIED` |

## Immutable predictions / reducer receipts

- `wow_predictions`: 4709 rows; newest `2026-11-09T15:38:05Z` (predates safety deploy window).
- `wow_nfl_event_predictions`: 1741 rows; `can_execute` never true; all rows currently stamp `model_probability_publishable=true` while champion publication remains held — exact #2512 false-green provenance defect still present in stored history.
- Newest Daily async run: `v17-daily-async-92c5c484-...` completed `2026-11-09T15:35:36Z` with balanced reconciliation (`12` in / `1` completed / `11` rejected). No post-safety async submission or Daily detail row was observed in `wow_v17_daily_async_runs` / `wow_v17_daily_run_row_detail`; the status of any synchronous Daily endpoint response is **unverified**.
- Newest Daily row-detail cohort carries `terminal_reduction` on every sampled row with `terminal_upgraded_from_rejection=false` and `can_execute=false`.

## Secondary item disposition (no paid retries)

| Item | Status | Evidence |
| --- | --- | --- |
| `1IP_CAPABILITY_MANIFEST_DRIFT` | **FIXED_VERIFIED** | Live `/v17/governance-detail` declares `MLB / 1ST_INNING_PITCHES_THROWN` as `SUPPORTED_HOLD_ONLY`, `route_active=true`, `declared_skill_status=TEST_ONLY`, `publication_allowed=false`, `exact_line_support_policy=EXACT_CERTIFIED_LINES_ONLY_REJECT_OOD`. Strikeouts remain `CERTIFIED_PRODUCTION`. |
| `INFRASTRUCTURE_BLOCKED_SEMANTICS_SUSPECT` | **FIXED_VERIFIED** | Unpaid decision-level recount under live SHA `e7384b9d2fed3256681b25715516dff2e97d3233`: **806** PROPS direction decisions / **422** rows / **55** runs where the same rejected decision carries `verdict_class=MODEL_REJECTED`, `model_evaluated=true`, `infrastructure_blocked=false`, and non-empty `concurrent_infrastructure_blockers`. Window `2026-09-15T15:28:06Z` → `2026-11-09T15:35:34Z`. Structured query (project `wow-engine-validation` / `iczfhsmjrrafhvcpmqhr`): expand `detail->result->outcomes[*]`, read markers from `payload.probability_qualification` (fallback `payload`), require all four on that decision. Sanitized receipt from `v17-daily-a2b7f50e-5ebd-4cab-a6c9-20e39edea2cc` @ `2026-11-09T15:35:34Z`: `verdict_class=MODEL_REJECTED`, `terminal_label=NO_LOW_PROBABILITY`, `model_evaluated=true`, `infrastructure_blocked=false`, concurrent `PAYOUT_UNRESOLVED` (no player/odds/probability values retained). |
| `ZERO_ROW_REASON_MISCLASSIFIED` | **DEPLOYED_NOT_OBSERVED** | Zero production Daily runs with `zero_row_reason=REQUESTED_ROW_LIMIT_ZERO`. Unit acceptance suite still green (`48` passed including zero-row typing). A future observational Daily must also show `lane_reconciliation.PROPS.canonicalized_count > 0` before this item can close — a bare `max_props=0` response without that candidate condition is not sufficient. |
| `SCORING_ATTEMPTED_SEMANTICS_AMBIGUOUS` | **DEPLOYED_NOT_OBSERVED** | Compact Daily row-detail persistence does not currently store attempt fields (0 matching rows). Unit suite green via `test_receipt_semantics_split.py`. Close only with a real invoked Action receipt on a selected PROPS row where `action_invocation_attempted=true`, `specialist_scoring_attempted=false`, **and** host-alias `scoring_attempted=true` — the original defect was `scoring_attempted=false` after a real Action call; both-true or a missing alias does not prove the split; `max_props=0` cannot persist these fields. |

Regression suite re-run for secondary contracts (local `.venv`):  
`test_receipt_semantics_split.py`, `test_daily_response_contract_acceptance.py`, `test_prop_capability_manifest.py`, `test_daily_terminal_reduction.py` → **48 passed**.

## Parallel lane status captured during this verification

### NFL

- Forward grades: **94 / 100** required (`INSUFFICIENT_FORWARD_EVIDENCE`, `DO_NOT_CERTIFY_YET`, blocker `FORWARD_GRADED_N_94_LT_100`).
- #2552 (aged schedule recheck) **merged** to protected `main` at `e82af6a9…` (not yet the live Render tip).
- #2512 (publishability provenance) **merged** at `2026-11-10T16:01:53Z` (`d3260b09…`) — not yet on the live Render tip; stored history still carries pre-merge publishability stamps.
- Legitimate forward grading continues via `/internal/v17/nfl-forward-shadow` (OIDC). This session could not invoke that path (no workflow dispatch / OIDC from the agent).

### NBA / NHL model capability (not infrastructure-only)

- Live governance: NBA `MODEL_UNAVAILABLE` / unregistered; NHL registered but `model_artifact_present=false` / `REGISTERED_NOT_READY`.
- Persistence: NBA/WNBA fitted artifact + calibrator rows exist (`promoted=false`, `active=false` for all 114 NBA model artifacts). Certification table empty. Confirms need for current-pregame hydration + publication bridge (#2472 / #2560 family) rather than more plumbing.
- Restorative PRs: #2509 NHL fitted specialist and #2560 NBA restorative rebase remain open for governed review/merge.

## Independent re-verify (2026-11-10T14:55Z, unpaid)

| Probe | Result |
| --- | --- |
| Live web deploy | `dep-dbpin52vcj2c73d9c1g0` SHA `e7384b9d2fed3256681b25715516dff2e97d3233` status `live` |
| `/health/live` | HTTP 200 `{status:ok, can_execute:false}` |
| `/governance` | `can_execute=false`, `V17_TERMINAL_REDUCER`, `deployment_gates.status=PASS`, `deployed_sha=e7384b9d…` |
| `wow_predictions` | 4709 rows; newest still `2026-11-09T15:38:05Z` |
| `wow_nfl_event_predictions` | 1741 rows; `can_execute` never true; all 1741 still `model_probability_publishable=true` (#2512 residual in stored history) |
| Newest Daily | `v17-daily-async-92c5c484-…` completed `2026-11-09T15:35:36Z` (still pre-safety); **0** post-safety async/detail rows observed; sync Daily status unverified |
| NFL forward health | `graded_n=94` / `minimum_forward_required=100`; `INSUFFICIENT_FORWARD_EVIDENCE`; `DO_NOT_CERTIFY_YET`; blocker `FORWARD_GRADED_N_94_LT_100` |
| NBA / NHL live | NBA `MODEL_UNAVAILABLE` / unregistered; NHL `REGISTERED_NOT_READY` (`model_artifact_present=false`) |
| NFL forward Action | Legitimate grading is `workflow_dispatch` / daily cron `wow-v17-nfl-forward-shadow` only (OIDC). Agent cannot mint OIDC or dispatch that workflow from this session. |

## Independent re-verify (2026-11-10T15:27Z–15:35Z, unpaid, separate principal)

Verdict: `SAFETY_MILESTONE_HELD_AND_PM_STILL_PENDING_VERIFY`. Evidence packet: cloud-agent artifact `prod-acceptance-independent-verify.md`.

| Probe | Result |
| --- | --- |
| Safety ancestry | Live SHA contains deactivated `#2554` safety commit `98a56157…` as ancestor (`behind_by=0`) |
| Post-safety Daily / prediction rows | Still **0** after safety finish; newest Daily remains `2026-11-09T15:35:36Z` |
| Post-safety Action receipts | See owner delta below — earlier three-receipt count is superseded |
| Attempt / zero-row fields in Daily detail | Across 28180 row-detail docs: 0 of `REQUESTED_ROW_LIMIT_ZERO`, `action_invocation_attempted`, `specialist_scoring_attempted`, `scoring_attempted` |
| Exact-head QA on this PR | Pre-`Incident:` body failed `QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS`. Body now carries exactly one `Incident: #2020` (parallel-restoration epic; not a PM-specific issue). A later QA pass clears PR identity wiring only — it does **not** close the postmortem |

## Owner read-only post-safety delta (2026-11-10, PR comment)

Mechanism under test: a green OIDC Daily workflow **before** the safety boundary cannot prove post-safety reducer/host residuals.

| Probe | Result |
| --- | --- |
| Live web / worker | Same live SHA `e7384b9d…` (`dep-dbpin52vcj2c73d9c1g0` / `dep-dbpin52vcj2c73d9c3b0`, finished 14:36 UTC); prior availability `98a56157…` at 13:23 UTC |
| Pre-safety Daily workflow | Scheduled `wow-v17-daily-snapshot` run `42801795794` at **10:45 UTC** succeeded OIDC + full-slate snapshot — **pre-safety**, not closure proof |
| Persisted Daily after safety | Latest `wow_v17_daily_async_runs.submitted_at` = `2026-11-09T15:33:38Z`; latest `wow_v17_daily_run_row_detail.captured_at` = `2026-11-10T10:46:10Z`; **0** detail rows at/after `2026-11-10T13:23:22Z` |
| Post-safety Action receipts | **Four** `SELF_ACCEPTANCE` `/score-prop` HTTP 422s (`13:23:31`, `13:23:34`, `14:36:36`, `14:36:40` UTC), all `PROP_EVIDENCE_SNAPSHOT_NOT_FOUND`, `can_execute=false` — startup probes only; not three-field scoring-attempted acceptance |
| Residual exercise caveat | Scheduled `wow-v17-daily-snapshot` requests only `lanes: ["MONEYLINE"]`, so it cannot produce either required PROPS residual (`ZERO_ROW_REASON_MISCLASSIFIED` / three-field Action split). Keep ~17:00 as a separate post-safety Daily observation; residual closure needs a permitted unpaid PROPS/Action path, not snapshot green |
| Next observation window | Existing OIDC scheduled `wow-v17-daily-snapshot` ~17:00 UTC calls `/internal/v17/daily-snapshot` synchronously — verify the workflow-logged `run_id` and any matching detail rows; do **not** require an async-run row. Residual PROPS observations remain a separate unpaid Action path |

## Independent re-verify (2026-11-10T16:05Z, unpaid)

| Probe | Result |
| --- | --- |
| Live web deploy | Still `dep-dbpin52vcj2c73d9c1g0` SHA `e7384b9d2fed3256681b25715516dff2e97d3233` status `live` |
| `/health/live` | HTTP 200 `{status:ok, can_execute:false}` |
| `/governance` | `can_execute=false`, `V17_TERMINAL_REDUCER`, `deployment_gates.status=PASS`, `deployed_sha=e7384b9d…` |
| Live sport readiness | NFL `model_capability_ready=true` / `CANDIDATE_REGISTERED_UNCERTIFIED` / `can_execute=false`; NBA `MODEL_UNAVAILABLE`; NHL `REGISTERED_NOT_READY` (`model_artifact_present=false`) |
| `wow_v17_daily_async_runs` | newest `submitted_at=2026-11-09T15:33:38Z`; **0** rows at/after safety `2026-11-10T13:23:22Z` |
| `wow_v17_daily_run_row_detail` | newest `captured_at=2026-11-10T10:46:10Z`; **0** post-safety |
| `wow_predictions` | newest `created_at=2026-11-09T15:38:05Z`; **0** post-safety |
| Action receipts post-safety | Still exactly **four** `SELF_ACCEPTANCE` `/score-prop` HTTP 422 `PROP_EVIDENCE_SNAPSHOT_NOT_FOUND` (`can_execute=false`) — no new receipts since 14:36 UTC |
| NFL forward health | Still `graded_n=94` / `minimum_forward_required=100`; `INSUFFICIENT_FORWARD_EVIDENCE`; `DO_NOT_CERTIFY_YET`; blocker `FORWARD_GRADED_N_94_LT_100` (row `created_at=2026-11-09T22:36:02Z`) |
| Parallel PR delta | `#2512` **merged** at `2026-11-10T16:01:53Z` (`d3260b09…`) — not yet on the live Render tip; stored `wow_nfl_event_predictions` history still carries pre-merge publishability stamps |
| Next observation | ~17:00 sync snapshot: prove post-safety MONEYLINE via logged `run_id` (+ detail rows if present); async-run absence alone is not a failure. PROPS residuals still need a separate unpaid Action path |

## Closure gate

`PM-2026-09-14-001` remains `DEPLOYED_PENDING_VERIFY` until **both** remaining secondary observations are recorded (`ZERO_ROW_REASON_MISCLASSIFIED` and `SCORING_ATTEMPTED_SEMANTICS_AMBIGUOUS`). A Reporter-scoped residual, if ever filed, is recorded as **unverified** and does **not** authorize a ledger status change. Safety/availability deployment is independently real; model certification / pick production is not restored. Do not paid-retry a Daily to manufacture residuals. Do not treat a future `#2558` QA PASS as `FIXED_VERIFIED`.

```text
can_execute = false
terminal_authority = V17_TERMINAL_REDUCER
post_safety_async_or_detail_rows = NOT_OBSERVED
post_safety_sync_daily_status = UNVERIFIED
post_safety_scoring_attempted_observation = NOT_OBSERVED
ledger_state = DEPLOYED_PENDING_VERIFY
```
