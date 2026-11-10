# External dependencies — where this principal cannot act alone

Status date: 2026-11-10T17:51:51Z (UTC).
Baseline `origin/main`: `d3260b09b7ca366c482194f8f27bdc00c0bd6e48`.
Production web LIVE deploy: `dep-dbpin52vcj2c73d9c1g0` @ `e7384b9d2fed3256681b25715516dff2e97d3233`
(`wow-governed-probability-engine` / `srv-da7sa9gu01pc73brt80g`).
Invariants: `can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`,
`terminal_authority=V17_TERMINAL_REDUCER`. This packet prepares exact next actions
for independent principals. It does **not** approve, merge, deploy, certify,
promote, invent market lines, or determine source rights.

Principal of this packet: Cursor cloud agent
`bc-b81d0581-f094-467e-a8e9-51639879315a` (External task dependencies).
This principal must not approve its own PRs and must not mutate trust-root
code that the same principal would also review.

---

## 1. Independent gates (QA App / Release Authority / SIRT)

### Hard boundary

QA App (`WOW Independent QA exact-head`, App ID 5245832), Release Authority
(`WOW Release Authority exact-head`, App ID 5245853), and SIRT acceptance must
come from **separate principals**. Engineering may prepare evidence; it may not
self-attest acceptance or merge on the strength of its own review.

### Current exact-head App status (open restorative / release PRs)

Observed 2026-11-10 via `gh pr view` check rollups (non-authoritative snapshot):

| PR | Head | Independent QA | Release Authority | Mergeable | Notes |
|---|---|---|---|---|---|
| #2560 NBA restorative rebase | `be05c789` | SUCCESS | SUCCESS | MERGEABLE | Authorized owner merge still required |
| #2558 unpaid prod secondary acceptance | `2f12c7ae` | SUCCESS | SUCCESS | MERGEABLE | Docs/acceptance only; unpaid Daily observation still open |
| #2565 release QA-policy neutralize | `ab9a51e5` | SUCCESS | SUCCESS | MERGEABLE | Trust-adjacent release admission; separate principal must merge |
| #2568 non-main QA neutralize | `9a422962` | SUCCESS | SUCCESS | MERGEABLE | Same |
| #2570 Daily evidence reconciliation | `7397b28a` | SUCCESS | SUCCESS | MERGEABLE | P0 infra |
| #2561 subagent/SIRT routing | `8786c9a6` | SUCCESS | SUCCESS | MERGEABLE | Do not treat as SIRT acceptance of product |
| #2562 NBA fit verification harness | `795db5a5` | SUCCESS | SUCCESS | MERGEABLE | Challenger/verification evidence, not promotion |
| #2566 source-rights hold docs/tests | `50ada96e` | SUCCESS | (absent in rollup) | MERGEABLE | Rights determination still external |
| #2567 graded source-rights receipt loader | `97631158` | (absent) | (absent) | MERGEABLE | Loader only; not a rights grant |
| #2571 NHL specialist restoration | `3b9b681d` | pending/re-attest | pending/re-attest | MERGEABLE | Tip moved; await fresh exact-head QA/Release |
| #2572 prop preflight per-stat family | `3014e8d3` | pending | pending | MERGEABLE | In flight |

### Prepared for independent principals

1. Do **not** merge from this principal.
2. For each PR above, independent QA/Release Apps must PASS on the **exact head
   SHA** in the table (or a newer tip that they re-attest). Prior-head PASS does
   not transfer.
3. SIRT remains a separate production/acceptance principal
   (`docs/wow/engineering/WOW_SIRT_INDEPENDENT_ASSURANCE.md`,
   cron `wow-sirt-external-sentinel`). Green CI ≠ SIRT acceptance.
4. Trust-root / release-admission paths (`#2565`, `#2568`, deploy workflow,
   branch protection, App IDs) must be reviewed by a principal that did not
   author the change.

### Smallest next external action

Owner/authorized merger picks one MERGEABLE PR with both App checks green on
exact head (recommended start: `#2560` or `#2570`) and merges through protected
main. No force-merge, no bypass.

---

## 2. Re-enabling `wow-v17-render-production-deploy`

### Hard boundary

Only an authorized repository/Render administrator may enable the workflow or
PATCH live Render writer settings. This principal prepares verification only.

