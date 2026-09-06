# WOW V17 Pick Core Skill

skill_id=WOW_V17_PICK_CORE
owner=V17_SHARED_CORE
can_execute=false

## Trigger
Use for requests such as `build my core`, `best 3-5 picks`, `build slips`, `pair these`, or after Daily Picks/Screenshot Review has produced governed candidates.

## Objective
Turn an already-governed candidate pool into the strongest practical 3–5 pick core without changing any leg's sporting probability and without forcing filler.

## Preconditions
A candidate must already have a governed sporting result. Portfolio construction cannot rescue or invent a probability for an unsupported row.

## Workflow
1. Start from official governed candidates only.
2. Preserve each leg's model probability, calibrated probability, calibrated lower bound, terminal status, and controlling specialist exactly.
3. Prefer stronger calibrated lower bounds and cleaner evidence, while respecting route-specific ceilings/holds.
4. Apply V17 portfolio governance:
   - `DEPENDENCY_CORRELATION_STRUCTURE`
   - `SESSION_DIRECTIONAL_DUPLICATE_THESIS_EXPOSURE`
   - duplicate-thesis exposure
   - weakest-leg elimination
5. Treat same-event dependence structurally when no certified joint-probability model exists. Record + hold the affected portfolio qualification rather than fabricating a correlation number.
6. Avoid duplicated marginal theses across multiple proposed slips/cards. Replace a common weak hinge with a superior independent candidate when available; otherwise shrink the card.
7. Do not penalize an individual sporting probability because of portfolio duplication/dependency.
8. Return 3–5 plays only when 3–5 genuinely qualify. Fewer is correct when the board is thin.

## Game Winner cash/profitability promotion — binding P0 rule
Probability qualification and cash-single qualification are separate contracts.

When a user asks for a `cash single`, `profitability plan`, `paid Game Winner`, `best Game Winner to enter`, or otherwise intends a governed team/event winner to enter a monetary workflow, do **not** promote directly from the ML probability leaderboard or `admitted_to_probability_card` state.

Required call order for a PrizePicks one-pick Game Winner:

```text
LLP governed sporting probability
→ exact payout + exact two-way/no-vig market audit
→ v17.game_winner_cash_single_gate.evaluate_game_winner_cash_single
→ structure/exposure governance
→ final refresh
→ immutable pregame write
→ V17_TERMINAL_REDUCER
```

Binding invariants:

```text
probability_rank_eligible != cash_single_eligible
HIGH_PROBABILITY != CASH_PROMOTION
cash_single_eligible must be true for profitability-lane inclusion
```

A Game Winner rejected by the cash gate may remain ranked in `Best ML Winners`. Never lower or rewrite its sporting probability solely because payout/value failed.

The cash gate must prove at minimum:
- calibrated probability package and lower bound;
- calibration health pass;
- market prior weight <= 50% when supplied;
- exact current PrizePicks gross multiplier and timestamp;
- exact fresh two-way reference market/no-vig probability;
- positive calibrated-lower-bound edge after the active economic safety buffer;
- resolved material model/market disagreement;
- final refresh pass;
- immutable pregame prediction write.

If any cash gate fails, preserve the probability result and exclude the row from cash/profitability output. Never replace it with an unqualified filler simply to maintain a requested number of singles.

## Output
### V17 Core
For each leg show:
- rank/core order
- selection
- source: autonomous discovery or screenshot
- calibrated probability
- calibrated lower bound
- terminal/model status
- core designation: `CORE`, `OPTIONAL`, or `HOLD`
- portfolio note: independent / same-event dependency / directional exposure / duplicate thesis / weakest-leg concern

For Game Winner rows used in a cash/profitability context, additionally show:
- exact PrizePicks gross multiplier;
- platform break-even probability;
- market no-vig probability;
- calibrated lower-bound platform edge;
- lower-bound edge after buffer;
- cash-single gate status;
- `cash_single_eligible`.

### Construction note
Briefly state why the final core is structurally better than the next-best alternatives. If a strong individual pick is excluded only because of portfolio dependency/exposure, say so explicitly rather than downgrading its model probability.

## Safety
`can_execute=false` always. This skill recommends structure only; it never places, approves, routes, modifies, or cancels wagers.