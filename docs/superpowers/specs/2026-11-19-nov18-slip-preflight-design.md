# WOW V17 — November 18 multi-market slip retrospective → preflight patch

Status: implemented in isolated PR; research-only, no certified sporting lift.

## Root cause and intended boundary

The chat-generated November 18 slips repeated UConn +3.5 in two combinations, included two totals whose eventual actual scores landed far from their proposed lines, and included a fragile 5-leg combination. These observations are **not** evidence that WOW's fitted specialist made these picks or had a bad calibration; no pregame fitted receipts, executable quote snapshots, authenticated joint scenario distribution, or actual user orders were established. Correct-grade counts, even when accurate, are not a measured causal model improvement.

The existing \`v17/slip_portfolio_optimizer.py\` already enforces structural repeated exact/family theses and Power hinge controls, and \`v17/experiments/nov16_ml_slip_learning\` handles isolated offline leg/joint grading. The missing boundary for ordinary **cross-sport provisional research cards** was a machine-checkable, fail-closed **pregame research evidence** intake. Reuse those components, add one pure preflight and explicitly opt it into the existing optimizer. Do not create a second authoritative scorer, new scheduler or trading endpoint.

## Research preflight contract

- Inputs: an immutable candidate proposal copied by the caller, single explicitly supplied \`as_of_utc\`, complete card list, canonical sport/event/period/market/side/line/settlement, pregame source snapshot ID/time, bookmaker quote ID/time/exact same-market line and price.
- Exact event and player/team verification: for props require source-attributed player ID, roster team ID and that team is one of the event participants. Claims are not externally authenticated by this pure function.
- Injury/news: require sport/event-specific independent-review provenance and pregame time; if a material change exists but no later model receipt is supplied, hold \`MATERIAL_NEWS_RESCORE_NOT_VERIFIED\`. A supplied receipt ID is never an actual model-rescore verification.
- Totals: require possessions, each team's opponent-adjusted points per possession, opponent efficiency source ID, lineup context, rest/travel, tail scenario source, and exact-line fitted distribution/calibrator receipt IDs. Check \`possessions × (home_ppp + away_ppp)\` direction against offered total as a deterministic **research contradiction check**, not a calibrated probability. Missing or contradictory context remains a typed hold.
- Global exposure: canonical exact thesis contains **sport** to avoid same-ID cross-sport collisions. Track distinct slip IDs; default at most one occurrence. Flag same-event exposure inside a card and 4+ legs; no synthetic joint probability and no claim of independence from different teams alone.
- Output: immutable-input diagnostic \`research_preflight\` with per-leg typed blockers, cross-slip counts, status, \`joint_probability=null\`, \`certified_sporting_probability=null\`, \`can_execute=false\`.
- Integration: \`optimize_portfolio(cards, research_preflight_enabled=True, research_as_of_utc=...)\` audits **original unshrunk cards first**, performs existing deterministic shrink/replacement, then marks all research-mode cards \`HELD_RESEARCH_ONLY\`, regardless of contextual completeness; this cannot make a card publishable.
- Existing callers are unchanged with the default \`research_preflight_enabled=False\`. The V17 Daily Picks instruction layer requests this mode when the user asks for cross-sport research slips.

## Acceptance

1. Synthetic tests: stale/foreign quotes, event chronology, exact market/line, player roster mismatch, material news not rescored, unverified totals distribution and opposing pace–PPP mean, duplicate UConn across slips, same raw ID different sport, same-event dependence, 5-leg hold, no mutation, and legacy-mode conservation.
2. Existing optimizer regressions plus exact-head required-three CI. No model/calibration/terminal code changes.
3. Separate future operational acceptance: actual readback in a consumer calling the opt-in mode with pregame source snapshots, independently official verification and genuine modeled total distributions. Do not claim the user-facing ChatGPT conversation automatically invoked a backend route.
4. Before any claim of calibration lift, compare untouched frozen pregame incumbent and challenger on independent official outcomes by sport and market with distributions, Brier/log loss and error decomposition. At least one slate cannot certify uplift. Preserve V17_TERMINAL_REDUCER and \`can_execute=false\`.

Rollback: protected revert of the isolated research preflight integration; default optimizer unaffected.
