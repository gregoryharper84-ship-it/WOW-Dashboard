# Backlog #1933 — NFL staging acceptance contract (NOT IMPLEMENTED)

**Status:** design-only, no migration applied, no Supabase connection, no repository governance override, no live ingestion job. Supports research receipts from draft PR #1937.

## Source facts and current exclusions

- nflverse `stats_player_week_2025.csv` 2025 immutable expected hash: `e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2` (19,422 raw rows). Source SHA must be freshly checked, never merely asserted.
- nflverse/nfldata `games.csv` is a distinct file in the **same source family**, not independent publisher validation.
- `games.csv` was **mutable on `master`** and changed SHA between two live canaries despite retaining 7,548 rows. The pilot now pins the file to upstream commit `a2c9f13593a311b09bfd2d00851e55dbdafb3c8d` (2026-10-21 22:05:13Z), with expected raw SHA-256 `6011ae7b41fe3dcea1572b6e508fc8af3b8e03cf063df2681eb9e9a8c19a4332`. This pin requires a new live read-only verification receipt; do not claim verified until that occurs.
- The ESPN scoreboard one-day pilot (`2025-09-07`) is independently sourced; it can confirm exact identity/final score for **that calendar date only**, not full-season parity. Capture retrieved UTC, endpoint and exact payload SHA. Never promote an unsupported full-season completeness claim.
- There are 18 quarantined 2025 regular-season player-stat rows without player IDs. The six audited settlement outcomes are zero in those rows; all opportunity metrics and special-teams outcomes require separate explicit audit before a training-readiness conclusion.
- Source manifest `NFLVERSE=LICENSE_REVIEW_REQUIRED`; dataset-specific CC-BY-4.0 metadata is not sufficient for automatic V17 production-training entitlement.

## Intended private staging schema (proposal only)

| Proposed entity | Required columns / properties | Contract |
|---|---|---|
| `wow_research.source_asset` | `asset_id`, `provider`, `dataset_id`, `origin_url`, `retrieved_at_utc`, `raw_sha256`, `raw_byte_count`, `source_schema`, `rights_state`, `license_id`, `source_as_of`, `run_id`, `immutable_storage_key` | Append-only content-addressed source snapshots; never overwrite changed bytes. No auth tokens in records. |
| `wow_research.ingestion_run` | `run_id`, `collector_version`, `head_sha`, `started_at`, `finished_at`, `source_count`, `accepted_count`, `quarantined_count`, `held_count`, `typed_status`, `typed_blockers`, `can_execute=false` | Final states are immutable. No `MODEL_UNAVAILABLE` substitution. |
| `wow_research.player_game_outcome` | `source_asset_id`, `source_record_id`, `nflverse_game_id`, `gsis_player_id`, `season`, `week`, `team_id`, `opponent_id`, `event_start_utc`, `stat_type`, `actual_value`, `evidence_domain=SPORTING`, `row_hash`, `normalizer_version`, `run_id` | Unique `(source_asset_id,source_record_id,stat_type,normalizer_version)`; never fabricate identifiers, rows, or zeros; only verified settled observations. |
| `wow_research.quarantine` | `source_asset_id`, `source_row_number`, `row_sha256`, `reason_code`, `raw_reference`, `outcome_materiality`, `opportunity_materiality`, `review_state`, `reviewed_at` | Exactly one disposition for every quarantined raw row; unresolved identity never promoted to accepted player outcome. |
| `wow_research.event_crosswalk` | `nflverse_game_id`, `espn_event_id`, `as_of_utc`, `source_asset_id`, `home`, `away`, `season`, `week`, `kickoff_utc`, `home_score`, `away_score`, `comparison_status` | Only exact final score + kickoff + team/season/week agreement; ties to independent ESPN raw SHA and calendar-date scope. |

Names above are proposed, not assertions that tables exist or have been migrated.

## Deterministic research evidence review (backlog implementation)

