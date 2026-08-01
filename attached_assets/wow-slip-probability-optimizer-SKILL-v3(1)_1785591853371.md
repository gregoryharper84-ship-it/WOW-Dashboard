# Skill: wow.slip-probability-optimizer

## Purpose

Convert an uploaded third-party slip into a **probability-only model-qualified slip review**.

The skill does **not** try to prove betting edge, expected value, positive CLV, stake size, or final approval. It answers a narrow question:

```text
Can this leg or replacement candidate be treated as YES — MODEL_QUALIFIED for high hit probability only?
```

The skill may:

- keep a leg as-is when it qualifies;
- flip MORE to LESS, or LESS to MORE, only after rerunning the full gates;
- change to a lower or higher available threshold when that exact board line exists;
- change to a different stat for the same player when that exact board line exists;
- remove a weak leg;
- replace a weak leg with a better high-probability prop from the same board/slate;
- shrink the slip to the smallest qualifying structure;
- output NO when the slip cannot be made model-qualified.

The skill may not:

- invent a line, prop, payout, player, or contract;
- approve from an influencer slip, screenshot, trend, narrative, or L5/L10 hit rate alone;
- call any result FINAL_APPROVED, MONEY_QUALIFIED, executable, or stake-ready;
- ignore source conflicts, stale data, role uncertainty, lineup uncertainty, settlement ambiguity, or unsupported markets;
- treat “probable to hit” as “profitable.”

```text
lane_status = PROBABILITY_ONLY
can_execute = false
stake = 0
money_label_allowed = false
final_approval_allowed = false
```

---

## Supported platforms

Initial platform scope:

```text
PrizePicks
Kalshi
```

PrizePicks support covers player props and slip construction.

Kalshi support covers sports-related contracts only when a configured Kalshi connector or source path can verify:

- exact contract identity;
- settlement language;
- market status;
- orderbook/price freshness when available;
- event status and rules;
- contract-side probability model.

If Kalshi inventory, event, settlement, or orderbook data is unavailable, the Kalshi candidate cannot receive YES.

---

## Supported sports

The skill supports all major sports only through league-specific submodules.

Default supported families:

```text
MLB
NBA
WNBA
NFL
NCAAF
NCAAB
NHL
Soccer
Tennis
Golf
MMA
```

A sport may be present in the slip but still receive NO when the available API, stat, role, matchup, weather, lineup, or market coverage is insufficient.

---

## Optimization objective

Primary goal:

```text
maximize_hit_probability
```

Secondary goals, in order:

1. Minimize data uncertainty.
2. Minimize role/status risk.
3. Minimize settlement ambiguity.
4. Minimize correlation and duplicate exposure.
5. Prefer fewer legs over forcing a full slip.
6. Prefer cleaner standard lines over fragile promotional or thin markets.
7. Preserve the original slip only where the original leg passes.

Explicitly disabled:

```text
edge_optimization = false
ev_optimization = false
staking = false
execution = false
```

The skill may still retrieve sportsbook lines, consensus, and no-vig probability as **sanity checks and calibration anchors**, but it must not rank by edge or EV.

---

## Decision labels

Only these probability-only labels are allowed.

```text
YES_MODEL_QUALIFIED
YES_MODEL_QUALIFIED_MODIFIED
NO_REPLACE
NO_REMOVE
NO_SOURCE_COVERAGE
NO_DATA_QUALITY
NO_ROLE_OR_STATUS
NO_MARKET_CONTRADICTION
NO_LOW_PROBABILITY
NO_BAD_STRUCTURE
NO_CORRELATION
NO_SETTLEMENT_UNCLEAR
NO_UNSUPPORTED_MARKET
```

Mapping to WOW terminal ceiling:

```text
YES_MODEL_QUALIFIED             => MODEL_QUALIFIED_HOLD
YES_MODEL_QUALIFIED_MODIFIED    => MODEL_QUALIFIED_HOLD
All NO labels                     => NO_PLAY / REJECT_* / REJECT_DATA_QUALITY by cause
```

