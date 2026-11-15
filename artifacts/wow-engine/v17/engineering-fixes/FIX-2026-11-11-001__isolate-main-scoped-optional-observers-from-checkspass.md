# FIX-2026-11-11-001 — Isolate optional main-scoped observers from Render `checksPass`

- **Incident:** #2422 (P0, open deadlock)
- **Status:** IMPLEMENTATION_COMPLETE_PENDING_REVIEW
- **Authority:** `can_execute=false`; this record certifies no production behaviour.
- **Scope:** CI check attribution only. No deploy writer, merge gate, evidence
  artifact, persistence writer, or admission decision was changed.

## Failure mode

`schedule` and `workflow_run` workflows run against the current default-branch
tip, so GitHub attaches their job check runs to that protected `main` SHA.
Render's `checksPass` trigger waits for a terminal successful conclusion of
*every* check on the SHA being deployed. An optional autonomous observer that
exits non-zero (for example Scout resumability finding no recoverable source, or
source adoption refusing a whitespace/git hold) therefore reddened an unrelated
`main` tip and blocked deploys of commits already merged and independently
verified.

`jobs.<job_id>.continue-on-error: true` is **not** a remedy: it prevents the
workflow run from failing, but the job check run still concludes `failure`. Only
a step whose final conclusion is `success` keeps its job check green.

## Change

Every step of the optional, non-authoritative jobs is marked
`continue-on-error: true`, so their check conclusion is `success` regardless of
outcome, while the steps themselves are unchanged and still emit the authentic
typed reason (`::error::...`, `*_HOLD`, `*_INCOMPLETE`) and the durable
`$GITHUB_STEP_SUMMARY` entry that owns it:

| Workflow | Jobs |
| --- | --- |
| `wow-v17-scout-persist-resume.yml` | `resume-persistence` |
| `wow-v17-engineering-auto-repair.yml` | `discover`, `adoption-readonly-preflight`, `adopt-reviewed-owner-source`, `route-hold-findings` |

The typed failure remains `FAIL`/`HOLD` on the operation that owns it (the
observer run log + step summary, the persistence writer run, the adoption App
PR), never converted to success and never re-attributed to an unrelated
protected-main commit.

## Deliberately unchanged (still fail-closed)

- `wow-verify.yml` and `wow-engine-verify.yml` merge gates: any red step still
  fails the check and still blocks merge/deploy.
- The disabled API deploy controller (`wow-v17-render-production-deploy.yml`)
  remains retired under Lean Deployment V4; no writer was re-enabled.
- `lean_production_readback.py` acceptance semantics and the manual Render
  runbook (`artifacts/wow-engine/DEPLOY_RENDER_AND_GPT_ACTION.md`) are untouched.
- Observation only: no superseded/foreign target is promoted, no artifact
  receipt is synthesised, no unrecoverable row is dropped.

## Verification

- `artifacts/wow-engine/tests/test_v17_optional_observer_checkspass_isolation.py`
  pins step-level isolation for all five jobs, requires the typed reason and
  durable summary write to remain, and asserts merge gates are not blanket
  suppressed.
- YAML parses and the changed workflows retain their original triggers,
  permissions and job graph (only `continue-on-error` keys added).

## Rollback

Revert this commit. The observers return to publishing failing checks on the
protected-main SHA; nothing else depends on the flag.
