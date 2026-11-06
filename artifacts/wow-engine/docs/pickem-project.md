# WOW Pick'em Intelligence — Dedicated Project

**Authoritative project hub:** [GitHub issue #2076](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2076)  
**Product lane:** NFL Pick'em downstream of the existing WOW V17 fitted NFL game-win specialist  
**Primary objective:** Accurate, reliable, well-calibrated NFL winner predictions and demonstrable improvement after every missed pick  
**Secondary scope:** User-visible weekly sheets, prelock pool intelligence, tiebreaker support, optional pool-equity research  
**Serving baseline:** `MAX_EXPECTED_CORRECT`; never silently substitute a research strategy  
**Execution:** `can_execute=false`; dry run only, no wagers or orders

## The outcome we want

For every NFL week, generate a complete usable Pick'em card, ready to print and sign **GH** where requested. The form must reflect the correct games and matchups and select a tiebreaker number *not already used* in the connected live weekly Drive sheet. The user receives a transparent distinction between fitted V17 model-based picks and provisional selections where the model has not produced a valid package.

**Primary success measure:** increase accurate picks on the complete NFL slate without hiding missing predictions; measure real hit rate, model coverage, expected correct, Brier score, log loss and calibration by confidence bucket. Investigate missed picks using only available pre-kickoff information: opponent quality, matchups, injuries and starting lineups, QB and offensive-line strength, game script, coaching scheme, home/road, rest/travel, weather, team trends, late news and source/model freshness. Distinguish correctable failures from ordinary variance. Every proposed improvement must beat the unchanged champion on untouched holdouts and independent review before a probability-serving change.

**Secondary research only:** assess whether pool-positioning can improve first-place or tied-first outcomes **without sacrificing validated sporting accuracy**. Weekly pool position, contrarian differentiation and tiebreaker strategy are not the lead weekly analysis and must never displace missed-pick investigations.

## Operating lanes

| Lane | Input | Deliverable | Fail-closed rule |
|---|---|---|---|
| Weekly manifest | Official NFL game IDs, scheduled kickoff/wave, week, byes/postponements | Exact eligible slate | Missing / contradictory event identity blocks certified full-card claim |
| Sporting probabilities | Current immutable fitted NFL specialist packages, timestamps, calibrated side probabilities/bounds and terminal | Probabilities and preferred sides, **only where governed model completed** | Market odds, pick shares, generic projections and narratives are not replacements |
| Pool evidence | User-authorized weekly Google Sheet, prelock visibility receipt, declared pool size and rules | Frozen, privacy-preserving vote and identity evidence | Distinguish all-visible sample from actual opponents; hidden/postlock data cannot drive prelock card |
| Selection | Baseline + optional independent research challenger | Explain unchanged/changed sides and expected-correct sacrifice | Challenger research may not silently override production baseline |
| Tiebreaker | Live weekly Drive entries and certified NFL game-total specialist where available | Unique number plus independently verified total-points source | Never duplicate used tiebreaker numbers or fabricate a model |
| User artifact | Original weekly sheet or verified template, desired signature | Marked 14–16 game printable PDF/image; GH signature when requested | Verify exact event/side mapping and saved output; never modify user's source sheet without permission |
| Settlement & learning | Original pregame card, independently final-settled official games, original fitted receipts | **Lead with complete-week hit rate, coverage, expected correct, Brier/log loss and game-by-game miss diagnoses; pool standings last** | Keep model accuracy, user's prepared/submitted card and pool construction separate |
| Reliability & release | Exact SHA, CI, SIRT/Independent QA and Release evidence | Approved research/release only when genuine gates pass | Green CI alone does not establish strategy validation or production deployment |

## Current linked work

- [PR #1680](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1680): full-pool research, prelock replay, visible-snapshot validator, ties; requires latest-head confirmation
- [Issue #1708](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1708): research-only scope and acceptance
- [Issue #1703](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1703): real opponent/pool pre-lock evidence and untouched holdout
- [Issue #1530](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1530): official weekly scorecards and delivery
- [Issue #1521](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1521): independent accuracy and reliability assurance
- [Issue #1852](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1852): Week 6 postmortem
- [Issue #1090](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1090): fitted total-points tiebreaker specialist history (closed); verify live capability each use
- [Issue #1116](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1116): proposed `MAX_WEEKLY_WIN_EQUITY` (closed/not planned); not proof of serving deployment

## Accuracy-first measurement and learning

1. Record **all scheduled games** and clearly separate games with certified frozen pregame probabilities, provisional sides, missing packages, unsettled results and officially settled outcomes; never inflate hit rate with coverage omissions.
2. Grade frozen model winner selections and the user-prepared/submitted GH card **independently**, using the same independently verified final outcomes. Keep submission evidence separate from an assistant-created printable form.
3. Report exact correctness numerator/denominator, completion coverage, expected correct, binary Brier and log loss, calibration/reliability bins with cohort sizes, and trends across multiple completed weeks. Quantify uncertainty; do not claim that a single upset proves the model is broken.
4. Diagnose **every miss** using immutable pregame model probability, calibration bounds, opponent/team attributes, injuries, lineups, coaching, home/road, schedule, weather and information freshness. Only attribute causes to evidence genuinely timestamped before that kickoff. Label postgame-only explanations as hypotheses.
5. Distinguish **avoidable model/source/identity/calibration defects** from unpredictable game variance. Open only evidence-backed smallest-fix experiments; require historical replay, adversarial counterexamples, holdout and independent promotion for Class C model changes.
6. Present pool rank, contrarian opportunity and tiebreaker analysis **after** accuracy work, unless explicitly requested.

## Definition of done

**Stage A — usable weekly card:** exact official slate and weekly Drive reconciliation; genuine model packages wherever available, transparent gaps; nonduplicated tiebreaker; verified signed printable artifact; no edits to the source sheet.

**Stage B — trustworthy pool evidence:** protected prelock snapshot, event identity mapping, source/provenance timestamps and exact audience; full pool vs visible sample distinguished; frozen baseline and untouched holdout weeks.

**Stage C — better predictions first:** demonstrate measured improvement in full-slate accuracy, calibration and leakage-safe holdout results; then, optionally, validate pool-equity challenger as separate research, not a substitute for model accuracy.

**Stage D — production release:** authorized independent SIRT/QA/Release approval, branch merge, Render deployed exact SHA, real action input-to-result receipt and successful user card verification. No execution authority.

## Weekly cycle

1. Before lock, verify NFL schedule and connected sheet, confirm participant count and all used tiebreakers, archive source-visible evidence with provider time.
2. Score each game using valid current V17 fitted packages. Research pool ownership separately, without changing sporting probabilities.
3. Generate GH-marked printable card and a pick ledger. Disclose any unsupported/provisional side.
4. After official settlement, lead with frozen model accuracy and completeness, each game's miss, model calibration (Brier/log loss), potential failure mechanisms and safe evidence-backed fixes; separately report GH score, then pool standing as optional context.
5. Accumulate a multiweek mistake taxonomy, test the smallest supported improvement on untouched holdouts, and deliver actionable regression/evidence tasks to existing Engineering/AMRT and SIRT; accept only verified fixes/reviews/deployments.

## Project operating principles

Debug. Dissolve. Deploy. Prefer small code changes and existing workflows, not a parallel model engine or new release bureaucracy. User does not want repeated checkpoints. For automation, reuse the already scheduled NFL Pick'em Engineering Build task rather than creating duplicate agents. Report meaningful verified milestones and indispensable decisions only.

**Privacy:** Player/contest participant names, signed pool sheets, detailed visible entries, and private Drive document addresses are user data. Keep those inside authorized private systems; public GitHub issues must contain only anonymized aggregate facts and grounded receipt references. Never copy private sheet entries into repository source code.

**Governance:** The fitted NFL specialist alone owns sporting probabilities. Ownership shares are contest-strategy evidence only. `V17_TERMINAL_REDUCER` remains terminal authority. `can_execute=false` always.