### Observed state (2026-11-10)

| Fact | Value | Evidence |
|---|---|---|
| Workflow | `wow-v17-render-production-deploy` | GitHub Actions workflow id `359018729` |
| Workflow state | `disabled_manually` | `gh api .../actions/workflows/359018729` |
| Lean V4 intent | Keep API controller disabled; use Render native `checksPass` | `docs/wow/engineering/WOW_V17_LEAN_RELEASE_V4_20261103.md` |
| Web auto-deploy | `yes` / trigger `checksPass` | Render service `srv-da7sa9gu01pc73brt80g` |
| Worker auto-deploy | `yes` / trigger `checksPass` | Render service `srv-dapkbc0473hc73c1978g` |
| LIVE web SHA | `e7384b9d2fed3256681b25715516dff2e97d3233` | Render deploy `dep-dbpin52vcj2c73d9c1g0` |
| GET-only observer | `wow-v17-lean-production-readback` **active** | workflow id `406216348` |
| Journal writer gate | Native auto-deploy must be **off** before API POST is eligible | `_writer_state` in `v17/github_release_journal.py` returns `RENDER_NATIVE_AUTO_DEPLOY_WRITER_ENABLED` when auto-deploy is on |
| Credential scope | Unattested → HOLD `RELEASE_RENDER_CREDENTIAL_SCOPE_UNATTESTED` | same journal |

### Exact admin verification before any flip

**Preferred path (A) — Lean V4:** leave workflow `disabled_manually`; retain
native `checksPass` on web + worker; verify with
`wow-v17-lean-production-readback` + SIRT. Do not enable the API controller.

**Option (B) — re-enable API controller — is currently BLOCKED**, not a runnable
cutover checklist. Do not PATCH either service's auto-deploy off, and do not
enable workflow `359018729`, until every blocker below is cleared by authorized
principals. The current packet documents why B fails closed; it is not
permission to proceed.

Hard blockers that keep (B) closed today:

1. **Worker deploy path gap.**
   `.github/workflows/wow-v17-render-production-deploy.yml` sets
   `WOW_RENDER_SERVICE_ID` only to web `srv-da7sa9gu01pc73brt80g`. The API
   controller does **not** deploy worker `srv-dapkbc0473hc73c1978g`. Turning the
   worker's native `checksPass` off without a separately verified authorized
   worker writer leaves the worker without a documented deployment path. Keep
   option B blocked until an authorized worker deployment path is designed,
   reviewed, and verified (GET identity + write path) — not merely asserted in
   prose.
2. **Hard-coded credential-scope HOLD.**
   `v17/github_release_journal.py` sets
   `RENDER_CREDENTIAL_SCOPE_ATTESTED = False`. While that constant is false,
   `plan_release_admission` returns HOLD
   (`RELEASE_RENDER_CREDENTIAL_SCOPE_UNATTESTED`) even if an administrator
   believes secrets were "attested" out-of-band. Independent verbal attestation
   alone cannot clear this gate. Clearing it requires a separately reviewed
   attestation-backed admission-path change that keeps the HOLD explicit until
   admission is actually authorized — never a silent PASS. Do not bypass the
   HOLD.
3. **Controller run is not a dry run.**
   When admission later returns `POST_ONCE`, the workflow calls the Render
   deploy API for the configured service; it does **not** pause for receipt
   inspection. A true no-POST dry run is a verified preflight that never reaches
   POST (journal HOLD / writer-off / credential HOLD). Label any enabled
   controller run that can reach `POST_ONCE` as a **potential production
   deploy**, never a dry run. Never claim deploy success from a HOLD receipt.

If those blockers are later cleared under separate review, an authorized admin
would still need: Lean V4 decision recorded; web (and any authorized worker
writer) identities GET-verified; `wow-release` environment with no admin bypass
and main-only branch policy; required QA + Release App admission on exact head;
and SIRT acceptance after LIVE SHA/health match. Competing native + API writers
must never both be enabled.

### What this principal will not do

Enable the workflow, PATCH Render auto-deploy, rotate secrets, flip
`RENDER_CREDENTIAL_SCOPE_ATTESTED`, or assert `production_verified=true`.

---

## 3. Current sportsbook offers (P0-3)

### Hard boundary

Zero-new-spend. No code from this principal may invent a current platform offer.
A lawful provider feed **or** a real user-supplied line is required.

