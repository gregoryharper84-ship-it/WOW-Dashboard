# Skill: wow.cross-ticket-exposure-governor

## Skill Name

**WOW Cross-Ticket Exposure Governor**

## Short Description

Audit every proposed PrizePicks card from the same slate as one combined research portfolio. Detect exact duplicates, alternate-threshold duplicates, shared player-game distributions, shared pitcher theses, same-event concentration, and repeated weak legs before any card is presented.

---

## Purpose

A card can appear acceptable by itself while the full set of cards is structurally fragile.

This skill answers:

```text
Are the proposed cards genuinely diversified, or are they repeating
the same underlying player, pitcher, role, game-script, or threshold thesis?
```

It is mandatory whenever more than one card is proposed, reviewed, rebuilt, or compared in the same slate.

---

## Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
lane_status=PORTFOLIO_GOVERNANCE
can_execute=false
capital_allocation=false
stake=0
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

Maximum ceiling:

```text
PORTFOLIO_QUALIFIED_HOLD
```

This skill does not recommend entry amounts.

---

## Required Inputs

For every card:

```text
card_id
platform
slip_type
leg_count
displayed_payout
source_timestamp
```

For every leg:

```text
row_id
player_or_contract
sport
team
opponent
event_id
event_date
market_type
stat_family
line
direction
offer_type
calibrated_probability
calibrated_probability_lower_bound
failure_path
role_context
shared_assumptions
```

When actual entry amounts are supplied, they may be recorded for postmortem exposure reconciliation. This skill must not generate stake advice.

---

## Identity Keys

Build all of the following:

```text
exact_leg_key =
player + event + stat_family + line + direction + settlement

player_event_key =
player + event

distribution_key =
player + event + latent_stat_distribution

pitcher_thesis_key =
pitcher + event + directional_workload_or_performance_thesis

event_script_key =
event + shared_game_script
```

### Latent Distribution Families

Treat the following as related exposure:

```text
Points
Points + Rebounds
Points + Assists
PRA
```

when the same player's scoring role is the primary driver.

Treat the following as related exposure:

```text
Rebounds
Points + Rebounds
Rebounds + Assists
PRA
```

when the same player's rebound opportunity is material.

Treat the following as related exposure:

```text
Assists
Points + Assists
Rebounds + Assists
PRA
```

when the same player's creation role is material.

For MLB pitchers, treat:

```text
Strikeouts MORE
Pitching outs MORE
Pitch count MORE
Batters faced MORE
```

as potentially sharing workload-survival exposure.

Treat repeated strikeout LESS selections on the same pitcher as one thesis regardless of card.

---

## Duplicate Classes

```text
EXACT_DUPLICATE
Same exact leg appears on more than one card.

ALTERNATE_THRESHOLD_DUPLICATE
Same player, event, stat, and direction at multiple lines.

SHARED_LATENT_PLAYER_EXPOSURE
Different stats depend materially on the same player-game distribution.

DUPLICATE_PITCHER_THESIS
Same pitcher and directional thesis repeated across cards.

SAME_EVENT_CONCENTRATION
Several legs share the same event or game script.

SHARED_TEAMMATE_STATUS
Several legs depend on the same injury or lineup assumption.

SHARED_PROMO_EXPOSURE
Several promotional thresholds hide the same underlying uncertainty.

INDEPENDENT_SUPPORTED
No material shared failure path found.

DEPENDENCE_UNRESOLVED
The overlap cannot be responsibly modeled.
```

---

## Hard Rules

```text
exact same leg on multiple proposed cards
=> keep on at most one card

same player-event alternate thresholds
=> count as one underlying thesis

same pitcher exact direction repeated across cards
=> keep on at most one card

weakest leg repeated on a second card
=> second occurrence prohibited

Power card built from legs already concentrated in a Flex card
=> REJECT_DUPLICATE_STRUCTURE

dependence unresolved
=> PORTFOLIO_DATA_UNOBTAINABLE
```

A repeated winning line is not multiple independent model confirmations.

A repeated losing line must not be described as several unrelated misses.

---

## Cross-Card Fragility

For every unique underlying thesis, calculate:

