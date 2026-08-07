# WOW-MASTER-SPEC.md

**Project:** Trio of Sports Models — LLP, OddsLinker, PrizeProfits  
**Document Type:** Project Master Specification  
**Version:** **WOW v16 Clean Core / Framework v2.2.0**  
**Status:** Active master spec  
**Effective Date:** 2026-06-27  
**Owner:** Gregory Harper  
**Primary Objective:** Build disciplined, edge-driven betting models that prioritize verified market value, clean data, controlled risk, and postmortem learning.

---

## 0. Version Control

### 0.1 Current Active Version

**WOW v16 Clean Core / Framework v2.2.0** is the active project specification.
Effective date: 2026-06-27. Supersedes WOW v15.3.0 / Framework v2.1.0 entirely.

This spec consolidates:

- WOW v14.9+ Complete System Enforcement Mode
- WOW v2.0 Complete Framework, last updated 2026-05-25
- WOW v2.1+ MLB DFS Correlation + Managerial Leash Patch
- WOW Data Validation + 6-Checkpoint Gate Patch
- Wrong-Side Reversal + Binary/Niche Data Discipline Patch
- Edge Finder + Market Cause Classification Patch
- Soccer Auto-Verification Engine Patch
- Soccer Team Market Desync + Identity Pricing Patch
- Stale Board + Cross-Market First Gate Patch
- Universal Cross-Market First Gate Patch
- LLP RF Team ML Guard v1.1.2
- WNBA/NBA Team Defense + Pace Patch
- WNBA Source Stack Patch
- WOW v16 Clean Core (effective 2026-06-04)
- PATCH-L Reliability Freeze (effective 2026-06-15)
- PATCH-N Universal Prop Checks
- LLP v16.1 Execution Governance (effective 2026-06-27)
- **WOW-PATCH-2026-06-27-ENFORCED-CALIBRATION-EV-LOCK** (effective 2026-06-27)
  - Module A: Layer 0.5 Calibration Health Gate
  - Module B: Required Data Contract
  - Module C: Payout Context on Every Prop
  - Module D: Probability Component Ledger + Shrinkage
  - Module E: Role Timestamp Enforcement
  - Module F: Failure Path Matrix
  - Module G: Directional Exposure Ledger
  - Module H: Source Timestamp Grading
- Postmortem/learning rules currently marked active

### 0.2 Pending / Not Yet Active

**WOW PATCH BUNDLE v15.2.1 — 9 Patches — Implementation Specs for Dashboard** is treated as a **postmortem implementation bundle only** until the nine detailed patch specs are provided. It is **not** a live pick-approval patch by itself.

### 0.3 Conflict Resolution

When rules conflict, use this priority order:

1. **No fake validation / data integrity rules**
2. **Data Contract enforcement (Module B — DATA_CONTRACT_FAIL blocks all)**
3. **Calibration Health Gate (Module A — SUPPRESSED blocks all approvals)**
4. **Universal Cross-Market First Gate**
5. **6-Checkpoint Validation Gate**
6. **Sport-specific hard bans and availability gates**
7. **Slip construction / correlation / exposure rules**
8. **Older version language**

Older v14.x and v15.x instructions are superseded where they conflict with this spec.
"Conditional" as a final label is superseded by v16 terminal bucket taxonomy throughout.

---

## 1. System Identity

WOW is the umbrella operating framework for three coordinated betting systems:

### 1.1 LLP Model — Lucky Longterm Profits

**Focus:** Straight bets, team markets, player props, selective parlays  
**Primary Platform:** BetUS  
**Sports:** NBA, MLB, NFL soon, soccer team markets where supported  
**Style:** Conservative, EV-driven, selective aggression  
**Goal:** Long-term profit through matchup edges, CLV, market value, role confirmation, and controlled unit sizing.

### 1.2 OddsLinker Model

**Focus:** Arbitrage, line discrepancies, stale prices, mispriced props  
**Platforms:** BetUS + second offshore book + PrizePicks  
**Style:** Low-variance value exploitation  
**Goal:** Identify pricing inefficiencies, implied probability gaps, stale derivative markets, and safer compounding opportunities.

### 1.3 PrizeProfits Engine

**Focus:** PrizePicks entries and fixed-payout optimization  
**Platform:** PrizePicks  
**Style:** Prop-first, +EV, market-aware, slip-disciplined  
**Goal:** Approve only props/slips with verified board availability, exact-line support, role stability, market sanity, and correlation/exposure discipline.

---

## 2. Core Philosophy

The model exists to **find edges, not picks**.

A play is not approved because it looks likely to hit. A play is approved only when the line/price is mispriced against a verified model probability and the data pathway supports the side.

### 2.1 Operating Principles

- No forced action.
- No fake validation.
- No approval from screenshots alone.
- No approval from L5/L10 alone.
- No approval from market drift alone.
- No approval from Random Forest/WOW score alone.
- No approval from narrative alone.
- No approval from influencer slips alone.
- Fewer, sharper plays are better than forced volume.
- **NO PLAY** is a valid model output.

### 2.2 Universal Lesson

The model must find **where the board is wrong** before explaining **why** it is wrong.

**Market edge comes before narrative edge.**

---

## 3. Master Workflow

Every board, screenshot, API response, sheet, or manual list runs through this order:

1. **Source Access Status**
2. **Layer 0.5 — Calibration Health Gate** *(new — runs before any board analysis)*
3. **Board Lock / Candidate Extraction**
4. **Universal Cross-Market First Gate**
5. **Reality Verification / Slate Lock**
6. **Individual Player or Team Audit**
7. **Sport-Specific Research Module**
8. **6-Checkpoint Validation Gate**
9. **Volatility and Failure-Path Review** *(now requires Failure Path Matrix — see Section 8.4)*
10. **Tier / Label Assignment**
11. **Slip Routing / Correlation / Exposure Review** *(now includes Directional Exposure — see Section 27.6)*
12. **Bankroll / Unit Guidance**
13. **Postmortem Logging after settlement**

---

## 4. Source Access Status Requirement

Every run must begin with a source status block.

### 4.1 Required Status Fields

- Board source: Screenshot / API / Sheet / Manual / Missing
- Market/odds source: Available / Partial / Missing / Failed / Not Called
- Player logs: Available / Partial / Missing / Failed / Not Called
- Official status/lineups: Available / Partial / Missing / Failed / Not Called
- Projections/simulations: Available / Partial / Missing / Failed / Not Called
- Weather/park/venue data: Available / Partial / Missing / Not Applicable / Not Called
- Random Forest/WOW score: Available / Failed / Not Called
- Timestamp of data pull where available

### 4.2 Non-Negotiable

**Not Called is not the same as Not Available.**

If a source was not checked, the model must say **Not Called**, not pretend the information does not exist.

### 4.3 Source Timestamp Grading

Every source used in prop analysis must carry a grade. Grade is based on source type and freshness. Grade controls approval cap.

**SOURCE GRADES:**

| Source Type | Grade | Notes |
|---|---|---|
| Direct API / stat feed with timestamp | A | Full trust — T0 |
| Sportsbook odds API with timestamp | A | Full trust — T0 |
| Official box score / gamelog | A- | High trust — T1 |
| Trusted stat site (StatMuse, BBRef, Her Hoop Stats) | B | Reliable — T1 |
| Article / preview / blurb | C | Context only — T3 |
| Screenshot only | D | Menu only — cannot verify |
| Social report unconfirmed | D | Cannot be used as primary source |
| No timestamp present | — | Caps at Watch regardless of type |