### Current code posture (already fail-closed)

- Autonomous discovery lines are `source_type=AUTONOMOUS_DISCOVERY` and
  non-executable (`v17/daily_prop_acquisition.py`).
- Historical openers cannot become current market evidence
  (`market_evidence_hardening.classify_freshness(historical=True)` →
  `current_market_evidence=False`).
- User-supplied market input is identity-only and never sporting probability
  (`v17/market_optional_qualification.py`,
  `v17/llp_rundown_market_bridge.py` → `USER_SUPPLIED_MARKET_INPUT`).
- REPORT-2026-11-09 §6: licensed/current platform offers remain blocked in this
  environment.

### Prepared intake contract (operator / provider)

Supply **one** of:

1. **Lawful provider feed** (existing The Odds API / TheRundown paths only when
   credentials and contractual rights already exist — no new spend from this
   agent), with exact event/market/line/period/timestamp receipts; or
2. **User-supplied line** via the existing Free-Core market input shape:

```json
{
  "market_family": "PROP",
  "selection": "<player or side>",
  "line": 0.0,
  "american_odds": null,
  "stat_type": "<STAT>",
  "direction": "MORE|LESS",
  "captured_at": "<RFC3339>",
  "source": "USER_SUPPLIED"
}
```

### Smallest next external action

Operator provides a real line/odds receipt **or** confirms an already-paid
provider credential is in scope. Until then, keep market/value publication
blocked while preserving any completed sporting probability upstream.

---

## 4. Source rights (§13 / independent rights determination)

### Hard boundary

Independent rights determination must come from someone other than the
implementing/research principal. Loaders and hold tests are not grants.

### Prepared evidence packages (do not treat as approved)

| Artifact / PR | Role | Not a grant of |
|---|---|---|
| `#2566` preserve source-rights holds | Regression that claimed receipts cannot clear holds | Rights approval |
| `#2567` graded source-rights receipt loader | Ports graded receipt loading for research | Rights approval |
| `docs/WOW_V17_FREE_SOURCE_INTEGRATION_20261029.md` | Free-source inventory; PropsMadness / OddsPapi boundaries | License |
| `wnba_prop_certification_replay` blocker `WNBA_SOURCE_RIGHTS_NOT_APPROVED` | Fail-closed without approval | Approval |

### Exact packet an independent rights principal should return

For each candidate source: legal basis, permitted uses (training / inference /
redistribution / storage), retention limits, identity of reviewer (not the
implementer), date, and SHA of the snapshot reviewed. Until that exists, keep
`SOURCE_RIGHTS_*` / `*_RIGHTS_NOT_APPROVED` blockers.

---

## 5. NFL certification

### Hard boundary

100 forward grades require real settled games. Moving 94 → 95 requires a real
post-final capture. Certification itself requires SIRT (and governed review),
not engineering self-certification.

### Observed forward-shadow evidence

Latest protected-main forward-shadow success
(`wow-v17-nfl-forward-shadow` run `42713206143` on SHA `4c8c1fb0…`,
2026-11-09):

```json
{
  "can_execute": false,
  "canonical_events": 108,
  "certification_recommendation": "DO_NOT_CERTIFY_YET",
  "forward_health": "INSUFFICIENT_FORWARD_EVIDENCE",
  "graded_events": 94,
  "latest_settled_gameday": "2026-11-08",
  "new_grades_persisted": 12,
  "settled_games_materialized": 420,
  "settlement_refresh_status": "COMPLETED",
  "status": "COMPLETED"
}
```

Threshold: `MIN_FORWARD_GRADED = 100` in `v17/nfl_forward_shadow.py`.
Audit baseline remains `DO_NOT_CERTIFY_YET`
(`v17/NFL-CERTIFICATION-AUDIT-2026-09-15.md`).

### Prepared next steps (external clock + SIRT)

1. Wait for additional NFL finals; OIDC path `wow-v17-nfl-forward-shadow` grades
   immutable predictions (do not mint grades).
2. Confirm `graded_events >= 100` and `certification_recommendation` transitions
   only under the real health function — never by editing thresholds.
3. Independent SIRT reviews runtime evidence before any certification label.

---

## 6. Class C model changes (NBA / NHL / props)

### Hard boundary

