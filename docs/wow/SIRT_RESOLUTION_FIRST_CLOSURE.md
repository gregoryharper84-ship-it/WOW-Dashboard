# SIRT resolution-first engineering closure contract

Status: PROPOSED — independent review required. Parent: #1562; independent assurance: #1563. This document does not grant SIRT engineering, approval, deployment, model-certification, or wagering authority.

## Non-negotiable outcome
Every observed blocker MUST be linked to an existing issue (or a new issue only if no existing issue covers it), a concrete repair plan, a named accountable engineering lane, reproducible evidence, prevention tests, and production acceptance criteria. Repeated reports without a changed receipt MUST point to the same repair and explicitly state why it remains open. An unassigned, unimplemented repair is never called fixed.

## Required finding / repair record
- Finding ID, existing issue URL, severity, sport × market × exact route, environment, exact observed SHA, UTC observation time and evidence URL/query.
- Reproduction steps and observed vs expected behavior; distinguish actual customer Action requests from SELF_ACCEPTANCE probes, model-queue status from auditor backlog, and memory-pressure warnings from proven scoring failures.
- Root-cause hypothesis labeled HYPOTHESIS until reproduced; confirmed root cause with minimal evidence and affected code/config/credential/data boundary.
- Permanent corrective action: specific proposed code, configuration, credential, model, or data repair; implementation PR URL and exact head; dependencies; accountable Engineering/LLP/Props lane and reviewer.
- Prevention: deterministic regression or security-negative test, expected failure before fix and success after fix; specify test artifact and exact head.
- Acceptance: exact deployed SHA, environment, actual user Action receipt and immutable prediction ID when applicable; fitted specialist identity/cert ID, calibrated probability and lower bound, terminal reducer receipt, settlement/skill denominator by exact sport × market × line/period.
- Remaining gaps and next actionable repair, with evidence status VERIFIED / FAILED / EVIDENCE_UNAVAILABLE. No fabricated owners, ETAs, denominators, or approvals.

## Closure state machine
DETECTED -> REPRODUCED -> ROOT_CAUSE_CONFIRMED -> REPAIR_SPECIFIED -> IMPLEMENTED -> REGRESSION_VERIFIED -> INDEPENDENTLY_AUTHORIZED -> DEPLOYED -> PRODUCT_ACCEPTED -> CLOSED.

Each transition requires its own exact-SHA evidence. CI green alone is not DEPLOYED. A merge is not PRODUCT_ACCEPTED. If certification or independent security evidence is absent, remain HOLD. Regression after closure reopens the same issue with a new evidence receipt; never silently downgrade a P0.

## Separation of duties and release-security invariant
SIRT is read-only for assurance; Engineering owns changes; independent QA owns exact-head verification; Release Authority owns governed release; the actual human owner authorization factor must be cryptographically distinct from Engineering's shared GitHub identity in EVERY mode. An issue comment, username match, self-review, or owner-name comparison is never proof of independent human authorization. No `WOW_OWNER_RELEASE_APPROVAL` secret in a QA/Claude job or other QA-reachable scope. QA receives no write token. Owner private signing material must be inaccessible to QA and Engineering. Independent QA and Release GitHub Apps have separate least-privilege installation/credential boundaries; Render deployment credentials are exclusively held by the authorized release path. Workflow trust roots (`.github/**`, `.agents/**`) require a protected, independently approved owner bootstrap, with negative tests for owner-factor absence, spoofing, replay, and privilege crossing. Unsafe or unverified => HOLD, not a temporary owner bridge. Do not alter workflow gates to make a failing release green.

`can_execute=false`; `V17_TERMINAL_REDUCER` is the sole terminal authority. Never promote research-only #1570 or shadow models without independent certification.

## Product acceptance by exact route
Genuine GPT Action -> exactly one fitted and certified specialist -> calibrated probability and uncertainty/lower bound -> immutable pre-event prediction/provenance -> V17_TERMINAL_REDUCER -> response tied to verified deployed SHA. Publish only when each receipt links to the same request/model lineage. If a stage is absent, report typed NO_CAPABILITY / HOLD and the first failing stage. For each sport × ML/spread/prop route include eligible, discovered, hydrated, scored, publishable, held, settled, graded, and forward-calibration denominators; Brier/log loss/ECE and bounds only where defensible. Spread requires exact signed-line cover/push/void evidence; props require stat × line × period × direction. Never substitute sportsbook odds or LLM estimates for fitted model probability.

## Current P0 repair register (proposed, not implementation claims)
| Existing issue | Concrete repair | Prevention and closure receipt |
|---|---|---|
| #1663 / #1577 / #1540 / #1550 | Isolate owner signer and App credentials, enforce protected trust-root authorization and non-write QA | Missing/spoofed owner-factor negative tests fail closed; independent exact-head authorization; credential-boundary audit |
| #1562 / #823 / #502 | Reproduce and repair full-slate acquisition, filtering, and real Action scoring | Complete real slate Golden Path; deployed exact SHA; genuine customer Action -> terminal receipt |
| #662 | Sport-specific fitted ML calibration and lower-bound reliability | Required chronological forward cohort and certified per-sport skill denominators |
| #987 / #999 / #1000 / #1152 | Exact-line spread data, settlement and calibration repairs | Signed-line replay and independent route-specific certification |
| #487 / #491 | Resolve per-artifact prop canary, cohort, calibration and promotion holds | Exact-stat certified artifacts and genuine Action canary; no broad promotion |
| #1514 | Repair auditor watchdog heartbeat-source/freshness reconciliation | Multiple consecutive successful watchdog checks, no false missing-heartbeat alerts |
| #1021 | Durable unattended repair-controller dispatch with least-privilege worker identity | Verified dispatch, bounded execution, audit trail, no self-approval |
| #1690 | Cross-sport production acceptance harness | Independent exact-head review and verified deployed end-to-end receipts |

## Reporting contract
Lead with: FIXED_AND_VERIFIED, PATCHED_NOT_DEPLOYED, REPAIR_IN_PROGRESS, or HOLD. For every blocker show **root cause | repair | owner/PR | prevention test | exact deployment/user acceptance | next action**. Put changes since last audit first. No duplicate GitHub comments on unchanged evidence. A comment may be posted only under an explicitly authorized write scope; read-only audits never comment. Never claim future unattended execution without a verified worker and dispatch receipt.
