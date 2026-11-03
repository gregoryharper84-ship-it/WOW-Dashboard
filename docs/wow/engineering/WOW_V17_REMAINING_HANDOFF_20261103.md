# WOW V17 restoration — handoff for the remaining authorized work

**Status:** descriptive handoff, not a policy, trust root, release authorization or
production proof.
**Prepared:** 2026-11-03 by Freebuff engineering.
**Scope:** everything left in the V17 triple-D restoration that requires an identity,
credential or authority this engineering session does not hold.

Read [WOW_GOVERNANCE_V3_SINGLE_OWNER.md](../../WOW_GOVERNANCE_V3_SINGLE_OWNER.md) and
[WOW_RELEASE_LANE_REQUIREMENTS.md](WOW_RELEASE_LANE_REQUIREMENTS.md) first. Live GitHub
enforcement, the installed ruleset and the actual App-published checks always win over
this document. This document supplies no approval and cannot enable execution:
`can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`,
`terminal_authority=V17_TERMINAL_REDUCER`.

---

## 2026-11-03 additional release-fence proof — reported local reproduction

**Source/provenance:** Freebuff's latest clean-database reproduction report, supplied
to the restoration session; repository code and the #2406 CI workflow confirm
the test-file inventory, but no separately archived local terminal transcript,
database snapshot or independent acceptance receipt was provided with this report.
Treat the local run numbers below as **Freebuff-reported local test evidence**,
not production or independent verification.

The workflow at #2406 head `2f1f90424a3f782bdc94dadc5ae399a53c42cdac`
executes **ten** Python test files. The previously unverified **nine** fence/
release tests below were run together on a fresh PostgreSQL cluster and reported
`207 passed, rc=0, 0 skipped` (2.21 s). The separately tested tenth file is
`test_v17_pick_request_lease_race_postgres.py`; do not silently count its
results again inside this nine-file total.

| Relative path within `artifacts/wow-engine/tests/` | Reported standalone result |
| --- | ---: |
| `test_render_release_service_fence_candidate.py` | 20 passed |
| `test_render_release_service_fence_postgres_integration.py` | 12 passed |
| `test_render_release_rpc_postgres.py` | 11 passed |
| `test_render_release_recovery_postgres.py` | 11 passed |
| `test_v17_render_production_deploy_workflow.py` | 17 passed |
| `test_v17_render_release_provenance_gate.py` | 39 passed |
| `test_v17_engineering_fenced_writer.py` | 46 passed |
| `test_v17_fenced_mutation_cli.py` | 28 passed |
| `test_v17_writer_epoch_postgres.py` | 23 passed |
| **Nine-file total, clean combined run** | **207 passed, 0 skipped** |

**Reproduction setup:** fresh `127.0.0.1:5432/wow_release_fence_test`;
workflow-equivalent seven roles, including
`wow_release_controller` with `CI_FENCE_EPHEMERAL_ONLY` test credential;
the `service_fence` and `release_intent` grants; the three
`REVIEW_ONLY` migrations applied using `ON_ERROR_STOP` (each rc=0);
`PYTHONPATH=artifacts/wow-engine`; `--confcutdir=artifacts/wow-engine/tests`;
`psycopg[binary]==3.2.10` and `pytest==8.4.2`; isolated release/writer
test DSNs and attestations. The combined fresh-run preflight counted two
already-migrated objects; fixtures provisioned five further objects.

**Deviation from CI:** local PostgreSQL **16.2** and trust authentication,
versus the workflow's `postgres:17.6` and password authentication.
The local results are not bit-identical CI and do not establish Supabase schema,
Render deployment, migration execution in production or fitted model delivery.

