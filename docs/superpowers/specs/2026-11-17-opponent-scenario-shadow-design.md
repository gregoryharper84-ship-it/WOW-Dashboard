# Opponent Scenario Shadow Bridge — Design

## Goal
Connect the existing LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1 pregame model to the November 16 prop/slip diagnostics using read-only opponent-win scenarios. Evaluate exact-event player usage without inferring count distributions from moneyline odds. Preserve full-slate picks and flexible slip construction.

## Approaches
- Chosen: additive research bridge reusing existing team-state and Nov16 engines. No duplicate training or serving path, but requires authentic fitted/frozen scenario artifacts.
- Rejected: another full fitted specialist (duplicative).
- Rejected: heuristics deriving player counts from market ML (uncalibrated).

## Contract
Accept externally fitted and frozen scenarios with official event identity, pregame fit and snapshot cutoffs, model digest, source digest, receipt ID, settlement basis, normalized positive weights, outcome labels and team game-state labels. Return research-only outcome and underdog pathways.

For matched exact-line player props, require scenario-specific fitted count mean, variance and P(over). Compute weighted mixtures; never invent missing distributions.

Reject stale, invalid, mismatched and incomplete data. In-game leading/trailing states are not identical to full-game outcomes.

## Non-goals
No automatic training, certification, market scraping, execution or production route modification. Team-state training and Nov16 holdout remain controlling owners; can_execute=false and V17_TERMINAL_REDUCER sole authority.

## Scientific next step
Independently verify pregame model/source receipts; fit and compare against incumbent on genuine untouched chronological data. Separate research code integration from model calibration, publication and production promotion.
