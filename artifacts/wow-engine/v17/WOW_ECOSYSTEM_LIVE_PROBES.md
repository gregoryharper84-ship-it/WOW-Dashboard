# WOW Ecosystem Conductor — Phase 2 Live Probe Contract

**Change class:** A  
**Probability authority:** none  
**Execution authority:** can_execute=false  
**Terminal authority:** V17_TERMINAL_REDUCER

## Purpose

Phase 2 converts the static ecosystem registry into a read-only evidence collector. It does not infer one component's success from another component's health.

Every probe emits a normalized receipt containing:

- probe_id
- probe_type
- observed_at
- status: PASS | DEGRADED | FAIL | BLOCKED | UNKNOWN | NOT_APPLICABLE
- evidence_refs
- first_failing_boundary
- typed_failure
- source_version where applicable
- deployed_sha where applicable
- can_execute=false

Missing configuration, missing credentials, unreachable evidence, absent receipts, pending exact-head CI, or missing durable proof never becomes PASS.

## Probe families

### GitHub exact-head

Reads pull-request workflow evidence for the exact commit SHA and requires the configured protected workflow names to exist and complete successfully.

### Render runtime

Reads backend health, governance, and host-contract endpoints and requires deployed-SHA identity when configured.

### Supabase durable evidence

Reads only the configured proof/telemetry table through Supabase REST. Empty query results are UNKNOWN/DURABLE_RECEIPT_NOT_FOUND, never success.

The default configuration distinguishes Action invocation telemetry from certification proof. The former proves transport activity only; the latter is a stronger exact-route proof and is not used as a substitute for model authority.

### Action / MCP contract

Reads the governed host contract from the configured backend origin. Authentication failures remain BLOCKED; transport ambiguity remains UNKNOWN.

### Typed receipt files

Scout, Betting Intelligence, Kalshi Weather, Systems Intelligence, Engineering handoff, Independent Verification, persistence, and user-response boundaries are consumed as explicit typed receipt artifacts. Missing receipt paths fail closed.

These file adapters are transitional evidence ingestion points. Phase 3 replaces ad-hoc receipt locations with the durable connection/work ledger while preserving the same normalized receipt schema.

## Work conservation

The Systems Intelligence receipt may carry the current work_items array. It is passed directly into the deterministic Conductor work-conservation validator.

The live probe layer does not rewrite owners, blockers, verification state, change class, or terminal disposition.

## Required environment

The default probe configuration names these environment bindings:

- GITHUB_REPOSITORY
- GITHUB_SHA
- GITHUB_TOKEN
- WOW_BACKEND_ORIGIN
- WOW_ACTION_API_KEY
- RENDER_GIT_COMMIT
- SUPABASE_URL
- SUPABASE_SERVICE_KEY
- WOW_USER_REQUEST_RECEIPT
- WOW_CONDUCTOR_RECEIPT
- WOW_PRODUCT_ORCHESTRATION_RECEIPT
- WOW_SCOUT_RECEIPT
- WOW_LLP_ROUTE_RECEIPT
- WOW_KALSHI_WEATHER_RECEIPT
- WOW_PERSISTENCE_RECEIPT
- WOW_SYSTEMS_INTELLIGENCE_RECEIPT
- WOW_ENGINEERING_HANDOFF_RECEIPT
- WOW_INDEPENDENT_VERIFICATION_RECEIPT
- WOW_USER_RESPONSE_RECEIPT

Secrets are read only from environment variables and are never placed in evidence_refs or output.

## Acceptance

Phase 2 is complete when:

1. every V1 component has a configured authoritative probe binding;
2. every V1 handoff has a configured authoritative probe binding;
3. absent/unreachable evidence stays UNKNOWN/BLOCKED/FAIL;
4. exact-head GitHub proof cannot pass with missing or pending required workflows;
5. Render runtime health cannot substitute for deployed-SHA identity;
6. Supabase empty durable evidence cannot pass;
7. missing persistence proof makes WOW Prop and LLP golden paths NOT_READY;
8. all-green authoritative fixtures prove WOW Prop, LLP Team/Event, and Kalshi Weather golden paths READY;
9. can_execute remains false;
10. no sporting or weather probability behavior changes.

Dynamic-import regression note: test loaders register the module in sys.modules before dataclass evaluation so CI collection matches normal import semantics.
