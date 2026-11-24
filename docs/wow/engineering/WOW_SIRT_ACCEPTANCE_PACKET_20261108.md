# WOW SIRT acceptance packet — protected `main` `db5e6b7c` (2026-11-08)

**Status: EVIDENCE RECORDED — NOT ACCEPTED.**
The production readback receipt is bound below, but it is observation, not SIRT
acceptance. No independent signature exists yet, so this packet deliberately
leaves the deployment terminal at `PENDING_SIRT_INDEPENDENT_ACCEPTANCE`. The
implementing agent cannot sign its own release; signing here would manufacture
the one factor this repository reserves for independent parties.

```text
bound_sha                     = db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c
status                        = EVIDENCE_RECORDED_PENDING_INDEPENDENT_ACCEPTANCE
terminal                      = PENDING_SIRT_INDEPENDENT_ACCEPTANCE
independent_verification      = NOT_PERFORMED
production_acceptance         = NOT_PERFORMED
production_verified           = false
can_execute                   = false
terminal_authority            = V17_TERMINAL_REDUCER
```

## Bound identity

| Field | Value |
| --- | --- |
| Source PR | #2523, 3 commits, 10 files, +537/−4 |
| Merge commit | `db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c` (parents `12d8e65e` + `f3115b9f`) |
| Merge method | merge commit after exact-head required checks |
| Engineering incident | #2422 |
| Protected ruleset | #24907422, active, no bypass actors |
| Required checks | WOW Independent QA exact-head (5245832), WOW Release Authority exact-head (5245853), WOW additional required regression, WOW governed probability backend, WOW required-three regression (15368) |

## Machine evidence, observed 2026-11-08

| Evidence | Observation |
| --- | --- |
| Release admission on `db5e6b7c` | 7 runs: 5 `success`, 2 neutral `skipped` (`--neutral-if-superseded --neutral-if-draft-target`), 0 failures |
| `wow-engine-verify` on `db5e6b7c` | shards 0–3, durable Agent Runtime integration, Render/Action contracts, governed probability backend all `success` |
| Deployment-truth job `128640154104` | `BOTH_SERVICES_LIVE_SHA_CONTAINED_IN_MAIN`; observed web+worker at `12d8e65e`, lag 4; `production_verified: false`, `can_execute: false` |
| Readback workflow `42451502788` (job `128641501478`) | `success`; receipt below |
| Web service `srv-da7sa9gu01pc73brt80g` | live `db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c`, deploy `dep-dbo0k97avr4c73aubphg` |
| Worker service `srv-dapkbc0473hc73c1978g` | live `db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c`, deploy `dep-dbo0k97avr4c73aubr90` |
| Public health | `/health/live` HTTP 200; `/health` and `/governance` return `deployed_sha: db5e6b7c…` |

The readback is strict rather than a health ping: `classify_readback` requires a
unique `live` deploy per service whose commit **equals current `main`**, with
`branch=main`, canonical repo and `autoDeployTrigger=checksPass`; any mismatch,
extra live deploy, stale main or non-200 health raises a typed HOLD and the job
exits non-zero. It still reports `PENDING_SIRT_INDEPENDENT_ACCEPTANCE` when
SHA and health match (see `WOW_V17_LEAN_RELEASE_V4_20261103.md`).

Verbatim receipt:

```json
{"can_execute": false, "health_live": "HTTP_200", "model_ready": "UNVERIFIED", "observed_main_sha": "db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c", "production_verified": false, "status": "WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED", "target_sha": "db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c", "terminal": "PENDING_SIRT_INDEPENDENT_ACCEPTANCE", "web_deploy_id": "dep-dbo0k97avr4c73aubphg", "worker_deploy_id": "dep-dbo0k97avr4c73aubr90"}
```

## What this packet does not establish

- SIRT acceptance or independent verification. The readback is produced by the
  delivering system, so it cannot verify itself.
- Production acceptance lanes (spread-forward canaries, spread certification,
  priority props, golden full slate) run by
  `wow-v17-post-deploy-verification-orchestrator.yml`. No such run is bound here.
- Model readiness, calibration, product acceptance or `production_verified=true`.
- Any execution authority. `can_execute=false` throughout.

## Formal closure evaluation

`assess_class_a_closure` was run on a receipt built from the evidence above with
`production_acceptance_status=NOT_PERFORMED`,
`independent_verification_status=NOT_PERFORMED` and
`independent_verifier=NOT_ASSIGNED`. It returns `CLOSURE_BLOCKED`, not a
pending-terminal pass:

```json
{
  "blockers": [
    "INDEPENDENT_VERIFICATION_EVIDENCE_REF_MISSING",
    "INDEPENDENT_VERIFICATION_NOT_PASS",
    "INDEPENDENT_VERIFIER_MISSING",
    "PRODUCTION_ACCEPTANCE_EVIDENCE_REF_MISSING",
    "PRODUCTION_ACCEPTANCE_NOT_PASS"
  ],
  "can_execute": false,
  "closure_eligible_for_independent_review": false,
  "status": "CLOSURE_BLOCKED",
  "terminal_authority": "V17_TERMINAL_REDUCER"
}
```

Reproduce with:

```bash
cd artifacts/wow-engine
python -c 'import json,sys; from v17.sirt_assurance import assess_class_a_closure; print(json.dumps(assess_class_a_closure(json.load(sys.stdin)), indent=2, sort_keys=True))' < receipt.json
```

## Signatures required before this becomes acceptance

| Role | Must independently establish | Status |
| --- | --- | --- |
| SIRT reviewer | both deploy IDs and LIVE commits, production critical logs, genuine behaviour, canaries, persistence, rollback readiness | NOT SIGNED |
| Independent Verification | exact-head test/review receipts and a verifier identity distinct from `implementation_owner` | NOT SIGNED |
| Post-deploy acceptance lanes | orchestrator run bound to `db5e6b7c` and its web/worker deploy IDs | NOT RUN |

Until those exist, the deployment stays `PENDING_SIRT_INDEPENDENT_ACCEPTANCE`,
`production_verified=false`, `can_execute=false`, with `V17_TERMINAL_REDUCER`
as the sole terminal authority. Recording this packet does not weaken the
pending state or advance any release gate.
