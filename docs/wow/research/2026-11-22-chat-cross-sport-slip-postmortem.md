# WOW V17 — November 22 cross-sport chat slips: settled retrospective

**Class:** OBSERVATIONAL CHAT RETROSPECTIVE / NOT CERTIFIED WOW FORECASTS / NO USER SUBMISSIONS VERIFIED.

**Scope:** Three hypothetical slips A, B, C proposed in ChatGPT on 2026-11-22. This post-event artifact is not an immutable pregame model receipt, actual PrizePicks/Kalshi ticket, bankroll record, production scoring invocation, or experiment proving improvement.

## Immutable-code fixture and independent settlement

Fixture: [2026-11-22-chat-cross-sport-slip-retro.json](./2026-11-22-chat-cross-sport-slip-retro.json). Each distinct pick is recorded once with the exact reported line, independent result source URL, observed value or official winner, and the three originally proposed slip compositions.

| ID | Exact proposed leg | Official/stat outcome | Grade | Included in |
| --- | --- | --- | --- | --- |
| tate | Carnell Tate UNDER 23.5 longest reception | 59-yard reception | LOSS | A, B, C |
| wagner | Franz Wagner OVER 6.5 assists | 6 assists | LOSS | A, B |
| kucherov | Nikita Kucherov OVER 2.5 shots | 4 shots | WIN | A, B |
| cook | James Cook OVER 92.5 rushing yards | 89 yards | LOSS | B |
| dallas | Dallas Stars full-game ML | Won 1–0 vs Pittsburgh | WIN | C |
| denver | Denver Nuggets full-game ML | Won 151–145 in overtime vs Chicago | WIN | C |
| nashville | Nashville SC 90-minute ML | Lost 0–1 vs Cincinnati | LOSS | C |
| samford | Samford full-game ML | Won 80–77 vs UNC Asheville | WIN | C |

**Unique selections:** 4 wins, 4 losses (8). **Slip-leg occurrences:** 5 wins, 7 losses (12). **All-legs-cleared combinations:** 0/3 (A: 1/3, B: 1/4, C: 3/5). An additional discussed Tampa Bay Lightning ML candidate lost but **was not in any of these slips** and is excluded from the unique eight.

**Shared exact selection exposure:** Tate appears in 3 slips, Wagner in 2, Kucherov in 2. Three reused-selection groups account for four extra position occurrences (12 occurrences minus 8 distinct selections). Tate's 59-yard score broke every proposed slip simultaneously. This is descriptive hindsight, not a calibrated pregame tail probability.

**Price/economics:** No verified entry, accepted quote, stake, payout schedule, or account order. Do not compute or claim realized ROI, profit, break-even probability, or won/lost *submitted* tickets. The unit-test input sets entry_cost=0 and gross_return=0 only to exercise existing postmortem structure and intentionally expects `roi=None`; any generic `non_losing_positions` count from zero-dollar hypotheses is **not** a payout result.

## Root cause / limits

1. **Single-play tail:** Longest-reception Under was repeatedly presented using small-sample historical hit rate, even though Dallas's explosive-pass vulnerability and Tate's evolving health/role had been flagged. The full receiver-target-depth/YAC/tackle-down distribution and an immutable exact-line calibrated prop receipt were absent; no replacement p-value may be imputed.
2. **Cross-slip concentration:** A repeated hinge invalidated all three possible winning combinations. The existing research preflight (merged #2733) already blocks duplicate exact-thesis exposure; this regression tests the exact Tate scenario.
3. **Role and line:** Wagner fell 0.5 assist below the required threshold, Cook 3.5 rush yards below. Real fitted opportunity/role distributions and time-correct injury receipts must precede strong-confidence labels.
4. **Favorite ≠ value:** Nashville ML lost. Denver required overtime; Dallas won by one. A favorite price does not substantiate a profitable exact offer or joint win probability.
5. **Attribution:** The connected validation ledger produced held rows and zero certified recommendations at the earlier read. No authenticated pregame specialist receipt linked these chat picks. These results **must be excluded from calibrated model grading**, and no model receipt may be backfilled after outcomes.

## Existing owner path; no duplicate engine

- **#2076** remains Project Hub; **LLP_TEAM_BETTING_ENGINE** owns winner/ML context; WOW fitted prop lane owns exact player distribution.
- **#2733 (merged)**: cross-sport preflight; do not bypass `research_preflight_enabled` or reinterpret a research card as a published pick.
- **#2807 (merged)**: prohibit deriving even provisional picks from ungoverned research/ledger reads; editor sync requires separate verification.
- **#2814 (open)**: material-context invalidation/read-only receipt requirement across sports and roles.
- **#2741 (open)**: PrizePicks exact player/line/availability/opportunity/payout preflight. Deliver any change for longest-reception tails and uncertain player usage through the controlling specialist/challenger workflow, **not** this postmortem patch.
- **#2829 (open)**: read-only distribution diagnostics and forward-source reconciliation; no claim of calibrated benefit from this single day.

## Additive implementation in this branch

- Extend the existing `postmortem_engine.build_postmortem_payload` **summary only** with `distinct_selections`, `reused_selection_groups`, `reused_selection_extra_positions`, `max_selection_position_exposure`, `all_leg_clear_positions`, and an explicit diagnostic identity basis.
- These group only exact observed field matches; missing official event ID never silently becomes a certified identity. Counts are *descriptive*, not a sporting/joint probability.
- New regression checks 8 distinct selections, 12 card-leg occurrences, three shared Tate exposures, all three hypothetical combos missing at least one leg, zero backfilled/calibration-eligible probabilities, no modeled ROI, and research preflight HOLD despite synthetic source claims.
- **No database writes** are performed by this fixture/test. No public API mutation, fitted-model parameter changes, auto-learning, calibrator, terminal reducer, execution permission, or new scheduler.
- Preserve `V17_TERMINAL_REDUCER` as sole terminal authority and `can_execute=false`.

## Acceptance and future work

Run `python -m pytest -q artifacts/wow-engine/v17/test_nov22_chat_slip_postmortem.py artifacts/wow-engine/v17/test_postmortem_engine.py artifacts/wow-engine/v17/test_slip_research_preflight.py` from the `artifacts/wow-engine` import context used in CI; exact-head independent QA/Release required before merge.

This patch fixes **retrospective visibility and a regression blind spot**, not the unknown true prop probability. A longest-reception model upgrade requires independently gathered pregame routes/depth/YAC/defender-contact and verified market chronology, a fitted/distributed candidate, chronological holdout/calibration acceptance, and immutable pregame receipts. A correlated slip optimizer similarly requires independent joint scenarios and exact accepted payout terms; do not multiply marginal percentages to assert success odds.

**No retrospective model promotion. No synthetic evidence used as real validation. No wagers or submission.**