**CAPS BY SOURCE GRADE:**
- All critical path sources grade A or A-: no cap from source grade alone
- Any critical path source grade B: cap at MODEL_QUALIFIED_HOLD unless:
  - (a) corroborated by a second independent B or higher source, OR
  - (b) directly reconstructed from official box score / gamelog rows (reconstruction method and source rows must be documented)
- Grade B + corroboration or reconstruction: no automatic cap
- Any critical path source grade C or D: cap at Watch
- Missing timestamp on critical source: cap at Watch regardless of grade
- SOURCE_CONFLICT (any grade): blocks money labels until resolved

**NOTE ON WNBA:** StatMuse, BBRef, Her Hoop Stats, and Across the Timeline are grade B sources. They do not automatically cap approvals when reconstruction from official box score rows is documented in the L10 ledger.

Critical path sources: line/price, status/role, L5/L10 values, market consensus.
Non-critical sources (projections, weather, context): grade noted but does not by itself cap approval.

---

## 5. Screenshots / Board Inputs

Screenshots define the **available prop universe** only.

### 5.1 Screenshots Provide

- Player/team/event name
- Sport
- Opponent/game where visible
- Prop/stat category
- Posted line
- Available side(s)
- Price/multiplier where visible
- Goblin/Demon/boost/discount where visible

### 5.2 Screenshots Do Not Need to Provide

- L5/L10 logs
- Medians/averages
- Official status
- Confirmed lineups
- Projections
- Market comparison
- Injury/news updates
- Weather/park/venue data

### 5.3 Execution Rule

Do not reject a prop because a screenshot lacks validation data. Use the screenshot as the menu, then run the full research stack.

**Screenshots define what can be considered. The full model determines what can be approved.**

---

## 6. Universal Cross-Market First Gate

### 6.1 Purpose

Apply stale-board, market-drift, and consensus-price validation across all sports before upgrading any prop, team side, player narrative, matchup edge, injury edge, role edge, or identity edge.

### 6.2 Applies To

- LLP team markets
- OddsLinker scans
- PrizePicks props
- PrizePicks Teams / event winners
- BetUS straight bets
- Player props
- Team sides
- Spreads
- Totals
- Derivative markets
- 1X2 soccer markets
- Low-liquidity/stale markets

### 6.3 Mandatory Order

1. Extract board line / price.
2. Convert price to implied probability when available.
3. Compare board line/price to consensus market.
4. Convert consensus to no-vig probability when possible.
5. Calculate board-vs-consensus delta.
6. Classify price/line drift.
7. Classify market cause.
8. Run sport-specific validation.
9. Run player/team context.
10. Produce final edge decision.

No prop, team side, spread, total, or derivative market may be approved from narrative, matchup, projection, L5/L10 hit rate, or model score alone.

### 6.4 Drift Grades — Price Markets

- **Severe Drift:** 8%+ probability gap
- **Strong Drift:** 5.0%–7.9% probability gap
- **Mild Drift:** 2.5%–4.9% probability gap
- **Aligned:** Under 2.5% probability gap

### 6.5 Drift Grades — Prop Lines

- **Severe Drift:** Board line materially off by 1+ full stat unit or equivalent.
- **Strong Drift:** Board line off by 0.5–1.0 stat unit with supporting market odds.
- **Mild Drift:** Small but directionally favorable difference.
- **Aligned:** No clear line advantage.

### 6.6 Execution Rules

- Severe / Strong Drift = primary candidate.
- Mild Drift = Watch only unless model edge is exceptional.
- Aligned = No Play unless model shows verified non-market edge.
- If no verified market cause is found, downgrade one tier.

### 6.7 Market Cause Classification

Every edge candidate must classify why the market may be wrong:

- stale board
- low-liquidity stale price
- injury/news lag
- lineup lag
- starter/goalie/pitcher lag
- role/deployment change
- weather/park lag
- public overreaction
- headline-player inflation
- derivative desync
- book shade/favorite tax
- promotion/boost distortion
- feed-label error
- team identity mismatch
- market already adjusted
- no verified cause

### 6.8 Market Already Adjusted Rule

If the board looks interesting but broader market has already corrected the angle, label:

**MARKET ALREADY ADJUSTED**

Do not promote it as a new edge.

### 6.9 Favorite / Over / Public-Side Rule

A bad price on the public side does not automatically approve the opposite side.

When a favorite, over, star prop, or popular side is overpriced:

1. Mark that side **PASS / FAVORITE TAX / PUBLIC TAX**.
2. Check the opposite side independently.
3. Compare the opposite side to consensus.
4. Approve only if the opposite side has actual positive price edge.

Do not turn “bad favorite price” into “underdog approval.”  
Do not turn “inflated over” into “under approval” without proof.

### 6.10 Output Requirement

Every approved or watch candidate must include:

- Board line / price
- Consensus line / price
- Board implied probability, if price exists
- Consensus no-vig probability, if available
- Board-vs-consensus delta
- Drift grade
- Market cause
- Market already adjusted? Yes / No
- Model probability
- Pure Edge Δ
- Final decision

---

## 7. Pricing Math

### 7.1 Decimal Implied Probability

`implied_probability = 1 / decimal_odds`

### 7.2 American Odds Implied Probability

For negative odds:

`implied_probability = abs(odds) / (abs(odds) + 100)`

For positive odds:

`implied_probability = 100 / (odds + 100)`

### 7.3 No-Vig Normalization

For a two-sided market:

`no_vig_side_probability = raw_side_probability / (raw_side_probability + raw_opposite_probability)`

For a three-way 1X2 market:

`no_vig_side_probability = raw_side_probability / sum(raw_probabilities)`

### 7.4 Pure Edge

`pure_edge = model_true_probability - no_vig_market_probability`

### 7.5 Kelly Fraction

For decimal odds:

`kelly = ((decimal_odds - 1) * p - (1 - p)) / (decimal_odds - 1)`

Use fractional Kelly, not full Kelly.

### 7.6 Minimum Edge Thresholds

General consideration threshold:

- Standard minimum: **+2.5% Pure Edge**
- Stronger inefficiency: **+3.5% to +5.0%**
- High-variance categories often require **+6% to +8%+**

Negative edge is automatic PASS even if the side is likely to win.

---

## 8. Layered Architecture

### 8.0 Layer 0.5 — Calibration Health Gate

This gate runs **before any individual prop analysis**. It checks historical bucket, archetype, sport, and prop-type health against the WOW-RESULTS-LOG.md archetype tracker. It cannot be skipped.

**REQUIRED INPUTS:**
- Active archetype tracker (from WOW-RESULTS-LOG.md)
- Bucket status for the market type being evaluated
- CLV grade history for this prop family (if available)

**SUPPRESSION RULES** (applied before analysis begins — must group by same dominant_failure_tag + bucket + sport + market_type):

| Signal | Action |
|---|---|
| 2 same-tag failures OR 1 negative CLV | MONITOR — track only, no cap |
| 3–4 same-tag failures | WARNING — Watch cap on this bucket |
| 5+ same-tag failures + 1+ negative CLV | WARNING — cap at MODEL_QUALIFIED_HOLD |
| 5+ same-tag failures + 3+ negative CLV | SUPPRESSED — no approvals |
| 8–10 same-tag failures OR negative EV after calibration | SUPPRESSED — no approvals |
| Positive CLV + bad results | Variance review — no auto action |
| Negative CLV + bad results | Strongest downgrade signal |
| No CLV data for bucket | Cap at MODEL_QUALIFIED_HOLD |
| Archetype tracker empty | NO_DATA — cap all buckets at MODEL_QUALIFIED_HOLD |

