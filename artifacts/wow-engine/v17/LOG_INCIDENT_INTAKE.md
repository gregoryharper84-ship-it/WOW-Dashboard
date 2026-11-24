# WOW V17 — read-only log-to-incident intake (source candidate)

## Boundary

This change adds only a detector/evidence producer into the existing WOW
Engineering Reporter/Resident Supervisor architecture. It does not create a
second repair controller, auto-create issues, acquire a writer lease, or grant
merge, deployment, probability or execution authority. Never treat a successful
GitHub Action or a candidate finding as a verified production repair.

## Trigger and credentials — RETIRED (2026-11-22, #1021)

Datadog is no longer a WOW source. Workflow
.github/workflows/wow-v17-log-incident-intake.yml runs only the pull_request
offline security/regression job. It has no scheduled, manual or
variable-gated fetch job and references no Datadog credential. The resident
Reporter stays off unless WOW_V17_LOG_REPORTER_ENABLED is exactly true; with
no producer run it holds as REPORTER_RUN_PROVENANCE_UNVERIFIED and does not
block engineering dispatch. Do not reconnect, retry or pay for Datadog.

The sections below describe the retired producer and are kept as forensic
reference for v17/log_incident_intake.py and the Reporter consumer contract.

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
