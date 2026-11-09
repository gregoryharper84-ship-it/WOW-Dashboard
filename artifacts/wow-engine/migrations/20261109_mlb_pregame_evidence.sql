-- WOW V17 source migration: event-specific prelock evidence for the MLB
-- team/event lane, plus the exact-version calibrator binding it must share.
--
-- Read-only contract: v17/model_evidence_registry_readers.py reads these tables
-- through the declared MLB lane. Nothing here certifies a model, promotes an
-- artifact, grants a role or makes a probability publishable. A row is
-- evidence only: the boundary still requires a CERTIFIED/PASS receipt in
-- wow_team_event_specialist_certifications for the exact sport/model_version/
-- artifact_sha256/feature_schema_version before any verification flag is
-- trusted, and the contract itself never raises probability_publishable.
--
-- Why separate tables:
--   * wow_nfl_pregame_feature_rows is a gameday training matrix: it has no
--     tipoff attestation, no source snapshot identity and no payload hash, so
--     it cannot satisfy the per-event pregame contract.
--   * wow_calibrators stores no calibrator checksum, cohort hash, holdout
--     evidence hash or holdout overlap, so it cannot bind an exact-version
--     calibrator either. Guessing those values would be evidence fabrication.
--
-- One current prelock row per (official_event_id, model_artifact_version,
-- feature_schema_version). A material pregame change supersedes the prior row
-- (is_current=false) and inserts a new one; payload columns are never rewritten,
-- and the reader only ever sees the single current row so a stale snapshot can
-- never be silently substituted for the fresh one.

create table if not exists public.wow_mlb_event_pregame_feature_rows (
    id bigserial primary key,
    sport text not null default 'MLB',
    model_family text not null,
    model_artifact_version text not null,
    feature_schema_version text not null,
    model_artifact_checksum text not null,
    official_event_id text not null,
    event_start_time timestamptz not null,
    event_status text not null default 'SCHEDULED',
    source_snapshot_id text not null,
    source_snapshot_timestamp timestamptz not null,
    source_payload_sha256 text not null,
    feature_snapshot_id text not null,
    feature_snapshot_timestamp timestamptz not null,
    feature_snapshot_sha256 text not null,
    is_current boolean not null default true,
    created_at timestamptz not null default now(),
    constraint chk_mlb_pregame_sport check (sport = 'MLB'),
    constraint chk_mlb_pregame_status check (
        event_status in ('SCHEDULED', 'POSTPONED', 'CANCELLED', 'SUSPENDED', 'FINAL')
    ),
    constraint chk_mlb_pregame_artifact_sha check (
        model_artifact_checksum ~ '^[0-9a-f]{64}$'
    ),
    constraint chk_mlb_pregame_source_sha check (
        source_payload_sha256 ~ '^[0-9a-f]{64}$'
    ),
    constraint chk_mlb_pregame_feature_sha check (
        feature_snapshot_sha256 ~ '^[0-9a-f]{64}$'
    ),
    -- Source captured before features, features frozen before the official
    -- start. A midnight-UTC "as_of" or a post-start backfill cannot pass this.
    constraint chk_mlb_pregame_prelock check (
        source_snapshot_timestamp <= feature_snapshot_timestamp
        and feature_snapshot_timestamp < event_start_time
    )
);

create unique index if not exists ux_mlb_pregame_current_event_version
    on public.wow_mlb_event_pregame_feature_rows (
        official_event_id, model_artifact_version, feature_schema_version
    )
    where is_current;

create index if not exists ix_mlb_pregame_event
    on public.wow_mlb_event_pregame_feature_rows (official_event_id);

create table if not exists public.wow_mlb_event_calibrator_evidence (
    id bigserial primary key,
    sport text not null default 'MLB',
    model_family text not null,
    model_artifact_version text not null,
    feature_schema_version text not null,
    model_artifact_checksum text not null,
    calibrator_id text not null unique,
    calibrator_checksum text not null,
    calibration_method text not null,
    bounds_method_version text not null,
    calibration_cohort_sha256 text not null,
    holdout_evidence_sha256 text not null,
    calibration_training_n bigint not null,
    holdout_overlap_rows bigint not null default 0,
    brier double precision not null,
    log_loss double precision not null,
    calibration_bias double precision not null,
    calibration_fit_end timestamptz not null,
    created_at timestamptz not null default now(),
    constraint chk_mlb_calibrator_sport check (sport = 'MLB'),
    constraint chk_mlb_calibrator_checksums check (
        model_artifact_checksum ~ '^[0-9a-f]{64}$'
        and calibrator_checksum ~ '^[0-9a-f]{64}$'
        and calibration_cohort_sha256 ~ '^[0-9a-f]{64}$'
        and holdout_evidence_sha256 ~ '^[0-9a-f]{64}$'
    ),
    constraint chk_mlb_calibrator_training_n check (calibration_training_n > 0),
    -- Disjoint chronological holdout: a positive overlap is leakage, not proof.
    constraint chk_mlb_calibrator_overlap check (holdout_overlap_rows = 0),
    constraint chk_mlb_calibrator_brier check (brier >= 0 and brier <= 1),
    constraint chk_mlb_calibrator_log_loss check (log_loss >= 0),
    constraint chk_mlb_calibrator_bias check (
        calibration_bias >= -1 and calibration_bias <= 1
    )
);

create index if not exists ix_mlb_calibrator_version
    on public.wow_mlb_event_calibrator_evidence (
        model_artifact_version, feature_schema_version
    );

-- Governed server-side readers only. No policy and no grant: anon and
-- authenticated roles gain nothing from these tables.
alter table public.wow_mlb_event_pregame_feature_rows enable row level security;
alter table public.wow_mlb_event_calibrator_evidence enable row level security;

comment on table public.wow_mlb_event_pregame_feature_rows is
    'Immutable event-specific MLB prelock corpus; one current row per event and fitted version. Evidence only, never certification or publication.';
comment on column public.wow_mlb_event_pregame_feature_rows.is_current is
    'Exactly one current snapshot per event/version; superseded rows are retained for audit, never deleted.';
comment on table public.wow_mlb_event_calibrator_evidence is
    'Exact-version MLB calibrator evidence bound to the artifact checksum; a PLATT/ISOTONIC label alone is not calibrated-lower-bound proof.';