**REQUIRED OUTPUT:**
```
calibration_health:   CLEAN | MONITOR | WARNING | SUPPRESSED | NO_DATA
bucket_current_cap:   [max label permitted for this bucket today]
archetype_note:       [one-line status of relevant archetype]
```

If `calibration_health = SUPPRESSED`, no prop in this bucket may be approved in this session without explicit ChatGPT override and documented justification.

### 8.1 Layer 0 — Reality Verification / Slate Lock

- Confirm today’s exact slate.
- Confirm game/event has not started or settled.
- Confirm player/team/opponent/role.
- Confirm platform board availability.
- If not on today’s slate, reject.
- If slate status is unverified, downgrade to Watch.

**ROLE TIMESTAMP ENFORCEMENT** (Module E):

Role/status check and data timestamp are separate required fields. A single `data_timestamp` does not satisfy the `role_timestamp` requirement.

Required fields:
- `status_timestamp` — when official injury/status was last confirmed
- `role_timestamp` — when role/minutes/deployment was last confirmed
- `primary_teammate_status_timestamp` — when key teammate status was last confirmed (if prop is role-sensitive to a teammate)
- `role_confirmation_age_minutes` — calculated: game_time minus role_timestamp
- `tip_or_first_pitch_time` — scheduled start time

**STALENESS RULES:**

| Role check age | Label impact |
|---|---|
| Within 90 minutes | Passes — no penalty |
| 91–120 minutes | Watch / recheck required before approval |
| Over 120 minutes | Cannot approve — cap at MODEL_QUALIFIED_HOLD; live final-lock required |
| Lineup / starter unconfirmed | HOLD or REJECT depending on market position |
| Late status change | Full gate rerun required |

If `role_confirmation_age_minutes` cannot be calculated: role check = FAILED. Failed role check caps prop at MODEL_QUALIFIED_HOLD regardless of other gates.

### 8.2 Layer 1 — Data Intake

Required where applicable:

- Board prop/line
- Board price/multiplier
- L5 exact-line hit rate
- L10 exact-line hit rate
- L10 median
- L10 average
- Season baseline
- Official status
- Role/deployment
- Market line/price/timestamp
- Projection/simulation output

### 8.3 Layer 2 — Adjustments

- Role/minutes/leash/deployment
- Matchup/stat pathway
- Pace/possession/game script
- Weather/park/umpire
- Fatigue/travel/back-to-back
- Blowout risk
- Opponent stat-family profile
- Team context

### 8.4 Layer 3 — Simulation / Probability Construction

**model_prob is not a single analyst estimate.** It must be constructed from documented components with influence caps enforced.

**REQUIRED COMPONENT LEDGER** (Module D):

| Component | Required? | Max Influence |
|---|---|---|
| No-vig market / sportsbook comp | Yes | 40–50% |
| L10 distribution / median / hit rate | Yes | 25–35% |
| Role / minutes / usage | Yes | 10–20% |
| L5 trend modifier | Optional | ±5% hard cap |
| Matchup / context | Optional | ±3–5% if quantified |
| Narrative / story | Never | 0% — blocked |

**SHRINKAGE RULE:** No L5/L10 sample can produce a model_prob ≥ 60% without shrinkage applied to at least one baseline: season baseline, role-split baseline, or market baseline.

Example:
- Raw L10 hit rate: 8/10 = 80%
- Shrinkage-adjusted probability: 59–62%
- Usable probability after uncertainty haircut: 57%
- Result: model_prob = 57%, not 80%

**REQUIRED OUTPUT FIELDS:**
- `model_prob_source` — list components used
- `calibration_status` — CALIBRATED | UNCALIBRATED | PROXY_ONLY
- `component_ledger` — table of components with weights
- `final_model_prob` — number
- `confidence_interval` — floor–ceiling (e.g. 53–61%)
- `uncertainty_haircut` — % subtracted from final_model_prob for usable prob
- `usable_probability` — final_model_prob minus uncertainty_haircut

While UNCALIBRATED: add 3% buffer to uncertainty haircut, apply quarter-Kelly max, block Power unless trusted-bucket rules allow. Probability interval is required. Point estimates without confidence interval cannot produce FINAL_APPROVED.

### 8.5 Layer 4 — Synthesis / Final Gatekeeper

- Convert price to implied probability.
- Remove vig where possible.
- Compare model probability to market probability.
- Calculate Pure Edge Δ.
- Calculate Kelly where possible.
- Check line movement and CLV potential.
- Decide Market Verified Value / Model Qualified / MODEL_QUALIFIED_HOLD / Watch / Reject / No Play.

No bet is approved without Layer 4 where odds/market data exist.

---

## 9. 6-Checkpoint Validation Gate

Final Lock is temporarily removed as a required checkpoint. The active validation gate has six checkpoints.

### 9.1 Required Checkpoints

1. L5/L10 exact-line game logs
2. L10 median support
3. Projection/simulation support where available
4. Market support via sportsbook/book odds/no-vig where available
5. Official status/lineup/starter/XI/role confirmation where available and sport-appropriate
6. Match/context fit

### 9.2 Label Caps

- Full official/status + L10 + market/projection + context = **Fully Data-Backed / Final Approved**
- Missing market but full model proof = **Model Qualified**
- Missing L5/L10 or projection = **Watch only** *(note: "Conditional" as a final label is superseded by v16 terminal bucket taxonomy)*
- Screenshot/Pikkit/board-only = **Candidate only**
- Influencer-only = **Candidate only with influencer-slip-source-risk**

### 9A Data Contract — Required Fields Enforcement (Module B)

Every prop object must carry all required fields before any gate or scoring runs. A prop with any missing required field receives terminal bucket DATA_CONTRACT_FAIL and **approval scoring does not run**. The prop still appears in full-board output with terminal label DATA_CONTRACT_FAIL and missing-field blockers listed — no hidden cuts.

**REQUIRED FIELDS:**
- `player` / `team`
- `opponent`
- `game_date`
- `sport`
- `market` / `prop_type`
- `side` (MORE/LESS/Over/Under)
- `line`
- `book_or_platform`
- `odds_or_payout`
- `data_timestamp`
- `status_timestamp` *(separate from data_timestamp — see Section 8.1)*
- `role_timestamp` *(separate from status_timestamp — see Section 8.1)*
- `l5_values` *(array of 5 raw values vs current line)*
- `l10_values` *(array of 10 raw values vs current line)*
- `l10_median`
- `l10_mean`
- `l5_line_used` *(must match current board line within 0.5)*
- `market_no_vig_probability` *(or SOURCE_CONFLICT / MARKET_UNAVAILABLE)*
- `model_probability_ledger` *(see Section 8.4)*
- `payout_context` *(see Section 29.1)*
- `failure_path_matrix` *(see Section 29.1)*
- `directional_exposure_tags` *(see Section 27.6)*
- `provisional_label` *(initial intake classification — not final)*
- `validation_status` *(PENDING | PASSED | FAILED)*
- `blocker_reason_if_blocked` *(required if validation_status = FAILED)*

