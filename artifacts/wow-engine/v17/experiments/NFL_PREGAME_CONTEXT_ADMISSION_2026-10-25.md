# NFL fitted winner accuracy — contextual feature admission pilot

Date: 2026-10-25  
Class: C research infrastructure, no fitted scoring / production probability change  
Owner: existing issue [#1342](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1342), evidence programme [#1939](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1939), Pick'em hub [#2076](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2076)

## Why

Active `NFL_OUTRIGHT_WIN_LOGREG_V1` / `NFL_EVENT_PREGAME_PRIOR_V1` uses 31 fields: historical overall offensive/defensive EPA, success, turnovers, sacks allowed, special teams, rest, win rate, point margin, prior game counters, week. It does **not** numerically consume starting-QB status, player injuries, OL health, or offense-vs-opposing-defense run/pass splits. A presentational/scouting claim about those factors cannot be relabeled as a modeled probability.

Existing #1342 experiment (merged PR #1346) diagnosed cumulative-history z-score drift; the 224-day stationary-history shadow variant won retrospective research Brier/log loss but did not qualify for production promotion. Earlier QB-history continuity and weather ablations **degraded** rolling proper scores. Injury/starter data were refused because row-level publication timestamps were absent in 2025–26 and some historic bulk snapshots postdated game kickoffs. Preserve those negative findings; don't bolt an arbitrary injury penalty onto the champion.

2026 Week 7 (first nine settled events) champion: correct 5/9, expected 5.347, Brier 0.2797, log loss 0.7682. SF @ ATL was a high-confidence 80.5% selected-side miss, **not by itself** proof that injury adjustment helps. Frozen 2025 (n=284) model Brier 0.23116 and log loss 0.65293.

## Delivered research capability

`v17/nfl_pregame_context_admission.py` builds a strict, event-specific research packet:
- QB confirmed starter and whether a starter changed from preceding game; player ID required as a source check but **not emitted** in features.
- Expected starting OL count (0–5), skill starters (0–5), and defensive starters (0–11) on each team; emitted count edges are only candidate inputs.
- Rolling rushing/passing offensive EPA per play and opponent rushing/passing **defensive EPA allowed per play**, each with a prior-game input cutoff, minimum sample and source timestamp; emits four unweighted team-specific matchup edges.
- Requires canonical event/team/side, source/publisher receipt, payload SHA256, declared data rights and SPORTING domain; strict observed ≤ published ≤ captured ≤ decision cutoff < game kickoff. Rejects future game statistics, duplicates, missing required fields and unlicensed/market evidence. Emitted digest is internal integrity only, not an independently authenticated source signature.
- Six required (home/away × three families) inputs; missing family -> `PREGAME_CONTEXT_EVIDENCE_INCOMPLETE` and **no partial numeric feature vector**. Verified model status stays false even for a complete research packet.
- Always `can_execute=false`, `promotion_authorized=false`, `probability_publishable=false`, no call into V17 production scorer or terminal authority.

## Next evidence before a fitted challenger

1. **Sources:** verify approved rights, publisher identity and independent immutable content+capture receipts for historical starter/inactive lineup availability and rushing/passing splits. The existing NFL data source pilot #1933 still records licensing review needed for nflverse; do not auto-entitle it.
2. **Replay:** obtain genuinely point-in-time starter/OL/injury information from 2021–2025 **as actually visible before each game**, not revised postgame retrospect. Mark historical gaps missing. Compare source coverage by year, team, week and injury severity.
3. **Training:** match challenger and champion on identical eligible games; separate 2021–23 training, 2024 calibration, untouched 2025 validation, and 2026 forward reserve. Compare original stationary-decay candidate, feature-augmented model, and controlled family ablations. Report binary Brier/log loss/ECE, actual favorite hit rate, AUC, no hidden per-game omissions; season-slice paired bootstrap and counterexamples.
4. **Confidence:** test contextual uncertainty separately from point predictions. Champion ±5.5918pp is a global holdout error margin **not** a validated game-level confidence interval. Do not advertise coverage until independently tested.
5. **Release:** only after evidence-complete replay and independent SIRT/QA + governed model promotion, not by passing schema tests. No change to serving model, terminal statuses, pool entries, wagers or releases from this PR.

**Current assessment:** point-in-time schema and tests ready for CI; real multi-year feature eligibility and proof of prediction accuracy gain not established. Never relabel this packet as `Full Model`.