**Rejected apparent errors:** first independent per-file runs succeeded,
but a subsequent combined run against the *same* used database gave
`184 passed, 23 errors` with `DuplicateTable` for
`wow_engineering_incident_writer_leases`. Recreating the cluster and
executing all nine files **once** eliminated those errors (`207 passed`).
Two vacuous attempts from incorrect relative paths were detected using
`no tests ran` and exit-code/skip-count guards and are **excluded**.
DDL fixtures are not repeat-idempotent on a reused test database; use a fresh
cluster per combined run. No production mutations resulted.

**Later live GitHub correction (as observed in this ChatGPT engineering
session):** #2415 is **ready**, not draft, at
`b70f35f0dc898c3a65503350cd6270205d5490e1`.
All eight source CI workflows completed successfully. Authentic exact-head
QA and Release Authority App checks reported **success**; the separate
`Trusted exact-head engineering governance` check reported **failure**.
Its job `126090484122` ended with `unexpected end of JSON input`
following the artifact-lookup loop; this is a native failure, **not** a PASS,
and its exact producing API/response still needs diagnosis. The effective
live ruleset `24907422` was read back with **seven** required exact-head
checks (three regression + Trusted Governance + QA + Security Review +
Release Authority). This supersedes the older three-check statement in the
historical snapshot below. No independent semantic security PASS or protected
merge is established.

**Status:** `WORKING_NOT_COMPLETE / SAFE_HOLD`. Preserve `can_execute=false`.

---

## 0. What is already done — do not redo

A real, previously undetected defect was found, reproduced, fixed and tested. It blocked
the V3 owner-authored governance contract in the protected path even though every unit
test passed.

**Defect.** `receipt_schema_version: 3` is overloaded across certification modes. In
`.github/workflows/wow-v17-trusted-governance-gate.yml` the first routing branch was
narrowed to `schema 3 && cert_mode != TRUST_ROOT_BOOTSTRAP`, but the following `elif`
still rejected every `schema != 2 && schema != 4`. An owner-authored
`TRUST_ROOT_BOOTSTRAP` schema-3 receipt therefore terminated as
`GOVERNANCE_RECEIPT_SCHEMA_UNSUPPORTED` (exit 1) before
`.github/scripts/verify_existing_pr_governance_receipt.py` could verify it.

**Fix.** The `elif` now also excludes `TRUST_ROOT_BOOTSTRAP`. Bootstrap receipts of
every schema are still validated by the Python verifier (owner id/login/human-type match,
exact-head binding, `release_authorized=false`, `owner_merge_required=true`, no fabricated
owner review, live check re-verification), so an unrecognized schema is still rejected at
the gate. No authority was removed and no check was weakened.

**Regression.** `test_existing_pr_governance_receipt.py` now extracts and executes the
gate's whole real `if/elif/fi` routing block — dedented like the `run: |` scalar and with
the embedded heredoc intact — instead of isolating its first condition with a synthetic
`else`. Observed pre-fix: `GOVERNANCE_RECEIPT_SCHEMA_UNSUPPORTED`, exit 1, for
`TRUST_ROOT_BOOTSTRAP` + schema 3.

**Work product**

| Item | Value |
| --- | --- |
| Branch (pushed) | `candidate/v17-v3-bootstrap-gate-alignment` |
| `main` base | `3fbc4c5de96513a3a723b20cfcee962f0aaee368` |
| #2415 commits replayed on it | `dafd18fa9` (`Align owner-authored bootstrap evidence…`), `167f435ed` (`Separate bootstrap schema-3 receipts…`) |
| This session's fix commit | `1d1d76d18292e637f57938748109579a14452cf3` |
| Diff vs `main` | 7 files, +273/−35 |

**Verification actually performed in this session**