**CONTRACT ENFORCEMENT LOGIC:**
1. Intake fields missing → DATA_CONTRACT_FAIL (approval scoring does not run)
2. Required pre-score fields present → proceed to gate analysis
3. Analysis complete → assign final_label

Raw data present but not scored = INPUT_FAILURE.

### 9.3 Prohibited Labels When Data Is Incomplete

Do not use:

- Approved
- Final Approved
- Auto Green
- Submit
- Power Play
- Safest leg
- Strongest leg
- Best slip

unless required checkpoints are satisfied.

---

## 10. Individual Player / Team Audit

Every prop or team side must be evaluated individually. Generic lane logic is not enough.

### 10.1 Player Prop Audit

Required:

- L5 hit rate vs exact line
- L10 hit rate vs exact line
- L10 median vs exact line
- L10 average vs exact line
- Season baseline vs exact line
- Current role/minutes/leash/deployment
- `role_timestamp` and `role_confirmation_age_minutes` *(see Section 8.1)*
- `status_timestamp` for player and primary teammate
- Matchup impact on exact stat
- Market support on exact line when available
- MORE and LESS compared at player level
- Failure path matrix — three scenarios required *(see Section 29.1)*

### 10.2 Team Market Audit

Required:

- Opening/current line
- Board price
- Consensus price
- No-vig fair probability
- Model true probability
- Pure Edge Δ
- Kelly
- Injury/rest/lineup/starter context
- Matchup identity conflicts
- Public/favorite tax
- CLV/timing status
- Failure paths

### 10.3 Failure Tags

- generic-lane approval
- player-specific-data-missing
- line-not-supported-by-median
- role-stat mismatch
- raw-win-probability-overtrusted
- negative-edge-trap

---

## 11. Core Labels

### 11.1 Market Verified Value

Use when:

- Board price/line exists
- Consensus comparison exists
- No-vig edge is positive
- Model probability supports the side
- Sport-specific validation passes
- Role/status/context pass

### 11.2 Model Qualified

Use when:

- Exact board prop exists
- L5/L10 and median support exist
- Role/deployment and matchup support exist
- Market is missing or incomplete
- No direct market contradiction exists

### 11.3 Conditional *(legacy label — superseded)*

In v16 terminal bucket taxonomy, "Conditional" is not a valid terminal label. Props that previously would have been labeled Conditional are now routed to MODEL_QUALIFIED_HOLD (pending data) or WATCH (interesting angle, missing proof). This label definition is retained for historical reference only.

Former use case: setup is strong, one or more confirmation layers are pending, the side cannot be called approved yet → now = MODEL_QUALIFIED_HOLD.

### 11.4 Watch

Use when:

- Interesting angle
- Missing meaningful proof
- Mild drift only
- Failure paths remain material

### 11.5 Reject

Use when:

- Data fails
- Median fails
- Market is against the side
- Player/team status fails
- Wrong side is supported
- Hard ban applies

### 11.6 No Play

Use when board quality does not produce enough verified clean plays.

---

## 12. Sport Routing Labels

Every approved prop receives a routing label before slip construction.

### 12.1 CORE

Low-fragility, volume-based, stable-role props.

Examples:

- Low-line assists
- Big-man rebounds
- Stable RA
- Full-game fantasy overs for proven stat-stuffers
- Stable attempts props

Eligible for Power/Flex if fully validated.

### 12.2 CEILING

Higher-upside but more volatile props.

Examples:

- Hitter fantasy ceilings
- TB / H+R+RBI ceilings
- Star usage overs
- Same-game script props

Flex only unless elite verified.

### 12.3 MICRO-WINDOW

Small-window or niche props.

Examples:

- 1st inning pitches
- 1Q points
- 1H props
- Fouls
- Defensive rebounds

Small-stake Flex only unless all hard gates clear.

### 12.4 CHAOS

High-variance props.

Examples:

- HR props
- Demon longshots
- Thin role-player scoring overs
- Foul props

Ultra only. Never standard Power.

### 12.5 TEAM MARKET

Team winner, spread, total, F5, event winner, 1X2 markets.

Route through LLP/OddsLinker team-market engine.

---

## 13. Universal Hard Bans / Red Zones

These remain restricted unless explicitly overridden by a fully verified special-mode analysis:

- Home run props as standard plays
- Superstar 1H PRA / FS LESS
- Broad-path 1H unders on primary engines
- Demon FTM MORE without elite context
- Thin FGA MORE
- Weak wing/guard RA MORE
- Broad-path star PRA LESS without strong inflation + script
- Inactive or unresolved-status players
- Fragile scoring hooks as anchors
- Same-game SOG clustering in suppressive NHL environments
- Non-hub center 0.5 AST MORE without facilitation role
- Thin MLB K overs as power anchors
- Pure scoring / goal / TD props as anchor foundation
- Hitter fantasy LESS unless elite under script
- Binary 0.5 event props as stacked anchors
- CS2 headshots in Power Play
- Showmatch / low-integrity esports without verification

---

## 14. LESS Expansion

LESS is a separate intake lane, not just a secondary both-side check.

### 14.1 Required Behavior

- Surface LESS candidates when available.
- Rank MORE vs MORE and LESS vs LESS before merging.
- Treat LESS equally to MORE.
- Do not force bad unders.

### 14.2 Preferred LESS Families

- NBA: Points LESS, FGA LESS, selective PRA LESS only when inflated/script-supported
- MLB: Pitcher K LESS on low-whiff/low-leash inflated lines
- NHL: TOI LESS on inflated lines
- Soccer: Defender passes LESS, clearances LESS
- CS2: Kills/headshots LESS on inflated lines vs stronger opponents

### 14.3 LESS Approval Requires

- Inflated line
- Weak recent over-hit rate
- Stable or compressing role
- No false-under trap from teammate absences
- No broad stat-stuffing path
- Market support or at least no contradiction
- Projected edge preferred at 2.0+ units or verified price edge

---

## 15. Wrong-Side Reversal Audit

The model must independently check whether the opposite side is better.

### 15.1 Rules

- If signal suggests MORE, check LESS.
- If signal suggests LESS, check MORE.
- If L5/L10, median, market, and role support the opposite side, label the posted side **Wrong Side**.
- Opposite-side support creates a Watch/Research candidate, not automatic approval.

### 15.2 Failure Tags

- wrong-side-selection
- opposite-side-supported
- side-reversal-missed

---

## 16. Binary / 0.5 Event Prop Discipline

Binary 0.5 event props are hard-blocked from standard builds.

Examples:

- Runs
- RBI
- Goals
- Assists
- TDs
- HRs
- Stolen bases
- Hitter strikeouts
- Singles

Exception: if player has zero of that stat in all L5, LESS 0.5 may be Tier 2 max and never Power Play.

---

## 17. NBA / WNBA Rules

### 17.1 Required Checks

- Season baseline
- L5/L10 exact stat logs
- Usage/minutes
- Starting status within 90 minutes of tip when needed
- Back-to-back/fatigue
- Injury role shifts
- Matchup
- Pace
- Defensive rating
- Blowout risk
- Game total
- Playoff/high-stakes context
- Team defensive efficiency
- Projected game pace
- Opponent stat-path weakness

### 17.2 Strong Lanes

- Low-line assists MORE
- Big-man rebounds MORE
- Big-man RA MORE
- Stable attempts props
- Stable peripheral/fantasy overs for stat-stuffers
- Selective single-lane LESS spots

