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

Phase 3 requires at least three completed regular seasons and an untouched final-season holdout. The initial raw corpus freeze therefore pins the four completed season assets below and deliberately excludes the current 2026 asset from the first research replay.

### Player box scores

Release tag: `nhl_player_boxscores`

| Season asset | SHA-256 |
|---|---|
| `player_box_2022.csv` | `60c457cfe62c125929861367816c4eb243ebf17574cd35ad9982318a1e484e4d` |
| `player_box_2023.csv` | `4f823eb8146a03becdaaf220128f68729528443c08a18852e54aae3f3833dd44` |
| `player_box_2024.csv` | `889d439dae5b5a2e831496a3a0dcbea4d385883d68d55b70d55a554169ff6e74` |
| `player_box_2025.csv` | `511f58b09996be6165c7ad2a0f475ac029f0206653ce4e11665e1ff8088516b0` |

The release describes these assets as NHL player box scores, one row per player per game, with historical assets from 2010 onward and upstream source `api-web.nhle.com`.

### Game metadata

Release tag: `nhl_game_info`

| Season asset | SHA-256 |
|---|---|
| `game_info_2022.csv` | `752fb3b3406c9f14b91d76d66fb6d1efd78eb17e94a6261b9931da484e632786` |
| `game_info_2023.csv` | `cd2746413a4eaf8819140c894b5297d57098a4736ec52aa5b7d9e17f58351fcc` |
| `game_info_2024.csv` | `03f87329a2113ddaf4b8f31b213952efc49d85d413155ec67b00d8b6c4043994` |
| `game_info_2025.csv` | `e743bafe13dfa761f3ac84ab978b8e7de73ea7f126f2daf4f27e437017f3212b` |

The raw release metadata contains older assets as well, even though its human-readable season-count summary is narrower. WOW therefore trusts only the concrete pinned asset identity plus digest, not an inferred coverage claim from the summary text.

## Raw-corpus rules

1. Download only the explicitly pinned GitHub release assets.
2. Verify SHA-256 before an asset is admitted to the frozen research snapshot.
3. Persist asset URL, filename, season, kind, digest, retrieval timestamp, and snapshot-manifest hash.
4. A digest mismatch fails closed and does not fall back to a newer/unpinned asset.
5. No credentials or secrets are required for these public release assets.
6. Raw acquisition does not parse or reinterpret schema fields.
7. Schema transformation into WOW canonical NHL SOG records is a separate step and must fail closed on unknown or changed columns.
8. The current/incomplete season is excluded from the first Phase 3 real-data replay.
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

The next allowed engineering step is to freeze the pinned raw corpus, inspect and version its real schema, transform it through the already-built Phase 1/2.1 contracts, and rerun Phase 3 as research. If the source-review gate remains open, any resulting challenger remains non-publishable regardless of model quality.
