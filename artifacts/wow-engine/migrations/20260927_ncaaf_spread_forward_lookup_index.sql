-- V17 NCAAF spread-forward cold-build access path.
--
-- Class B serving reliability only. This index does not change sporting-model
-- mathematics, training rows, fitted artifacts, calibration, bounds,
-- certification, publication, ranking, terminal authority, or can_execute.
-- It matches the exact immutable feature-ledger predicate used by
-- _load_ncaaf_persisted_feature_rows and preserves official_event_id ordering.

create index if not exists wow_d1_training_rows_ncaaf_spread_lookup_idx
    on public.wow_d1_training_rows (
        sport,
        model_family,
        feature_schema_version,
        official_event_id
    );