### 17.3 WNBA Source Hierarchy

Primary:

- Her Hoop Stats
- Basketball Reference WNBA Advanced

Prop/market/projection:

- BettingPros WNBA props where available
- Establish The Run WNBA projections where available
- Daily Fantasy Fuel where applicable and supported

Lineups/injuries:

- RotoWire WNBA
- Official WNBA/team injury reports

### 17.4 NBA/WNBA Failure Tags

- pace-projection-mismatch
- defense-efficiency-contradiction
- stat-path-not-supported
- team-context-overlooked
- pace-inflated-over
- suppressed-environment-risk
- false-usage-without-pace
- matchup-resistant-defense
- rebound-path-mismatch
- assist-conversion-risk
- returning-injury-role-risk

---

## 18. MLB Rules

### 18.1 MLB Pitcher Props

Required:

- Confirmed starter
- K/9 and current form
- Opponent K%
- Opponent P/PA
- Lineup patience/contact profile
- Recent pitches/IP efficiency
- Pitch count/leash
- Bullpen quality/rest
- Manager leash tendency
- Catcher framing
- Weather/park
- Umpire/zone when available
- L5/L10 exact-line support
- Market comparison

### 18.2 Pitcher K Rules

For K overs:

- Prefer modest 4.0–4.5 or 5.0 lines.
- 5.5+ requires elite verification.
- Need skill × opportunity × leash alignment.

For K unders:

- If K/9 > 9, use adjusted threshold = line × 1.15.
- If adjusted L5 support < 50%, kill the under.
- For K/9 > 10 or velocity > 99, run dual-outcome model.

### 18.3 Pitching Outs Rules

17.5 and 18.5 outs are high-leverage pull-risk thresholds.

Target Outs LESS only when supported by:

- pitch count/leash trend
- manager quick-hook profile
- bullpen strength/rest
- opponent OBP/contact/patience
- third-time-through-order risk
- game total/run environment
- recent IP/pitch efficiency

Do not auto-hammer all 17.5/18.5 unders.

### 18.4 MLB 1st-Inning Pitches Thrown

Tier 1 requires all six:

1. L5 ≥ 4/5 exact/closest line
2. L10 ≥ 7/10 exact line
3. Median raw value clearly above line and not within one pitch
4. BB/9 > 4 or last start ≥ 2 walks
5. Opponent P/PA > 3.75 or BB% > 8
6. No active line-above-recent-efficiency or dual-outcome under-path conditions ≥ 2

High K/velocity narrative is not data.

### 18.5 Line-vs-Recent Efficiency

`recent_P_per_inn = most_recent_total_pitches / most_recent_IP`

`efficiency_gap = (line - recent_P_per_inn) / recent_P_per_inn`

- Gap > 0.15 = line-above-recent-efficiency, downgrade one tier, block Tier 1.
- Gap < -0.15 = boost Over signal.
- Contact lineup K% < 21 adds Under-path condition.

### 18.6 MLB Hitter Props

Check:

- BA / OBP / SLG
- Platoon split
- L5/L10 logs
- Home/away
- Opposing pitcher quality
- Bullpen
- Park/weather
- Lineup slot
- Plate appearance volume
- Pitch-type matchup

### 18.7 MLB DFS Correlation Lane

Allowed only when every leg passes standalone gates.

Allowed correlations:

- Pitcher K LESS + opposing hitter Hits/TB/H+R+RBI MORE
- Pitcher Outs LESS + opposing hitter volume MORE
- Pitcher K LESS + batter strikeout LESS when available

Never call correlation “safe” unless each leg is individually playable.

### 18.8 MLB Failure Tags

- outs-threshold-trap
- manager-leash-misread
- bullpen-rest-misread
- sixth-inning-pull-risk
- TTTO-risk
- fake-correlation
- correlation-without-standalone-edge
- pitcher-shell-path-overstated
- same-game-correlation-risk
- PA-volume-overstated
- walk-path-not-TB-path
- catcher-framing-risk
- day-after-night-fatigue
- post-injury-leash-risk

---

### 18.9 MLB Plate Appearances Props

Plate Appearances are modeled as a discrete opportunity/volume distribution, not treated as binary merely because many PrizePicks lines display at a half-point threshold (e.g. 4.5). The half-point-threshold exemption applies only to market classification -- it does NOT waive exact-line distribution modeling, settlement verification, calibration, role/lineup verification, promotional economics, or failure-path analysis. Standard 0.5 event prop hard-ban (Section 16) does not apply to PA -- PA is evaluated as a volume stat, same treatment as Section 18.3 Pitching Outs.

Required PA opportunity distribution (the final probability must come from this distribution, not from L10 hit rate treated as model probability):
- P(PA = 3), P(PA = 4), P(PA = 5), P(PA >= 6)
- P(MORE exact line), P(LESS exact line)
- expected_PA, median_PA, PA_distribution_interval

Required lineup/role inputs:
- Confirmed starting lineup status and batting order slot
- Slot-specific modeling -- do not treat slots 1-6 as homogeneous; model slots 1-3, 4-6, and 7-9 separately
- L5/L10 exact-line PA hit rate, L10 median, L10 average (evidence for the distribution, not the model itself)

Required home/away and game-state inputs:
- home_away
- probability_bottom_9th_is_played (home team leading after top 9 does not bat in the bottom 9, reducing opportunity)
- team_implied_run_total (market/environment prior only -- influences expected PA volume, does not replace independent modeling)

Required opposing pitching inputs (replaces vague "pace"):
- opposing_starter_run_prevention
- opposing_starter_BB_rate
- opposing_starter_WHIP_or_baserunner_environment
- expected_starter_length
- opposing_bullpen_quality
- opposing_bullpen_availability

Required substitution/removal failure paths:
- pinch_hit_risk
- defensive_replacement_risk
- platoon_substitution_risk
- injury_or_return_from_injury
- recent_full_game_start_rate
- manager_substitution_tendency_when_material

Required game-state regime modeling (failure-path layer):
P(prop) = sum over i of [ P(regime_i) x P(prop | regime_i) ], across regimes:
NORMAL_9_INNING_GAME, LOW_OFFENSE_SHORT_OPPORTUNITY, HIGH_OFFENSE_EXTRA_TURNOVER, HOME_TEAM_NO_BOTTOM_9TH, EXTRA_INNINGS, PLAYER_EARLY_REMOVAL, PLATOON_OR_PINCH_HIT_REMOVAL, WEATHER_OR_GAME_INTERRUPTION

Required promotional/board verification:
- exact_line_verified = true
- offer_type_verified = true (Standard / Goblin / Demon)
- direction_available_verified = true
- For Demon/Goblin/promotional PA lines: probability qualification does not equal payout qualification -- promo economics must still separately pass the applicable payout/slip layer (Section 26-27)

Routing decision logic:
- Starting lineup unconfirmed => REJECT_DATA_QUALITY / HOLD
- Batting slot unresolved => HOLD
- Exact PA line unavailable => REJECT_DATA_QUALITY
- L5/L10 ledger unavailable => REJECT_DATA_QUALITY
- No PA opportunity distribution built => REJECT_DATA_QUALITY
- Platoon/substitution risk materially unresolved => MICRO_WINDOW / HOLD
- Slots 7-9 with unstable start history => MICRO_WINDOW ceiling (not automatic reject -- may exceed ceiling if model demonstrates sufficient opportunity probability)
- Confirmed stable slots 1-6 + complete distribution + calibration + failure paths resolved => CORE eligible