No result from this skill may exceed:

```text
MODEL_QUALIFIED_HOLD
```

because edge, EV, payout economics, and final live-money gates are intentionally out of scope.

---

## High probability definition

A candidate can receive YES only if the calibrated probability lower bound clears the active high-probability floor.

Default floor:

```text
calibrated_probability_lower_bound >= 65%
```

The model must show both:

```text
raw_model_probability
calibrated_probability_lower_bound
```

If the model is uncalibrated, thin, unsupported, or missing required inputs:

```text
YES prohibited
```

A leg with a strong recent hit rate but missing current role, matchup, market sanity, or settlement evidence must be NO.

---

## Required inputs

### Slip upload

The user may provide:

- screenshot;
- copied slip text;
- CSV/table;
- manual list;
- shared influencer slip;
- PrizePicks board upload;
- Kalshi contract list.

Required extracted fields per leg:

```text
row_id
platform
sport
player_or_contract
team
opponent
event
event_date
market_type
stat_or_contract_type
line_or_threshold
direction_or_side
offer_type
visible_payout_or_multiplier_if_any
source_capture_timestamp
```

Screenshots and third-party slips are treated as **menus only**. They define candidates, not validation.

### External source paths

The skill should attempt each applicable source path before labeling a row:

```text
board_or_contract_source
official_event_status
official_player_status_or_lineup
player_or_team_game_logs
role_minutes_usage_or_workload_data
matchup_data
weather_or_venue_when_material
sportsbook_or_consensus_market
projection_or_reconstruction_source
settlement_rules
news_conflict_check
```

Source statuses:

```text
RETRIEVED
RECONSTRUCTED
PROXY_ONLY
DATA_UNOBTAINABLE
INPUT_FAILURE
SOURCE_CONFLICT
NOT_CALLED
FAILED
```

`NOT_CALLED` is never a final answer. The skill must attempt the public/API paths first.

---

## Acquisition gate

No leg may enter probability scoring until every required field has been attempted.

For every row, write an acquisition report:

```text
board_status
identity_status
event_status
role_status
lineup_status
logs_status
market_status
projection_status
settlement_status
news_status
weather_status_if_applicable
failure_path
source_timestamps
```

If a field is missing after attempts, assign the appropriate cap or NO label.

---

## Core workflow

1. **Normalize slip**
   - Extract every visible leg.
   - Standardize names, teams, opponents, stat labels, directions, lines, and event dates.
   - Detect duplicate players, duplicate events, alternate lines, and component/composite conflicts.

2. **Slate and source purge**
   - Remove past, postponed, canceled, settled, ambiguous, or inactive events.
   - Stop rows with no valid sport/event match as `NO_SOURCE_COVERAGE`.

3. **Exact board or contract verification**
   - PrizePicks: verify the prop exists at the exact current line and side through the configured board/API/source path.
   - Kalshi: verify the exact contract, side, settlement language, event, status, and current market state.

4. **Reality verification**
   - Confirm player identity, team, opponent, expected role, injury/status, starter/lineup, minutes/workload, or contract event reality.

5. **Historical ledger**
   - Build L10 and L5 at the exact current role and exact line.
   - Use L5 only as a trend modifier.
   - If role changed, use matching role splits.
   - If L10/L5 divergence exceeds 20%, isolate outliers and recompute a role-valid L9/L10 where possible.

6. **Sport-specific model**
   - MLB: starter/lineup/batting order/weather/park/pitcher workload/umpire when material.
   - NBA/WNBA: minutes, usage, shot profile, pace, opponent, rest, blowout, teammate absences.
   - NFL/NCAAF: snap share, target/rush route share, game script, weather, injuries, opponent coverage/front.
   - NHL: line assignment, power play, shot volume, goalie confirmation, pace, opponent suppression.
   - Soccer: starting XI, role, set pieces, opponent style, market identity.
   - Tennis/Golf/MMA: event-specific settlement, opponent/matchup style, format, fatigue, surface/course/fight profile.

