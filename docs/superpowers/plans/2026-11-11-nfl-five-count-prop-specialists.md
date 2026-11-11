# NFL Five Missing Count-Prop Specialists Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development, with RED → GREEN per producing-layer change.

**Goal:** Restore five currently unbuilt NFL count-stat prop specialties without bypassing the V17 lifecycle.

**Architecture:** The new offline, non-serving candidate builder fits exactly one independent Poisson count model for each of COMPLETIONS, PASSING_TOUCHDOWNS, PASS_ATTEMPTS, RECEPTIONS and RUSH_ATTEMPTS from frozen nflverse player-weekly source files. The first stage records hashes, independent 2024 calibration, untouched 2025 holdout and fail-closed errors. Candidate output never registers into production, becomes rank eligible, or changes the terminal reducer.

**Tech Stack:** Python, pandas, numpy, scipy, sklearn, pytest.

**Spec:** WOW V17 Player-Prop Qualification Blockers — Engineering Recovery Handoff, dated 2026-11-11 (user-provided handoff).

## Global constraints

- custom_gpt_identity=WOW_BETTING_ENGINE
- runtime_generation=V17_ACTIVE
- terminal_authority=V17_TERMINAL_REDUCER
- can_execute=false
- DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
- Never fit from odds, heuristics, research narratives or an LLM probability.
- No probability, calibration, catalog, threshold, certification, display or protected-release modifications in the candidate stage.
- Preserve every original NFL certified direct-prop lane.

## Review focus

1. Missing/NaN/fractional counts → typed source rejection, never fabricated zero.
2. Repeated player/game identity → reject, never double-count target or history.
3. Later game outcomes entering a pregame feature → forbid; only earlier games feed L10/L5.
4. Missing 2024 or 2025 cohort → no calibration/holdout claim.
5. Apparently successful candidate → remains non-publishable and unregistered.

## Task 1: Candidate acquisition and feature contract — implemented on this branch

**Files:** scripts/train_nfl_missing_prop_candidates.py; tests/test_nfl_missing_prop_candidates.py.

- [x] RED: 10 initial tests failed on unimplemented scaffolds.
- [x] GREEN: strictly validated source/identity/count, role- and stat-specific leakage-safe features, independent five-route fits, candidate-only artifact.
- [x] Local targeted tests: 15 passed; compileall passed.
- [x] Offline synthetic CLI: all five candidate artifacts emitted, registration_attempted=false, all probability_publishable=false.
- [ ] Hosted CI against exact GitHub head and independent code review.

## Task 2: Genuine source and independent model selection — not done

- [ ] Retrieve authorized nflverse 2021–2025 frozen source bytes and verify license/reproducible hashes; do not commit protected raw datasets.
- [ ] Execute 5 independent candidate fits, report train/cal/holdout counts and OOD without using the holdout for model selection.
- [ ] Predeclare held-out exact-line probability metrics, conditional calibration, empirical count-tail/overdispersion and role-regime tests. Compare Poisson, negative-binomial and other genuinely fitted specialists as appropriate. The initial Poisson candidate is not presumptively certifiable.
- [ ] Run independent source audit, graded forward replay and leakage-proof feature hydration.
- [ ] Keep any model failing a quality/rights gate unavailable or held; never promote by changing a status flag.

## Task 3: Governed per-stat production integration — not done

- [ ] Register each independently approved model artifact, calibrator/bounds and exact controlling specialist in the existing governed path.
- [ ] Verify stat synonyms, correct event/player identity, role validity, start time, literal line, MORE/LESS distributions and push/void settlement.
- [ ] Unit/integration/replay, exact-head CI, independent non-author QA/SIRT/Release acceptance, then governed production deployment.
- [ ] Verify live Render SHA, real /score-pick-request terminal receipts, display authorization, no regressions in four existing NFL lanes, and strict can_execute=false.
- [ ] Record scoreboard delta and V17 memory ledger; never mark fixed without real production receipts.

## Acceptance and current disposition

**CANDIDATE_BUILDER_IMPLEMENTED; PRODUCTION_NOT_FIXED.** 0 of 5 new lanes certified by this branch, no runtime registry change, no deploy. No numeric fitted probability from this candidate builder is currently authorized for publication.


## Authoritative nflverse source-release metadata receipt (2026-11-11)

**Read-only metadata**, not CSV bytes or training proof. From the upstream `nflverse/nflverse-data` `stats_player` release metadata at https://api.github.com/repos/nflverse/nflverse-data/releases/tags/stats_player . Repository license is CC-BY-4.0 (primary license text https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md); independent source-rights approval is NOT established by this receipt.

| Source asset | Published size (bytes) | Published SHA-256 |
|---|---:|---|
| stats_player_week_2021.csv | 8477784 | 41915fb49238902ad1f129ebf0405b11a1e710454ae0fe8f7b3e4f9145875f48 |
| stats_player_week_2022.csv | 8408729 | ad426c3fe5bf1cc30c3f137fdfe96d054e19d400879ee4413129da49fa7b54be |
| stats_player_week_2023.csv | 8332874 | f19cb71a5de0dce7fd09376026237c9ee9d5a93fe13815a2ea3ec2d37204cb17 |
| stats_player_week_2024.csv | 8470040 | 3ddc45a84f759aa348ce465ae001752c530575455717657cdfe1f8abfcdb4759 |
| stats_player_week_2025.csv | 8656387 | e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2 |

