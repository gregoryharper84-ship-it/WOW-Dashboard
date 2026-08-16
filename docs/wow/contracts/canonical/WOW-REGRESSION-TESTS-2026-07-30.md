# WOW Regression Tests — WNBA Composite, MLB Directional, and Cross-Ticket Governance

**Patch:** WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE  
**Mode:** Prompt-level and implementation-level QA  
**Governance:** `can_execute=false`

---

## Test 1 — Exact MLB Duplicate Across Flex and Power

### Input

```text
Card A: Matthew Boyd LESS 4.5 strikeouts
Card B: Matthew Boyd LESS 4.5 strikeouts
Same event, same line, same side
```

### Expected

```text
duplicate_class=EXACT_DUPLICATE
retained_occurrences=1
second_occurrence=REJECT_EXACT_DUPLICATE
calibration_observations=1
```

---

## Test 2 — Duplicate Pitcher Thesis Across Alternate Lines

### Input

```text
Card A: Pitcher X LESS 4.5 strikeouts
Card B: Pitcher X LESS 5.0 strikeouts
Same event
```

### Expected

```text
duplicate_class=ALTERNATE_THRESHOLD_DUPLICATE
underlying_theses=1
both may be modeled
only strongest verified threshold retained
```

---

## Test 3 — Power Card Copied From Flex Card

### Input

```text
Flex: Boyd LESS, Rodriguez LESS, Singer MORE outs
Power: Boyd LESS, Rodriguez LESS, Davis Martin LESS
```

### Expected

```text
shared_core_theses=2
portfolio_fragility=FRAGILE
Power structure=REJECT_DUPLICATE_STRUCTURE
```

---

## Test 4 — Morrow Alternate PRA Thresholds

### Input

```text
Aneesah Morrow MORE 17.5 PRA
Aneesah Morrow MORE 18.5 PRA
Aneesah Morrow MORE 19.0 PRA
Same game
```

### Expected

```text
player_event_observations=1
alternate_threshold_group=true
financial exposures may remain separate in postmortem
calibration rows=1
```

---

## Test 5 — Compare PRA Against Component

### Input

```text
Player has verified PRA and assists lines
PRA lower bound=61%
Assists lower bound=68%
Role is stable
```

### Expected

```text
best_stat_family=ASSISTS
PRA not selected by default
YES_MODEL_QUALIFIED_MODIFIED for assists
```

---

## Test 6 — Role-Unresolved Discounted PRA

### Input

```text
Goblin PRA line
Player questionable
Expected minutes unresolved
```

### Expected

```text
NO_ROLE_OR_STATUS
promo cannot upgrade
can_execute=false
```

---

## Test 7 — Role-Split Ledger

### Input

```text
Player averages materially different output with primary guard OUT
Primary guard is confirmed OUT today
```

### Expected

```text
ROLE_SPLIT_LEDGER_APPLIED
today model uses matching expanded-role sample
all-games average not controlling
```

---

## Test 8 — Outlier Changes Composite Verdict

### Input

```text
L10 mean supports MORE
Removing one documented season-high game changes verdict to NO
```

### Expected

```text
OUTLIER_CONTAMINATED
highest confidence prohibited
WNBA_COMPOSITE_WATCH
```

---

## Test 9 — High-Volatility Assists for Non-Playmaker

### Input

```text
Forward median assists=2
One game assists=7
Assist standard deviation > mean
```

### Expected

```text
HIGH_VOLATILITY_ASSIST_COMPONENT
robust/shrinkage estimator used
raw mean not controlling
```

---

## Test 10 — Multi-Path PRA

### Input

```text
Winning simulation dominant shares:
Points 0.42
Rebounds 0.34
Assists 0.24
```

### Expected

```text
multi_path_class=THREE_PATH_STABLE
```

---

## Test 11 — One-Path-Dependent PRA

### Input

```text
Winning simulation dominant shares:
Points 0.76
Rebounds 0.16
Assists 0.08
```

### Expected

```text
multi_path_class=ONE_PATH_DEPENDENT
highest confidence prohibited unless points pathway independently clears
```

---

## Test 12 — DNP Handling

### Input

```text
Player did not play
Slip reverted and returned a payout
```

### Expected

```text
projection_result=DNP_OR_VOID
model_hit=false
calibration observation excluded from hit/miss numerator
settlement state retained
```

---

## Test 13 — K LESS Over-Reliant on Short Outing

### Input

```text
P(LESS)=0.67
0.38 probability mass comes from early-exit pathway
```

### Calculation

```text
short_outing_support_share=0.38/0.67=0.567
```

### Expected

```text
MLB_K_LESS_WATCH
HIGH confidence prohibited
```

---

## Test 14 — K LESS Supported by Skill and Matchup

### Input

```text
P(LESS)=0.66
short-outing contribution=0.18
opponent-contact and low-K-rate contribution=0.48
all sources verified
```

### Expected

```text
short_outing_support_share=0.273
still capped by temporary WATCH_ONLY lane
forward-test row written
```

---

## Test 15 — Outs MORE Uses Conditional Probability Incorrectly

### Input

```text
P(MORE | normal workload)=0.74
P(normal workload)=0.70
Other regimes produce P(MORE)=0.12
```

### Expected

The model must calculate an unconditional mixture. It may not report 74% as the final probability.

```text
conditional_as_unconditional=MODEL_INVALID
```

---

## Test 16 — Outs MORE Workload Survival Fails

### Input

```text
Line=14.5 outs
P(reach 15 outs) lower bound=0.59
Active high-probability floor=0.65
```

### Expected

```text
NO_LOW_PROBABILITY
```

---

## Test 17 — Directional Ledgers Remain Separate

### Input

```text
10 K MORE rows
10 K LESS rows
10 outs rows
```

### Expected

```text
three separate calibration summaries
no combined pitcher-prop hit rate used for promotion
```

---

## Test 18 — Cross-Card Critical Thesis

### Input

```text
Three cards
Same player-game thesis appears on two cards
```

### Expected

```text
share_of_cards_at_risk=2/3
portfolio_fragility=FRAGILE
duplicate removed or one card shrunk
```

---

## Test 19 — No Replacement Available

### Input

```text
Duplicate leg removed
No independent verified replacement clears floor
```

### Expected

```text
card shrinks
no forced filler
NO_BAD_STRUCTURE avoided by reducing size
```

---

## Test 20 — Forward-Test Milestone Not Met

### Input

```text
WNBA unique graded player-games=12
MLB K LESS unique rows=6
```

### Expected

```text
WNBA highest ceiling=MODEL_QUALIFIED_HOLD
MLB_K_LESS=WATCH_ONLY
no promotion
```

---

## Test 21 — Forward-Test Duplicate Exclusion

### Input

```text
20 displayed WNBA wins
8 are alternate thresholds or exact repeated player-games
```

### Expected

```text
unique graded player-games=12
milestone_not_met
```

---

## Test 22 — Defensive Rebounds Market Isolation

### Input

```text
Awa Fam MORE 4.5 defensive rebounds
```

### Expected

```text
not silently modeled as total rebounds
dedicated rebound-opportunity data required
otherwise NO_UNSUPPORTED_MARKET or NO_DATA_QUALITY
```

---

## Test 23 — Bidirectional Requirement

### Input

```text
PRA MORE fails the active floor
```

### Expected

```text
LESS is not automatically approved
full LESS acquisition and model rerun required
```

---

## Test 24 — Governance

### Expected on every test

```text
can_execute=false
stake=0
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```
