# NCAAB Historical Source Pair Integrity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and test-driven-development for task execution.

**Goal:** Prevent ambiguous historical SportsDataverse NCAAB home/away rows from silently creating labeled training examples.
**Architecture:** A pure two-row integrity preflight runs inside the existing NCAAB-only source collector immediately before construction of a game; it never registers a model.
**Tech Stack:** Python 3 / pytest; no paid source or new controller.
**Spec:** #1644 candidate source replay and #455 universal sport evidence contract.

## Global Constraints
- Do not edit #2550's canonical-event branch, shared terminal/release code, or the NFL Pick'em lane.
- Preserve `can_execute=false`, research-only/certification hold and sole `V17_TERMINAL_REDUCER` authority.
- Unknown/duplicate contradictory source pairs must HOLD, not select first rows.

## Review Focus
- Duplicate home/away records from a merged source cohort must not make an arbitrary pair.
- Same participant on both sides must not create a winner label.
- Different source asset or season in two halves must not be merged.
- Missing scores remain non-training rows, not automatic outcomes.
- Canonical game ID mismatch must not slip into features.

### Task 1: Pair-validation seam (isolated source-only)
**Files:**
- Create `artifacts/wow-engine/v17/ncaab_source_pair_integrity.py`
- Create `artifacts/wow-engine/tests/test_v17_ncaab_source_pair_integrity.py`
- Modify `artifacts/wow-engine/v17/ncaab_sportsdataverse_candidate.py` in `fetch_games`.

**Interfaces:**
- Consumes source-grouped rows under one `game_id`.
- Produces `validated_team_box_pair(game_id, rows) -> (home, away) | None`; raises typed `NCAABSourcePairError` for contradictions.
- Existing collector converts a typed source error to `NCAABCandidateUnavailable`.

- [x] Write adversarial fixtures for six independent behaviors.
- [x] Verify RED locally (missing module / import failed).
- [x] Implement pure validation and collector hook.
- [x] Focused local pytest: 6 passed; compileall: exit 0.
- [ ] Hosted exact-head complete repository CI and independent review.
- [ ] Verify real source cohort conservation; if it changes, quarantine rather than silently re-pin artifacts.
- [ ] Authorized merge and authenticated runtime acceptance if needed.

**Test scope:** Current local runner lacks authenticated GitHub source access; full repository tests are delegated to hosted exact-head CI rather than claimed complete.
