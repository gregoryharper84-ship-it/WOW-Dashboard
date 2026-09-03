# Skill: wow.kalshi-betting-assistant

## Skill Name

**WOW Kalshi Betting Assistant**

## Version

```text
skill_version=1.0
runtime_generation=V17_ACTIVE
host=WOW_BETTING_ENGINE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Short Description

A V17 Kalshi-specific orchestration skill that ingests Kalshi screenshots, PDFs, pasted contracts, tickers, or discovered markets; canonicalizes exact settlement conditions; routes each contract to exactly one certified controlling probability specialist; maps the returned sporting/weather distribution onto the exact Kalshi YES/NO settlement event; audits live executable price, fees, spread, liquidity, and lower-bound edge; searches adjacent thresholds from the same governed distribution; applies duplicate-thesis and portfolio governance; performs final refresh; and returns a ranked research-only Kalshi leaderboard.

This skill is **not** a new sporting probability model and is **not** a terminal authority.

---

## Purpose

The skill answers:

```text
Given a Kalshi board or set of contracts, which exact contracts are supported by a governed WOW V17 probability package, remain favorable on the exact Kalshi settlement basis after calibration and market frictions, and survive portfolio/final-refresh governance?
```

It exists to make Kalshi a first-class V17 workflow without moving baseball, football, soccer, weather, or other domain modeling into a generic Kalshi model.

---

## Architectural Position

```text
WOW V17
  -> wow.kalshi-betting-assistant
      -> board/contract extraction
      -> exact settlement normalization
      -> deterministic specialist routing
          -> WOW prop lane for player/scalar props
          -> LLP_TEAM_BETTING_ENGINE for team/event winner probabilities
          -> Kalshi Weather Market Expert for supported weather contracts
          -> other certified specialist when explicitly supported
      -> governed probability package
      -> Kalshi settlement transformation
      -> dynamic calibration/lower-bound preservation
      -> exact-line/payout/fee/edge audit
      -> threshold-value search
      -> Kalshi portfolio risk/combo governor
      -> final refresh
      -> V17_TERMINAL_REDUCER
```

### Non-Negotiable Ownership Rule

Exactly one controlling specialist owns the sporting or weather probability for each contract.

The Kalshi Betting Assistant may discover, extract, normalize, route, reconcile, transform, audit, rank, and present. It may **not** invent a probability when the controlling specialist is unsupported, unavailable, or fails.

---

## Direct Invocation Phrases

Treat the following as equivalent orchestration requests when Kalshi context is clear:

```text
Run Kalshi Betting Assistant
Scan Kalshi
Scan this Kalshi board
Analyze attached Kalshi markets
Find the best Kalshi contracts
Run V17 on this Kalshi board
Kalshi full model
Kalshi sports props
Kalshi weather markets
```

A focused request containing only a supported weather contract may route directly through the Kalshi Weather Market Expert, but the broad multi-category Kalshi workflow remains owned by this assistant.

---

## Supported Input Modes

```text
KALSHI_SCREENSHOT
KALSHI_PDF
KALSHI_PASTED_BOARD
KALSHI_TICKER_LIST
KALSHI_CONTRACT_URL_OR_METADATA
KALSHI_AUTONOMOUS_DISCOVERY
KALSHI_OPEN_POSITION_REVIEW
KALSHI_SETTLED_POSITION_POSTMORTEM
```

For screenshot/PDF input, extract every readable contract before filtering. Do not silently collapse the board to one sport or one market family.

Screenshot prices are discovery evidence only and cannot satisfy live executable-price qualification.

---

## Canonical Contract Schema

Normalize every contract into:

```text
row_id
platform=KALSHI
contract_id_or_ticker
series_ticker
market_title
market_family
sport_or_domain
league_or_series
event_id
event_date
event_start_time_utc
subject
opponent_or_counterparty
period
stat_or_measurement
threshold_or_bracket
boundary_operator
side=YES|NO
contract_wording
settlement_source
settlement_rule_version
void_cancel_correction_rule
source_snapshot_id
board_timestamp
extraction_confidence
```

Build a canonical settlement identity:

```text
kalshi_settlement_identity_key =
  event_or_measurement
  + date
  + participant_or_location
  + period
  + stat_or_measurement
  + threshold_or_bracket
  + boundary_operator
  + official_settlement_source
  + void_cancel_correction_rule
