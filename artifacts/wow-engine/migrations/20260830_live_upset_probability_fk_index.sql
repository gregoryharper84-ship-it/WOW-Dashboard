-- Cover the immutable live-prediction source-snapshot foreign key.
create index if not exists wow_live_prediction_source_snapshot_idx
    on public.wow_live_probability_predictions(source_snapshot_id);
