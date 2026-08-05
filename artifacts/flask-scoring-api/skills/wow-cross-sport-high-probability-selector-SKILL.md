---
name: wow-cross-sport-high-probability-selector
description: >
  Cross-sport daily selector: ranks candidates into four output lanes —
  Highest Hit Probability, Highest True Probability, Best Verified Edge,
  and Best Multi-Leg Structure — across tennis, MLB, WNBA/NBA, NHL, NFL,
  soccer moneylines, supported props, and Kalshi sports contracts. Runs
  governance sync, event-identity lock, specialist-model delegation,
  market normalization, cross-leg dependence audit, weakest-leg
  elimination, and mandatory final refresh before publishing. Never
  auto-executes, never sizes stake; human confirmation always required;
  NO_PLAY is always valid. Kalshi sports contracts route exclusively
  through /wow/kalshi/category-scan (portfolio_governor, max 2 total,
  max 1/event); Kalshi combos of 3+ markets are unconditionally blocked.
  This skill owns ranking and selection only — specialist gates and the
  backend own terminal-label assignment. Triggers on "cross-sport card",
  "best plays today across sports", "highest probability slate", "build
  a compact card", "weakest leg check". STATUS: ANALYTICAL SHADOW MODE —
  ChatGPT Step 3 approved with required revisions (2026-08-05).
  wow-high-hit-engine is absent from the active skill stack; no
  coexistence or replacement decision is required at this time.
---

# Skill: wow-cross-sport-high-probability-selector

## Purpose

Identify the strongest daily cross-sport outcome candidates across supported sports and separate them into four ranked output lanes:

1. **Highest Hit Probability** — by raw model probability
2. **Highest True Probability** — by calibrated probability with lower-bound floor
3. **Best Verified Edge** — by lower-bound edge over no-vig market probability
4. **Best Multi-Leg Structure** — by joint lower bound after dependence audit and weakest-leg elimination

This skill ranks and selects candidates only. It does not assign terminal labels to legs. Terminal-label authority belongs exclusively to the specialist gates and the backend.

---

## Permanent Governance

```text
auto_execute=false
requires_human_confirmation=true
stake_sizing=false
bankroll_allocation=false
can_execute=false
dry_run_only=true
NO_PLAY=valid
```

**These seven invariants are permanent and unconditional.** They may not be altered by any future calibration milestone, shadow-mode lift, or skill update without a new patch that clears the full ChatGPT governance review pipeline. Execution-layer safety does not lift with calibration quality.

**Activation status:** ANALYTICAL SHADOW MODE. ChatGPT Step 3 approved with required revisions (2026-08-05). Calibration milestones for `can_execute` qualification have not been reached; no output from this skill is actionable until those milestones are explicitly confirmed via a new patch.

---

## Ownership Separation

This skill's authority is strictly limited to:

```text
OWNS:
  - candidate discovery across supported sports
  - ranking within each of the four output lanes
  - cross-leg dependence audit
  - weakest-leg elimination
  - final refresh trigger
  - output formatting and lane assignment

DOES NOT OWN:
  - terminal-label assignment for any leg
  - calibration model decisions
  - specialist-gate verdicts (wow-gate-enforcer, wow-llp-runner,
    wow-kalshi-sports-gate, wow-llp-moneyline-probability-expert, etc.)
  - sportsbook-line validation
  - injury-report classification
  - settlement definition
```

When a candidate leg reaches a specialist gate and receives a terminal label from that gate, this skill's ranking opinion is advisory only. If the specialist gate returns a blocking label (`REJECT_*`, `SLATE_PURGE`, `SOURCE_CONFLICT`, `DUPLICATE_EXPOSURE_BLOCK`), the leg is excluded from all lanes regardless of its ranking score. The selector does not override specialist gates.

---

## Daily Objective

Build a compact card from independently strong candidates across sports, preferring:

```text
verified event identity
fresh participant status
stable role
complete specialist model
high calibrated lower bound
low failure-path concentration
clean market confirmation
low cross-leg dependence
```