Volatility flags:
- Green: locked-in slots 1-6, 15+ consecutive full-game starts, stable order, no material substitution risk
- Yellow: slots 7-9, recent order changes, platoon situation, moderate substitution risk
- Red: recent callup/debut, inconsistent starts, active lineup battle, elevated substitution risk

---

## 19. NFL Rules

Required:

- Snap share
- Target/carry share
- Air yards
- Red-zone usage
- L5 logs
- Weather/wind
- Implied team total
- Spread/game script
- Injury report
- Offensive line context
- Opponent defensive profile

Strong lanes:

- Receptions
- Rush attempts
- Pass attempts/completions
- Rush+rec for dual-usage backs
- Receiving yards only with secure target share / air-yards role

Avoid:

- TDs as anchors
- Longest play props
- Yardage overs without volume proof

---

## 20. NHL Rules

Required:

- TOI
- PP TOI/unit
- Line combinations
- Goalie confirmation
- Goalie SV%
- Game total
- Shot environment
- Opponent shot suppression
- Market comparison

Strong lanes:

- SOG MORE
- TOI/minutes
- Goalie saves
- Selective TOI LESS

Restrictions:

- Max 1 SOG over per game unless both are elite-volume and environment supports both.
- Avoid same-game SOG clustering in suppressive matchups.

---

## 21. Soccer Rules

### 21.1 Soccer Verification Engine

Every soccer prop or team market must route through the Soccer Verification Engine.

Required fields:

- XI status: Confirmed XI / Projected XI / XI Unknown / Bench/Sub Risk / Out/Not In Squad
- Role/minutes/sub risk
- Possession expectation
- Market sanity
- L5/L10 exact-line support where applicable
- Match context

### 21.2 Soccer Source Access Object

Every soccer run must output:

- lineup_source: Available / Partial / Missing / Not Called
- stat_source: Available / Partial / Missing / Not Called
- market_source: Available / Partial / Missing / Not Called
- possession_source: Available / Partial / Missing / Not Called
- projection_source: Available / Partial / Missing / Not Called

### 21.3 Soccer Prop Families

Passes:

- XI/projection
- role
- possession script
- L5/L10 exact-line support
- median
- market sanity

Clearances:

- defensive pressure
- opponent crosses/entries/shots
- possession disadvantage/favorite context
- XI/projection
- L5/L10 support
- role

GK saves:

- confirmed/projected keeper
- opponent shot volume/SOT profile
- expected pressure
- line range
- L5/L10 support
- market sanity

Shots/key passes:

- attacker role
- minutes
- set-piece/creative role
- opponent allowance
- L5/L10 support
- market sanity

### 21.4 Soccer Final Labels

- Market Verified Approved
- Model Qualified — Soccer
- MODEL_QUALIFIED_HOLD — XI Pending *(formerly Conditional — XI Pending)*
- Watch — Projected XI Only
- Reject

### 21.5 Soccer Team Market Desync

Before pricing international soccer team markets, verify:

- Senior / U23 / Olympic / B-team / domestic-only / split squad
- Official squad release or trusted confirmation
- Overseas-player inclusion/missing
- Coach intent
- Tournament motivation
- Match status: A-international, youth, friendly, or tournament
- Cross-feed labels from platform, odds feed, federation/media

### 21.6 Market Desync Grades

- A+ = Market broadly mislabels team identity and most public feeds are wrong
- A = Market mostly generic; verified squad mismatch not widely reflected
- B = Fragmented labels; some feeds correct, some generic
- C = Market likely aware of squad identity
- D = No verified desync

### 21.7 Stale Board + Cross-Market First Gate

For soccer team markets, stale-board/consensus drift check comes before identity narrative.

Mandatory order:

1. Board price extraction
2. Consensus market comparison
3. No-vig board probability
4. No-vig consensus probability
5. Board-vs-consensus delta
6. Cause classification
7. Team identity audit
8. Final value decision

### 21.8 Draw Value Scanner

When favorite is severely compressed, automatically check the draw.

Draw becomes candidate when:

- favorite board price is much shorter than consensus
- draw board price is longer than consensus
- match type is friendly/low-urgency/rotation-prone
- underdog win path is thin but draw path is credible

### 21.9 Soccer Failure Tags

- soccer-xi-missing
- projected-xi-only
- sub-risk-high
- possession-script-contradiction
- market-against-soccer-side
- missing-soccer-book-comp
- role-position-mismatch
- clearance-pressure-missing
- pass-volume-overstated
- gk-save-pressure-missing
- soccer-opposite-side-supported
- soccer-team-identity-mismatch
- market-label-fragmentation
- youth-squad-uncertainty
- domestic-only-squad-risk
- favorite-overpriced
- favorite-fade-not-underdog-approval
- winless-streak-overweighted
- ranking-overweighted
- opponent-strength-not-adjusted
- stale-roster-blocker
- market-desync-overstated
- stale-board-overlooked
- consensus-market-ignored
- identity-narrative-overweighted
- price-desync-not-verified
- market-already-adjusted
- favorite-fade-overconverted
- longshot-overlay-overstated
- draw-value-missed

---

## 22. Tennis Rules

Tennis is support/ceiling by default, not core.

Required:

- Official match context
- Surface
- Rank/form
- Opponent style
- Fatigue
- Injury
- Tournament motivation
- Market sanity
- Exact-line support where available

Avoid:

- Same-surface/same-tournament/same-prop correlated cards
- Promotional/influencer slips without independent validation
- Total-games overs as Power anchors

---

## 23. Golf Rules

Required:

- Course history
- Recent results last three tournaments
- SG:Total
- Injury/withdrawal flags
- Course fit
- Weather where relevant
- Market comparison

---

## 24. Esports / CS2 Rules

CS2 showmatches are structural kill spots unless format, role, map, opponent, recent logs, and motivation are verified.

CS2 headshot/Kill props require:

- Match format
- Map pool
- Role
- Opponent
- Recent L5/L10 logs
- Market/projection sanity

Restrictions:

- CS2 headshots banned from Power Play.
- 5-leg same-match CS2 cards are maximum-correlation risk and generally Do Not Play.

Failure tags:

- showmatch-structural-kill
- maximum-correlation-risk

---

## 25. LLP Team Bet Synthesis

All LLP team-side bets must pass Layer 4B Team Bet Synthesis.

Required:

- Opening/current line
- Board price
- Implied probability
- No-vig fair probability
- Model win probability
- Pure Edge Δ
- Kelly
- Confirmed starter/goalie/lineup
- Injury/rest context
- Bullpen reliability for MLB
- Prop-correlation support
- Variance/failure-path review
- Stake cap

### 25.1 Team Bet Rules

- Short favorites with implied probability ≥ 55% require ≥ 4% edge and Tier 1 prop correlation or PASS.
- MLB full-game ML defaults to F5 unless bullpen edge ≥ 0.65 or edge ≥ 6%.
- Team bets require 2+ supporting props with correlation ≥ 0.60 each or 1 Tier 1 prop correlation ≥ 0.70.
- Final stake = min(tier cap, Kelly).
- Kelly < 0.5% = PASS.
- Largest stake on card with edge < 5% must be reduced or passed.
- No narrative-only team bets.

---