| Command (from repo root) | Result |
| --- | --- |
| `pytest .github/scripts/tests/test_existing_pr_governance_receipt.py` | 181 passed |
| `pytest artifacts/wow-engine/test_v17_governance_dispatcher.py` | 119 passed |
| `pytest artifacts/wow-engine/test_v17_independent_release_checks.py` | 214 passed |
| `python -c "import yaml; yaml.safe_load(...)"` on the gate | parses, job `trusted-governance` |
| `git merge-tree --write-tree` vs `main`, for #2406/#2414/#2415 | all three clean, no conflicts |
| `pytest test_v17_governance_dispatcher_rate_limit.py` at #2414 head `a701d884…` | 27 passed |
| #2406 pick-request suites at head `2f1f9042…` | 40 passed on a real PostgreSQL 16.2 backend: the 9 lease-race tests passed 5 consecutive times (0 failures, 0 skips) alongside `v17/test_pick_request_state_runtime.py`, `v17/test_interactive_pick_parallel.py`, `test_pick_request_run_status_classification.py` |

Local prerequisites used: `python3 -m venv`, `pip install pytest pyyaml celery redis
fastapi httpx pydantic`, and a `python`→`python3` shim on `PATH` (the workflows invoke
`python`).

The PostgreSQL proof needs neither root nor Docker: `uv venv --python 3.11` then
`uv pip install -r artifacts/wow-engine/requirements.txt pgserver 'pytest==8.4.2'`, and
run `initdb`/`pg_ctl` from the bundled `pgserver/pginstall/bin` on `127.0.0.1:5432` with
trust auth and database `wow_release_fence_test` — the test's own guard rejects any other
dbname, any non-loopback host and any extra libpq option. Then run pytest with
`WOW_PICK_ISOLATED_TEST_DSN=postgresql://postgres:local-ci-test-only@127.0.0.1:5432/wow_release_fence_test`,
`WOW_PICK_ISOLATED_TEST_ATTESTATION=NONPRODUCTION_EPHEMERAL_ONLY`,
`PYTHONPATH=artifacts/wow-engine` and `--confcutdir=artifacts/wow-engine/tests` (the
engine-wide conftest imports Celery). An unconfigured environment makes that module skip
itself at collection, which is never concurrency evidence — assert `9 passed` rather
than trusting a green exit. The fixture applies
`20260920_pick_request_durable_run_state.sql`, `20260921_pick_request_run_control.sql`,
`20260921_pick_request_durable_job_queue.sql` and #2406's
`20261103_pick_request_governed_rejection_status.sql`, so a passing run also demonstrates
that forward migration applies cleanly on a real backend. This is a local reproduction
of CI's isolated proof and is **not** production migration or deployment evidence.

---

## 1. Verified repository facts

All three repairs share the same base and apply to current `main` without conflict, so
there is no rebase or dependency-ordering work left in Git.

| PR | Head | Base | Commits | Merge |
| --- | --- | --- | --- | --- |
| #2406 runtime classification / PostgreSQL lease safety | `2f1f90424a3f782bdc94dadc5ae399a53c42cdac` | `3fbc4c5de` | 9 | clean |
| #2414 bounded GitHub read rate-limit recovery | `a701d884e0976e0077bac8fbf47a57bd2f6ad900` | `3fbc4c5de` | 3 | clean |
| #2415 V3 governance receipt alignment | `84824fb3dd908fe75ad66a42916e337fbb41e35f` | `3fbc4c5de` | 2 | clean, **draft** |

Any push to any of these heads invalidates every exact-head check, review and receipt.
Freeze heads during the merge window.

---

## 2. The bootstrapping constraint that remains (owner decision required)

`wow-v17-trusted-governance-gate.yml` and `wow-v17-existing-pr-governance-review.yml`
both run from **protected `main`** (`actions/checkout` with `ref: main`). They therefore
execute `main`'s copy of the verifier. A change to that verifier cannot certify itself:
on today's `main`, an owner-authored protected PR still yields `TRUST_ROOT_OWNER_AUTHORED`
from `build_trust_root_bootstrap`, which is exactly what #2415 removes.

Only two legitimate resolutions exist. Choosing between them is an authority decision,
not an engineering one — do not improvise a third:

