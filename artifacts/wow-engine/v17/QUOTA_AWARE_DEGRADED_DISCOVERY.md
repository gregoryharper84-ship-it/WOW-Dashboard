# V17 Quota-Aware Degraded Discovery

This contract is acquisition/orchestration only. It does not alter sporting-model mathematics, fitted artifacts, calibration, rank thresholds, model ownership, terminal authority, or execution posture.

## Runtime invariants

- `runtime_generation=V17_ACTIVE`
- `terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- no market-implied probability substitution
- no generic-reasoning probability substitution

## Behavior

1. Existing public schedule-first discovery remains first for configured team sports.
2. Definitive paid-provider quota/auth/policy failures open a provider circuit for the rest of the current scan.
3. Generic 429/burst throttling remains distinct from definitive quota exhaustion and does not get silently promoted to a quota state.
4. Public scoreboard event IDs remain provider aliases until canonical official identity is independently resolved.
5. A probability-only scan does not require paid market enrichment after sporting probability completion.
6. Every scan emits paid/public acquisition counters and a `BOARD_COVERAGE_STATUS`.
7. If configured-source coverage cannot be proven, `BOARD_COVERAGE_STATUS=PARTIAL_OR_UNPROVEN`; discovered rows still reconcile and are never silently discarded.

## Usage receipt

The `quota_aware_acquisition` block reports:

- `paid_provider_calls_attempted`
- `paid_provider_calls_succeeded`
- `paid_provider_calls_blocked_by_quota_policy`
- `paid_provider_calls_saved_by_cache`
- `paid_provider_calls_saved_by_model_prefilter`
- `paid_provider_calls_saved_by_free_discovery`
- `public_discovery_requests`
- `public_discovery_successes`
- `market_rows_enriched`
- per-provider state/circuit receipts

Counters that cannot be proven by the active acquisition layer remain `0`; the runtime does not invent savings.
