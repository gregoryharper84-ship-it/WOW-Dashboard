# WOW V17 Engineering — AI Resource Resilience (CISA-aligned)

**Change class:** Class A/B engineering observability. **No new controller, workflow, provider spending authority, model, or release gate.**

## Scope

The existing provider dispatcher continues to own routing, provider failures, remote-writer fencing, and deterministic SURVIVAL. This change writes an additional redacted `wow-ai-resource-pressure.json` receipt in the same GitHub Actions artifact and summary. It does **not** change dispatcher selection or authorization. It uses no additional paid model calls.

The receipt distinguishes:

- **Observed provider failure:** the exact allowlisted quota, rate-limit, billing or upstream error already present in a governed route reason.
- **Fresh reported metering:** optional provider-specific `used_tokens`, `limit_tokens`, and `captured_at` supplied by a caller, at most 15 minutes old, not yet provider-attested.
- **Unknown metering:** the default until a verified provider metering source is connected. No metric is inferred from the existence of an API key, billing flag, GitHub workflow, or successful model call.

At reported utilization **70%**, **80%**, and **90%**, the receipt emits informational, warning, or critical advisory bands. At 80% and above it recommends deferring *optional* analysis while retaining P0 repair capacity. This is advisory only: unauthenticated or synthetic meter data can never alter provider routing, bypass protected CI, override approvals, release a writer reservation, reset a circuit breaker, or authorize spending.

## Production integration

The existing `.github/workflows/wow-v17-engineering-provider-dispatcher.yml` invokes the `resource-pressure` subcommand of the already-owned `engineering_provider_failover.py` module for a zero-network, zero-model-call receipt immediately after its native provider route receipt. No new governance trust-root script is registered. The step is `continue-on-error` so observability cannot become a bootstrap deadlock. The receipt is uploaded with the current provider-control artifact for 14-day retention and its pressure band and metering status appear in the Actions summary.

**Initially, expected metering status is `UNAVAILABLE`.** Actual per-provider percentage alerts require a trustworthy, provider-accounted and provenance-validated meter feed; the optional CLI `--usage-receipt` is deliberately advisory and does not attest its own input. Do not treat unknown as 0%, infer credits from token volume, or claim automated budget enforcement from this PR. The existing persistent quota breakers and explicit opt-in fallback budgets remain authoritative.

## Regression and activation

Run:

```bash
python -m pytest artifacts/wow-engine/test_v17_engineering_ai_resource_resilience.py -q
python -m pytest artifacts/wow-engine/test_v17_engineering_provider_failover.py -q
python -m pytest artifacts/wow-engine/test_v17_provider_workflow_identity.py -q
```

Independent exact-head CI and protected source adoption are required. After integration, verify a real provider route yields the resource receipt and summary with no extra remote call, no changed dispatch decision and no regression to existing survival behavior. Then wire *attested* usage from existing telemetry to support meaningful 70/80/90 alarms as a separate reviewed change. Do not install an untrusted usage feed as an admission gate.

## Invariants

`can_execute=false` always. `V17_TERMINAL_REDUCER` remains sole terminal authority. One controlling fitted sport/event or prop specialist remains responsible for probability. No betting/trading execution, synthetic probability, provider billing approval, or change to GitHub protected reviews is included.
