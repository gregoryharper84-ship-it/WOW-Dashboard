-- V17 NBA candidate forward shadow (research-only evidence lane, Class B).
-- Scores the registered SHADOW candidate before tipoff so prospective evidence
-- can accrue without certification. Never publishes, ranks, certifies or
-- executes. Adds new objects only; no existing table, view or flag changes.

create table if not exists public.wow_nba_candidate_forward_shadow (
  capture_id uuid primary key default gen_random_uuid(),
  schema_version text not null
    check (schema_version = 'NBA_CANDIDATE_FORWARD_SHADOW_V1'),
  official_event_id text not null check (official_event_id ~ '^espn-[0-9]+$'),
  event_game_date date not null,
  event_start_time_utc timestamptz not null,
  home_team_id text not null check (home_team_id ~ '^espn-[0-9]+$'),
  away_team_id text not null check (away_team_id ~ '^espn-[0-9]+$'),
  model_artifact_version text not null,
  model_artifact_checksum text not null check (model_artifact_checksum ~ '^[0-9a-f]{64}$'),
  calibrator_version text not null,
  calibration_method text not null,
  candidate_capability_status text not null,
  raw_home_win_probability double precision not null
    check (raw_home_win_probability > 0 and raw_home_win_probability < 1),
  home_win_probability double precision not null
    check (home_win_probability > 0 and home_win_probability < 1),
  feature_schema_version text not null,
  features jsonb not null check (jsonb_typeof(features) = 'object'),
  feature_payload_sha256 text not null check (feature_payload_sha256 ~ '^[0-9a-f]{64}$'),
  dataset_id text not null check (dataset_id ~ '^[0-9a-f]{64}$'),
  history_max_game_date date,
  source_snapshot_id text not null check (source_snapshot_id ~ '^[0-9a-f]{64}$'),
  source_observed_at timestamptz not null,
  knowledge_cutoff timestamptz not null,
  probability_publishable boolean not null default false check (probability_publishable = false),
  rank_eligible boolean not null default false check (rank_eligible = false),
  can_execute boolean not null default false check (can_execute = false),
  created_at timestamptz not null default now(),
  constraint wow_nba_candidate_forward_shadow_teams check (home_team_id <> away_team_id),
  constraint wow_nba_candidate_forward_shadow_order
    check (source_observed_at <= knowledge_cutoff and knowledge_cutoff < event_start_time_utc),
  constraint wow_nba_candidate_forward_shadow_identity
    unique (official_event_id, model_artifact_version, feature_payload_sha256, dataset_id)
);

create index if not exists wow_nba_candidate_forward_shadow_event
  on public.wow_nba_candidate_forward_shadow(official_event_id, created_at);

alter table public.wow_nba_candidate_forward_shadow enable row level security;
revoke all on public.wow_nba_candidate_forward_shadow from public, anon, authenticated;
grant select, insert on public.wow_nba_candidate_forward_shadow to service_role;

-- Server clock owns created_at; a row at or after tipoff, or whose knowledge
-- cutoff is later than the insert instant, is rejected. Append-only.
create or replace function public.wow_v17_nba_candidate_forward_shadow_guard()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  if tg_op = 'INSERT' then
    new.created_at := statement_timestamp();
    if new.created_at >= new.event_start_time_utc then
      raise exception 'NBA_CANDIDATE_CAPTURE_NOT_PRE_TIPOFF';
    end if;
    if new.knowledge_cutoff > new.created_at then
      raise exception 'NBA_CANDIDATE_CAPTURE_CUTOFF_AFTER_INSERT';
    end if;
    return new;
  end if;
  raise exception 'NBA_CANDIDATE_FORWARD_SHADOW_IMMUTABLE';
end;
$function$;

revoke all on function public.wow_v17_nba_candidate_forward_shadow_guard() from public, anon, authenticated;

drop trigger if exists wow_nba_candidate_forward_shadow_insert_guard
  on public.wow_nba_candidate_forward_shadow;
create trigger wow_nba_candidate_forward_shadow_insert_guard
before insert on public.wow_nba_candidate_forward_shadow
for each row execute function public.wow_v17_nba_candidate_forward_shadow_guard();

drop trigger if exists wow_nba_candidate_forward_shadow_immutable
  on public.wow_nba_candidate_forward_shadow;
create trigger wow_nba_candidate_forward_shadow_immutable
before update or delete on public.wow_nba_candidate_forward_shadow
for each row execute function public.wow_v17_nba_candidate_forward_shadow_guard();

drop trigger if exists wow_nba_candidate_forward_shadow_no_truncate
  on public.wow_nba_candidate_forward_shadow;
create trigger wow_nba_candidate_forward_shadow_no_truncate
before truncate on public.wow_nba_candidate_forward_shadow
for each statement execute function public.wow_v17_nba_candidate_forward_shadow_guard();

-- One grade per event: the latest pre-tipoff capture (ties by capture_id),
-- joined to the persisted official final with matching teams. Fresh join, so a
-- changed settlement or a later valid capture changes the grade immediately.
create or replace view public.wow_nba_candidate_forward_verified_grades
with (security_invoker = true, security_barrier = true)
as
select s.official_event_id, s.capture_id, s.created_at, s.event_start_time_utc,
       s.model_artifact_version, s.model_artifact_checksum, s.calibrator_version,
       s.home_win_probability,
       case when o.home_win then 1 else 0 end as outcome,
       power(s.home_win_probability - case when o.home_win then 1 else 0 end, 2.0::double precision) as brier,
       case when o.home_win then -ln(s.home_win_probability)
            else -ln(1.0 - s.home_win_probability) end as log_loss,
       'LATEST_VALID_PRETIPOFF_V1'::text as selection_policy,
       s.can_execute
from public.wow_nba_candidate_forward_shadow as s
join public.wow_nba_training_games as o
  on o.game_id = s.official_event_id
where s.schema_version = 'NBA_CANDIDATE_FORWARD_SHADOW_V1'
  and s.can_execute is false
  and s.created_at < s.event_start_time_utc
  and (o.scheduled_start_utc is null or s.created_at < o.scheduled_start_utc)
  and o.settled is true
  and o.home_team_id = s.home_team_id
  and o.away_team_id = s.away_team_id
  and o.home_score >= 0 and o.away_score >= 0
  and o.home_score <> o.away_score
  and o.home_win = (o.home_score > o.away_score)
  and not exists (
    select 1 from public.wow_nba_candidate_forward_shadow as q
    where q.official_event_id = s.official_event_id
      and q.schema_version = s.schema_version
      and q.can_execute is false
      and q.created_at < q.event_start_time_utc
      and (o.scheduled_start_utc is null or q.created_at < o.scheduled_start_utc)
      and (q.created_at > s.created_at or (q.created_at = s.created_at and q.capture_id > s.capture_id))
  );

revoke all on public.wow_nba_candidate_forward_verified_grades from public, anon, authenticated;
grant select on public.wow_nba_candidate_forward_verified_grades to service_role;
