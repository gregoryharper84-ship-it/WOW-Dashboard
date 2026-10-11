# WOW V17 — Gemini Standby Integration (Class A/B, OFF by default)

## Contract
Gemini is a **non-authoritative evidence challenger** behind WOW_ECOSYSTEM_CONDUCTOR.
It is NOT a sporting/meteorological probability specialist, calibration service,
terminal reducer, QA authority, release authority, market-order engine, or credential owner.
Existing LLP/WOW/Kalshi golden paths remain unchanged. `can_execute=false`.

## What is prebuilt
- `v17/gemini_optional_worker.py`: Gemini generateContent HTTPS transport, strict
  text-only task allowlist, input-classification gate, response validation, timeout,
  and typed fail-closed errors.
- `v17/ecosystem-registry.json`: disabled optional challenger, noncritical request/return handoffs.
- `v17/engineering_receipts.py` and migration: allow Gemini evidence receipts only;
  no independent QA PASS, release authorization, deployed SHA, or FIXED_AND_VERIFIED.
- Tests and canonical failure-code entries.
- No new daemon, scheduler, automatic fallback, production endpoint, or API request
  is enabled by this PR.

## Configuration — leave all disabled until explicit user decision
On an approved, restricted server worker ONLY, configure:
```text
WOW_GEMINI_ENABLED=false
WOW_GEMINI_ACTIVE_TASKS=
GEMINI_MODEL=
GEMINI_API_KEY=[server secret; never commit]
```
Safe status / manual canary entrypoint:
```bash
cd artifacts/wow-engine
python -m v17.gemini_optional_worker --status
# only after approved activation:
python -m v17.gemini_optional_worker --task code_review \\
  --data-classification PUBLIC --input-file /path/to/reviewed_public_fixture.txt
```
No background workers, cron, or automatic trigger is installed.

Activation requires setting `WOW_GEMINI_ENABLED=true`, a reviewed model identifier,
`GEMINI_ACTIVE_TASKS` under `WOW_GEMINI_ACTIVE_TASKS` (comma-separated), and the
API key in the server secret store. Supported tasks are `code_review`,
`research_challenger`, `sirt_challenger`, and `implementation_draft`.
These are *analysis only*, even if called an implementation draft.
A separate approved caller must invoke `run_challenger` with `PUBLIC` or
`SANITIZED` text; never transmit raw tickets, PII, credentials, model artifacts,
sensitive business data, or service-role secrets. The classification flag is a
caller attestation, not a complete DLP scrubber. Do not wire an unattended caller
until a dedicated sanitization/privacy review exists.

## Activation checks
1. Merge with exact-head governance, independent review, QA, and approved release.
2. Apply/read back the SQL migration in the right Supabase environment.
3. Verify prior providers still write receipts; Gemini evidence-only provider is
   accepted; Gemini QA/release authority is rejected at both Python and DB layers.
4. Run unit tests:
   `cd artifacts/wow-engine && python -m pytest -q tests/test_v17_gemini_optional_worker.py test_v17_engineering_receipts.py tests/test_v17_ecosystem_conductor.py`
5. Configure Google AI Studio **project spend cap** and account billing/rate limits.
   A project cap can lag and is NOT a strict real-time cost circuit breaker.
   Before scheduling autonomous use, add durable usage accounting and hard quota gates.
6. After owner approval: install API key, select current supported model, authorize
   only the reviewed task subset, and perform a single redacted canary.
7. Collect latency/cost, schema-valid output, evidence quality, 429/5xx behavior,
   privacy audit, and actual read-back receipts. Independent Verification signs off.
8. On any anomaly: flip `WOW_GEMINI_ENABLED=false`. Existing production
   scoring paths must function unchanged without Gemini.

## No automatic promotion
Gemini output is `UNVERIFIED_CHALLENGER_OUTPUT`; evidence must be checked by
Engineering/SIRT. No Gemini output may set probability, rank, calibration,
specialist ownership, terminal disposition, or release/QA acceptance. Missing
Gemini capacity must never turn into MODEL_UNAVAILABLE for a fitted specialist.