Challenger → historical replay → counterexample → holdout/forward → regression →
governed promotion review. This principal may build challengers and evidence; it
may not promote controlling specialists.

### Prepared / in-flight challenger & restoration evidence (not promotion)

| Lane | Prepared artifact | Stop short of |
|---|---|---|
| NBA restorative path | PR `#2560` (rebase of `#2472`) + harness `#2562` + draft schema gate `#2559` | Certified publication / DB promotion |
| NHL fitted specialist restoration | PR `#2571` (incident `#1952`) | Artifact promotion; goalie confirmation still separate |
| NFL research challengers | `#2545`, `#2548` (research only) | Production champion replacement |
| Prop families | Manifest still has `BUILD_REQUIRED` / `CANDIDATE_ONLY` lanes; preflight `#2572` | Silent promotion |

### Smallest next external action

Independent Model Excellence / governed review consumes the challenger packets
after exact-head CI. No registry flag flips from this principal.

---

## 7. Migration and goalie sources

### NBA production DB migration

- Draft gate PR `#2559` (`draft(P): NBA verifier receipt schema and fail-closed
  Render predeploy gate`) is the nearest prepared migration/predeploy fence.
- Restorative application path `#2560` is merge-ready on Apps but is not itself
  a production schema apply.
- Applying SQL to production Supabase/Render-linked DB requires the authorized
  deployment/migration path (environment credentials + release admission). This
  principal does not run production migrations.

### NHL goalie confirmation — two independent official sources

Code already fail-closes without confirmed starters:

- `v17/nhl_pregame_gamecenter_capture.py` — read-only capture; explicitly does
  **not** promote projected/roster goalies to `CONFIRMED`.
- `validate_nhl_goalie_status_handoff` — requires pinned, refreshed, confirmed
  home/away identities (`NHL_STARTING_GOALIE_NOT_CONFIRMED`,
  `NHL_GOALIE_SOURCE_PIN_MISSING`, `NHL_GOALIE_SOURCE_NOT_REFRESHED`).
- `scout_source_policy.py` NHL `goalie` tiers: `LEAGUE_OFFICIAL`,
  `TEAM_OFFICIAL`, `PRIMARY_BEAT_REPORTER` (beat is not a substitute for two
  independent **official** confirmations).

### Prepared dual-source acceptance shape (external evidence needed)

For each side (home/away), before `CONFIRMED`:

1. Source A: league official (e.g. NHL official gamecenter / league feed) with
   payload SHA, retrieval time, player id, event id.
2. Source B: team official (club confirmation) with distinct publisher identity,
   payload SHA, retrieval time, same player/event ids.
3. Both must agree on starter identity and `CONFIRMED` status; disagreement or
   missing side → hold.

No such dual-source live receipts are present in this session. Do not invent
them.

---

## Operator checklist (copy/paste)

- [ ] Separate principal: Independent QA App PASS on exact head
- [ ] Separate principal: Release Authority App PASS on exact head
- [ ] Separate principal: authorized protected merge (not the implementing agent)
- [ ] Separate principal: SIRT production acceptance when claiming FIXED_VERIFIED
  or certification
- [ ] Admin decision: keep `wow-v17-render-production-deploy` disabled (Lean V4)
  **or** complete the §2(B) writer cutover before enabling
- [ ] Lawful current-offer feed **or** real user-supplied line for P0-3 market
  verification
- [ ] Independent source-rights determination recorded for any new training /
  retention source
- [ ] NFL `graded_events >= 100` via real post-final OIDC shadow, then SIRT
- [ ] Class C promotion review before NBA/NHL/prop controlling artifacts change
- [ ] Authorized production migration path for NBA schema/predeploy gates
- [ ] Two independent official NHL goalie confirmations (league + team) with
  pinned receipts

`can_execute=false` throughout.

---

## 8. E2E observation (2026-11-10T17:59Z) — engineering complete, gates still external

This section records the end-to-end **observation** pass executed by the same
principal after the preparation packet. It does not clear any hard boundary.

### 8.1 Docs PR exact-head CI (`#2573` / `70083b3d`)

| Check | Result |
|---|---|
| WOW required-three regression | SUCCESS |
| WOW additional required regression | SUCCESS |
| WOW Render and Action release contracts | SUCCESS |
| Release / Production Verification agent | SUCCESS |
| WOW Independent QA exact-head | **FAILURE** — `QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS` (PR body lacked standalone `Incident: #N`) |
| WOW Release Authority exact-head | Not yet PASS on this head (queued/waiting after QA) |

