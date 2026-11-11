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

## Addendum — media headlines, narratives and three-script sensitivity

A complete matchup research finding includes attributed current media headlines with real HTTPS links and chronologically eligible publication/source timestamps, source-linked storylines, an explicit counter-narrative, the favored winner script, the opponent upset script and a third stress scenario. The research sensitivity summary counts valid **unweighted** distinct scenarios favoring each team; this is not calibrated probability or validated scenario robustness. Headlines, analyst opinions, media buzz and market popularity do not directly change sporting probabilities or certify a winner.

Read-only module v17.llp_matchup_media_script_audit audits source metadata, source-linked narratives and time restrictions. The existing v17.llp_research_winner_card integrates it as nonterminal evidence warnings, independent of a full official-slate selection. Late/fake/unsourced news is excluded from the research summary and cannot be fed to frozen pregame model grades. Source receipt IDs and verification booleans here are **not** publisher authenticity proofs: future live intake must do independent source/ledger readback before any claim of independent verification.

Tests include scenario preference counts, split and contradicted scripts, future and uncalibrated scenario numbers, linked headlines/storylines and counter narrative, future news, invalid URLs, and missing media. Protect can_execute=false and sole V17 terminal authority. Do not alter the Google Sheet, fitted weights, calibration, line prices or automatic submission.

## Verification and release status

Historical local synthetic verification applied only to earlier source revisions, not this amended head. The expanded research winner-card suite must pass on the exact current commit, including truthful boolean slate attestation, invalid/duplicate/late pick isolation, independent sources, game-script versus opponent scenario declarations, scenario-probability alias rejection and media chronology. Even a green exact-head CI suite does NOT authenticate publisher data, certify sporting model probabilities or prove deployed integration.

Remaining gates: hosted exact-head CI on current branch, independent semantic/QA/SIRT review, protected authorized merge, deployed exact SHA readback, Custom GPT/editor sync and true full-slate/frozen-real-result evidence. This patch is not operationally released.


## Addendum — 2026-11-10 game-script learning

The prior patch checked injuries, team efficiencies and market conflicts but did not explicitly require game scripts. Every user-requested full-slate research winner now needs a pre-selection scenario record: (1) selected team's expected winning path; (2) opponent's distinct upset path; (3) concrete swing factors; (4) scenario as-of timestamp. Matchup findings should explain why the preferred side wins across the better-supported scripts while openly identifying the contrary path. The preflight flags missing, late, duplicate-path or incomplete scripts, including missing or contradictory explicit winner/opponent script sides and absence of a manual contradictions-review declaration; these are evidence-quality warnings rather than discretionary PASS commands. Invalid/late/duplicate winner selections never carry admissible news, game scripts or source counts forward as pregame evidence.

Sport-specific examples: NFL early-lead vs trailing run/pass balance, QB pressure/turnovers; NBA transition vs half-court pace, three-point swings, rotations and foul trouble; NHL 5v5 control, goalie performance, penalties/special teams, late empty-net and OT/SO variance.

Implementation: optional research game_script input audited in llp_research_winner_card.py with focused tests. It does not recalculate a selected side, assign scenario weights, supply certified model p/bounds or invoke the legacy/shadow game-state simulator. The separate certified LLP team/event route and V17_TERMINAL_REDUCER remain unmodified. A later postmortem may evaluate pre-registered script mechanisms only against timestamped independent play-by-play evidence; no hindsight reclassification. Hosted CI and independent exact-head review remain required.

## 2026-11-11 ranked cross-sport top-15 retrospective patch (research only)

The November 10 conversation reported a 13/15 ranked winner-card record with Chelsea drawing and Duke losing. This chat report is **not** an independently authenticated pre-kickoff archive, market snapshot, or official final ledger and must not become a V17 fitted certification statistic. The finding that the published picks were already market favorites is a methodological warning: raw favorite accuracy is not evidence of incremental model edge.

Additive audit: `v17.llp_ranked_winner_retro.audit_ranked_winner_retro` and synthetic tests in `artifacts/wow-engine/test_llp_ranked_winner_retro.py`. This is a **ranked subset** diagnostic distinct from the complete NFL/NBA/NHL official manifest and distinct from submitted GH Pick'em entries.

Independent inputs required: a frozen verified ranked-card cohort and unique pregame pick receipts; exact official event/teams/start, prelock user visibility and timestamp; independently verified official outcomes and score/settlement basis; and a separately verified timestamp-matched complete two-way (NFL/NBA/NHL/NCAAF/NCAAB) or three-way regulation (UWCL) market snapshot with unique receipt and full event identity. For a soccer draw, both team-winner predictions are losses in the three-way market. Missing or postfreeze odds block market-baseline comparison without rewriting valid winner grades. Market snapshot authenticity is caller-attested here, not independently fetched by the helper.

The baseline compares **number correct against the same-time market favorite**, not bets, implied model probabilities, yield or ROI. `incremental_correct_vs_market=0` is descriptive, not statistical proof of no edge; even a positive single-day difference would not certify one. Signed verified spread outcomes are *separate diagnostics*, not a grade of the original straight-up pick. Narrative explanations (such as college half-court defense or soccer rotation) are candidate hypotheses to be tested on frozen future cohorts, not proven historical causes.

Acceptance: synthetic tests for market-favorite parity, soccer draws, invalid two-way soccer odds, missing/late market, late pick, invalid official final, strict boolean cohort attestation, swapped event, postfreeze availability, and winner-versus-spread separation. No fit, probability, terminal, database schema, calendar, GH Google Sheet, money or automatic action changes. Existing #2547 independent SIRT/QA/Release exact-head gates still apply; no claim of merge, deployed route, live GPT/editor sync or authenticated 15-game audit.
