# V17 promotion transaction — schema review — November 10, 2026

Status: **BLOCKED_AT_SCHEMA_REVIEW / NOT_EXECUTED / NOT_IMPLEMENTED**.
`can_execute=false`, `probability_publishable=false` unchanged everywhere.
No migration was written, no certification was inserted, no promotion occurred,
and no database was contacted. This memo records why the proposed
`migrations/20261110_promote_v17_certified_specialists.sql` transaction cannot
be implemented against this repository as specified.

## Scope

Review of the proposed atomic promotion transaction (demote, rank by Brier,
promote one specialist per `(sport, routed_lane, market_key)`, seed NBA team
aliases, write `V17_TERMINAL_REDUCER` receipts, assert `can_execute = FALSE`)
against the actual governed schema in this checkout.

## Finding 1 — every table the transaction targets is absent

Each object referenced by the proposed SQL has **zero** matches in the
repository. Searched all 167 `.sql` files plus all Python/TS sources:
`v17_model_registry`, `v17_slate_candidates`, `v17_governance_promotion_receipts`,
`v17_canonical_team_aliases`, `v17_nhl_pregame_goalie_inputs`.

There is also no repository-root `migrations/` directory. Governed migrations
live under `artifacts/wow-engine/migrations/` with timestamp-prefixed names
(for example `20261107064224_v17_mlb_strikeout_snapshot_min3.sql`).

The proposed SQL would therefore fail at first reference. It is not a near-miss
in naming; the real registries model different things:

| Proposed object | Actual governed object |
| --- | --- |
| `v17_model_registry` (one row per lane/market) | `wow_team_event_specialist_certifications`, `wow_basketball_team_event_model_artifacts` + `wow_basketball_team_event_calibrators` + `wow_basketball_team_event_certification`, `wow_prop_fitted_model_artifacts`, `wow_ncaaf_fitted_model_artifacts` |
| `v17_governance_promotion_receipts` | the immutable certification row itself (no separate receipt table) |
| `v17_canonical_team_aliases` | `wow_player_aliases` / `wow_canonical_players` / `wow_unresolved_entity_aliases` (player-level, no team table) |
| `v17_slate_candidates` | no such candidate table |
| `v17_nhl_pregame_goalie_inputs` | no such table; NHL inputs are validated in code |

Nor do the proposed columns exist: `fitted_brier_score`, `platt_slope_a`,
`platt_intercept_b`, `calibration_replay_rows`, `promoted_on_deploy_sha`,
`routed_lane`, `lane_type`, `market_key`, `is_active`, `is_promoted` appear in
no DDL in this repository. `receipt_sha256` exists only as a Python receipt
field, never as a governed registry column.

## Finding 2 — two existing constraints reject the mutations outright

These are not style objections; the transaction would abort.

