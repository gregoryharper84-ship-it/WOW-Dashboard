-- Class B exact-once persistence guard for governed NFL sporting predictions.
-- Historical immutable rows remain untouched; only new rows carry the key.
-- Probability math, calibration, qualification, and can_execute=false are unchanged.

alter table public.wow_nfl_event_predictions
  add column if not exists prediction_identity_key text;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'wow_nfl_event_prediction_identity_key_format'
  ) then
    alter table public.wow_nfl_event_predictions
      add constraint wow_nfl_event_prediction_identity_key_format
      check (
        prediction_identity_key is null
        or prediction_identity_key ~ '^[0-9a-f]{64}$'
      );
  end if;
end
$$;

create unique index if not exists wow_nfl_event_predictions_identity_key_uq
  on public.wow_nfl_event_predictions (prediction_identity_key)
  where prediction_identity_key is not null;

comment on column public.wow_nfl_event_predictions.prediction_identity_key is
  'Deterministic V17 exact-once sporting prediction identity for new NFL rows. Historical rows may remain null. Never grants execution authority.';