```text
card_count_containing_thesis
leg_count_containing_thesis
failure_probability
cards_failed_if_thesis_misses
share_of_total_cards_at_risk
```

Required output:

```text
largest_cross_card_failure_contribution
critical_underlying_thesis
portfolio_fragility_class
```

Default provisional classification:

```text
DIVERSIFIED
No thesis can independently break more than 25% of proposed cards.

CONCENTRATED
A thesis can break 25% to 50% of proposed cards.

FRAGILE
A thesis can break more than 50% of proposed cards.
```

These thresholds require calibration and do not override explicit dependence modeling.

A `FRAGILE` proposal cannot receive a portfolio-qualified label.

---

## Card Rebuild Order

When overlap is detected:

1. Keep the strongest occurrence by calibrated lower bound and structure.
2. Remove repeated exact legs from all other cards.
3. Compare alternate thresholds and keep only the strongest verified one.
4. Replace shared latent exposure with an independent verified candidate.
5. Recalculate every card.
6. Shrink cards when no qualifying replacement exists.
7. Prefer one clean card to several overlapping cards.

Preserving the number of cards is not an objective.

---

## Allowed Labels

```text
PORTFOLIO_QUALIFIED_HOLD
PORTFOLIO_REJECTED
PORTFOLIO_DATA_UNOBTAINABLE
NO_DUPLICATE_EXPOSURE
REJECT_EXACT_DUPLICATE
REJECT_ALTERNATE_THRESHOLD_DUPLICATE
REJECT_SHARED_LATENT_EXPOSURE
REJECT_DUPLICATE_PITCHER_THESIS
REJECT_SAME_EVENT_CONCENTRATION
REJECT_DUPLICATE_STRUCTURE
```

---

## Required Output Format

```text
WOW CROSS-TICKET EXPOSURE AUDIT

As of:
Cards supplied:
Legs supplied:
Unique underlying theses:
can_execute=false
capital_allocation=false
```

### Duplicate Ledger

| Thesis | Cards | Duplicate Class | Lower Bounds | Shared Failure Path | Required Action |
|---|---|---|---|---|---|

### Fragility Audit

```text
Critical underlying thesis:
Cards failed if it misses:
Share of cards at risk:
Largest cross-card failure contribution:
Portfolio fragility class:
```

### Final Decision

```text
portfolio_status:
cards_retained:
cards_removed_or_shrunk:
duplicates_removed:
smallest_clean_structure:
primary_blocker:
lowest_ceiling:
can_execute=false
```

---

## Postmortem Ledger

Record:

```text
slate_date
card_ids
exact_duplicate_groups
alternate_threshold_groups
shared_latent_groups
pitcher_thesis_groups
actual outcomes
cards failed by each thesis
whether governor would have removed the duplicate
```

---

## Acceptance Tests

1. The same exact pitcher prop on a Flex and Power card is retained only once.
2. PRA at 17.5, 18.5, and 19.0 for one player-game is one thesis.
3. Points and PRA for the same player are flagged when both depend on scoring role.
4. A Power card copied from a Flex card is rejected as duplicate structure.
5. A weak leg cannot be preserved merely to maintain card count.
6. A DNP is not treated as diversification.
7. A repeated winning leg counts once in calibration.
8. A repeated losing leg is attributed to one underlying thesis.
9. A fragile cross-card set is rebuilt or shrunk.
10. `can_execute=false` appears in every output.

---

## Activation Prompt

> Activate WOW Cross-Ticket Exposure Governor. Treat every proposed PrizePicks card from the current slate as one portfolio, build exact-leg, player-event, latent-distribution, pitcher-thesis, and event-script keys, remove repeated exact or alternate-threshold exposure, reject copied Power/Flex structures, calculate cross-card fragility, preserve only the strongest verified occurrence of each thesis, shrink when no independent replacement qualifies, and return dry-run-only portfolio governance with can_execute=false.

---

## One-Line Definition

**WOW Cross-Ticket Exposure Governor is a slate-level portfolio-control skill that stops repeated player, pitcher, threshold, and game-script theses from being mistaken for independent high-probability cards.**
