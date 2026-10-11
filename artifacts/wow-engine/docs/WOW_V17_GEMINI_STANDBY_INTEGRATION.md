# WOW V17 Gemini — minimal standby evidence challenger (#1655)

## Control and scope
Gemini is an **optional, disabled-by-default, non-authoritative** engineering,
research and SIRT evidence challenger. It is not a fitted model, calibrator,
probability source, independent reviewer/QA authority, release signer,
terminal reducer or wagering executor. `can_execute=false` always.
WOW/LLP/NFL scoring paths and the existing V17 terminal reducer are unchanged.

This deliberately contains NO unattended task schedule, provider Batch/cache
creation, budget ledger, GitHub write privilege or production activation.
Follow-on budget/Batch work belongs in #1657, caching/privacy in #1658.
The Gemini adapter is one-shot, manual only and fail closed.

## Current Free-Tier Google API key
Owner has manually demonstrated authentication and fixed **public synthetic**
content generation in the Render Shell. This does NOT establish the persistent
Render environment variable, data-safe vendor acceptance, or paid API project.
The Free-Tier canary is a separate workstream; **never** set
`WOW_GEMINI_PAID_API_PROJECT_VERIFIED=true` based on the API key, Google AI Pro
subscription or a synthetic response.

## Configuration: OFF unless independently approved
On a reviewed, isolated worker only; NOT the production betting scorer:
```text
WOW_GEMINI_ENABLED=false
WOW_GEMINI_ACTIVE_TASKS=
WOW_GEMINI_MANUAL_CHALLENGE_APPROVED=false
WOW_GEMINI_PAID_API_PROJECT_VERIFIED=false
GEMINI_MODEL=
GEMINI_API_KEY=[protected secret, never commit or print]
```
Read-only status (never opens network):
```bash
cd artifacts/wow-engine
python -m v17.gemini_optional_worker --status
```
After real paid API project, scope, privacy and release approval, an owner
may manually enable one allowed task and supply a reviewed PUBLIC/SANITIZED
fixture via `--task code_review --data-classification PUBLIC --input-file ...`.
Classification is operator attestation, not DLP certification. Never send
source secrets, private records, credentials, personal data, model artifacts,
live betting picks or proprietary WOW files.

## Acceptance / rollback
1. Required exact-head CI passes, independent SIRT security and QA/Release
   checks approved. Receipt schema and DB migration are Class A protected.
   `SYSTEM_ARCHITECT_AGENT_REQUIRED=true` until signed architect decision.
2. Authorized migration and read-back: Gemini engineering evidence receipt
   accepted; Gemini QA/release/deployed SHA/FIXED_AND_VERIFIED attempts rejected
   in both Python and Postgres.
3. Verify disabled/default state generates **zero** HTTP calls. Verify
   malformed/429/PII/private input holds with typed codes, redacted output.
4. Establish paid project terms/budget, strict DLP and task policy before
   any dynamic project material or unattended traffic. Separate approved free
   canary is immutable synthetic text only.
5. On anomaly, set `WOW_GEMINI_ENABLED=false`; no effect on existing
   WOW/LLP/prop model scoring. Never promote Gemini evidence to governed
   sporting probabilities.

No deploy, live Gemini execution or independent certification is claimed by
this draft.