`v17/nfl_research_evidence_gate_1933.py` evaluates the canary JSON to keep four independent states distinct:

- **Source provenance and capture:** pinned exact SHA-256 and URL for player and schedule assets, frozen training-report consistency and row reconciliation.
- **Independent identity parity:** 13/13 ESPN finalized games and scores for *2025-09-07 only*; no full-season claim.
- **Quarantine:** 18 player-ID-missing rows remain excluded, with all 12 audited fields zero. Any nonzero/invalid opportunity, hash mismatch or changed count fails the research audit.
- **Permissions:** `LICENSE_REVIEW_REQUIRED` and `SOURCE_VALIDATION_HOLD` remain as-is; the gate always emits `storage_write_authorized=false`, `training_ready=false`, `model_certified=false`, `probability_publishable=false`, `can_execute=false`.

`RESEARCH_EVIDENCE_AUDIT_CANDIDATE` means only a structurally coherent *unattested* data receipt. It does **not** authorize staging writes, training, source licensing, model publishing, or wagering. The function cannot validate CI principal/commit provenance from supplied JSON alone and always reports `attestation_verified=false`. A separate independently authenticated importer and reviewed rights agreement would be necessary.

---

## Access, invariants and source boundaries

- Prefer a **private, unexposed** `wow_research` Postgres schema; no `anon`/`authenticated` table grants. Review Data API exposure configuration and RLS with a dedicated least-privilege ingestion identity before implementing.
- Store raw assets only in access-controlled immutable object storage via supported Storage APIs (do not modify the internal `storage` metadata tables). Separate code paths for read-only acquisition, quarantining, and later authorized staging writes.
- No direct joins/updates to `wow_predictions`, production player-prop features, model registry, calibrated probabilities, publishability, market prices, tickets or wagers. `can_execute=false` throughout.
- Use exact-URL and dataset allowlists, bounded payloads, immutable hashes, typed HTTP failures, licensing state, `as_of`/retrieval timestamps, and reference hashes. HTTP 403/429 must remain stops, not IP/account rotation triggers.
- Preserve raw source revisions rather than destructive upserts. A newer source revision creates a new asset record; snapshots consumed in any later fitting must be frozen in the training manifest.
- `MARKET` and `SPORTING` evidence remain separate. Odds, line histories, and sportsbook probabilities are never accepted as sporting model labels or probabilities.

## Strict acceptance conditions for next separate PR

1. Document and ratify license/contract state per provider and dataset, including attribution obligations and intended storage/training purpose.
2. Show read-only canary receipts with raw SHA256 and row-count reconciliation for both NFLverse inputs, plus **real** ESPN final-event crosswalk for at least one explicitly scoped 2025 date. Any unsupported scoreboard day or wrong final score stays typed `PARTIAL_HOLD`.
3. Account for every in-scope player row: accepted + quarantined + unjoined equals source in-scope count; include zero/nonzero/invalid opportunity values for identity-quarantined rows.
4. Write/apply **a separate approved migration** only after exact schema/privileges/security review and disconnected staging verification. Execute idempotent import twice and prove no duplicate accepted rows; hash tampering, missing IDs, postgame-to-pregame leakage and unauthorized role access must fail.
5. Show no writes to production model/predictions/card tables, no deployed workflow, no scheduled refresh, no auto-merge, no wagering execution.
6. Preserve immutable pregame `as_of` cutoffs for eventual training. No future-game score, final report or closing betting line may enter pregame feature history.
7. SIRT/QA readiness receipt must distinguish `SOURCE_VALIDATION_HOLD`, `LICENSE_REVIEW_REQUIRED`, and scorer/model errors. Research-source verification alone does **not** make NFL moneyline, spread or prop capabilities publishable.

**Authority:** WOW V17 terminal reducer remains sole global terminal owner. This contract supplies possible offline source evidence, never a governed probability, selection, or execution authority.
