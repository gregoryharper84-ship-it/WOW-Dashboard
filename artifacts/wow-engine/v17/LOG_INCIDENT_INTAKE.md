# WOW V17 — read-only log-to-incident intake (source candidate)

## Boundary

This change adds only a detector/evidence producer into the existing WOW
Engineering Reporter/Resident Supervisor architecture. It does not create a
second repair controller, auto-create issues, acquire a writer lease, or grant
merge, deployment, probability or execution authority. Never treat a successful
GitHub Action or a candidate finding as a verified production repair.

## Trigger and credentials

Workflow .github/workflows/wow-v17-log-incident-intake.yml polls the last two
hours hourly (intentional one-hour overlap) **only** when repository variable
WOW_V17_LOG_INTAKE_ENABLED is exactly true. GitHub secrets
WOW_DATADOG_LOG_READ_API_KEY and WOW_DATADOG_LOG_READ_APP_KEY must contain a
read-only Datadog API/application key pair and must never be copied to source
or a report. Optional WOW_DATADOG_SITE must match a fixed allowlist.

Missing credentials, timeout, HTTP 429, invalid schema, incomplete pagination
or oversized page produce SOURCE_UNAVAILABLE and a nonzero workflow result,
not a false NO_ACTION. Disabled workflow does not mean telemetry is healthy.
The GitHub Action produces a 7-day redacted evidence artifact; it cannot
write incidents or dispatch workers.

## Typed candidate evidence and independent admission

v17/log_incident_intake.py accepts only the two exact WOW services (scoring API and engineering worker) and a
strict time window. It emits only known codes, route-template IDs, severity,
windowed counts, sanitized versions, and stable SHA-256 dedupe fingerprints.
No raw messages, URLs, query strings, request/response payloads, credentials,
customer identifiers, untrusted LLM instructions or raw stack traces persist.
Root-path 404 probes are noise; thresholded 422/401/5xx and known model/queue
failures are candidates requiring reproduction. Worker warnings have an exact
allowlist of non-success outcomes including REMOTE_WRITER_RECONCILIATION_REQUIRED.
These are indexed by Datadog at info severity despite their application warning
text. Repeated worker holds are P1 candidates; never auto-release a fenced
writer or turn a candidate into a GitHub writer command.

**Required next integration before autonomous repair:** the authorized
Reporter/supervisor must consume the artifact under its existing authenticated
incident ledger and atomic single-writer lease. Reporter verifies
completeness, matches the fingerprint against durable incident history,
attaches source evidence, assigns the canonical issue ID and reentry trigger.
The existing Engineering dispatcher may only admit a governed approved task
after severity, WIP, source proof, writer-identity and lease admission; never
directly from an untrusted log. Independent QA, Release and SIRT verify exact
SHA and production acceptance. If the bridge is absent or blocked, retain
typed REPORTER_BRIDGE_NOT_WIRED status; do not claim zero-touch self-healing.

## Local verification

Run:

    python -m pytest -q artifacts/wow-engine/test_v17_log_incident_intake.py artifacts/wow-engine/test_v17_log_intake_workflow.py

This is Class A read-only observability and a protected Class B workflow
trust-root requiring independent review and authorized integration. Sporting
probabilities, models, calibration, bounds, qualification decisions,
V17_TERMINAL_REDUCER and execution restrictions remain unchanged.

Preserved exactly:
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