**Required next execution (same task, not a new queue):** Fetch the five assets using the already authorized free public GitHub release-download path in a network-enabled engineering workspace; independently recompute SHA-256 from the *same bytes used for parsing* and compare against the table; explicitly reject any hash mismatch or changed release version before fitting. Report exact role/player-week/column coverage, licensing attribution, and observed source hashes. The current chat's GitHub reader returned 404 for the release-asset binary download despite access to release JSON metadata; no source CSV was transferred or fitted here. This is a source-access limitation, not a model scoreability improvement.

## Pending source-identity adjudication and new independent review (2026-11-11)

CodeRabbit independently reports **18 REG 2021 rows lacking player identities** in its pinned-source byte inspection. A separate indexed community schema describes some nflverse player-week records as unattributed team-credit rows; this supports a possible explanation, **not** adjudication of the 18 exact rows or their statistical fields. Current source loader conditionally quarantines only records with no name or eligible route role, verified all-zero modeled count fields, and a valid season/week/game key. Each quarantined row has its original filename and source-line key (season asset + source_line), count/type evidence, and per-row audit digest. A nonzero modeled count, eligible position, named player, or missing game identity causes an explicit hold. Quarantine does not authorize data licensing, candidate fitness, participant identity, calibrator or production publication.

- [ ] Independently fetch and verify actual pinned CSVs in the network-enabled existing engineering runner, then review *all* 18 row lineages. Prove eligible player events were not excluded, even when their modeled counts are zero.
- [ ] Establish whether any other 2022–2025 record has missing player identity and whether metadata/columns differ.
- [ ] Run local focused suite at latest commit and hosted exact-head CI, inspect new CodeRabbit/TestDriver findings.
- [ ] Require independent C-class semantic SIRT acceptance on final head and governed model evaluation/registration path; retain can_execute=false.
- [ ] Observe main and Render live SHA parity only after actual authorized merges; never count this research PR as deploy/certification.

## Offensive participation completeness audit (added 2026-11-11)

**Causal reproduction:** A valid snap-count player can appear with 8 offensive snaps and be absent from the player-week stats rows. An inner-only training cohort then excludes an observed participant, silently altering historical opportunities and fitted probabilities. A second false-green would arise from comparing against an empty snap cohort. See CodeRabbit PR #2605 review and `test_nfl_count_prop_participation_audit.py`.

**Implemented as a research preflight, not a new release gate:** `artifacts/wow-engine/scripts/audit_nfl_count_prop_participation.py` consumes five locally frozen **official-digest-verified** snap-count CSVs, the pinned `players.csv` PFR↔GSIS crosswalk and the original five pinned player-week CSVs. It validates exact season/week/game identity, one-to-one crosswalk, nonnegative finite snap counts, no duplicate participant keys and an actual positive-snap cohort for each season. It emits a bounded JSON report and exits nonzero on missing or unmapped offensive participant games. It never backfills zero counts, creates a fitted probability, registers a specialist, approves rights, or enables execution.

**Runner invocation:** From `artifacts/wow-engine`, with sanctioned frozen sources already present locally:
```bash
python scripts/audit_nfl_count_prop_participation.py \
  --stats-dir /path/to/frozen/player_week \
  --snap-dir /path/to/frozen/snap_counts \
  --players-csv /path/to/frozen/players.csv
```
The source-asset digests are embedded in the script from upstream nflverse release metadata. It performs **no network fetching** and should run in the existing network-enabled engineering runner after approved no-cost source acquisition. Exit code 2 means coverage gaps; other nonzero means typed invalid inputs. A zero exit means only the narrowly defined REG QB/RB/WR/TE positive-offensive-snap participant-game keyset reconciled; it is **not** independent semantic review or model certification.

- [ ] Run the audit with verified real 2021–2025 official CSV bytes and attach audited counts, missing examples, full SHA manifest and source-rights conclusion to existing #2020.
- [ ] Investigate Charlie Kolar 2024 Week 1 and all other missing snap-positive player-game events by exact GSIS/PFR/game IDs. Never turn missing events into scored 0 without independent event evidence.
- [ ] Independently challenge coverage scope: this does not prove participation with zero offensive snaps, all eligible roles, or any market-level availability.
- [ ] Review new script and negative-path tests at *current* head, obtain non-author SIRT and native App acceptance before governed protected merge.

## No-cost official source fetch (2026-11-11)

In the existing network-enabled engineering research runner only (no new workflow, deployment, purchase or self-approval):

    cd artifacts/wow-engine
    python scripts/acquire_nfl_count_prop_sources.py --dest ./frozen
    python scripts/acquire_nfl_count_prop_sources.py --dest ./frozen --verify-only
    python scripts/audit_nfl_count_prop_participation.py --stats-dir ./frozen/stats_player --snap-dir ./frozen/snap_counts --players-csv ./frozen/players/players.csv

The standalone user kit is independently hardened against editable manifest hash/size injection. This repository downloader reads static reviewed SHA pins directly from the existing trainer and snap audit and enforces published byte sizes. Eleven frozen assets sum to 61,620,003 bytes. This conversation's real download failed DNS; no raw bytes or real candidate fit have been verified here. Candidate activation remains forbidden until source rights, missing player-games, holdout metrics, forward certification, independent SIRT and QA/Release checks pass. Never coerce unknown participation to zero.