7. **Market sanity and calibration anchor**
   - Retrieve sportsbook/consensus/no-vig or closest comparable market when available.
   - Use it to detect contradiction, stale lines, or market already adjusted.
   - Do not calculate stake or EV.
   - If the market materially contradicts the model, label `NO_MARKET_CONTRADICTION` or hold by conflict.

8. **Bidirectional candidate scoring**
   - Score both MORE and LESS, YES and NO, OVER and UNDER, or equivalent side pairs.
   - A failed original direction does not automatically approve the opposite direction.
   - Flipping direction restarts the full workflow.

9. **Modification search**
   - Try these in order:
     1. Keep original leg.
     2. Same leg, better available threshold.
     3. Same player, different stat with cleaner probability.
     4. Same event, lower-correlation replacement.
     5. Same sport/slate, best high-probability replacement.
     6. Remove leg.
   - Never use a replacement that was not verified as currently available.

10. **Slip rebuild**
    - Prefer the smallest qualifying slip.
    - Remove weak, correlated, duplicate, or unsupported legs.
    - Preserve only one opportunity per player/stat/event distribution.
    - During Freeze, max two same-event legs and no prohibited Power structures.
    - Kalshi and PrizePicks legs must not be mixed unless the user explicitly asks for a cross-platform research card; even then, no execution label is allowed.

11. **Yes/No label**
    - Output one YES/NO decision per original leg and per replacement.
    - Output one final slip-level YES/NO.
    - Preserve blocker reasons for removed legs.

12. **QA fail-closed review**
    - Confirm all rows reconcile.
    - Confirm no missing required data was silently ignored.
    - Confirm no edge/money/final/stake language slipped into the output.
    - Confirm `can_execute=false`.

---

## Modification rules

### Direction flips

Allowed only when:

```text
opposite side exists
same exact line or verified alternate exists
full acquisition gate reruns
both sides are scored
no contradiction remains
calibrated lower bound >= 65%
```

Required output:

```text
original_side_probability
replacement_side_probability
side_probability_gap
why_original_failed
why_replacement_passed
```

### Threshold changes

Allowed only when:

```text
alternate threshold exists on the live board or contract list
settlement is identical
payout/offer type is documented for context
probability is recalculated at the exact threshold
```

Promotional/Goblin/Demon/discounted thresholds cannot upgrade source quality or bypass missing evidence.

### Stat changes

Allowed only when:

```text
same player or event identity is verified
new stat has a cleaner data path
new stat is not a hidden duplicate exposure
component/composite mutex is clean
current role supports the stat pathway
```

Example mutex checks:

```text
points vs points+rebounds+assists
hits vs hits+runs+RBIs
pitcher strikeouts vs outs recorded
rushing yards vs rush attempts
shots on goal vs points
```

### Remove and replace

Allowed only when the replacement:

```text
has higher calibrated probability than the removed leg
has no stronger blocker than the removed leg
does not increase correlation risk
does not create duplicate exposure
does not rely on a prohibited or unsupported market
```

If no replacement clears:

```text
NO_REPLACE
```

---

## PrizePicks lane

PrizePicks rows require:

```text
exact board line
direction
offer type
player identity
game date
opponent
status/role
exact-line historical ledger
projection/reconstruction
market sanity check when comparable
slip structure check
```

Output for each row:

```text
Original: YES/NO
Action: Keep / Flip / Change line / Change stat / Replace / Remove
Recommended card: YES/NO
Confidence: High / Medium / Low
Calibrated probability lower bound
Blockers
```

Power/Flex handling is research-only. Do not return stake size.

---

## Kalshi lane

Kalshi sports rows require:

```text
contract ticker_or_id
contract title
event
side
settlement text
official settlement source
market status
orderbook/price freshness when available
liquidity status when available
event status
sports rules mapping
model probability
calibrated probability lower bound
```

Kalshi YES requires:

```text
contract identity verified
settlement rule unambiguous
event status valid
market not stale or impossible to verify
model probability lower bound >= 65%
no source conflict
```

Kalshi NO conditions include:

```text
INVENTORY_EMPTY
CONTRACT_NOT_FOUND
SETTLEMENT_UNCLEAR
ORDERBOOK_STALE
MARKET_SUSPENDED
EVENT_STATUS_UNVERIFIED
SOURCE_CONFLICT
LOW_PROBABILITY
```

Kalshi outputs remain:

```text
research_only
can_execute=false
```

---

## Output format

Start every run with:

```text
DECISION: YES / NO / PARTIAL
Mode: Probability-only model qualification
Platforms: PrizePicks, Kalshi
Edge/EV: Not evaluated by user request
can_execute=false
```

Then provide the slip table:

| Original Leg | Original YES/NO | Action | Replacement | Replacement YES/NO | Calibrated Probability Lower Bound | Key Evidence | Blocker |
|---|---:|---|---|---:|---:|---|---|

Then provide the final card:

| Slot | Platform | Sport | Selection | Side | Line/Contract | Calibrated Probability Lower Bound | Status |
|---|---|---|---|---|---|---:|---|

Then provide the acquisition report:

| Row | Board | Identity | Event | Role/Lineup | Logs | Market | Projection | Settlement | News | Result |
|---|---|---|---|---|---|---|---|---|---|---|

End every run with:

```text
SKILL COMPLIANCE
workflow=normalize→verify→ledger→model→market_sanity→bidirectional_score→modify→slip_rebuild→QA
mode=probability_only
edge_ev_evaluated=false
lowest_ceiling=MODEL_QUALIFIED_HOLD
money_label_allowed=false
final_approval_allowed=false
can_execute=false
row_reconciliation=<rows_in>/<rows_out>/<bucket_totals>
unresolved_blockers=<list>
```

---

## Example response style

```text
DECISION: PARTIAL — original slip does not qualify as uploaded, but a smaller probability-only card qualifies.

Leg 1: NO_REMOVE
Reason: player role conflict and market contradiction.

Leg 2: YES_MODEL_QUALIFIED
Reason: verified active role, clean L10 role split, no news conflict, calibrated lower bound 67%.

Leg 3: YES_MODEL_QUALIFIED_MODIFIED
Action: changed MORE 24.5 points to LESS 7.5 assists only because the new stat was verified on-board and passed its own model gates.

Final card: 2 legs, research-only.
can_execute=false
```

---

## Failure behavior

If no public/API path returns a valid sport/event match:

```text
DECISION: NO
label=NO_SOURCE_COVERAGE
action=stop
can_execute=false
```

If the upload is unreadable:

```text
DECISION: NO
label=INPUT_FAILURE
action=ask_for_clearer_upload
can_execute=false
```

If the user asks for execution, stake size, or “lock” language:

```text
Refuse execution language.
Return research-only YES/NO.
can_execute=false
```

---

## Governance note

This skill is intentionally narrower than the full WOW money engine. It can help transform a third-party slip into a cleaner high-probability research card, but it cannot override active WOW rules requiring verified market value, edge, EV, payout context, and live recheck for money/final labels.

Use the full WOW engine for:

```text
MONEY_QUALIFIED
FINAL_APPROVED
edge
EV
staking
unit sizing
execution
```

---

# v2 Patch Integration — Failure Paths, Weakest Leg, and Fragility

## Mandatory workload-dependent failure-path audit

Before a pitcher or other workload-dependent leg can enter the final card, invoke the applicable failure-path model.

For MLB starting-pitcher props, use:

```text
wow.mlb-pitcher-failure-path-expert
```

Required fields added per leg:

```text
failure_path_score
normal_workload_probability
conditional_probability_given_normal_workload
unconditional_probability
primary_failure_path
failure_path_status
```

The optimizer must rank the leg using the calibrated lower bound of the **unconditional** probability.

## Mandatory weakest-leg elimination cycle

After candidate scoring and before final card output:

```text
1. Rank all legs by calibrated lower bound.
2. Identify the weakest leg.
3. Calculate marginal contribution to joint failure.
4. Search for a verified replacement.
5. Rebuild and rescore the slip.
6. Repeat until no improvement exists.
7. Remove the weak leg when no replacement qualifies.
```