1. **Deliberate protected owner merge.** V3 §2 states owner authorization to release is
   established by an intentional authenticated merge after the other requirements pass.
   The installed ruleset (`24907422`) requires exactly three App `15368` check runs; the
   governance gate is evidence, not one of those three. Under this route the owner merges
   the governance change directly through the protected path and the new contract governs
   subsequent PRs.
2. **Engineering-App-authored equivalent.** Re-propose the same diff from
   `wow-engineering[bot]` with the immutable adoption commit and the owner's exact-head
   `APPROVED` review, satisfying the existing bootstrap path. Note that
   `.github/scripts/wow_simple_security_review.py::owner_fallback_review` rejects
   owner-origin source, so this route is only viable if the source PR is genuinely
   non-owner authored.

Do not force-push, do not disable the gate, do not fabricate a receipt, and do not
re-label a HOLD as PASS to break this cycle.

---

## 3. Exact mechanics of the two App-gated checks (audit result)

These two checks are what stand between a green PR and a governance-complete PR.

| Field | `WOW Independent QA exact-head` | `WOW Release Authority exact-head` |
| --- | --- | --- |
| App id (pinned by ruleset) | `5245832` | `5245853` |
| Workflow | `.github/workflows/wow-v17-independent-qa-check.yml` | `.github/workflows/wow-v17-release-authority-check.yml` |
| Repo variable required | `WOW_QA_APP_ID` | `WOW_RELEASE_APP_ID` |
| Secret required | `WOW_QA_APP_PRIVATE_KEY` | `WOW_RELEASE_APP_PRIVATE_KEY` |
| Token | `actions/create-github-app-token`, `permission-checks: write` | same |
| Failure when unconfigured | `QA_APP_CREDENTIAL_MISSING`, **no check published** | `RELEASE_APP_CREDENTIAL_MISSING`, **no check published** |
| Decider | `artifacts/wow-engine/v17/independent_release_checks.py::qa_findings` | `::release_findings` (re-derives every QA finding) |

`REQUIRED_REGRESSION_CHECKS` = `WOW required-three regression`, `WOW governed probability
backend`, `WOW additional required regression`; QA additionally requires
`WOW V17 change impact gate`. All from App `15368` at the exact head.