```

No model/market comparison is valid until the settlement identity is resolved.

---

## Contract Interpretation Gate

Translate the contract into explicit mathematical settlement states.

Examples:

```text
"9+ strikeouts"          => K >= 9
"over 8.5 runs"         => R > 8.5
"team wins by 2+"       => margin >= 2
"YES 75F to 76F"        => 75 <= daily_high <= 76
"NO 75F to 76F"         => daily_high outside [75,76]
```

Explicitly preserve any third state:

```text
YES_WIN
NO_WIN
VOID_OR_CANCEL
```

Do not force YES + NO = 1 when the exact contract rules create a material void/cancel/correction state.

---

## Routing Matrix

### Player / Scalar Props

Route to the WOW prop lane and exact certified specialist.

Examples:

```text
pitcher strikeouts
player hits
home runs
RBIs
points
rebounds
assists
passing/rushing/receiving stats
player shots/goals/aces/etc.
```

Use the canonical V17 prop scoring Action when available. If the exact route is unsupported, preserve the backend's typed unsupported/OOD result.

### Team / Event Winner

Route to:

```text
LLP_TEAM_BETTING_ENGINE
```

Examples:

```text
ML / outright winner
match winner
fight winner
tournament/event winner
favorite/underdog/upset probability
```

### Weather

Supported daily-high temperature contracts route to the **Kalshi Weather Market Expert**.

The Weather Expert remains the controlling probability specialist for those contracts, including station identity, forecast distribution, intraday truncation, and settlement-specific weather logic.

### Spread / Total / Team Total / Period Markets

Route only when an explicit certified V17 specialist exists for the exact sport, event, period, and settlement basis.

Otherwise:

```text
MODEL_UNAVAILABLE
```

or the exact typed backend unsupported result.

Do not convert a moneyline model, sportsbook implied probability, generic simulation, or narrative analysis into a spread/total probability unless that route is certified.

### Unsupported Domain

Fail closed.

```text
controlling_specialist=UNRESOLVED_OR_UNAVAILABLE
rank_eligible=false
can_execute=false
```

---

## Governed Probability Contract

The assistant may consume only an Action-returned or otherwise V17-certified package such as:

```text
controlling_specialist
model_version
model_probability
calibrated_probability
calibrated_lower_bound
calibrated_upper_bound
failure_path_score
probability_publishable
rank_eligible
model_as_of
model_status
blockers
```

Never relabel these as governed model probability:

```text
Kalshi displayed chance
Kalshi midpoint
Kalshi bid/ask
sportsbook implied probability
sportsbook no-vig probability
external projection
recent hit rate
historical raw frequency
LLM judgment
Scout/Research opinion
```

A missing market price may block edge publication but must not erase a completed sporting probability.

---

## Kalshi Settlement Transformation

The probability produced by the controlling specialist must be transformed onto the **exact Kalshi settlement event**.

Examples:

```text
Pitcher strikeout PMF -> P(K >= 9)
Run distribution      -> P(total > 8.5)
Margin distribution   -> P(team margin >= 2)
Temperature PDF       -> P(75 <= H <= 76)
```

Required audit:

```text
model_settlement_basis_matches_kalshi=true
```

If false or unresolved:

```text
KALSHI_SETTLEMENT_BASIS_MISMATCH
rank_eligible=false
```

Market prices must never be used to repair a missing model transformation.

---

## Threshold Curve Engine

### Purpose

When Kalshi offers multiple nested thresholds for the same latent variable, derive all probabilities from **one governed underlying distribution** rather than independently reasoning about each contract.

Examples:

```text
K >= 8
K >= 9
K >= 10

