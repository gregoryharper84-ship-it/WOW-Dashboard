---
name: wow-cross-sport-high-probability-selector
description: >
  Cross-sport daily selector: ranks candidates into Highest Hit
  Probability, Highest True Probability, Best Verified Edge, and Best
  Multi-Leg Structure across tennis, MLB, WNBA/NBA, NHL, NFL, soccer
  moneylines, supported props, and Kalshi sports contracts. Runs status
  locking, specialist models, market normalization, cross-leg dependence
  audit, and weakest-leg elimination before publishing. Never
  auto-executes or sizes stake; human confirmation always required;
  NO_PLAY is valid. Triggers on "cross-sport card", "best plays today
  across sports", "highest probability slate", "build a compact card",
  "weakest leg check". Research/ranking only — does not replace
  wow-gate-enforcer, wow-llp-runner, or sport-specific gates, which
  still govern final leg approval. STATUS: PROPOSED, not yet
  ChatGPT-approved (see governance/ patch) — treat no output as live
  until approved. Runs alongside, not in place of, wow-high-hit-engine
  pending resolution of which is authoritative for cross-sport ranking.
---

# Skill: wow-cross-sport-high-probability-selector

## Purpose

Identify the strongest daily cross-sport outcome candidates across supported sports, then separate:

1. **Highest Hit Probability**
2. **Highest True Probability**
3. **Best Verified Edge**
4. **Best Multi-Leg Structure**

This skill is designed for moneylines, match winners, and selected supported player props. It must use live governance, Replit, connected APIs, official sources, specialist models, and deep web research before publishing a probability or edge.

A successful prior card is evidence for postmortem review, not proof that the model is calibrated.

---

## Permanent Governance

```text
auto_execute=false
requires_human_confirmation=true
stake_sizing=false
bankroll_allocation=false
NO_PLAY=valid
```

`can_execute` may be controlled by live governance, but no wager may ever be placed automatically.

**Activation status:** This skill's scoring thresholds and shadow-mode status are PROPOSED, not live. See `governance/WOW-PATCH-CROSS-SPORT-HIGH-PROBABILITY-SELECTOR.md` — do not treat this skill's calibration ceiling as approved until that patch clears ChatGPT review per WOW governance (Claude does not self-approve spec or patch changes).

---

## Daily Objective

Build a compact card from independently strong candidates across sports, preferring:

```text
verified event identity
fresh participant status
stable role
complete model
high calibrated lower bound
low failure-path concentration
clean market confirmation
low cross-leg dependence
```

Never force a requested leg count.

---

## Source Order

```text
1. Replit backend
2. Official league, event, team, and player sources
3. Connected APIs
4. Sportsbook / exchange APIs
5. Specialist sources
6. Trusted structured providers
7. Deep web research
8. Reconstruction from verified facts
9. Proxy only as a last resort
```

Deep web research is mandatory whenever a required field remains incomplete.

Every important field must record:

```text
source_url
retrieved_at
freshness_age
source_grade
conflict_status
```

---

## Supported Candidate Families

Only active specialist models may qualify.

Examples:

```text
Tennis match winner
MLB moneyline
WNBA moneyline
NBA moneyline
NFL moneyline
NHL moneyline
Soccer three-way or draw-no-bet where supported
Supported player props
Kalshi sports contracts
```

Unsupported sports or markets fail closed.

---

## Mandatory Workflow

```text
1. Governance sync
2. Slate discovery
3. Event identity lock
4. Participant/status lock
5. Exact market and settlement lock
6. Specialist sport model
7. Opponent and matchup model
8. Failure-path model
9. Calibration and uncertainty
10. Market normalization
11. Probability / edge separation
12. Cross-leg dependence audit
13. Weakest-leg elimination
14. Final refresh
15. Immutable prediction write
16. User-facing output
```

No later step may erase an earlier blocker.

---

## Candidate Scoring

Each candidate must return:

```text
raw_probability
calibrated_probability
calibrated_lower_bound
calibrated_upper_bound
model_status
market_probability
lower_bound_edge
failure_path_score
status_freshness
market_freshness
correlation_tags
terminal_label
```

### High Hit Probability

Starting threshold:

```text
raw_probability >= 0.70
```

### High True Probability

Starting threshold:

```text
calibrated_probability >= 0.70
calibrated_lower_bound >= 0.65
```

### Clear Edge

```text
lower_bound_edge =
calibrated_lower_bound
- no_vig_market_probability
- friction_buffer
```

