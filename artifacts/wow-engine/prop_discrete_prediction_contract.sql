-- WOW_PROP_FITTED_MODEL_V1 prediction-ledger extension.
-- Backward compatible: existing event/legacy prop rows remain valid. The new
-- fields are mandatory only when market_type = 'PROP_DISCRETE_PMF'.
-- No execution authority or public table access is introduced here.

alter table public.wow_predictions
    add column if not exists model_provider_identity text,
    add column if not exists model_family text,
    add column if not exists model_artifact_version text,
    add column if not exists model_artifact_checksum text,
    add column if not exists model_bundle_fingerprint text,
    add column if not exists model_artifact_lifecycle_state text,
    add column if not exists feature_schema_version text,
    add column if not exists feature_transform_version text,
    add column if not exists feature_snapshot_hash text,
    add column if not exists training_dataset_hash text,
    add column if not exists training_code_sha text,
    add column if not exists specialist_version text,
    add column if not exists certification_id text,
    add column if not exists distribution_type text,
    add column if not exists probability_more numeric,
    add column if not exists probability_less numeric,
    add column if not exists push_probability numeric;

-- Prevent a generic fitted-prop row from being persisted with legacy-shaped or
-- partial provenance. All three line outcomes are unconditional and must
-- normalize while preserving material push probability.
do $$
begin
    if not exists (
        select 1
          from pg_constraint
         where conname = 'chk_prop_discrete_prediction_contract'
           and conrelid = 'public.wow_predictions'::regclass
    ) then
        alter table public.wow_predictions
            add constraint chk_prop_discrete_prediction_contract
            check (
                market_type <> 'PROP_DISCRETE_PMF'
                or (
                    model_provider_identity = 'WOW_PROP_FITTED_MODEL_V1'
                    and model_family is not null and btrim(model_family) <> ''
                    and model_artifact_version is not null and btrim(model_artifact_version) <> ''
                    and model_artifact_checksum is not null and btrim(model_artifact_checksum) <> ''
                    and model_bundle_fingerprint is not null and btrim(model_bundle_fingerprint) <> ''
                    and model_artifact_lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
                    and feature_schema_version is not null and btrim(feature_schema_version) <> ''
                    and feature_transform_version is not null and btrim(feature_transform_version) <> ''
                    and feature_snapshot_hash is not null and btrim(feature_snapshot_hash) <> ''
                    and training_dataset_hash is not null and btrim(training_dataset_hash) <> ''
                    and training_code_sha is not null and btrim(training_code_sha) <> ''
                    and specialist_version is not null and btrim(specialist_version) <> ''
                    and certification_id is not null and btrim(certification_id) <> ''
                    and distribution_type = 'DISCRETE_PMF'
                    and effective_sample_size is not null and effective_sample_size > 0
                    and probability_more is not null and probability_more >= 0 and probability_more <= 1
                    and probability_less is not null and probability_less >= 0 and probability_less <= 1
                    and push_probability is not null and push_probability >= 0 and push_probability <= 1
                    and abs((probability_more + probability_less + push_probability) - 1.0) <= 0.000000001
                )
            );
    end if;
end
$$;

comment on column public.wow_predictions.model_provider_identity is
    'Provider identity for generic fitted prop rows; WOW_PROP_FITTED_MODEL_V1 when market_type=PROP_DISCRETE_PMF.';
comment on column public.wow_predictions.model_bundle_fingerprint is
    'Immutable fingerprint binding model/calibrator/feature/specialist/certification artifacts.';
comment on column public.wow_predictions.push_probability is
    'Unconditional exact-line push probability derived from the direction-free discrete PMF.';