Never force a requested leg count. If no candidate qualifies across all lanes, return NO_PLAY.

---

## Source Order

```text
1. Replit backend (live endpoints)
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

```text
Tennis match winner
MLB moneyline
WNBA moneyline
NBA moneyline
NFL moneyline
NHL moneyline
Soccer three-way or draw-no-bet where supported
Supported player props (via active sport-specific prop module)
Kalshi sports contracts (see Kalshi Routing section)
```

Unsupported sports or markets fail closed. No candidate is ranked without a confirmed active specialist model.

---

## Kalshi Routing and Portfolio Governor

Kalshi sports contracts are subject to routing and combo restrictions that are not negotiable for this skill.

### Required Route

All Kalshi sports-contract candidates must route through:

```text
GET /wow/kalshi/category-scan
```

This endpoint runs:
  - category_router (classifies each market as weather / sports / economics / combo / disabled)
  - 9-gate sports filter (`kalshi_engine/sports_gate`)
  - portfolio_governor (`kalshi_engine/portfolio_governor`)
  - Recovery-Mode survivor ranking

The selector must use only the pool returned by this endpoint. It must not attempt to independently scan Kalshi markets or bypass the portfolio governor.

### Portfolio Governor Caps (hard, not advisory)

```text
max 2 Kalshi sports-contract candidates in the output pool
max 1 Kalshi candidate per event
can_execute=False always (unconditional)
```

### Combo Restrictions (combo_gate.py Reliability Freeze)

```text
1–2 Kalshi markets in a combo → allowed (proceed to joint EV check)
3-market Kalshi combo         → REJECT_BAD_STRUCTURE
4+-market Kalshi combo        → HARD_REJECT_COMBO_MULTIPLICATION
```

These are enforced by `gate_engine/combo_gate.py` with `RELIABILITY_FREEZE=True`. The selector must not present a Kalshi combo that has not cleared the combo gate. If combo_gate returns `allowed=False`, the candidate is excluded from all lanes with the combo gate's reject_code preserved.

### Recovery Mode

When `/wow/kalshi/category-scan` returns a pool via the Recovery-Mode portfolio governor, the selector presents only the survivors — candidates filtered out by the portfolio governor or combo gate are excluded from all lanes and listed in Lane D (Rejected) with their governor reject label.

---

## Mandatory Workflow

```text
1.  Governance sync
2.  Slate discovery
3.  Event identity lock
4.  Participant/status lock
5.  Exact market and settlement lock
6.  Specialist sport model (delegated to sport-specific gate or module)
7.  Opponent and matchup model
8.  Failure-path model
9.  Calibration and uncertainty
10. Market normalization
11. Probability / edge separation → lane assignment
12. Cross-leg dependence audit
13. Weakest-leg elimination
14. Final refresh (mandatory; see Final Refresh section)
15. Prediction record attempt (see Ledger Degradation section)
16. User-facing output
```

No later step may erase an earlier blocker. A blocking result from Step 6 (specialist gate) overrides any lane assignment from Step 11.

---

## Four Output Lanes — Scope and Authority

### Lane A — Highest Hit Probability

**Scope:** Candidates ranked by raw_probability, descending.
**Threshold:** raw_probability >= 0.70
**Authority:** Ranking only. Terminal labels come from the specialist gate, not from this lane's assignment.
**Output fields:**
```text
rank
candidate (sport / event / market / side)
raw_probability
calibrated_probability
lower_bound
model_status
specialist_gate_label (pass-through from specialist gate — never upgraded)
```

### Lane B — Highest True Probability

**Scope:** Candidates ranked by calibrated_probability, with lower-bound floor.
**Threshold:** calibrated_probability >= 0.70 AND calibrated_lower_bound >= 0.65
**Authority:** Ranking only. Calibrated values come from the active calibration model; the selector does not adjust them.
**Output fields:**
```text
rank
candidate
calibrated_probability
calibrated_lower_bound
calibrated_upper_bound
model_status
calibration_status
```

### Lane C — Best Verified Edge

**Scope:** Candidates ranked by lower_bound_edge over no-vig market probability.

```text
lower_bound_edge = calibrated_lower_bound - no_vig_market_probability - friction_buffer
```

**Required:**
```text
lower_bound_edge > 0
exact market match confirmed
settlement definition match confirmed
fresh two-sided prices (both sides of the market)
material_market_conflict=false
```

If any required field is missing (e.g., stale market price, settlement mismatch), the candidate is excluded from Lane C but may still appear in Lanes A or B.
**Output fields:**
```text
rank
candidate
no_vig_market_probability
calibrated_lower_bound
lower_bound_edge
conservative_EV
market_freshness
```

### Lane D — Rejected Popular Candidates

**Scope:** All candidates that were discovered but excluded from Lanes A, B, or C, or that failed the specialist gate, combo gate, portfolio governor, or dependence audit.
**Authority:** reject_reason and terminal_label are passed through from the originating gate or module — this skill never authors a terminal label.
**Output fields:**
```text
candidate
reject_source (which gate/module produced the block)
reject_reason
terminal_label (from specialist gate or governor — never invented here)
```

---

## Candidate Scoring Fields

Each candidate must return before lane assignment:

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
specialist_gate_label       (from gate — advisory to this skill)
```