## 26. PrizePicks / Fixed-Payout Rules

### 26.1 PrizePicks Implied Probability

For decimal-style multipliers:

`implied_probability = 1 / multiplier`

Then apply payout friction, multiplier tax, and market comparison.

### 26.2 Hit-Rate Thresholds

- 2-pick Power Play: each leg > 57.8%
- 3-pick Power Play: each leg > 64.2%
- 4-pick Power Play: each leg > 67.9%

These are minimum break-even thresholds, not approval thresholds. Model approval still requires validation and edge.

### 26.3 Power Play Rule

A Power Play can only include fully validated Tier 1 legs.

No MODEL_QUALIFIED_HOLD, Watch, Research, or incomplete-data legs in Power.

### 26.4 Flex Rule

Flex can include Tier 1 plus max one Tier 2 unless the card is intentionally higher variance and labeled accordingly.

---

## 27. Slip Construction

### 27.1 General Rules

- Build slips only after approved prop pool exists.
- No duplicate players inside a slip.
- No excessive same-game fragility.
- No player reuse unless elite verified.
- Max two props per game unless intentional and approved correlation.
- Diversify sport/game/team exposure.
- No forced volume.

### 27.2 Power Plays

- Tier 1 only.
- Prefer 2-pick Power.
- 3+ Power only when all legs are elite verified.
- No micro-window or chaos legs unless explicitly allowed by active patch.

### 27.3 Flex

- Tier 1 plus limited Tier 2.
- Protection does not make a fragile card safe.
- Max one MICRO-WINDOW unless exceptional.

### 27.4 Ultra / Ceiling Mode

Optional only when explicitly requested.

Structure:

- 2–3 strong base legs
- 2–3 ceiling legs
- Max 1 chaos/extender leg
- Small stake only

Restrictions:

- No lottery foundation.
- No random same-game chaos stacking.
- Ceiling layer allowed only after base is strong.
- Tennis/games/TB/hitter ceilings are extenders only.

### 27.5 Aggressive Ceiling Scout

Optional scout lane only. It may surface:

- micro-lines
- attempt props
- correlated game-script props
- role-player micro props
- star usage overs
- suppression unders

Labels:

- Core
- Support
- Fragile
- Chaos

Never call this lane safe.

### 27.6 Directional Exposure Ledger (Module G)

Per-player and per-game caps do not catch hidden correlated game-script risk. This module tracks exposure by script type across all legs in a session. It runs after per-player and per-game caps, before final slip label.

**SCRIPT TYPES TO TRACK:**

| Script Type | Examples |
|---|---|
| Fast pace / over environment | Multiple MORE points/PRA/assists from same game |
| Slow pace / under environment | Multiple LESS props from same game |
| Blowout script | Favorite overs, dog unders, bench-risk legs combined |
| Pitcher dominance | Ks MORE + opponent hitters LESS + game under |
| Starter short leash | Outs LESS + bullpen angle + opponent late scoring |
| Injury-role script | Multiple props dependent on same teammate being out |
| Pace-sensitive combo | Any 3+ legs whose hit probability changes materially if pace is 5+ possessions different from projection |

**RULES:**

*Slip-level:*
- 3+ legs in one slip sharing the same script type = DIRECTIONAL_EXPOSURE_BLOCK unless correlation EV math is documented and positive
- Correlation EV math must show: individual leg EVs, joint failure probability, net slip EV after accounting for correlated failure. Assertion is not math.
- DIRECTIONAL_EXPOSURE_BLOCK is a terminal slip label, not a warning.

*Session-level:*
- 4+ same-script legs across the full session = SESSION_EXPOSURE_WARNING (logged, no block — analyst must acknowledge)
- 6+ same-script legs across the full session = SESSION_DIRECTIONAL_EXPOSURE_BLOCK (no further same-script legs may be added to any slip without ChatGPT override)

**REQUIRED OUTPUT:**
```
directional_exposure_tags:     [list of script types on each leg]
directional_exposure_count:    [legs sharing dominant script — slip level]
session_directional_count:     [legs sharing dominant script — session level]
directional_exposure_scope:    SLIP | SESSION | BOTH
directional_exposure_verdict:  CLEAN | WARNING | BLOCK | SESSION_WARNING | SESSION_BLOCK
```

---

## 28. Bankroll and Unit Rules

### 28.1 General

- Use fractional Kelly.
- Kelly must be ≥ 0.5% bankroll for a standard bet.
- Cap by tier and volatility.
- No max-Kelly escalation from stale-line triggers alone.
- Stale/frozen line increases candidate priority only, not automatic unit size.

### 28.2 Suggested Unit Ranges

- Market Verified Tier 1: 0.75u–1.25u depending on edge and variance
- Model Qualified: 0.25u–0.75u
- MODEL_QUALIFIED_HOLD / Watch: No standard bet
- Longshot value: 0.10u–0.25u unless fully market verified
- Ultra Ceiling: Small stake only

### 28.3 Portfolio Caps

- Cap units per slate.
- Cap units per sport.
- Limit same-player exposure.
- Limit same-team exposure.
- Limit correlated game scripts — see Section 27.6 for enforcement mechanism and hard block thresholds.
- Do not chase.

---

## 29. Output Templates

### 29.1 Standard Candidate Output

For every candidate:

- Player/team/event
- Sport
- Prop/market
- Side
- Line/price
- Board source
- Board implied probability
- Consensus line/price
- Consensus no-vig probability
- Board-vs-consensus delta
- Drift grade
- Market cause
- L5 exact-line hit rate
- L10 exact-line hit rate
- L10 median/average
- Role/status
- `role_timestamp` / `role_confirmation_age_minutes`
- Matchup/stat pathway
- Projection/model probability
- `component_ledger` / `confidence_interval` / `usable_probability`
- Pure Edge Δ
- Kelly/unit guidance
- Volatility flag
- Source grades (critical path)

**PAYOUT CONTEXT** *(required on every prop — Module C):*
```
Intended format:         [2-pick Power / 3-pick Power / 3-pick Flex / 4-pick Flex /
                          5-pick Flex / 6-pick Flex / Straight bet / FORMAT_PENDING]
Slip break-even:         [% required for this format, or N/A if FORMAT_PENDING]
Required per-leg prob:   [% each leg needs from PP payout table, or N/A]
Model probability:       [final_model_prob]
Uncertainty haircut:     [-X%]
Usable probability:      [model_prob minus haircut]
EV gap:                  [usable_prob minus required_per_leg_prob]
Payout slip label:       POSITIVE_EV | NEGATIVE_EV | MARGINAL_EV | FORMAT_PENDING |
                          UNUSABLE | UNVERIFIED
```

PAYOUT LADDER:
- Known slip type + positive EV → eligible for slip construction
- Known slip type + negative EV → MARKET_QUALIFIED_BUT_SLIP_NEGATIVE — blocked from slip
- Known slip type + payout unavailable → cap MODEL_QUALIFIED_HOLD
- Not yet determined → FORMAT_PENDING — cannot enter slip yet, analysis continues
- Straight sportsbook bet → use sportsbook price / no-vig edge, not PP payout table

