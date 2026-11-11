# WOW V17 — Lean Deployment V4

**Migration:** WOW-V17-DEPLOY-V4-20261103  
**Status:** WORKING_NOT_COMPLETE until exact-head CI, authorized admin cutover, merge, two-service live readback and SIRT/product acceptance.  
**Owners:** WOW Engineering + AMRT (source and delivery), repository owner (ruleset/Render configuration and protected merge), SIRT (independent production verification), Betting Intelligence (model/output acceptance).

## Verified baseline — November 3, 2026

Live GitHub ruleset #24907422 is ACTIVE and presently enforces seven App-pinned contexts, strict base-up-to-date policy, PR-only, no deletion/non-fast-forward and no bypass actors.

Live Render contradicts the intended blueprint and the old API deploy controller:

| Role | Render service | Actual live auto-deploy |
|---|---|---|
| web | srv-da7sa9gu01pc73brt80g | commit |
| worker | srv-dapkbc0473hc73c1978g | off |

Both services were read back LIVE on SHA 3fbc4c5de96513a3a723b20cfcee962f0aaee368, but with different deployment triggers. `render.yaml` declares `checksPass` for both. The historical `wow-v17-render-production-deploy.yml` is an additional API writer candidate, but its release journal deliberately refuses POST without independently attested credential scope and auto-deploy off. Never flip that hard hold to PASS or claim the API controller deployed the live services.

## New architecture

**Source PR → exact-head GitHub regression/QA/Release checks → protected owner merge → Render native checksPass on web and worker → GET-only exact-SHA readback → SIRT acceptance → Betting Intelligence product acceptance.**

There is no Claude/Gemini security-review requirement and no trusted-governance artifact requirement in the target architecture. The migration is a conscious reduction of semantic assurance for a single-owner repository; it never manufactures an independent review PASS.

### Five pinned required checks

| Context | Expected GitHub App ID |
|---|---:|
| WOW required-three regression | 15368 |
| WOW governed probability backend | 15368 |
| WOW additional required regression | 15368 |
| WOW Independent QA exact-head | 5245832 |
| WOW Release Authority exact-head | 5245853 |

Remove ONLY:
- `Trusted exact-head engineering governance` (15368)
- `WOW Independent Security Review` (5245832)

Keep PR-only protection, strict exact-head base-policy, no bypass actors, signed provenance and all meaningful security-negative tests. P-class changes remain owner-authorized; automatic Morning-Green merging is limited to its existing restorative scope/changed-file guard.

### One live Render deployment mechanism

Both Render services must be explicitly PATCHed and independently read back to `autoDeployTrigger=checksPass`, `branch=main`, canonical repo. The historical API deploy controller must then be disabled as a workflow to avoid competing production writers. Merely editing `render.yaml` or disabling the controller without inspecting the two actual service settings is not acceptance.

The new `wow-v17-lean-production-readback.yml` observes a genuine successful `wow-verify` run on protected main, then reads BOTH Render service configurations and their exact LIVE commits; it never calls POST/PATCH or asserts `production_verified=true`. It emits `PENDING_SIRT_INDEPENDENT_ACCEPTANCE` even when SHA/health match. Missing settings, stale main, foreign source run, partial web/worker parity and health failures emit typed HOLD.

SIRT independently verifies both deploy IDs and LIVE commits, production critical logs, genuine behavior, canaries, persistence, scorer/Action receipts and rollback readiness. Model publication is still separately controlled by certified fitted specialists and `V17_TERMINAL_REDUCER`.

## Coordinated cutover, owner-authenticated

1. Freeze the exact PR #2417 SHA and require the three native GitHub regressions PASS. Verify relevant security-negative tests and no unresolved hard findings. Do not trigger paid review.
2. Preserve live ruleset and Render web/worker setting backups. Generate the exact deterministic ruleset payload with `artifacts/wow-engine/v17/lean_release_ruleset_migration.py`; it refuses unknown ruleset drift. No apply mode exists in this planner.
3. Authorize BOTH Render services to `checksPass` via their PATCH API (setting only `autoDeployTrigger`) and verify GET response for both. Render's Update Service API states that configuration changes do not themselves deploy.
4. Apply the exact seven-to-five protected GitHub ruleset via the authenticated repository-admin PUT API; immediately verify the five App-pinned contexts using `--verify-target`. Keep all other protections.
5. Set `WOW_QA_INDEPENDENT_REVIEW_ACTIVE=false`, `WOW_QA_ZERO_CREDIT_MODE=true` in repository AND any overriding `wow-qa` environment. Disable `wow-v17-simple-security-review.yml`, `wow-v17-trusted-governance-gate.yml`, `wow-v17-existing-pr-governance-review.yml`, the competing old `wow-v17-render-production-deploy.yml`, and the nonrequired paid `wow-v17-release-production-verification-agent.yml` after confirming native settings. The new GET-only observer must not turn an early Render PENDING into a failing branch CI check (it records a typed receipt and stays nonblocking). Do not delete historical artifacts.
6. Mark PR ready; obtain all five actual checks at final source SHA, verify no blocking findings, and perform the owner's exact-head protected merge. Never override missing/failed checks.
7. Verify the effective main SHA, GitHub push CI, BOTH Render LIVE deploy SHAs and IDs, read-only observer receipt, two canary cycles, worker behavior and real Action receipts. SIRT independently accepts. `COMPLETE` requires that independent proof; CI green alone is not production or model completion.

The owner-admin cutover helper shipped separately in the conversation performs readback and preflight before administration mutation; it does not place wagers or certify outcomes. It requires an exact-head explicit owner opt-in and valid Render credential.

### Failure and rollback

Any failed native required check, missing or foreign App, real security finding, stale PR head, render-service identity mismatch, wrong/live SHA or uncertain API response causes SAFE_HOLD. Keep before/after JSON and original settings for authorized rollback. Do not issue an untracked duplicate Render POST or force a merge.

If cutover fails after changing service settings but before GitHub ruleset acceptance, leave deployments in CI-gated safe hold and escalate the concrete stage. If ruleset has been changed but source cannot be approved, do NOT claim V4 activated; preserve live five-check protection and record the missing owner/QA/Release condition. Restoring seven checks is a separate authenticated owner decision.

**Invariant:** `can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`; `V17_TERMINAL_REDUCER` sole probability terminal authority; SIRT read-only and never a deployer.
