# WOW #1933 — 2025 NFL full regular-season independent source receipt

**Evidence classification:** VERIFIED READ-ONLY HISTORICAL FINAL-SCORE COMPARISON ONLY. This file documents observed GitHub Actions evidence, not a source-rights decision, authentication attestation or a governed sporting probability.

- GitHub read-only job: [run #39699796142](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/39699796142), job `119565984769`, `SUCCESS`.
- Exact executing branch SHA: `577b159c4e09ae87b14373283aa90886abea0186`.
- UTC execution on 2026-10-22. No deployment or merge.
- `tests/test_nfl_espn_full_regular_shadow_1933.py`: **9 tests passed**.
- 2025 NFL **regular season only**: **272/272** final game identities, kickoff windows and final scores cross-checked between pinned nflverse schedule and independently fetched ESPN weekly scoreboard responses; **18/18** weeks; **zero unresolved, contradictory or ambiguous matches**.
- Immutable nflverse schedule: `https://raw.githubusercontent.com/nflverse/nfldata/a2c9f13593a311b09bfd2d00851e55dbdafb3c8d/data/games.csv`; raw file SHA-256 `6011ae7b41fe3dcea1572b6e508fc8af3b8e03cf063df2681eb9e9a8c19a4332`.
- SHA-256 of ordered `nflverse_game_id` ↔ `espn_event_id` crosswalk JSON: `94a76f77cb87a2415d303adc32d3ea8b1cec00e76ce1fbde20c7c8b301358967`; **272** matched game IDs. The unhashed raw ESPN weekly payloads were not committed or stored as a production asset.
- Collection scoped to public ESPN historical weekly regular-season scoreboards, with exactly 18 allowlisted URLs and raw response digests. No scraped credentials and no sportsbook fields.

## Week-by-week independent-source provenance

| 2025 week | Final scores matched | Conflicts / missing | ESPN scoreboard raw JSON SHA-256 |
|---:|---:|---:|---|
| 1 | 16/16 | 0 | `2f8a3ea73aad0eb74e4ed4c6581ae4fc5f31b8acef60f9ec08b3d5053d226dd1` |
| 2 | 16/16 | 0 | `9dfa1cb1916100eacb25503c763ae0f9822f443b38e3517c8a34dc638dea0881` |
| 3 | 16/16 | 0 | `a650b1d7fbed74af81bce085de69d3a7a75df5102ecc261ca934bf482baa8715` |
| 4 | 16/16 | 0 | `76c7f75b1606a1a131c09fec621b4ccb261f0cc4f1520f48d4970f4968e670e7` |
| 5 | 14/14 | 0 | `d4c3ccb16bcb5dbbd86ab40906f01880d0be5070c896100f1bc037a447ef184d` |
| 6 | 15/15 | 0 | `3a00a6d7da939b6a948bb669651c99cd37444d7c8e51e47f870100d4ce9f6409` |
| 7 | 15/15 | 0 | `b97d1187d25a9355ed88b196af4a87e34bd909d567fb394eb622fea6d1242005` |
| 8 | 13/13 | 0 | `87af436bed705429c46c5d8bda5b36146ff467bf15d81aefe714296fbade11d2` |
| 9 | 14/14 | 0 | `bb158b0e33bf1cb2c22e2c7333bce51b4c324ed00de7d74340ee53144e67bc70` |
| 10 | 14/14 | 0 | `f78c0e780abb5f6dad80ffb30f0b9c53b2167ba496c20b6573f2155ff06c1cf4` |
| 11 | 15/15 | 0 | `f2466e18bb5a627def0d8efd099071b5e7d0ebe331432f24b656a49a1403a3be` |
| 12 | 14/14 | 0 | `efffc1d92ae800ed3a1a3a8a97240436e452857473f5d815ac410c22dffb78dd` |
| 13 | 16/16 | 0 | `689db247a3c7b600e3425e5b44adc84c8e5a695bef3c5d125c66b28636d51ba8` |
| 14 | 14/14 | 0 | `f28dc8a921d932c4b783ade16fbf382c78ea5be3fbdcd7fa8f88cda8876c97ad` |
| 15 | 16/16 | 0 | `691091db7bc49934de8f7a678730f27577ddc9fa04e93c17c5bf9d4a18512765` |
| 16 | 16/16 | 0 | `5bff52e412d56be66b2707e2a701b46191c8a57f7a4b470ec59e783269713556` |
| 17 | 16/16 | 0 | `83c67054a2d1ac5a0e3718d5d2ba4a16cb9edf52fe2cc9c49fe38a0454fecd24` |
| 18 | 16/16 | 0 | `9251ba86307599bfbea54e602ee2bb354b77c43487eb8fd11e273a1c45f5bdc8` |

These hashes are evidence of the observed response bytes **at collection time**. ESPN's endpoint responses may change; a future response changing hash requires a new dated immutable evidence receipt, not silent substitution.

## Separate holds — do not erase

- **Player-stat source:** the previously verified 2025 nflverse weekly player source has 19,422 rows and SHA-256 `e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2`; its 18 missing-player-ID rows remain quarantined. **This 272-game score parity does not cross-validate player-level yardage, touchdowns, usage or player IDs against ESPN.**
- **License:** nflverse source manifest remains `LICENSE_REVIEW_REQUIRED`. Separate ESPN permitted acquisition/storage/training rights remain **unreviewed**; published nflverse CC BY 4.0 does not license ESPN's endpoint.
- **Data staging:** proposal only, no Supabase migration/table/write or authenticated importer.
- **Model:** `training_ready=false`, `model_certified=false`, `probability_publishable=false`, `storage_write_authorized=false`, `attestation_verified=false`, `can_execute=false`.
- **V17 terminal authority:** unchanged. This research receipt creates no model probability, ranked selection, betting card or ability to place a wager.

**Next bounded work:** independently attest source rights and investigate 18 missing player IDs if training support is needed. Keep draft PR #1937 unmerged and issue #1933 open until separately reviewed.