Preserving the requested leg count is not an optimization objective.

## Slip fragility audit

Required slip-level outputs:

```text
weakest_leg
critical_leg_index
slip_fragility_score
largest_single_failure_contribution
joint_hit_probability_after_correlation
recommended_structure
```

Default labels:

```text
BALANCED
CONCENTRATED
FRAGILE
```

A `FRAGILE` slip cannot receive a probability-qualified final-card YES until the weakest-leg cycle has been rerun.

## Updated final workflow

```text
normalize
→ verify
→ ledger
→ sport-specific model
→ failure-path audit
→ market sanity
→ bidirectional score
→ modification search
→ weakest-leg elimination
→ fragility audit
→ slip rebuild
→ QA
```

## Added acceptance tests

1. A workload-dependent leg is ranked by unconditional probability.
2. A high normal-workload projection cannot hide a high failure-path score.
3. Every proposed card identifies its weakest leg.
4. No verified replacement means the card shrinks.
5. A forced filler leg causes `NO_BAD_STRUCTURE`.
6. Correlated legs use joint failure treatment.
7. `can_execute=false` remains enforced.

---

# v3 Patch Integration — Cross-Slip Exposure Guard

## Active patch

```text
WOW-PATCH-2026-08-01-CROSS-SLIP-DUPLICATE-GUARD
```

Before final card output, query or construct the same-slate portfolio exposure state.

Source order:

```text
1. active session exposure ledger
2. open/unsettled-slip ledger
3. same-slate proposed-card ledger
4. tracker workbook for fallback or reconciliation only
```

Portfolio denominator:

```text
portfolio_stake_base =
submitted unsettled same-slate stake
+ proposed same-session stake
```

Exact duplicate exposure:

```text
duplicate_leg_exposure_pct =
sum(stake of every slip containing the identical leg)
/
portfolio_stake_base
```

Shared-distribution exposure:

```text
distribution_family_exposure_pct =
sum(stake of every slip containing a materially overlapping player thesis)
/
portfolio_stake_base
```

Classification:

```text
EXPOSURE_TIER_0
No duplicate or material overlap
=> PASS

EXPOSURE_TIER_1
Exposure <= 10%
=> PASS_WITH_DISCLOSURE

EXPOSURE_TIER_2
Exposure > 10% and <= 20%
=> HOLD_CONFIRMATION_REQUIRED

EXPOSURE_TIER_3
Exposure > 20%
=> HARD_STOP_CROSS_SLIP_OVEREXPOSURE
```

The following are overlapping, not diversified:

```text
same player + same stat + same direction + nested thresholds
points + PRA
rebounds + rebounds/assists
strikeouts + pitcher fantasy score
hits + hits/runs/RBIs
pitching outs + pitcher fantasy score
```

Required output:

```text
portfolio_stake_base
duplicate_groups
shared_distribution_groups
duplicate_leg_exposure_pct
distribution_family_exposure_pct
exposure_tier
exposure_action
cross_slip_blockers
```

Missing denominator:

```text
CROSS_SLIP_EXPOSURE_UNRESOLVED
=> maximum slip label = MODEL_QUALIFIED_HOLD
```

An `EXPOSURE_TIER_3` leg must be removed, replaced, or reduced. User confirmation cannot override the hard stop.

## Updated final workflow

```text
normalize
→ verify
→ ledger
→ sport-specific model
→ failure-path audit
→ market sanity
→ bidirectional score
→ modification search
→ weakest-leg elimination
→ fragility audit
→ cross-slip exposure audit
→ slip rebuild
→ QA
```

## Added acceptance tests

1. An exact duplicate over 20% hard-stops.
2. Nested same-direction thresholds route to shared-distribution exposure.
3. A stale workbook cannot override the current session ledger.
4. Missing portfolio stake base caps the card at HOLD.
5. Exposure Tier 2 requires confirmation or reduction.
6. Exposure Tier 3 cannot be overridden.
7. `can_execute=false` remains enforced.