**Review-policy mode ledger** (`wow-v17-independent-qa-check.yml`, "Select exact-head
review policy"):

- `WOW_QA_ZERO_CREDIT_MODE=true` → `mode=zero_credit`, `independent_review_active=false`.
  Reduced-assurance evidence only; no provider credential is used. `qa_findings` then
  requires `zero_credit_enabled=true`, `current_review_active` not true,
  `source_review is None`, and a policy target matching PR/head/ref exactly. Its check
  summary must begin `ZERO-CREDIT DETERMINISTIC QA ONLY`.
- `WOW_QA_INDEPENDENT_REVIEW_ACTIVE=true` → `mode=independent`, `review_active=true`.
  Requires a verified semantic source-review artifact, i.e. a working provider credential
  (`secrets.WOW_QA_REVIEW_OPENAI_API_KEY` for the QA review step). Absent → decision
  fails closed, no PASS.
- Both true → `QA_REVIEW_MODES_CONFLICT`, hard failure at the policy step.
- `mode=canary` only via `workflow_dispatch` with `WOW_QA_INDEPENDENT_REVIEW_CANARY_PR` /
  `..._CANARY_SHA` bound to the exact PR and SHA; canary additionally rejects
  `QA_REVIEW_CANARY_OWNER_AUTHORED` and `QA_REVIEW_CANARY_TRUST_ROOT`.
- Otherwise `mode=legacy`.

**Hard preconditions in `qa_findings` that are easy to miss:**

- `QA_PR_DRAFT` — a draft PR can never pass QA. #2415 is a draft today.
- `QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS` — the PR body must contain **exactly one**
  canonical `Incident: #<number>` line. No line, or two different ids, fails both lanes.
- `QA_HEAD_STALE`, `QA_PR_NOT_OPEN`, `QA_PR_BASE_NOT_MAIN`,
  `QA_PR_HEAD_REPOSITORY_MISMATCH`.
- `QA_INDEPENDENT_SOURCE_REVIEW_MISSING` in `independent` mode.
- Trust-root diffs: owner authorship is permitted under V3 (`QA_TRUST_ROOT_OWNER_AUTHORED`
  is retired and no longer emitted); non-owner authorship still needs the owner's
  exact-head `APPROVED` review, else `QA_TRUST_ROOT_OWNER_APPROVAL_MISSING`.

**Readiness ordering.** Both workflows call `classify-release-readiness` first. Until the
native `wow-verify` run (workflow id `338954264`) completes for that head, they publish
nothing (`RELEASE_QA_EVIDENCE_PENDING`, `count=0`). Native runs older than
`MAX_NATIVE_RUN_AGE` (2 hours) do not satisfy it. A missing check is not a failure — it is
an absence, and `.github/workflows/wow-v17-governance-dispatcher.yml` keeps the PR at
`HOLD: REQUIRED_CHECK_MISSING:<name>` until the check exists and is green at the exact
head.

---

## 4. Ordered execution plan

**Step 0 — Freeze.** Pick one candidate SHA per PR. Do not push during steps 1–6.

**Step 1 — Publish the gate fix.** Either push `1d1d76d18292e637f57938748109579a14452cf3`
onto #2415's branch (cleanest single-artifact history) or open a PR from
`candidate/v17-v3-bootstrap-gate-alignment`. The branch is already pushed.

**Step 2 — Settle §2** (protected owner merge vs Engineering-App route) before any
other governance work.

**Step 3 — Prepare #2415.** `gh pr ready 2415`; confirm the body has exactly one
`Incident: #…` line; confirm `WOW_QA_APP_ID`, `WOW_RELEASE_APP_ID`, the two App private
keys and the chosen policy variables exist and are mutually consistent.

**Step 4 — Exact-head QA and Release.** Trigger both on the exact head and capture the
run URLs and check-run ids. If a lane reports `count=0`, read
`RELEASE_QA_EVIDENCE_PENDING:<checks>` — the cause is native regression, not a code
defect. Never re-trigger to shop for a friendlier verdict.

**Step 5 — Merge.** Owner-only, protected path. Record PR number, final merge SHA and the
authentic merge event.

**Step 6 — Remaining repairs.** #2414 (bounded transport resilience) and #2406 (runtime
classification, then the governed forward migration) in dependency-safe order. Refresh
every exact-head artifact after any head change.

**Step 7 — Deploy.** Accepted changes to the intended Render services
(`wow-governed-probability-engine`). Apply #2406's accepted forward migration through the
governed database release process, then verify stopped-run fencing, concurrent lease
claims and absence of unintended run-state transitions. Record deployed revision SHA,
`/health/live`, route readback and service revision parity. A merge is not a deployment.

**Step 8 — Model delivery.** Authenticated production acceptance on the intended V17
routes with at least one exact supported team/event winner candidate and one supported
player prop. Verify official event identity, exact selection, freshness, the correct
fitted specialist and artifact version, the native model/scorer result, the governed
calibrated probability and lower bound, prediction receipt persistence, final refresh and
terminal disposition. Preserve typed failures (`MODEL_UNAVAILABLE`,
`MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`). A healthy
backend is not a fitted-model prediction. A fully reconciled board of legitimate model
rejections must not be reported as a transport outage.

**Step 9 — Live Custom GPT.** Confirm the production GPT editor has the V17 instructions,
Actions, schema and connection saved, then run a real user-facing test and show an
authenticated Action response matching a persisted backend prediction receipt. Keep
`LIVE_GPT_EDITOR_SYNC` separate from model capability and backend runtime.

---

## 5. Read-only evidence commands

