# Opponent Scenario Shadow Bridge Implementation Plan

> For agentic workers: use Superpowers executing-plans and test-driven-development.

**Goal:** Research-only pregame opponent and player-usage scenario bridge.
**Architecture:** Add one focused module and tests under artifacts/wow-engine/v17/experiments/opponent_scenario_bridge/; fitted trainer and Nov16 evaluator remain authorities.
**Tech Stack:** Python 3, pytest; no new packages.
**Spec:** docs/superpowers/specs/2026-11-17-opponent-scenario-shadow-design.md

## Global constraints
- can_execute=false; no automatic promotion; V17_TERMINAL_REDUCER sole terminal authority.
- No postgame hindsight, market-odds probability substitution, inferred player tails.
- Scientific promotion needs authenticated fitted evidence and independent comparison.

## Review focus
Duplicated and whitespace-altered scenario IDs, invalid exact lines, stale pregame fit, two-way vs three-way settlement, and teams trailing in-game yet winning final.

### Task 1 — Outcome mixture
- [x] Write red tests for normalized outcomes, underdog paths, identity/date/malformed market basis.
- [x] Implement evaluate_scenario_outcomes and verify green.

### Task 2 — Player usage
- [x] Write tests for exact-event matching, normalized mixture, missing states, invalid alt lines and stale fits.
- [x] Implement evaluate_player_usage and verify green.

### Task 3 — Source and acceptance
- [x] Add contract and research-only docs; focused local pytest 17 passed.
- [ ] Exact-head GitHub CI, code review and protected research-only integration.
- [ ] Authentic paired incumbent/challenger holdout with independent receipts.
- [ ] Separately authorized production serving release only after scientific acceptance.