Remediation within this principal: add exactly one `Incident: #2020` line to the
PR description (parent production-restoration incident used by `#2558`) and
push a fresh tip so Apps re-attest. Merge remains external.

### 8.2 Production health / governance (LIVE)

GET `https://wow-governed-probability-engine.onrender.com/health/live`:

```json
{"status": "ok", "can_execute": false}
```

GET `/governance` (selected fields):

- `can_execute=false`
- `global_terminal_authority=V17_TERMINAL_REDUCER`
- `deployed_sha=e7384b9d2fed3256681b25715516dff2e97d3233`
- `deployment_gates.status=PASS` (11 gates, none failed)
- `probability_publishable` remains row-scoped / null at diagnostics
- team-event `model_capability_ready_sport_names=["MLB","NFL"]` (capability ≠ certification)

### 8.3 Lean production readback (GET-only)

Workflow run `42868150127` (schedule, expected SHA `d3260b09…`) receipt:

```json
{
  "can_execute": false,
  "expected_sha": "d3260b09b7ca366c482194f8f27bdc00c0bd6e48",
  "model_ready": "UNVERIFIED",
  "production_verified": false,
  "reason": "RENDER_LIVE_SHA_MISMATCH:web",
  "status": "READBACK_HOLD"
}
```

Web + worker LIVE deploys both still at `e7384b9d…` (`dep-dbpin52vcj2c73d9c1g0` /
`dep-dbpin52vcj2c73d9c3b0`), `autoDeployTrigger=checksPass`. Protected `main` is
**26 commits ahead** of LIVE. Native deploy catch-up is pending Render/CI — not
an API-controller flip. SIRT acceptance remains `PENDING`.

### 8.4 Unpaid Daily schedule (no paid dispatch)

Latest `schedule` run of `wow-v17-daily-snapshot`: `42801795794` at 2026-11-10T10:45Z.

```json
{
  "run_id": "v17-daily-68ec9b47-162a-40ec-8435-fad7355e3d24",
  "run_status": "COMPLETED",
  "requested_slate_date": "2026-11-10",
  "moneyline_rows": 24,
  "rows_completed": 0,
  "rows_held": 24,
  "rows_rejected": 0,
  "can_execute": false,
  "global_terminal_authority": "V17_TERMINAL_REDUCER",
  "cross_sport_row_reconciliation": "PASS",
  "blockers": []
}
```

Observation only: full-board held (0 completed). Does not invent market lines
or clear P0-3. Next unpaid cron watch remains schedule-bound (no
`workflow_dispatch` from this principal).

### 8.5 NFL forward grades (unchanged)

Still `graded_events=94`, `DO_NOT_CERTIFY_YET` from main run `42713206143`. No
newer main forward-shadow since 2026-11-09T22:35Z. 94→95 still needs a real
post-final capture.

### 8.6 Gate-by-gate e2e disposition

| Gate | E2E disposition | Missing principal / clock |
|---|---|---|
| 1 Independent QA/Release/SIRT | `BLOCKED_HARD_BOUNDARY` | Separate Apps (after incident metadata fix) + owner merge + SIRT |
| 2 Re-enable API deploy workflow | `BLOCKED_HARD_BOUNDARY` / Lean V4 prefers leave disabled | Admin only; LIVE uses native `checksPass` |
| 3 P0-3 current offers | `BLOCKED_HARD_BOUNDARY` | Lawful feed or real USER_SUPPLIED line |
| 4 Source rights | `BLOCKED_HARD_BOUNDARY` | Independent rights determination |
| 5 NFL certification | `BLOCKED_HARD_BOUNDARY` | Real games to ≥100 + SIRT |
| 6 Class C promotion | `BLOCKED_HARD_BOUNDARY` | Governed promotion review |
| 7 NBA migration + NHL goalie dual sources | `BLOCKED_HARD_BOUNDARY` | Authorized migrate path + two official goalie sources |

**Reporter-style closure for this engineering lane:**
`BLOCKED_HARD_BOUNDARY` — preparation + e2e observation complete; no gate cleared
unilaterally; `can_execute=false`.
