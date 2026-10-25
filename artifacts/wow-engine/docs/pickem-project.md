# WOW Pick'em Intelligence — Dedicated Project

**Authoritative project hub:** [GitHub issue #2076](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2076)  
**Product lane:** NFL Pick'em downstream of the existing WOW V17 fitted NFL game-win specialist  
**Scope:** User-visible weekly pick sheets, prelock pool intelligence, weekly-equity research, tiebreaker support, full-week scoring and continuous learning  
**Serving baseline:** `MAX_EXPECTED_CORRECT`; never silently substitute a research strategy  
**Execution:** `can_execute=false`; dry run only, no wagers or orders

## The outcome we want

For every NFL week, generate a complete usable Pick'em card, ready to print and sign **GH** where requested. The form must reflect the correct games and matchups and select a tiebreaker number *not already used* in the connected live weekly Drive sheet. The user receives a transparent distinction between fitted V17 model-based picks and provisional selections where the model has not produced a valid package.

In addition, test whether finite-pool positioning improves **first-place or tie-adjusted first-place** outcomes compared to the established `MAX_EXPECTED_CORRECT` baseline. The objective is measured weekly contest advantage, not maximization of contrarian selections, not an arbitrary weekly percentage target, and never a guaranteed result.

## Operating lanes

| Lane | Input | Deliverable | Fail-closed rule |
|---|---|---|---|
| Weekly manifest | Official NFL game IDs, scheduled kickoff/wave, week, byes/postponements | Exact eligible slate | Missing / contradictory event identity blocks certified full-card claim |
| Sporting probabilities | Current immutable fitted NFL specialist packages, timestamps, calibrated side probabilities/bounds and terminal | Probabilities and preferred sides, **only where governed model completed** | Market odds, pick shares, generic projections and narratives are not replacements |
| Pool evidence | User-authorized weekly Google Sheet, prelock visibility receipt, declared pool size and rules | Frozen, privacy-preserving vote and identity evidence | Distinguish all-visible sample from actual opponents; hidden/postlock data cannot drive prelock card |
| Selection | Baseline + optional independent research challenger | Explain unchanged/changed sides and expected-correct sacrifice | Challenger research may not silently override production baseline |
| Tiebreaker | Live weekly Drive entries and certified NFL game-total specialist where available | Unique number plus independently verified total-points source | Never duplicate used tiebreaker numbers or fabricate a model |
| User artifact | Original weekly sheet or verified template, desired signature | Marked 14–16 game printable PDF/image; GH signature when requested | Verify exact event/side mapping and saved output; never modify user's source sheet without permission |
| Settlement & learning | Original pregame card, independently final-settled official games, original fitted receipts | Whole-week correctness, Brier/log loss, pool standing and strategy audit | Keep model accuracy, user's submitted card and pool construction separate |
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

## Definition of done

**Stage A — usable weekly card:** exact official slate and weekly Drive reconciliation; genuine model packages wherever available, transparent gaps; nonduplicated tiebreaker; verified signed printable artifact; no edits to the source sheet.

**Stage B — trustworthy pool evidence:** protected prelock snapshot, event identity mapping, source/provenance timestamps and exact audience; full pool vs visible sample distinguished; frozen baseline and untouched holdout weeks.

**Stage C — empirically better strategy:** repeatable baseline/challenger comparison across multiple independent weeks, uncertainty assessment, Monday tiebreaker and tied games modeled or limitations explicitly held; independent QA validates uplift.

**Stage D — production release:** authorized independent SIRT/QA/Release approval, branch merge, Render deployed exact SHA, real action input-to-result receipt and successful user card verification. No execution authority.

## Weekly cycle

1. Before lock, verify NFL schedule and connected sheet, confirm participant count and all used tiebreakers, archive source-visible evidence with provider time.
2. Score each game using valid current V17 fitted packages. Research pool ownership separately, without changing sporting probabilities.
3. Generate GH-marked printable card and a pick ledger. Disclose any unsupported/provisional side.
4. After official settlement, grade the immutable pregame card; report model calibration, GH score and league result separately.
5. Deliver actionable regression or evidence tasks to existing Engineering/AMRT and SIRT; accept only verified fixes/reviews/deployments.

## Project operating principles

Debug. Dissolve. Deploy. Prefer small code changes and existing workflows, not a parallel model engine or new release bureaucracy. User does not want repeated checkpoints. For automation, reuse the already scheduled NFL Pick'em Engineering Build task rather than creating duplicate agents. Report meaningful verified milestones and indispensable decisions only.

**Privacy:** Player/contest participant names, signed pool sheets, detailed visible entries, and private Drive document addresses are user data. Keep those inside authorized private systems; public GitHub issues must contain only anonymized aggregate facts and grounded receipt references. Never copy private sheet entries into repository source code.

**Governance:** The fitted NFL specialist alone owns sporting probabilities. Ownership shares are contest-strategy evidence only. `V17_TERMINAL_REDUCER` remains terminal authority. `can_execute=false` always.
