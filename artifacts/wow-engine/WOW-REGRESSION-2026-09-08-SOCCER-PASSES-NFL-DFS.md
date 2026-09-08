# WOW Regression Matrix — 2026-09-08 — Soccer Passes + NFL DFS

## Governance
```text
runtime_generation=V17_ACTIVE
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Soccer Passes Attempted

1. **Completed-pass shortcut blocked**
   - Input: 80 accurate passes/90, exact PrizePicks line 75.5 Passes Attempted.
   - Expected: accurate passes/90 may not directly approve MORE; specialist simulation required.

2. **Completion reconstruction remains evidence only**
   - Input: 72 accurate passes/90, 90% completion.
   - Derived: 80 reconstructed attempts/90.
   - Expected: `source_status=RECONSTRUCTED`; no governed probability from reconstruction alone; maximum research ceiling before specialist = `RESEARCH_INTEREST`.

3. **Minutes normalization regression**
   - Input: 52 attempts/90 baseline, expected minutes centered at 70.
   - Expected: total-match projection is not 52; minutes distribution numerically reduces attempt distribution.

4. **Role/minutes differentiation**
   - Compare 90-minute center-back to 65-minute winger at the same nominal per-90 rate.
   - Expected: materially different full-match attempt distributions and lower bounds.

5. **Opponent environment is numerical**
   - Low-block possession expansion and high-press suppression must change the simulated team/player attempt distribution; prose-only adjustment fails.

6. **Game-state and red-card paths**
   - Leading/trailing and own/opponent red-card states alter team pass volume/player share as applicable.

7. **Prior-season dominance blocked**
   - Changed team/manager/formation/role cannot silently inherit full prior-season weight.

8. **Bidirectional exact-line scoring**
   - MORE and LESS at the exact settlement line are both scored; failed MORE does not auto-approve LESS.

9. **Final refresh**
   - Started match, lineup scratch, material formation/role change, or settlement mismatch removes/reruns the pregame row.

## NFL DFS Fantasy Score

10. **Exact scoring adapter mandatory**
    - Missing platform scoring profile.
    - Expected: `MODEL_INPUTS_INSUFFICIENT`; no generic scoring substitution.

11. **Cross-platform projection substitution blocked**
    - DraftKings/FanDuel/external DFS projection supplied for a different platform fantasy-score prop.
    - Expected: evidence/prior only; cannot become governed model probability.

12. **Generic PPR substitution blocked**
    - Standard/PPR/half-PPR scoring cannot substitute for the platform's exact scoring adapter.

13. **QB-pass catcher dependence**
    - QB pass yards/TDs and WR/TE receiving yards/TDs preserve shared game and scoring dependencies.

14. **Teammate opportunity dependence**
    - Multiple pass catchers on one team share target-volume constraints; independent target simulations are prohibited.

15. **RB game-script sensitivity**
    - Leading/trailing scripts numerically alter carries/routes/targets and fantasy-score distribution.

16. **Starting-QB uncertainty**
    - Unresolved starting QB when material => `MODEL_INPUTS_INSUFFICIENT` or configured readiness/confidence cap; prose warning alone is insufficient.

17. **Inactive/role redistribution**
    - Teammate inactive status alters opportunity shares before fantasy scoring.

18. **Weather materiality**
    - Weather changes distributions only when sourced and materially modeled; generic weather narratives cannot change probability.

19. **Bidirectional exact-line scoring**
    - MORE and LESS both scored; failed MORE does not auto-approve LESS.

20. **Typed scorer failure semantics**
    - Capability absent => `MODEL_UNAVAILABLE` only when established by backend route/capability contract.
    - Required inputs absent => `MODEL_INPUTS_INSUFFICIENT`.
    - Invoked scorer throws/times out => typed scorer/completion failure.
    - Malformed probability package => `MODEL_OUTPUT_INVALID`.

## NFL DFS Lineup Optimizer

21. **Governed projection required**
    - External projection-only player cannot enter final optimized player pool.

22. **Platform rules required**
    - Missing/stale salary file or unresolved roster rules fails closed.

23. **Cash/GPP objective separation**
    - CASH prioritizes robust floor/low fragility; GPP prioritizes ceiling and modeled correlation. One objective cannot silently substitute for the other.

24. **Correlation-aware lineup distribution**
    - QB stack, bring-back, multiple same-team pass catchers, and RB/script interactions are not modeled as independent.

25. **Weakest-player cycle**
    - If a verified legal replacement improves the lineup, rebuild/rescore. If only filler preserves structure, reject rather than force.

26. **Final refresh**
    - Inactive player, starting-QB change, started game outside verified late-swap handling, stale salary/eligibility, or material weather change triggers removal/rerun.

27. **No execution**
    - No contest submission, entry, stake, bankroll, or live action. `can_execute=false` and `capital_allocation=false` remain invariant.

## Required Verification Status

Repository artifacts alone establish contract/governance intent. They do **not** prove production scorer deployment.

Report separately:
```text
REPOSITORY_GOVERNANCE
MODEL_CAPABILITY
BACKEND_RUNTIME
LIVE_GPT_EDITOR_SYNC
```

A final `FIXED_VERIFIED` claim requires backend route registration, focused tests, full regression, deployed runtime verification, and replay of the original soccer-pass shortcut scenario plus an NFL DFS scoring-adapter scenario.