# WOW release lanes — evidence and authority binding

**Status:** read-only, descriptive inventory of the installed mechanisms as checked against GitHub `main` `5222570c384adfe7c1c46dd450438dd7f8cf3312` on 2026-10-30. This document is **not a policy, trust root, executable configuration, new gate, release authorization or substitute for live readback**. For the current single-owner policy see [WOW Governance V3](../../WOW_GOVERNANCE_V3_SINGLE_OWNER.md); that file itself labels its adoption status as proposed. Current live GitHub enforcement wins over either document. Historical [reviewer incident #2136 simplification](WOW_REVIEWER_INCIDENT_2136_SIMPLIFICATION.md) is not an authorization source.

## 1. Distinct lanes — do not combine their prerequisites

| Lane | Source/verifier | Identity and evidence required | Meaning of PASS / HOLD |
| --- | --- | --- | --- |
| **GitHub protected PR merge** | Live ruleset `24907422`; `.github/scripts/verify_existing_pr_governance_receipt.py::REQUIRED_CHECKS` | Exactly three exact-head GitHub Actions App `15368` **check runs**: `WOW required-three regression`, `WOW governed probability backend`, `WOW additional required regression`. Read back live ruleset on every release. | Native branch protection admission only, **not** semantic security clearance, owner authorization, production health or model readiness. |
| **Existing PR trusted governance** | `.github/workflows/wow-v17-existing-pr-governance-review.yml` lines 466–473; `.github/scripts/verify_existing_pr_governance_receipt.py::REQUIRED_WORKFLOWS` | Seven exact-source-head **workflow run** name+path pairs, completion/conclusion and bounded provenance; additional identity, review, PR and artifact controls in its verifier. | Separate governance evidence. A passing native three-check ruleset does **not** prove this lane's requirements are met. |
| **Owner-signed release (optional, manually invoked)** | `.github/workflows/wow-v17-owner-signed-release.yml` lines 75–80, 133–150, 187–224 and 228–250; `.github/release/verify_owner_release.sh` | Offline SSH signature in `wow-release` namespace, binding repository, PR, exact head, incident and unexpired expiry (max 24 h), plus its seven workflow-run pairs, independent read-only QA and protected writing-job re-verification. Additional class/scope restrictions apply. | Verified intent on that path only. **Never** substitute for an independent semantic security PASS, grant automated self-approval, or assert production deployment. No completed signed invocation was verified in this audit. |
| **Deterministic QA and Release App roles** | `artifacts/wow-engine/v17/independent_release_checks.py::qa_findings/release_findings`; workflows `wow-v17-independent-qa-check.yml` and `wow-v17-release-authority-check.yml` | Exact source head and authentic App identities (`5245832` QA, `5245853` Release) with required evidence. Owner-authored protected changes are not blanket-denied; nonowner source needs owner's exact-head approval in the normal QA path. | Evidence/identity checks, **not** proof of adversarial semantic review; `ZERO-CREDIT DETERMINISTIC QA ONLY` means exactly that. |
| **Independent semantic security review** | `.github/workflows/wow-v17-simple-security-review.yml`; protected-main `.github/scripts/wow_simple_security_review.py` | Signed/attributed exact-head source and diff review with native `PASS`, `HOLD` or `REJECT` from QA App `5245832`. | A genuine substantive `REJECT` stays a rejection for that SHA, even when the three required checks and deterministic QA/Release pass. No retry to get a friendlier verdict. |
| **Production release/deployment** | `.github/workflows/wow-v17-render-production-deploy.yml` (notably `validate_release_provenance` and required-check verification); `.github/workflows/wow-v17-release-production-verification-agent.yml` | Merged PR/main lineage, exact-head required regression and QA/Release App provenance, `wow-release` environment, followed by actual deployed revision/health/reconciliation evidence. Production verification agent separately checks six workflow-run pairs and trusted governance. | A merge or workflow heartbeat is **not** deployment verification. Protected deployment requirements are distinct from GitHub branch-protection requirements. |
| **V17 fitted-model publication** | `docs/WOW_GOVERNANCE_V3_SINGLE_OWNER.md` §5; actual fitted specialist and `V17_TERMINAL_REDUCER` route | Certified fitted specialist, feature/market inputs, calibration/bounds/freshness and exact persisted row evidence. | Engineer/CI/production PASS never substitutes for a fitted probability or allows live wagers/orders. |

### The workflow-run inventory: six shared plus one path-specific

The exact six run name/path identities jointly required by **all three** observed workflow inventories are:

1. `wow-verify|.github/workflows/wow-verify.yml`
2. `wow-engine-verify|.github/workflows/wow-engine-verify.yml`
3. `wow-v17-rapid-repair|.github/workflows/wow-v17-rapid-repair.yml`
4. `wow-v17-change-impact-gate|.github/workflows/wow-v17-change-impact-gate.yml`
5. `wow-v17-engineering-auditor-code-health|.github/workflows/wow-v17-engineering-auditor-code-health.yml`
6. `wow-v17-spread-forward-shadow|.github/workflows/wow-v17-spread-forward-shadow.yml`

`wow-v17-owner-signed-release.yml` and `wow-v17-existing-pr-governance-review.yml` each require the **seventh**
`wow-v17-release-production-verification-agent|.github/workflows/wow-v17-release-production-verification-agent.yml`.
That production-verification agent itself requires the shared six **plus** `Trusted exact-head engineering governance` as a separate check and its own receipt validation. Excluding itself from its internal run list is consistent with a nonrecursive producer, **not evidence of drift by itself**. Workflows currently contain literal lists; this inventory does not make them dynamically share a definition or assert a completed deduplication.

The existing `.github/scripts/verify_existing_pr_governance_receipt.py::REQUIRED_WORKFLOWS` is the closest seven-entry executable mapping but other workflows do **not** currently import it. Any proposal to replace literal workflow predicates with a shared executable source, alter their authority, or change `.github/release/owner_allowed_signers` is **Class P**; compare **exact original run identities and order/behavior**, write semantic negative and regression tests, and obtain independent sign-off before merging. Do not silently drop the extra seventh or substitute the three checks for the seven workflow runs.

## 2. V3 §4 decision states: who may assert each one

| State | Producer/evidence and what it actually proves | Must stay distinct from |
| --- | --- | --- |
| `CI_PASS` | Latest exact-head native check runs from correct App `15368` satisfying the live ruleset; targeted tests where applicable. | Security, owner authorization, deploy, fitted model. |
| `OWNER_AUTHORIZED` | Authenticated owner action through the effective governed merge process, or an **actually verified** offline signature *within the owner-signed lane*, scoped to exact PR/head/incident/expiry. | Public signer registration; agent-authored actions using owner's login; third-party review. |
| `SECURITY_FINDINGS_RESOLVED` | Applicable real security-negative tests and independent exact-head findings have been repaired and re-evaluated. Where semantic review reports `REJECT`, this state is **false/unverified**, not advisory `PASS`. | Native check/QA App deterministic green. |
| `INDEPENDENT_REVIEW_NOT_PERFORMED` | Explicit provenance showing a zero-credit/deterministic-only or missing semantic review. This is an **evidence disclosure**, not a substitute for `SECURITY_FINDINGS_RESOLVED`; a recorded `REJECT` is not `NOT_PERFORMED`. | Independent PASS. |
| `PRODUCTION_VERIFIED` | Independent deployed-artifact SHA appropriate to the service's deploy root, `/health/live`, route readback, and persisted production evidence; return `PENDING` if any missing. | Successful GitHub merge/CI or Render deploy attempt. |
| `MODEL_READY` | Persisted model-row evidence from one V17-certified fitted probability specialist with calibration and `V17_TERMINAL_REDUCER` admission. | A working research canary, app health, odds/heuristics or shadow scoring. |

These are **semantic decision categories**, not presently one emitted six-field artifact. Never fabricate a successful result merely because a label appears in docs. Treat missing evidence as unknown/failed closed. In particular, status `BLOCKED_WITH_EXACT_REASON` is a valid truthful **nonrelease** disposition, not a reason to bypass any predecessor predicate.

## 3. Corrections and practical readback

- **Owner signer already registered:** `.github/release/owner_allowed_signers` contains the public `wow-owner` key and SHA256 fingerprint `BGiNe5ggZrgozxmFBXsFEEyn8szHPHFmZSCN5nJ/+SM`. The earlier comment saying registration is *pending* is historical/stale; it is not the effective verifier state. Updating that comment changes a protected trust-root file and must be reviewed separately; **do not rotate or expose the private key**.
- **Owner-authored trust-root PRs:** `independent_release_checks.py::qa_findings` lines 728–735 allows authenticated owner authorship in the normal policy, and checks exact-head owner review when the author is someone else. Canary mode has different restrictions. Older issue narratives asserting a universal `QA_TRUST_ROOT_OWNER_AUTHORED` block are not current evidence.
- **No invented unified admission gate:** The live ruleset, installed workflows and actual app-published check runs decide their respective lanes. This documentation is the **human-readable binding**, not permission to add another controller or bypass independent findings.
- **Specific Flint example:** [PR #2138](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2138) at `7945cc87c6e497a4cf30aefdcc962d30c2c6e42c` had the native three and deterministic QA/Release checks PASS while independent semantic review from run `41143682909` was substantive `REJECT`. It remains **unapproved** regardless of signer registration or this reconciliation. No production improvement or betting-model readiness was verified.

**Safety invariant:** `custom_gpt_identity=WOW_BETTING_ENGINE`; `runtime_generation=V17_ACTIVE`; `terminal_authority=V17_TERMINAL_REDUCER`; `can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`. This inventory cannot enable live execution or capital allocation.
