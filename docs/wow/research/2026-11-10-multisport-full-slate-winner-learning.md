# 2026-11-10 — NFL/NBA/NHL full-slate winner and retro learning

Status: SOURCE ONLY / NOT MERGED OR DEPLOYED
Hub: #2076 | Existing retro PR: #2547 | Verification: #1521 | Grading: #1530/#1852
Controlling owner: LLP_TEAM_BETTING_ENGINE | Terminal authority: V17_TERMINAL_REDUCER | can_execute=false

## Clarified user expectation

When the user asks for all straight-up NFL/NBA/NHL winners, deliver exactly one researched winner for every playable scheduled event. Uncertainty triggers deeper current research, not a discretionary PASS. Keep certified fitted model HOLD/NO_PICK separate: a mandatory user-facing research winner never becomes a certified probability, fitted prediction, or governed terminal decision.

## Learning candidates from November 9 retrospective

The conversation reported an eight-of-twelve result and discussed player availability, replacement usage, shooting variance, market favorite identification, NHL goalie and OT/shootout effects, and overly strong qualitative confidence. This conversational recap has not yet been independently reconciled to authenticated pregame freeze receipts, actual historic rosters/prices, a complete official schedule, and official settlement receipts. It is a diagnostic hypothesis set, not fitted-model performance evidence. Do not backfill it into forward certification or assert the retrospective's causal stories as historical facts.

## Implementation

- Read-only helper: artifacts/wow-engine/v17/llp_research_winner_card.py
- Focused tests: artifacts/wow-engine/test_llp_research_winner_card.py
- Updated skill instructions: ML Winners, Research Market Context, Daily Picks skill pack
- Verified official slate receipt + one selected side per playable event are required to claim board_complete; incomplete, duplicated, late or extra picks do not silently disappear.
- A separately reported research_evidence_fully_reconciled flag requires complete selections AND no evidence warnings. Insufficient independent sources, source timestamp conflicts, contradictory injuries and market disagreement do not erase an otherwise valid research winner.
- Critical checks: NFL starting QB, injuries and turnover risk; NBA star availability, replacement rotation, rest and shooting variance; NHL confirmed goalie, injuries, travel/rest, special teams, OT/SO.
- Require as-of dates and independent nonmarket sources where available. Flag unsupported HIGH confidence; do not invent a numerical research probability.
- Keep research preferred picks, fitted-model winners, upset-watch alternatives, GH submissions and official result postmortems as separate provenances and denominators.
- Postmortem: grade immutable pregame picks only with independent official finals; analyze the explanation separately from winner correctness and compare to a timestamp-matched market-favorite baseline.
- Future model changes require pre-registered chronological challenger and untouched holdout, calibration validation, and independent QA/SIRT/Release acceptance. No automatic model promotion, shared Google Sheet changes, wagers, or submission.

## Verification and release status

Seven local focused synthetic tests passed, including three-sport coverage under model HOLD, contrary evidence without pass conversion, bad/source-late and market discrepancies, duplicate/late/missing picks, unverified slate, and no numeric model spoofing. These synthetic tests do NOT validate an actual sport selection or prove live operational integration.

Remaining gates: hosted exact-head CI on current branch, independent semantic/QA/SIRT review, protected authorized merge, deployed exact SHA readback, Custom GPT/editor sync and true full-slate/frozen-real-result evidence. This patch is not operationally released.
