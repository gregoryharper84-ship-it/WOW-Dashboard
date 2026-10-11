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
- `v17/gemini_cost_routing.py`: pure, non-executable tier/urgency/batch/cache routing planner and model-specific offline price-card forecast. Neither adapter nor automatic dispatch is installed.
- No new daemon, scheduler, automatic fallback, production endpoint, or API request
  is enabled by this PR.

## Configuration — leave all disabled until explicit user decision
On an approved, restricted server worker ONLY, configure:
```text
WOW_GEMINI_ENABLED=false
WOW_GEMINI_ACTIVE_TASKS=
WOW_GEMINI_PAID_API_PROJECT_VERIFIED=false
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

## Economics / privacy contract — October 2026 documentation review

Official Google sources:
- [Pricing by model, mode, and effective period](https://ai.google.dev/gemini-api/docs/pricing)
- [Batch API](https://ai.google.dev/gemini-api/docs/batch-api)
- [Caching](https://ai.google.dev/gemini-api/docs/caching)
- [Thinking / output budget](https://ai.google.dev/gemini-api/docs/generate-content/thinking)
- [Additional API terms](https://ai.google.dev/gemini-api/terms)

1. **Paid API project must be independently verified.** `WOW_GEMINI_PAID_API_PROJECT_VERIFIED=true`
   is an operator assertion requiring a checked paid Google Cloud billing project. It is
   *not* verification provided by the flag itself. Even PUBLIC canary calls are blocked
   when this is not true. Google AI Pro subscription does not establish API Paid Tier.
   Do not send raw proprietary data through any tier; privacy/DLP approval still required.
2. **Batch API** can cost ~50% of Standard, but target turnaround is up to 24 hours.
   It is noninteractive and must have durable submission IDs, deduplication, retries,
   out-of-order result mapping, timeout/expiry/cancellation handling, and durable result
   read-back. No Batch submission/poller exists in this scaffold: the offline planner
   returns `GEMINI_BATCH_NOT_IMPLEMENTED`. Immediate CI/P0/pregame freshness cannot
   wait on batch, and must not be silently rerouted to full-price Standard.
3. **Caching** uses minimum token thresholds and model-specific TTL/storage costs. Never
   assume whole repositories can be cached or that cache is free. Explicit caching
   requires content allowlist, redact-first review, provenance version/hash, short
   bounded TTL, deletion plan, billing/retention approval, and verified model support.
   No explicit cache creation/refresh/delete exists; the planner returns
   `GEMINI_CACHE_NOT_IMPLEMENTED`. Implicit caching may occur automatically in
   supported models and must be reflected in provider usage metadata when reported.
4. **Flash-first is a candidate optimization, not a certification**: routine tasks
   may be planned for Flash, deep tasks for Pro, but actual tiers/models and eligibility
   require benchmarked evidence quality. Do not assert 80% share or 10x price advantage
   without workload testing and current rate cards.
5. **2027 pricing**: some published rates expire December 31, 2026 and increase
   January 1, 2027. Forecast both date regimes using the *exact chosen model*
   and pricing mode. Never apply one universal multiplier across models.
6. **Thinking tokens** are billable output. The transport exposes reported
   `thinking_tokens`, `visible_output_tokens`, `cached_input_tokens`,
   and other reported usage counters as non-authoritative accounting metadata.
   `maxOutputTokens` is a response limit, not a strict spend guarantee; small
   limits can truncate reasoning and still accrue charges. `thinkingLevel` /
   `thinkingBudget` are model-family-dependent and are deliberately not
   turned on automatically.
7. **Cost controls**: prior to *any automated* calling, require real durable
   per-project token/cost ledgers, pricing snapshots, budget reservation and
   idempotent settlement, rate limits, task caps, anomaly alerts, and a kill switch.
   No such automation is active. The estimator uses operator-verified price cards
   and returns `FORECAST_ONLY_NOT_BILLING_EVIDENCE`.

Example of a non-executable planning probe:
```python
from v17.gemini_cost_routing import plan_challenger
plan_challenger(
    task="research_challenger", urgency="BACKGROUND", complexity="ROUTINE",
    data_classification="PUBLIC", paid_api_project_verified=False,
    batch_requested=True, explicit_cache_requested=False,
)
# HOLD: GEMINI_BILLING_UNVERIFIED; no network
```

### Engineering + SIRT additions to activation acceptance
- Verify actual paid API Cloud project, not merely an individual Gemini subscription.
- Confirm Google region/data policies, retention, vendor and DLP treatment.
- Verify real use-metadata recording including *thinking* and cached tokens.
- Price samples under 2026 and 2027 rate periods using model/mode-specific cards.
- Compare Flash vs Pro on fixed historical fixtures, not narrative assertions.
- Approve actual Batch and explicit cache adapters as separate work items when
  automated processing is needed. Until then `STANDARD` is manual-only.

## Async + cache lifecycle foundation (standby iteration)

`v17/gemini_async_contracts.py` and
`tests/test_v17_gemini_async_contracts.py` now define **offline-only** REST
manifests and reconciliation. These functions intentionally do not create
Google jobs, cached content, or charge the paid API project. Supported:
- Stable per-row request keys, manifest SHA-256 and provenance refs for inline
  Batch requests. Exact approved task allowlist, max 16 items, text-only,
  BACKGROUND-only; no implicit full-price interactive fallback.
- Provider-reported Batch states PENDING/RUNNING/SUCCEEDED/FAILED/
  CANCELLED/EXPIRED, where terminal failures always hold.
- Key-based result set reconciliation, duplicate and unknown key rejection,
  no list-order-based result attribution; never accept unverifiable findings
  as governed sporting probabilities or independent QA.
- Separate refusal for file-output batches pending reviewed JSONL download
  support and bounded ingestion.
- Cache-manifest preparation from vetted excerpts with content SHA, provenance
  refs and TTL 5–60 minutes; metadata-only cache expiration/identity checks.
  `cacheContents` resource creation, update, deletion and application to
  model prompts are not wired yet.

**Next release boundaries:** real Batch submission needs an atomic durable
reservation/receipt writer and budget gate *before* network POST; polling needs
a durable lease and typed per-row result readback. Explicit cache CREATE must
prove a paid API project, supported model/minimum token count, TTL and retention
approval, and independently reviewed DLP. Neither transport is enabled by
the registry, dispatcher, environment or scheduled jobs.

Documentation: [Google Batch API](https://ai.google.dev/gemini-api/docs/batch-api),
[Google CachedContent API](https://ai.google.dev/api/caching).