**FAILURE PATH MATRIX** *(required on every prop — Module F):*
```
PRIMARY KILL PATH:
  scenario:           [most likely single failure mode]
  probability_band:   [e.g. 15–25%]
  model_adjustment:   [e.g. "-3% applied to model_prob"]
  evidence:           [source or data point]

SECONDARY KILL PATH:
  scenario:           [second most likely failure mode]
  probability_band:   [e.g. 10–18%]
  model_adjustment:   [e.g. "-2% applied"]
  evidence:           [source or data point]

BLACK SWAN PATH:
  scenario:           [low probability but catastrophic]
  probability_band:   [e.g. 2–5%]
  void_dnp_risk:      [Yes / No / Unknown]
  evidence:           [any supporting signal]
```

FAILURE PATH RULES:
- Primary kill path probability_band floor > 30%: model_prob must be haircut; adjustment must be documented. Cannot approve without haircut applied.
- Two kill paths each with floor ≥ 20%: downgrade prop one tier.
- Undocumented kill path: gate fails. Cannot approve.
- Role/minutes kill path (blowout sub, foul trouble, leash): treated as serious downgrade signal.
- Abstract "failure paths reviewed" without populating all three scenarios = DATA_CONTRACT_FAIL.

**Final label**

### 29.2 Run-Level Output

Every run must return:

1. Source Access Status
2. Layer 0.5 Calibration Health Gate output
3. Execution Validation Report
4. Board Classification
5. Market Verified Value
6. Model Qualified
7. MODEL_QUALIFIED_HOLD
8. Watch
9. Rejects with failure tags
10. DATA_CONTRACT_FAIL rows (with missing-field blockers listed)
11. MARKET_QUALIFIED_BUT_SLIP_NEGATIVE rows
12. FORMAT_PENDING rows
13. Slip output only if requested
14. No Play / Data Insufficient if applicable

---

## 30. Volatility Flags

### 30.1 Green

- 15+ consistent games
- Confirmed starter/full role
- No injury change
- L5 ≥ 3/5
- Line clearly separated from average
- MLB BB/9 < 2.5 for pitchers where relevant

### 30.2 Yellow

- 5–10 game sample
- Fluctuating minutes/TOI
- Position battle
- Unfamiliar opponent
- Post-IL pitch cap
- MLB BB/9 3.5–5

### 30.3 Red

- Debut / fewer than 5 games
- Returning injury
- Key teammate uncertainty
- MLB BB/9 > 5
- Career-average line
- Rookie / ≤ 15 MLB starts
- Tommy John return

---

## 31. Postmortem Engine

Every settled pick must be logged.

### 31.1 Required Fields

- Player/team
- Sport
- Prop/market
- Side
- Line/price
- Classification
- WOW score/model probability
- Closing line
- Actual result
- Win/loss/push
- Process grade
- CLV grade
- Failure tags
- Patch needed
- Future rule

### 31.2 Process Categories

- Clean win
- Fragile win
- Lucky win
- False-signal win
- Bad beat
- Good process loss
- Bad process win
- Model failure

### 31.3 Postmortem Discipline

- Process > outcome.
- Wins can be bad process.
- Losses can be good process.
- Repeated failure archetype 3+ same-tag failures = WARNING — see Section 8.0 (Layer 0.5) for full calibration health ladder. "3x = downgrade" is no longer the authoritative rule; Section 8.0 governs.
- Sustained winning archetype = eligible for upgrade review.

---

## 32. Failure Tag Library

Use exact tags where possible.

### 32.1 Data / Validation

- data-validation-gap
- missing-l10-ledger
- missing-market-support
- missing-projection-support
- pikkit-signal-overtrusted
- influencer-slip-source-risk
- reasoned-not-modeled
- no-vig-layer-skipped
- model-score-overtrusted
- l5-l10-overtrusted

### 32.1a Enforcement / Contract (new — Module B–H)

**Failure tags:**
- data-contract-fail
- calibration-suppressed
- payout-friction-prop-level
- probability-unshrunk
- role-timestamp-stale
- failure-path-undocumented
- directional-exposure-block
- source-grade-degraded

**Terminal / status labels (centralized for dashboard mapping):**
- DATA_CONTRACT_FAIL — required field missing; approval scoring blocked; prop still appears in full-board output
- MARKET_QUALIFIED_BUT_SLIP_NEGATIVE — prop passes market gates but EV is negative at intended slip format
- FORMAT_PENDING — slip type not yet determined; prop holds out of slip construction pending format selection
- DIRECTIONAL_EXPOSURE_BLOCK — slip-level: 3+ legs share same game script without documented correlation EV math
- SESSION_EXPOSURE_WARNING — session-level: 4+ legs share same game script; analyst acknowledgment required
- SESSION_DIRECTIONAL_EXPOSURE_BLOCK — session-level: 6+ legs share same game script; no further same-script legs without ChatGPT override

### 32.2 Market / Pricing

- consensus-market-ignored
- stale-board-overlooked
- market-already-adjusted
- narrative-overweighted
- price-desync-not-verified
- opposite-side-overconverted
- public-tax-misread
- stale-line-without-cause
- derivative-desync-unverified
- raw-win-probability-overtrusted
- negative-edge-trap
- line-freeze-overbet
- stale-derivative-edge-unvalidated
- max-kelly-overreach
- market-cause-unverified
- payout-friction-underestimated
- pure-edge-below-threshold

### 32.3 Slip / Exposure

- power-play-overpromotion
- power-play-overuse
- repeat-failed-leg-exposure
- cross-slip-overexposure
- same-archetype-overstacking
- maximum-correlation-risk
- correlation-without-standalone-edge

### 32.4 Sport-Specific

- pace-projection-mismatch
- defense-efficiency-contradiction
- stat-path-not-supported
- matchup-resistant-defense
- rebound-path-mismatch
- assist-conversion-risk
- pitching-outs-leash-risk
- outs-threshold-trap
- manager-leash-misread
- bullpen-rest-misread
- TTTO-risk
- catcher-framing-risk
- day-after-night-fatigue
- soccer-xi-missing
- projected-xi-only
- possession-script-contradiction
- market-against-soccer-side
- soccer-team-identity-mismatch
- market-label-fragmentation
- market-desync-overstated
- showmatch-structural-kill

---

## 33. Activation Prompt

Use this as the short operational prompt:

```text
Load WOW v16 Clean Core / Framework v2.2.0 Master Spec.
Run Complete System Enforcement Mode.
Treat screenshots/boards as the available menu only.
Start with Source Access Status, then run Layer 0.5 Calibration Health Gate before any board analysis.
Apply Universal Cross-Market First Gate before narratives.
Enforce Data Contract (Section 9A) — missing required fields = DATA_CONTRACT_FAIL, no approval scoring runs.
Run board intake, slate lock, individual audit, L5/L10 exact-line validation, role/deployment check (with role timestamps), matchup/stat pathway, probability component ledger, market sanity, payout context, LESS lane, failure-path matrix, and Layer 4 Synthesis where odds exist.
Return Market Verified Value, Model Qualified, Watch, and Rejects.
Do not fake data. Do not force slips. If slips are requested, build only from approved props and apply correlation, directional exposure, bankroll, and slip QA.
```

---

## 34. One-Line Definition

**WOW v16 Clean Core is a cross-market-first, enforced-gate betting framework that treats boards as menus, runs calibration health before analysis, enforces a data contract on every prop, verifies payout friction at the prop level, requires probability component documentation with shrinkage, mandates quantified failure paths, and only executes when market edge, model probability, role freshness, and slip EV all survive.**

---

## 35. Final Operating Rule

**Find the price error first. Prove the data pathway second. Execute only if both survive.**
