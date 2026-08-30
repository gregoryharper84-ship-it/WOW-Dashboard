-- Bind every model-sensitive live-state snapshot to the exact certified LIVE artifact.
alter table public.wow_live_state_snapshots
    add column if not exists feature_model_family text not null,
    add column if not exists feature_model_artifact_version text not null,
    add column if not exists feature_schema_version text not null,
    add column if not exists feature_artifact_checksum text not null;

comment on column public.wow_live_state_snapshots.feature_model_family is 'Exact live model family that produced the derived state features.';
comment on column public.wow_live_state_snapshots.feature_model_artifact_version is 'Exact certified LIVE artifact version that produced the derived state features.';
comment on column public.wow_live_state_snapshots.feature_schema_version is 'Feature schema consumed by the serving LIVE artifact.';
comment on column public.wow_live_state_snapshots.feature_artifact_checksum is 'Checksum binding derived state features to the certified serving artifact.';
