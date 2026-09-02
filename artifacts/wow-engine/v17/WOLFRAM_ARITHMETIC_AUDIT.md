# WOW V17 WolframAlpha Arithmetic Audit

## Authority boundary

`wolfram_arithmetic_auditor.py` is an independent deterministic arithmetic
checker. It does not create sporting probability, select a side, calibrate a
model, resolve correlation, or publish a terminal decision.

The controlling fitted specialist remains the sole source of sporting
probability. A WolframAlpha failure may hold a market, payout, no-vig, push, or
EV claim, but it may not erase a completed probability package or be relabeled
as `MODEL_UNAVAILABLE`.

## Production configuration

```text
WOW_WOLFRAM_ARITHMETIC_AUDIT_ENABLED=1
WOLFRAM_ALPHA_APP_ID=<server-side Render secret>
WOW_WOLFRAM_TIMEOUT_SECONDS=5
```

The App ID is backend-only. It must never be accepted in a request, returned in
a response, persisted in an audit receipt, or added to a Custom GPT Action.

## Closed calculation templates

The backend accepts only server-built claims from the closed template registry:

- `PROBABILITY_TOTAL`
- `PROBABILITY_COMPLEMENT`
- `AMERICAN_ODDS_IMPLIED_PROBABILITY`
- `DECIMAL_ODDS_IMPLIED_PROBABILITY`
- `MARKET_HOLD`
- `TWO_WAY_NO_VIG`
- `PUSH_ADJUSTED_PROBABILITY`
- `POWER_JOINT_BREAK_EVEN`
- `EQUAL_LEG_BREAK_EVEN`
- `GROSS_RETURN`
- `NET_PROFIT`
- `EXPECTED_GROSS_MULTIPLIER`
- `EXPECTED_VALUE_PER_DOLLAR`
- `FIXED_ODDS_EXPECTED_PROFIT`
- `FIXED_ODDS_BREAK_EVEN_UNCONDITIONAL`

Arbitrary caller-supplied Wolfram expressions are prohibited. Each result is
calculated locally with `Decimal`, independently evaluated by the WolframAlpha
Full Results API, and compared with the value the governed backend would
publish.

## Typed results

```text
PASS
NOT_REQUIRED
WOLFRAM_AUDIT_DISABLED
WOLFRAM_AUDIT_INPUT_INVALID
WOLFRAM_AUDIT_UNAVAILABLE
WOLFRAM_OUTPUT_INVALID
WOLFRAM_CALCULATION_MISMATCH
WOLFRAM_AUDIT_LEDGER_WRITE_UNPROVEN
```

`PASS` is required before an enabled production path may represent affected
market/payout arithmetic as verified. Every receipt includes the template,
input hash, local result, provider result, reported result, deltas, tolerance,
timestamp, and `can_execute=false`; it never includes the App ID.

Every required attempt is appended to `wow_wolfram_arithmetic_audits` after
the governed prediction exists. The table is hash-bound to the receipt,
references the exact prediction, blocks update/delete, denies client roles,
and grants the service role insert/select only. If that write is unproven, the
provider verdict remains visible as `provider_verdict`, while the effective
arithmetic verdict becomes `WOLFRAM_AUDIT_LEDGER_WRITE_UNPROVEN`.

## Tolerances

```text
probability and normalization: 0.000001
general EV/multiplier arithmetic: 0.000001
currency: 0.01
```

Callers may request a tighter tolerance but cannot widen the governed default.

## Objective separation

When the audit is required and does not pass:

- `MODEL` remains governed by the fitted specialist and calibration contract;
- `MARKET`, `SETTLEMENT`, and/or `MONEY` carry the typed Wolfram hold;
- terminal reduction cannot advance the affected economics claim;
- `can_execute=false` remains unconditional.