The selector does not emit a terminal_label of its own. If the user-facing output requires a label on an excluded candidate (Lane D), the label is reproduced verbatim from the specialist gate or governor.

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
Failure paths: retirement, fitness decline, serve collapse, tiebreak variance, surface mismatch, fatigue

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
Failure paths: starter early collapse, bullpen failure, lineup scratch, weather interruption, extra-inning variance

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
Failure paths: late scratch, minutes restriction, rotation surprise, shooting variance, foul trouble, garbage-time distortion

### Player Props

Use only the active sport-specific prop module and exact-line L5/L10 ledger.

### Kalshi Sports Contracts

Route exclusively through `/wow/kalshi/category-scan`. Apply combo gate and portfolio governor caps as specified in the Kalshi Routing section. Do not score Kalshi contracts independently.

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

Never treat midpoint as no-vig. Never compare markets with different settlement definitions.

---

## Cross-Leg Structure

A card must minimize dependence. Block or penalize:

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

Default target: 2–4 independently strong legs. Prefer fewer legs when the next candidate materially weakens joint lower bound.

---

## Weakest-Leg Elimination

For every proposed multi-leg card, test:

```text
full card
card minus weakest leg
card with replacement candidate
smallest qualifying card
```

Weakest leg is ranked by:

```text
lowest calibrated lower bound      (primary)
highest uncertainty
highest failure-path score
stale status
market disagreement
highest correlation contribution
```

Remove if joint lower bound or conservative EV improves materially after removal. This test runs before any output is produced — it is not optional.

---

## Final Refresh

**Final refresh is mandatory.** No output may be produced without completing it.

Within the active freshness window, recheck:

```text
event status
participant status
lineup / starter
market price (both sides)
settlement status
weather
cross-ticket exposure
```

If any material change is detected, the full workflow restarts from Step 3 (event identity lock).

If the final refresh cannot be completed (API unavailable, freshness window expired), return NO_PLAY with `final_refresh_status=FAILED` rather than producing output from stale data.

---

## Ledger Degradation

### Immutable Prediction Ledger

**Current status: NOT_AVAILABLE**

No `wow_prediction_ledger` table, endpoint, or module exists in the current backend. Step 15 (Prediction record attempt) degrades as follows:

```text
prediction_write_attempted = true
prediction_write_status    = NOT_AVAILABLE
prediction_write_reason    = "wow_prediction_ledger not present in backend"
```

This degradation is non-blocking — it does not prevent Lane output. Every output must include the `prediction_write_status` field so the GPT can confirm the ledger is still unavailable and flag it in postmortem review. Silently passing this check is prohibited.

When the immutable prediction ledger is eventually built, a new patch is required to remove this degradation path and activate Step 15 fully.

### Cross-Ticket Exposure Ledger