runs > 7.5
runs > 8.5
runs > 9.5
```

Assign:

```text
threshold_family_id
underlying_distribution_id
```

### Monotonicity Rules

For increasing upper-tail thresholds:

```text
P(X >= a) >= P(X >= b) when a < b
```

For increasing lower-tail thresholds:

```text
P(X <= a) <= P(X <= b) when a < b
```

For mutually exclusive brackets, enforce coherent bracket coverage and preserve explicit residual/void states.

If model outputs violate mathematical monotonicity beyond numeric tolerance:

```text
MODEL_OUTPUT_INVALID
reason=KALSHI_THRESHOLD_CURVE_INCOHERENT
```

Do not manually smooth probabilities to manufacture coherence unless the certified model contract explicitly defines a reconciliation method.

---

## Threshold Value Search

After a governed distribution exists, inspect adjacent Kalshi thresholds from the same family when fresh market evidence is available.

For each threshold calculate independently:

```text
calibrated_probability
calibrated_lower_bound
executable_break_even_probability
point_edge
lower_bound_edge
```

Return:

```text
requested_contract_result
preferred_threshold_if_any
threshold_family_rank
```

A better adjacent threshold may be recommended for research, but the exact requested contract must remain in the reconciliation output.

---

## YES / NO Bidirectional Audit

Evaluate the two sides separately on their executable economics.

Where the settlement is binary with no material third state:

```text
P_NO = 1 - P_YES
```

Where void/cancel is possible:

```text
P_YES + P_NO + P_VOID = 1
```

For market economics retrieve, when available:

```text
yes_bid
yes_ask
no_bid
no_ask
orderbook_timestamp
market_status
fee_basis
```

Do not assume the most likely side is the best-priced side.

---

## Exact Price / Fee / Edge Audit

Consume the WOW Exact Line, Payout, Push & Two-Way No-Vig Edge Auditor rules.

Required before edge qualification:

```text
exact contract verified
exact settlement verified
market open
non-empty orderbook
fresh executable price
correct side identified
fee schedule verified when material
spread/slippage treatment verified
model settlement basis matches contract
```

Screenshot-only price:

```text
price_status=DISCOVERY_ONLY
edge_verified=false
```

### Edge Metrics

Primary calculations:

```text
point_edge = calibrated_probability - effective_break_even_probability
lower_bound_edge = calibrated_lower_bound - effective_break_even_probability
```

Where required, incorporate verified fee/slippage economics into the effective break-even or separately report:

```text
friction_adjusted_point_edge
friction_adjusted_lower_bound_edge
```

The official Kalshi opportunity leaderboard ranks primarily by **positive friction-adjusted lower-bound edge**, subject to V17 rank eligibility and market qualification.

A positive point edge with non-positive lower-bound edge is not a verified edge.

---

## Market Quality Score

Market quality is a **separate market attribute**, never a sporting probability modifier.

Recommended inputs:

```text
price_freshness
bid_ask_spread
book_depth
recent_volume
contract_clarity
settlement_clarity
time_until_lock
price_volatility
both_sides_available
```

Return:

```text
market_quality_score_0_100
market_quality_tier=HIGH|MEDIUM|LOW|UNRESOLVED
```

Never modify:

```text
model_probability
calibrated_probability
calibrated_lower_bound
```

because of liquidity, volume, spread, or popularity.

---

## Research / Scout Responsibilities

Research and Scout may acquire and reconcile:

```text
schedule/event identity
starters/lineups/rosters
injuries/status
weather
role/workload
official rules
settlement source
market metadata
live orderbook evidence
historical context
```

They never publish sporting probability.

Material contradiction evidence must reach the certified model input package when the specialist supports that feature. Otherwise report it as a downstream evidence/risk flag without manually adjusting model probability.

---

## Wolfram Arithmetic Verification

Wolfram is an arithmetic verifier only.

It may verify:

```text
PMF/CDF transforms
YES/NO complement math
bracket integration
break-even calculations
fee-adjusted payout math
no-vig transforms
push/void probability identities
expected value arithmetic
joint-probability arithmetic
Frechet bounds
```

It may not create:

```text
sporting probability
weather forecast distribution
player distribution
team win probability
```

Only backend-governed Wolfram audit receipts count as verified. A Wolfram mismatch/unavailable result may hold the affected transformation/edge claim but must not erase a completed sporting probability.

---

## Portfolio / Duplicate-Thesis Governance

Every individually qualified Kalshi row must pass `wow.kalshi-portfolio-risk-combo-governor` before being presented as part of a multi-contract card/portfolio.

Assign:

```text
duplicate_thesis_id
dependency_group
shared_factor_tags
```

Examples of shared factors:

```text
same player
same game
same team
same starter
same lineup assumption
same game script
same weather station
same weather system
same settlement source
same macro/news event
```

Duplicate-thesis exposure is a portfolio risk, not a second probability penalty.

Do not change model probability or calibrated lower bound because two contracts share a thesis.

### Drawdown State

Consume the current governed state:

```text
NORMAL
WATCH
RECOVERY
HARD_STOP
```

Do not assume the historical July 2026 emergency Recovery initialization is permanently active. Use the current governed portfolio state/history.

When current state or active patch requires singles-only, enforce it.

`can_execute=false` and `capital_allocation=false` remain mandatory.

---

## Final Refresh

Before final publication, refresh material facts including:

```text
event start/status
market open/closed status
latest executable price
ticker/contract identity
starter/lineup/roster status where relevant
weather/settlement observation where relevant
rules/settlement changes
critical source conflicts
```

If a material change affects a fitted model input, rerun the controlling specialist rather than manually editing the probability.

Started/final/postponed/canceled events must follow V17 lifecycle rules.

---

## Labels

The assistant may add a Kalshi presentation label while preserving the underlying V17 terminal/model state.

Allowed presentation labels:

```text
KALSHI_MODEL_QUALIFIED
KALSHI_EDGE_QUALIFIED
KALSHI_RESEARCH_INTEREST
KALSHI_MARKET_HOLD
KALSHI_MODEL_HOLD
KALSHI_NO_EDGE
KALSHI_REJECT
```

Examples:

```text
terminal_label=MODEL_SCORER_FAILED
kalshi_assistant_label=KALSHI_MODEL_HOLD
```

```text
terminal_label=<valid publishable model result>
kalshi_assistant_label=KALSHI_MARKET_HOLD
reason=STALE_ORDERBOOK
```

The presentation label must never overwrite the typed backend terminal label.

---

## Ranking Rules

A contract may appear on the official Kalshi leaderboard only when all required lane contracts pass:

```text
exact identity resolved
exact settlement resolved
controlling specialist resolved
valid governed numeric probability package
probability_publishable=true when required
rank_eligible=true when required
calibrated lower bound valid
model-to-Kalshi settlement transform valid
fresh executable price available for edge ranking
fees/frictions resolved when material
lower-bound edge > 0 for edge-qualified ranking
portfolio rules satisfied
final refresh passed
```

### Ranking Priority

```text
1. friction_adjusted_lower_bound_edge
2. calibrated_lower_bound
3. market_quality_score
4. independence / lower structural concentration
5. fresher model and market evidence
```

Do not use displayed Kalshi probability as the model ranking score.

---

## User-Facing Output

### A. Reconciliation Summary

```text
contracts_extracted
contracts_canonicalized
contracts_scored
contracts_model_qualified
contracts_edge_qualified
contracts_held
contracts_rejected
unsupported_routes
```

### B. Leaderboard

Recommended columns:

```text
Rank
Contract
Side
Controlling Specialist
Calibrated P
Lower Bound
Executable Break-Even
Lower-Bound Edge
Market Quality
Assistant Label
Primary Blocker
```

### C. Special Callouts

When supported:

```text
BEST PURE PROBABILITY
BEST LOWER-BOUND EDGE
BEST VALUE THRESHOLD
BEST NO-SIDE OPPORTUNITY
MARKET OVERPRICED / NO EDGE
UNSUPPORTED MODEL ROUTES
DUPLICATE-THESIS WARNING
```

Do not force a fixed number of picks. Fewer or zero qualified contracts is valid.

---

## Required Output Schema

```text
KALSHI_BETTING_ASSISTANT_RESULT

