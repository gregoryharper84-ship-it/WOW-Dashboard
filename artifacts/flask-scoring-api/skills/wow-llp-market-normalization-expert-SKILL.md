# Skill: wow.llp-market-normalization-expert

## Purpose

Normalize exact outright-winner markets, calculate reproducible two-way or three-way no-vig probabilities, and prevent raw implied probability or mismatched outcomes from being presented as market probability.

## Governance

```text
lane_status=MARKET_NORMALIZATION
maximum_ceiling=MARKET_VERIFIED_HOLD
can_execute=false
```

## Required Inputs

```text
candidate_id
sport
market_type
period
settlement_rule
selection
sportsbook
odds_timestamp
all_outcome_prices
```

## Market Structures

```text
TWO_WAY: A/B
THREE_WAY: HOME/DRAW/AWAY
FIELD: all mutually exclusive entrants
```

## American Odds Conversion

```text
A < 0: q=abs(A)/(abs(A)+100)
A > 0: q=100/(A+100)
```

## No-Vig

Two-way:

```text
p_a=q_a/(q_a+q_b)
p_b=q_b/(q_a+q_b)
```

Three-way:

```text
p_home=q_home/sum(q)
p_draw=q_draw/sum(q)
p_away=q_away/sum(q)
```

## Required Audit

```text
raw_implied_each_outcome
market_hold
no_vig_each_outcome
normalization_sum
selected_outcome_no_vig
market_structure
exact_market_match
```

Normalization tolerance:

```text
abs(sum(no_vig)-1.0000) <= 0.0005
```

## Hard Blocks

```text
MISSING_OPPOSING_PRICE
MISSING_DRAW_PRICE
OUTCOME_IDENTITY_MISMATCH
PERIOD_MISMATCH
SETTLEMENT_MISMATCH
NORMALIZATION_FAILURE
STALE_MARKET
```

## Prohibited Behavior

- Do not use the favorite's no-vig probability for the underdog.
- Do not subtract a model from raw implied probability while labeling the result no-vig edge.
- Do not normalize soccer with two prices.
- Do not compare advancement probability with full-time moneyline probability.

## Required Output

| Outcome | Odds | Raw Implied | No-Vig | Selected? |
|---|---:|---:|---:|---:|

```text
market_hold=
normalization_sum=
selected_no_vig=
result=
can_execute=false
```
