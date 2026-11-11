-- Additive, nullable: persist the validated tz-aware scheduled start (tip-off) instant of each
-- settled basketball training game so pregame feature timing can be audited against an
-- authoritative cutoff (#2536). No backfill here; NULL means "tip-off not recorded".
-- Must be applied BEFORE deploying code that writes the column. No model/probability change;
-- can_execute stays false.
alter table public.wow_nba_training_games  add column if not exists scheduled_start_utc timestamptz;
alter table public.wow_wnba_training_games add column if not exists scheduled_start_utc timestamptz;
comment on column public.wow_nba_training_games.scheduled_start_utc is
  'Source-provided scheduled start instant (UTC), validated tz-aware; NULL when not recorded.';