Require:

```text
lower_bound_edge > 0
exact market match
settlement match
fresh two-sided prices
material_market_conflict=false
```

---

## Sport-Specific Requirements

### Tennis

Require:

```text
surface-adjusted form
hold and break rates
serve/return matchup
fatigue and travel
injury status
recent workload
best-of format
head-to-head only as context unless sample is meaningful
```

Failure paths:

```text
retirement
fitness decline
serve collapse
tiebreak variance
surface mismatch
fatigue
```

### MLB Moneyline

Require:

```text
confirmed starter
lineup
bullpen availability
park/weather
starter quality and workload
defense
offense split
travel/rest
market normalization
```

Failure paths:

```text
starter early collapse
bullpen failure
lineup scratch
weather interruption
extra-inning variance
```

### WNBA / NBA Moneyline

Require:

```text
official injury report
starting lineup
minutes/usage state
rest and travel
pace
matchup
blowout/foul-trouble scripts
```

Failure paths:

```text
late scratch
minutes restriction
rotation surprise
shooting variance
foul trouble
garbage-time distortion
```

### Player Props

Use only the active sport-specific prop module and exact-line L5/L10 ledger.

---

## Market Normalization

For every candidate:

```text
book_or_exchange
market
side
price
timestamp
raw_implied_probability
market_hold
no_vig_probability
fee_adjusted_breakeven
```

Never treat midpoint as no-vig.

Never compare markets with different settlement definitions.

---

## Cross-Leg Structure

A card must minimize dependence.

Block or penalize:

```text
same event concentration
same injury thesis
same player repeated
alternate lines on same distribution
component/composite overlap
shared game script
multiple legs exposed to one weather event
cross-book parlay illusion
```

Default target:

```text
2-4 independently strong legs
```

Prefer fewer legs when the next candidate materially weakens joint probability.

---

## Weakest-Leg Elimination

For every proposed card, test:

```text
full card
card minus weakest leg
card with replacement
smallest qualifying card
```

The weakest leg is ranked by:

```text
lowest calibrated lower bound
highest uncertainty
highest failure-path score
stale status
market disagreement
correlation contribution
```

Remove it if joint lower bound or conservative EV improves materially.

---

## Final Refresh

Within the active freshness window, recheck:

```text
event status
participant status
lineup
starter
market price
settlement status
weather
cross-ticket exposure
```

Material change requires rerun.

---

## Daily Output

### A. Top Probability Candidates

```text
rank
candidate
raw_probability
calibrated_probability
lower_bound
model_status
```

### B. Top Edge Candidates

```text
rank
candidate
market_probability
lower_bound_edge
conservative_EV
```

### C. Best Compact Card

```text
legs
joint_probability
joint_lower_bound
dependence_adjustment
weakest_leg
```

### D. Rejected Popular Candidates

```text
candidate
reason
terminal_label
```

### E. Live Recheck Deadline

---

## Postmortem Mode

After settlement, record:

```text
prediction_id
candidate
published_probability
lower_bound
market_probability
terminal_label
official_result
settlement_source
observed_failure_or_success_path
closing_market_probability
process_classification
```

Do not rewrite the original prediction.

A winning card does not automatically validate the model.

Use accumulated results to measure:

```text
Brier score
log loss
expected calibration error
calibration bias
lower-bound reliability
CLV
process compliance
```

---

## Lessons Encoded from the Successful Four-Leg Card

The reviewed card combined:

```text
two tennis match winners
one MLB moneyline
one WNBA moneyline
```

The structure was favorable because:

```text
sports were diversified
events were independent
no repeated player or injury thesis
no same-game stack
each leg had a simple settlement path
```

The skill should preserve that structural logic while still requiring fresh model and market validation for every future card. **One favorable prior structure is a sample size of one — it informs the cross-leg dependence heuristics above, it does not certify them.**

---

## Terminal Labels

All user-facing labels must match the backend verbatim.

Examples:

```text
FINAL_APPROVED
MONEY_QUALIFIED
MARKET_VERIFIED_HOLD
MODEL_QUALIFIED_HOLD
RESEARCH_INTEREST
SOURCE_CONFLICT
REJECT_NO_EDGE
REJECT_BAD_STRUCTURE
REJECT_DATA_QUALITY
SLATE_PURGE
DUPLICATE_EXPOSURE_BLOCK
NO_PLAY
```

Never invent or upgrade a backend label.