```bash
# current state of the three repairs
for pr in 2406 2414 2415; do
  gh pr view $pr --json number,isDraft,state,mergeable,mergeStateStatus,reviewDecision,headRefOid | jq
done

# exact-head check runs with App provenance (the only trusted source)
gh api "/repos/gregoryharper84-ship-it/WOW-Dashboard/commits/<HEAD_SHA>/check-runs?per_page=100" \
  --jq '.check_runs[] | select(.name|test("WOW |Trusted exact-head")) | {name, app: .app.id, status, conclusion}'

# review inventory (automated COMMENTED reviews are not approval)
gh api "/repos/gregoryharper84-ship-it/WOW-Dashboard/pulls/<N>/reviews?per_page=100" \
  --jq '.[] | {user: .user.login, state, commit_id, submitted_at}'

# the live ruleset is the merge gate; read it, do not assume it
gh api "/repos/gregoryharper84-ship-it/WOW-Dashboard/rulesets/24907422" --jq '.rules'
```

Reproduce this session's local verification:

```bash
python3 -m venv /tmp/venv && /tmp/venv/bin/pip install pytest pyyaml celery redis
mkdir -p /tmp/shim && ln -sf "$(command -v python3)" /tmp/shim/python
PATH=/tmp/shim:$PATH /tmp/venv/bin/python -m pytest \
  .github/scripts/tests/test_existing_pr_governance_receipt.py -q
cd artifacts/wow-engine && PATH=/tmp/shim:$PATH /tmp/venv/bin/python -m pytest \
  test_v17_governance_dispatcher.py test_v17_independent_release_checks.py -q
```

---

## 6. Required acceptance record

| Objective | Owner | Required proof |
| --- | --- | --- |
| V17-RESTORE-01 governance | Engineering + authorized reviewer | Exact-head accepted review; gate fix merged; trusted checks at the final head |
| V17-RESTORE-02 protected merge | Release authority | PR number, merged SHA, authentic merge event |
| V17-RESTORE-03 runtime | Engineering | Render deployment receipt, `/health/live`, deployed SHA == merged SHA |
| V17-RESTORE-04 database | Engineering | Migration applied, concurrency/lease proof, persistence readback |
| V17-RESTORE-05 model delivery | Betting Intelligence + Engineering | Fitted scoring + persisted prediction receipt, calibrated lower bound, typed failures |
| V17-RESTORE-06 GPT integration | Engineering + Independent Verification | Saved editor config + authenticated Action response matching a persisted receipt |
| V17-RESTORE-07 regression | SIRT / Independent Verification | Independent no-regression acceptance, including NFL Pick'em and certified routes |

Report each as COMPLETE / WORKING_NOT_COMPLETE / BLOCKED / FAILED / SAFE_HOLD /
UNSUPPORTED, with links, full SHAs, UTC timestamps and evidence identifiers. Green CI
alone is not restoration.

---

## 7. Prohibitions (unchanged and binding)

No self-approval, no fabricated owner review, no manufactured PASS, no relabelling a
`REJECT` as provider unavailability, no weakening or skipping a required check, no direct
or force push to `main`, no history rewrite of the preserved rescue lineage, no expanding
public/anon mutation access, no changing model probabilities, calibration or thresholds,
no replacing a missing fitted model with market-implied or qualitative probability, no
live wagering or market orders, and no `can_execute=true`.

## 8. Blockers this session could not clear

| Blocker | Why it is not an engineering task |
| --- | --- |
| PR actions (ready-for-review, review, merge, workflow dispatch) | No GitHub API identity available in the workspace (`gh auth status` unauthenticated); only server-managed git fetch/push |
| App-gated checks | Require the QA and Release App private keys and their repo variables |
| Render deployment and revision parity | No Render workspace access |
| Database migration and lease proof | No Supabase/production database access |
| Live GPT verification | No Custom GPT editor access |
| Independent acceptance | Engineering cannot approve its own implementation |