**Current status: PARTIAL**

The following modules exist and should be queried:
- `gate_engine/portfolio/slip_exposure_ledger.py` — per-slip exposure tracking
- `gate_engine/portfolio/cross_slip_exposure.py` — cross-slip exposure logic
- `cross_ticket_governor` (imported in app.py) — cross-ticket governor

These modules are slip-scoped, not prediction-keyed. The health check must report:

```text
cross_ticket_exposure_status = PARTIAL
cross_ticket_exposure_detail = "slip_exposure_ledger + cross_slip_exposure available;
                                prediction-keyed cross-ticket ledger not yet built"
```

The cross-ticket exposure step in final refresh (Step 14) should query the available slip-scoped modules. Results from these modules are advisory; they do not block Lane output. Missing prediction-keyed data must be flagged in the output, not silently ignored.

---

## wow-high-hit-engine

`wow-high-hit-engine` is **absent from the active skill stack** as of 2026-08-05 (not in `skill-registry.json` or any flat skill file). No coexistence decision, replacement decision, or deprecation is required at this time. If wow-high-hit-engine is introduced in a future patch, the relationship to this skill's Lane A and Lane B must be resolved in that patch — do not assume this skill supersedes or is superseded by a skill that does not yet exist.

---

## Daily Output

### Lane A — Highest Hit Probability
```text
rank / candidate / raw_probability / calibrated_probability / lower_bound
model_status / specialist_gate_label
```

### Lane B — Highest True Probability
```text
rank / candidate / calibrated_probability / calibrated_lower_bound / calibrated_upper_bound
model_status / calibration_status
```

### Lane C — Best Verified Edge
```text
rank / candidate / no_vig_market_probability / calibrated_lower_bound
lower_bound_edge / conservative_EV / market_freshness
```

### Compact Card (Best Multi-Leg Structure)
```text
legs (from any lane, post-dependence-audit and weakest-leg-elimination)
joint_probability
joint_lower_bound
dependence_adjustment
weakest_leg_identified
```

### Lane D — Rejected Popular Candidates
```text
candidate / reject_source / reject_reason / terminal_label (from gate, verbatim)
```

### Ledger Status Block (mandatory in every output)
```text
prediction_write_attempted
prediction_write_status         (NOT_AVAILABLE / OK)
cross_ticket_exposure_status    (PARTIAL / OK)
final_refresh_status            (COMPLETED / FAILED)
```

### Live Recheck Deadline

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

Do not rewrite the original prediction. A winning card does not automatically validate the model.

Accumulated results measure:
```text
Brier score / log loss / expected calibration error
calibration bias / lower-bound reliability / CLV / process compliance
```

---

## Lessons Encoded from the Successful Four-Leg Card

The reviewed card combined two tennis match winners, one MLB moneyline, and one WNBA moneyline. The structure was favorable because sports were diversified, events were independent, no player or injury thesis was repeated, there was no same-game stack, and each leg had a simple settlement path.

**One favorable prior structure is a sample size of one.** It informs the cross-leg dependence heuristics above; it does not certify them.

---

## Terminal Labels

This skill reproduces terminal labels verbatim from specialist gates and the backend. It does not author them. The full label set the backend may return:

```text
FINAL_APPROVED / MONEY_QUALIFIED / MARKET_VERIFIED_HOLD / MODEL_QUALIFIED_HOLD
RESEARCH_INTEREST / SOURCE_CONFLICT / REJECT_NO_EDGE / REJECT_BAD_STRUCTURE
REJECT_DATA_QUALITY / SLATE_PURGE / DUPLICATE_EXPOSURE_BLOCK / NO_PLAY
HARD_REJECT_COMBO_MULTIPLICATION / COMBO_EV_UNOBTAINABLE
REJECT_DUPLICATE_PLAYER_EXPOSURE / REJECT_DUPLICATE_THESIS / REJECT_CROSS_SLIP_CONCENTRATION
```

Never invent or upgrade a backend label. Never apply a label from one gate to a candidate processed by a different gate.