1. **Player props can never publish.**
   `artifacts/wow-engine/prop_fitted_model_registry.sql`:
   `constraint wow_prop_fitted_never_publish check (probability_publishable = false)`.
   The proposed promotion sets `probability_publishable = TRUE` for
   `lane_type = 'PROP'`, which violates that constraint. The prop lane is
   deliberately publish-blocked until a certified end-to-end path exists, and
   that registry states it explicitly ("Do not flip PROP_PROBABILITY to
   AVAILABLE here").

2. **Certifications are append-only and enum-constrained.**
   `artifacts/wow-engine/migrations/20260921_create_team_event_specialist_certifications.sql`:
   `certification_status text not null check (certification_status in ('CERTIFIED','REVOKED'))`
   plus `create trigger ... before update or delete ... execute function
   public.wow_reject_immutable_mutation()`. The proposed
   `certification_status = 'CERTIFIED_ACTIVE'` is outside the enum, and the
   proposed rollback `UPDATE ... SET certification_status = 'SHADOW'` is both
   an enum violation and blocked by the immutability trigger. Demotion is a new
   `REVOKED` row referencing `supersedes_certification_id`, not an update.
   `migrations/20260926_harden_team_event_specialist_certification_privileges.sql`
   grants only `select, insert` to `service_role`, so the transaction would also
   be denied at the privilege layer even if the trigger were absent.

A third structural block: `wow_ncaaf_fitted_model_artifacts` has
`create unique index uq_wow_ncaaf_fitted_active_route ... (feature_schema_version) where active`,
so "best artifact per `market_key`" cannot be expressed there — there is one
active artifact per feature schema, not per market.

## Finding 3 — NBA promotion is a join, not a boolean flip

`artifacts/wow-engine/migrations/20260913_basketball_team_event_specialists.sql`
gates NBA availability on `wow_basketball_certified_model_artifact(p_sport)`,
which requires `a.promoted and a.active` **and**
`c.capability_status = 'CERTIFIED'` on a certification row keyed
`(sport text primary key)`. Flipping `is_promoted`/`is_active`:
`probability_publishable` on artifacts cannot produce a publishable lane while
that certification row still reads `UNAVAILABLE`/`SHADOW`. Model quality is
recorded as `holdout_brier` / `holdout_log_loss` on artifacts and
`brier`/`ece` on calibrators — not as the proposed single-row metric set.

## Finding 4 — there is no team-alias target, and the real queue is player-level

`artifacts/wow-engine/migrations/20261002_entity_alias_reconciliation_gateway.sql`
defines `wow_player_aliases` (FK to `wow_canonical_players`), a
`wow_entity_alias_gateway_control` row per sport defaulting to `SHADOW` with
`can_execute=false`, and `wow_unresolved_entity_aliases` (`entity_type` may be
`TEAM`, but only as an unresolved queue entry — there is no canonical team
mapping table). Seeding `('NBA','GS','GSW')`-style rows has no destination, and
rewriting a held candidate's `official_event_id` by string concatenation would
manufacture event identity rather than prove it against the schedule.

## Finding 5 — the NHL goalie request does not match the NHL input contract

`artifacts/wow-engine/v17/nhl_pregame_evidence_boundary.py` defines `REQUIRED`
as `official_event_id, home_team, away_team, event_start_time_utc,
goalie_status, injury_report, rest_travel, settlement_basis, home_elo,
away_elo, home_goalie_sv_pct, away_goalie_sv_pct, home_pp_pct, away_pk_pct,
calibration_artifact`. There is no `home_starter_confirmed` /
`away_starter_confirmed` pair and no `sv_pct_l10` or `gsax_per_60`.

The contract is stricter than presence checks: `VERIFIED_GOALIE_STATUSES =
{frozenset('CONFIRMED')}`, `goalie_status_observed_at` must be past and within
`max_status_age_minutes = 60`, `event_start_time_utc` must be in the future,
save percentages must be numeric in `[0, 1]`, and a
`NHL_CANONICAL_IDENTITY_RECEIPT_MISSING`/`MISMATCH` blocker applies unless a
trusted server-side resolver supplies an `NHL_PUBLIC_WEB_API` club-schedule
receipt matching event ID, both teams and start time within 30 minutes.
`v17/nhl_candidate_preflight.py` adds `MAX_GOALIE_SNAPSHOT_AGE = 4 hours` and
`CONFIRMED_STARTING_STATUSES = {CONFIRMED, CONFIRMED_STARTER, OFFICIAL_STARTER}`.

Consequently an SQL upsert of "confirmed starters" would satisfy none of those
checks, and would be fabricating inputs to pass a gate — a stop condition under
the engineering contract, independent of schema.

## Finding 6 — defects in the transaction even if the schema existed

1. The NBA alias reconcile uses
   `FULL JOIN public.v17_canonical_team_aliases AS a_home ON FALSE`, producing a
   cartesian product between away and home alias sets. A candidate matching two
   aliases yields multiple source rows, and PostgreSQL applies one arbitrary
   join row per target — nondeterministic event identity.
2. Section 3 demotes the entire target lanes before promotion, while section 6
   raises if any winner is missing. Because `RAISE EXCEPTION` rolls back the
   whole transaction, a single absent NCAAB/NCAAF qualifier promotes nothing,
   including NBA and props — the opposite of "clears 17 of 24".
3. NFL `TEAM` rows are excluded from the demotion in section 3 but asserted to
   be zero in section 6, so any pre-existing promoted NFL team artifact aborts
   the whole transaction instead of being held.
4. `PERFORM set_config('wow.v17.can_execute','false',true)` inside the same
   `DO` block that then reads it is a self-attestation, not a lock; it cannot
   fail. The binding safety property is the column `check (can_execute = false)`
   constraints already present on the real registries.
5. `hold_reason = NULL` is cleared on alias reconciliation without revalidating
   the resulting identity against the schedule.

## Finding 7 — the stated deploy identity does not match the repository record

`artifacts/wow-engine/v17/engineering-fixes/LLP-BLOCKER-RESTORATION-2026-11-10.md`
records audited `main` at `4c8c1fb0d375b01352fcf35eea3a7be6a0999295`, PR `#2551`
head `b748e804a97c9e486e6ad481e7c9fd1d8f2fbeac`, and live Render deploy
`dep-dbotumss728c73am2c80` at `4c8c1fb0`. The proposed transaction stamps
`deploy_sha = '3c92f8c0'`, which matches neither. That memo also records the
disposition the proposed SQL contradicts: NBA "no complete registered production
publication bridge", NCAAB "model replay/calibration, fitted-model promotion and
publication bridge remain required", NHL "registered research bridge is not
certified artifact availability", NCAAF "complete governed
publication/failure-path/dynamic-bound chain remains required". Those are
missing fitted evidence and bridges, not missing boolean flags.

## Disposition

No promotion migration, verification query, or rollback script was created,
because each would target objects that do not exist and, where an analogous
object does exist, would violate a binding constraint. Creating them would
produce an artifact that appears implementation-ready and cannot execute.

The lanes remain fail-closed with native semantics (`MODEL_UNAVAILABLE`,
`MODEL_INPUTS_INSUFFICIENT`) and `probability_publishable=false`,
`can_execute=false`.

Genuine next steps, each requiring model-governance decisions that engineering
cannot self-approve:

1. For NBA: insert a `wow_team_event_specialist_certifications` row (or move
   `wow_basketball_team_event_certification.capability_status` to `CERTIFIED`)
   only after real fitted artifact + calibrator + independent replay evidence
   exists, including `artifact_sha256`, `calibration_sha256` and
   `independent_verification_status = 'PASS'`.
2. For NCAAB/NCAAF: the same, against `wow_ncaaf_fitted_model_artifacts` with
   `lifecycle_state` in `('PROSPECTIVE_CERTIFIED','CHAMPION')`, a calibrator
   version and `calibration_training_n > 0`.
3. For props: no promotion is possible at all while
   `wow_prop_fitted_never_publish` stands; that constraint is a governance
   decision, not an engineering toggle.
4. For NBA event identity: write verified rows through the existing alias
   gateway (or resolve the queue entry in `wow_unresolved_entity_aliases`) and
   prove identity against the schedule resolver.
5. For NHL: acquire goalie confirmation from the official source at ingestion
   time so the boundary's freshness and identity-receipt checks pass; do not
   pre-seed values.
