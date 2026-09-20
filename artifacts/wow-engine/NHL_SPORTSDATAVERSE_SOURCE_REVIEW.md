# NHL SportsDataverse Source Review — Provenance-Ready, Not Production-Approved

Status: `SOURCE_REVIEW_REQUIRED`

`can_execute=false`

`probability_publishable=false`

This document records the source-governance decision for the NHL Skater Shots on Goal research vertical. It does **not** certify a model, grant probability authority, register a production artifact, or publish a capability.

## Decision

`SPORTSDATAVERSE_NHL` may be used as a **pinned, provenance-complete research/corpus-hydration distribution layer** while source review remains open.

It is deliberately classified as:

- `CANDIDATE_FIRST_PARTY_UNDOCUMENTED` in `v17/model_source_entitlements.py`;
- `LICENSE_REVIEW_REQUIRED` in `historical_source_manifest_v1.json`;
- `fitted_training_allowed_when_ready=true` for candidate/research work only;
- `certification_source_review_required=true`;
- `probability_source=false`;
- `can_execute=false`.

Production fitted-model training remains blocked by the historical-source readiness layer until source review is explicitly cleared. Source availability, a successful download, or strong model metrics cannot clear that gate.

## Why this is not marked `TRAINING_OPEN_LICENSED`

The SportsDataverse release repository and associated software may carry permissive repository/software licensing, but the NHL release metadata states that the NHL datasets are derived from the **NHL public API (`api-web.nhle.com`)**. That upstream provenance is controlling for this review.

Accordingly, WOW does **not** infer that the repository/software license automatically grants production training rights over NHL-derived data. In particular, this review does not claim `CC-BY-4.0` for `SPORTSDATAVERSE_NHL`.

The safe governed state is therefore source-review-pending rather than open-licensed or `V17_APPROVED`.

## Candidate corpus

Phase 3 requires at least three completed regular seasons and an untouched final-season holdout. The first real-data replay pins **2024, 2025, and 2026** because those SportsDataverse player-boxscore assets expose direct player × game identity fields (`game_id`, `season`, and `game_date`) alongside player/team identity and `shots_on_goal`.

The older 2022 and 2023 player-boxscore assets were downloaded and inspected during source research but are **quarantined from the first model replay**: their raw schema does not expose `game_id`, `season`, or `game_date`. WOW will not reconstruct those joins from file order, row position, or another heuristic merely to increase sample size.

### Player box scores

Release tag: `nhl_player_boxscores`

| Season asset | SHA-256 |
|---|---|
| `player_box_2024.csv` | `889d439dae5b5a2e831496a3a0dcbea4d385883d68d55b70d55a554169ff6e74` |
| `player_box_2025.csv` | `511f58b09996be6165c7ad2a0f475ac029f0206653ce4e11665e1ff8088516b0` |
| `player_box_2026.csv` | `41a35357d65e0d51967568ca9d0d16dae0dba593bf0193372f6cd14e5a46b102` |

The release describes these assets as NHL player box scores with upstream source `api-web.nhle.com`. The pinned 2026 asset was published after the 2025-26 season and is treated as historical research data, not current-game evidence.

### Game metadata

Release tag: `nhl_game_info`

| Season asset | SHA-256 |
|---|---|
| `game_info_2024.csv` | `03f87329a2113ddaf4b8f31b213952efc49d85d413155ec67b00d8b6c4043994` |
| `game_info_2025.csv` | `e743bafe13dfa761f3ac84ab978b8e7de73ea7f126f2daf4f27e437017f3212b` |
| `game_info_2026.csv` | `15bfbde574d9b84f07ffc387460127cb66b0173a06c6812132fbc718b524b610` |

The release metadata contains older assets as well, even though its human-readable season-count summary is narrower. WOW trusts only concrete pinned asset identity plus digest, not an inferred coverage claim from the summary text.

## Timing limitation discovered by schema inspection

The pinned `nhl_game_info` CSV schema contains `game_date` but no exact puck-drop timestamp. Phase 2.1's leakage contract requires exact historical event timing and settled-stat availability semantics; date-only metadata is not sufficient to manufacture `game_start_time`.

Therefore:

- WOW will **not** synthesize midnight/noon start times from `game_date`;
- SportsDataverse remains the candidate distribution layer for settled box-score/game metadata;
- exact `game_start_time` must come from a separately governed, exact-`game_id` timing source before a Phase-3 row is admitted;
- if exact timing cannot be resolved for a game, that game fails closed from the real replay rather than receiving a guessed timestamp.

## Raw-corpus rules

1. Download only the explicitly pinned GitHub release assets.
2. Verify SHA-256 before an asset is admitted to the frozen research snapshot.
3. Persist asset URL, filename, season, kind, digest, retrieval timestamp, and snapshot-manifest hash.
4. A digest mismatch fails closed and does not fall back to a newer/unpinned asset.
5. No credentials or secrets are required for these public release assets.
6. Raw acquisition does not parse or reinterpret schema fields.
7. Schema transformation into WOW canonical NHL SOG records is a separate step and must fail closed on unknown or changed columns.
8. 2022/2023 are explicitly quarantined from the first replay because their player-boxscore schema lacks direct player × game identity.
9. No sportsbook/market evidence is part of this corpus.
10. No action in this source lane can set `can_execute=true`.

## Promotion boundary

The following remain separate decisions and are **not** authorized by this source review artifact:

- changing `SPORTSDATAVERSE_NHL` to `V17_APPROVED`;
- setting source review to `PASS`;
- registering a fitted NHL SOG artifact;
- adding `NHL / SHOTS_ON_GOAL` to the production capability manifest;
- publishing governed probability or a calibrated lower bound;
- wiring `/score-pick-request` to a production NHL SOG specialist;
- any wager/order execution.

The next allowed engineering step is to freeze the 2024-2026 pinned corpus, inspect/version its schema, add exact game timing by verified `game_id`, transform it through the already-built Phase 1/2.1 contracts, and rerun Phase 3 as research. If the source-review gate remains open, any resulting challenger remains non-publishable regardless of model quality.
