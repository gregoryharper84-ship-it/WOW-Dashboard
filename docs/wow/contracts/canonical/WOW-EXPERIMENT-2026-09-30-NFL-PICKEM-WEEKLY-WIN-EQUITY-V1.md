# WOW EXPERIMENT — NFL Pick'em Weekly Win Equity V1

Date: 2026-09-30
Issue: #1116
Change class: B — downstream contest-decision optimization; no sporting probability change
Status: CHALLENGER_IMPLEMENTED_PENDING_GOVERNED_REVIEW_AND_RUNTIME_INTEGRATION

## Purpose

The user's Pick'em pool awards the weekly prize to the entry with the most correct NFL selections. `MAX_EXPECTED_CORRECT` remains the governed baseline, but it does not directly optimize the weekly contest objective when opponent pick behavior is highly concentrated.

This experiment adds a downstream `MAX_WEEKLY_WIN_EQUITY` optimizer. It consumes only an already-governed NFL Pick'em board plus a separate, current, provenance-bearing opponent pick-share snapshot.

## Probability governance

The controlling NFL fitted specialist remains the sole sporting probability authority.

Opponent pick shares are contest-behavior inputs only. They may affect the downstream card choice, but they may not alter:

- home/away sporting probabilities;
- calibration or calibrated bounds;
- fitted-model ownership or model version;
- V17 typed failures or source terminals;
- sportsbook/market probability semantics.

`can_execute=false` remains invariant.

## Inputs

The challenger requires:

- a complete `MAX_EXPECTED_CORRECT` Pick'em board;
- total pool entries including the user's entry;
- one two-sided opponent pick-share record for every governed event;
- opponent pick-share source, snapshot id, timezone-aware observed timestamp, and `freshness_status=CURRENT`;
- bounded search radius around the baseline card.

Missing, stale, malformed, non-normalized, duplicate, or unproven opponent pick-share data fails closed. The optimizer never invents pool behavior.

## Decision method

For up to 16 games, the challenger enumerates the full governed marginal outcome space and derives each outcome probability from the controlling per-game NFL probabilities under an explicitly disclosed cross-game independence approximation.

For each outcome, opponent score distributions are computed from the supplied event pick shares. First-place equity uses an IID-opponent / equal-tie-share proxy:

- sole-first probability is tracked separately;
- tied-first states receive `1 / tied_entries` expected prize share;
- Monday-night opponent tiebreaker guesses are not invented and therefore are not modeled in this V1 challenger.

The card search is deterministic and bounded by Hamming distance from the `MAX_EXPECTED_CORRECT` baseline. Default radius is three flips; maximum allowed radius is six. The output explicitly reports whether the global optimum was proven. For a normal 16-game board, V1 does not claim a global optimum outside the searched radius.

## Required output disclosures

Every optimized row preserves both governed team probabilities and reports:

- baseline pick;
- weekly-equity pick;
- whether the selection changed;
- selected governed probability;
- opponent pick share plus provenance/freshness;
- `sporting_probability_modified=false`;
- `pool_popularity_used_as_sporting_probability=false`;
- `can_execute=false`.

Board output reports baseline vs optimized expected correct, first-place equity, sole-first probability, equity gains, number of changed selections, search radius, candidate count, method assumptions, and the unresolved opponent-tiebreaker limitation.

## Deterministic acceptance

The test suite covers:

1. a small pool retaining the higher-probability favorite;
2. a large pool selecting an unpopular underdog when the modeled weekly-equity improvement is sufficient;
3. missing opponent pick shares failing closed;
4. required provenance and freshness;
5. popularity never mutating the governed probability package;
6. extreme pick-share values preserving probability coherence;
7. a full 16-game bounded acceptance board;
8. deterministic tie-share behavior and explicit Monday-night opponent-tiebreaker limitation;
9. `can_execute=false` throughout.

## Promotion boundary

This challenger is not production-ready merely because its deterministic tests pass.

Production promotion requires:

1. governed review of the downstream assumptions and search method;
2. runtime input contract for pool size and current opponent pick shares;
3. durable/resumable Pick'em transport from #1111 so the 16-game board is not run as one fragile synchronous Action call;
4. a fresh live Custom GPT Action acceptance proving the full weekly path end to end;
5. Week 4 acceptance comparing the `MAX_EXPECTED_CORRECT` and `MAX_WEEKLY_WIN_EQUITY` cards with all 16 source games reconciled;
6. no change to the controlling NFL probability specialist or `can_execute=false`.