research_run_id
as_of
source_snapshot_id

contract_id
ticker
event_id
sport_or_domain
market_family
subject
period
threshold_or_bracket
side
settlement_definition
settlement_identity_key

controlling_specialist
model_capability
model_status
model_probability
calibrated_probability
calibrated_lower_bound
calibrated_upper_bound
failure_path_score
model_as_of
probability_publishable
rank_eligible

yes_bid
yes_ask
no_bid
no_ask
price_as_of
market_status
fee_status
effective_break_even_probability

point_edge
lower_bound_edge
friction_adjusted_lower_bound_edge
market_quality_score
market_quality_tier

threshold_family_id
underlying_distribution_id
duplicate_thesis_id
dependency_group
portfolio_status

terminal_label
kalshi_assistant_label
blockers
wolfram_audit_status
can_execute=false
capital_allocation=false
```

---

## Fail-Closed Rules

Never weaken these states to manufacture a pick:

```text
MODEL_UNAVAILABLE
MODEL_INPUTS_INSUFFICIENT
MODEL_SCORER_FAILED
MODEL_OUTPUT_INVALID
EXACT_LINE_NOT_FOUND
SETTLEMENT_MISMATCH
KALSHI_SETTLEMENT_BASIS_MISMATCH
KALSHI_ORDERBOOK_STALE
KALSHI_ORDERBOOK_EMPTY
KALSHI_MARKET_CLOSED
KALSHI_FEE_UNRESOLVED
JOINT_DEPENDENCE_UNRESOLVED
FINAL_REFRESH_INVALIDATED
```

If the controlling model was selected and invoked but throws, times out, or returns no valid package, preserve the typed scorer/completion failure. Do not rewrite it as `MODEL_UNAVAILABLE`.

---

## Acceptance Tests

1. A Kalshi screenshot is fully extracted before market-family filtering.
2. A screenshot-displayed percentage is never labeled model probability.
3. A screenshot price cannot satisfy executable-price qualification.
4. MLB pitcher strikeout contracts route to the WOW prop lane, not the weather expert or LLP.
5. Team/event winner contracts route to `LLP_TEAM_BETTING_ENGINE`.
6. Supported daily-high weather brackets route to the Kalshi Weather Market Expert.
7. Unsupported spread/total routes fail closed rather than borrowing a moneyline probability.
8. Exactly one controlling specialist owns every scored row.
9. Scout/Research cannot publish a probability.
10. A selected/invoked scorer timeout returns `MODEL_SCORER_FAILED`, not `MODEL_UNAVAILABLE`.
11. Missing live Kalshi price holds edge publication but preserves a completed sporting probability.
12. Kalshi settlement wording is translated into explicit mathematical boundaries.
13. A model probability on a mismatched settlement basis cannot be used for edge.
14. Nested thresholds derived from one distribution satisfy monotonicity.
15. An incoherent threshold curve returns `MODEL_OUTPUT_INVALID` unless a certified reconciliation rule exists.
16. Threshold value search may prefer an adjacent threshold but must retain the requested row in reconciliation.
17. YES and NO are evaluated on separate executable prices.
18. A material void/cancel state is preserved rather than forcing YES+NO=1.
19. A positive point edge with non-positive lower-bound edge cannot be `KALSHI_EDGE_QUALIFIED`.
20. Market quality never changes sporting model probability.
21. Same-player/same-game threshold contracts receive shared dependency/duplicate-thesis identifiers.
22. Duplicate-thesis exposure is handled by portfolio governance, not by reducing model probability.
23. Current portfolio state is consumed; the historical July Recovery initialization is not assumed permanently active.
24. Multi-contract cards pass the Kalshi Portfolio Risk & Combo Governor.
25. Final refresh invalidates stale/started/closed contracts as required.
26. Wolfram may verify arithmetic but cannot generate sporting probability.
27. A Wolfram verification failure holds the affected arithmetic/edge claim without erasing a completed sporting probability.
28. Every extracted row appears in final reconciliation.
29. Zero qualified Kalshi contracts is a valid terminal outcome.
30. Every output contains `can_execute=false`.
31. Every output preserves `V17_TERMINAL_REDUCER` as sole global terminal authority.

---

## Activation Prompt

> Activate WOW Kalshi Betting Assistant under V17. Fully extract and canonicalize every visible or supplied Kalshi contract, verify exact settlement identity, route each contract to exactly one certified controlling specialist, obtain only governed sporting/weather probabilities, map each probability package to the exact Kalshi YES/NO settlement basis, construct coherent threshold curves from shared fitted distributions where supported, retrieve and audit fresh executable Kalshi prices and fees, calculate point and calibrated-lower-bound edge, search adjacent thresholds for superior value, keep market quality separate from sporting probability, run duplicate-thesis/dependence and Kalshi portfolio governance, perform final refresh, preserve typed backend failures, rank only qualified rows, reconcile every input row, and enforce `can_execute=false` and `V17_TERMINAL_REDUCER` as sole terminal authority.

---

## One-Line Definition

**WOW Kalshi Betting Assistant is the V17 Kalshi orchestration layer that converts a Kalshi board into exact governed specialist routes, settlement-matched probabilities, threshold-consistent value audits, portfolio-governed research rankings, and fail-closed terminal outputs without becoming a probability model or execution system.**
