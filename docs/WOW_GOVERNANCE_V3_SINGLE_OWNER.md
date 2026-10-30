# WOW Governance V3 — Single-Owner, Capability-Aware Engineering

**Status:** Proposed until this policy and its accompanying tests are merged by the authorized owner through the repository's effective protected-branch rules.  
**Scope:** WOW-Dashboard engineering release, reviewer infrastructure, NFL/NBA/NCAAF restoration.  
**Incident:** #2136. **Supersedes:** the distinct-human-review release requirement in `docs/WOW_GOVERNANCE_V2.md` and the temporary Class D/S proposal once adopted.

## 1. Operational reality and explicit risk choice

This repository is operated by **one authorized human owner** with engineering automation. A distinct, separately authenticated human Security/Architect is not a continuously available organizational capability. For this operating model, the owner deliberately accepts the reduced independence of a single-owner engineering release process.

Do not fabricate another principal, treat an Engineering App's byte-for-byte copy of owner source as independent human authorship, or convert an AI outage or `HOLD` into a semantic `PASS`.

Independent automated semantic review may run as **additional advisory evidence**, with strict provenance. Its absence or credential failure is not a required GitHub merge check under the three-check ruleset baseline. An actual substantiated high/critical security finding or substantive security REJECT is **not** a provider-unavailability condition; resolve and re-review the source or stop the release. Retain rejected and held receipts without rewriting history. This policy is a reduction in assurance, not equivalent assurance to the former independent human requirement.

## 2. Two engineering release categories

### R — Routine engineering
Data ingestion, log/diagnostic improvements, integration repair, scorer transport/retry changes that do not change probability logic, non-semantic code fixes, and recovery tooling.

- Require the three existing GitHub-protected exact-head regression checks.
- Run applicable targeted tests. Preserve identity, credential, and `can_execute=false` safeguards.
- Only an authorized GitHub merge/release actor may merge.
- Validate the deployed Render web/worker revisions and affected behavior independently of CI.
- Do not infer model readiness from an engineering deployment.

### P — Protected engineering
Changes to `.github/**`, reviewer decision behavior, secret/permission boundaries, trusted agent workflows, release safety, or a file consumed as a governance trust root. The reviewer category/schema change in PR #2301 is **P**, not diagnostic-only.

- Require the same three real GitHub checks at the exact final head, plus scoped security-negative and compatibility tests for the changed trust boundary.
- If the original PR is authored by the authenticated repository owner, authorship is **permitted**; no impossible self-approval or App copy is required. Owner authorization to release is established by an intentional authenticated GitHub merge after the other requirements pass, not by CI or the PR author's identity alone.
- If another human/bot authors a protected PR, require the authenticated owner's exact-head `APPROVED` review before QA's owner-authorization evidence passes.
- QA status describes **deterministic exact-head evidence**, not independent human/AI review. Optional semantic-review receipts retain their native status.
- A real security issue, malformed changed-policy behavior, insufficient regression coverage or missing required GitHub check must be fixed, not waived by status relabeling.
- Preserve historical REJECTs. A new source head and fresh review are needed to reconsider a prior substantive rejection.

### Category interaction
A change to probability logic, model artifacts, calibration or thresholds may also be protected engineering, but **model activation is a separate gate**, below. Engineering admission does not authorize probability publication.

## 3. Exactly three required GitHub regression checks

Ruleset `24907422` was observed to require:
- `WOW required-three regression`
- `WOW governed probability backend`
- `WOW additional required regression`

The installed ruleset is the source of truth. Verify its live effective requirements and exact-head app provenance at each release; this document does **not** change branch protection. If the ruleset differs, follow the actual GitHub enforcement and record drift.

The optional `WOW Independent Security Review` check and legacy QA/Release App checks do not become additional merge gates through documentation alone. An optional review that reports `HOLD` remains `HOLD`; it is not proof of security review or a manufactured `PASS`.

## 4. Release invariant: no semantic security laundering

Never:
- Rename a substantive REJECT as reviewer unavailability, or shop providers after a genuine security rejection.
- Treat an untrusted PR body or App copy as distinct authenticated approval.
- Suppress stale-head checks, real test failures, secret detection, unsafe workflow permissions, or `can_execute=false` protections.
- Claim independently reviewed when only deterministic CI and the single owner authorized the release.
- Auto-deploy or declare production LIVE merely because a PR merged.
- Treat a missing external paid reviewer credential/credit as a model-scoring failure.

A release decision must distinguish `CI_PASS`, `OWNER_AUTHORIZED`, `SECURITY_FINDINGS_RESOLVED`, `INDEPENDENT_REVIEW_NOT_PERFORMED`, `PRODUCTION_VERIFIED` and `MODEL_READY`.

## 5. V17 model-activation contract — never weakened

`BACKEND_RUNTIME`, `MODEL_CAPABILITY`, `REPOSITORY_GOVERNANCE`, and `LIVE_GPT_EDITOR_SYNC` are separate fields. Code that repairs a model or data route may deploy in disabled/shadow form before model publication.

A model route only publishes an official governed probability when the exact controlling fitted specialist returns a valid numeric package, with required features, failure-path modeling, dynamic calibration, valid bounds, freshness, immutable pregame recording when required, and `V17_TERMINAL_REDUCER` approval.

`MODEL_UNAVAILABLE` is only actual certified capability/artifact absence; `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`, `MARKET_DATA_UNOBTAINABLE` and invocation failures remain distinct. Sportsbook implied probability and generic reasoning are not governed substitutes. Rankings use the governed calibrated lower bound where required.

Always keep:
```text
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE (when verified)
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## 6. Recovery workstreams

NFL, NBA and NCAAF model restoration can progress on independent engineering branches/PRs. A separate diagnostic PR (#2299) and its existing rejected findings do **not** block these lanes. Do not close, rewrite or retroactively pass #2299's historical receipt.

For each sport, separate transport, inputs, scorer, fitted model artifact, calibration, persistence, market evidence and production deployment. Advance every independently valid repair, preserve typed failures, and do not publish unsupported sporting probabilities.

## 7. Applied migration and verification

1. Adopt this policy via an explicit authorized protected GitHub merge with effective required checks passed; do not infer adoption from this draft PR.
2. Update exact-head QA decision logic so authenticated owner authorship is not a `QA_TRUST_ROOT_OWNER_AUTHORED` failure. Preserve owner approval for non-owner-authored protected PRs. Keep existing source-review verification when explicitly selected.
3. Reconcile regression tests and the failure-code registry. Historical `QA_TRUST_ROOT_OWNER_AUTHORED` remains in old receipts, but is retired as a new V3 QA finding.
4. Revalidate PR #2301's structured severity/category schema, versioned consumers, malformed output and substantive REJECT behavior before its merge. Do not assume this policy makes its code correct.
5. Check required CI and authenticated merge at the final SHA, then verify Render deployment parity and behavioral acceptance. Document the exact live revisions, or state deployment is not verified.
6. Keep `LIVE_GPT_EDITOR_SYNC` separate: editor installation is verified only after live instructions and Actions are saved and tested.

**Acceptance:** no reviewer-identity deadlock for owner-authored protected changes; no bypass of required GitHub regression/security-negative tests; no false independence claim; no change to V17 model qualification or execution safety.
