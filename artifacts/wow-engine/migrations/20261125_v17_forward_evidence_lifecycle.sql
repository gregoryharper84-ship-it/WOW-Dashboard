-- Class B (shadow evidence persistence) | Incident #1951 | migration candidate, NOT APPLIED
-- Shared prospective forward-evidence lifecycle for research candidates that
-- have no forward capture today (NCAAF first; NHL, NCAAB, soccer adapters).
--
-- registrations: one frozen EVALUATION identity per sport/league/market.
--   Refits never move it; challengers need their own CHALLENGER row.
-- captures: immutable pregame forecasts. created_at is server clock time and
--   must precede kickoff. Identity and probability shape are checked here,
--   not trusted from the caller.
-- settlements: immutable official outcomes, recorded only after kickoff.
-- wow_v17_forward_evidence_graded: latest pregame capture per registration
--   and event, joined to its settlement (VOID excluded).
--
-- Evidence only. No column here can publish, rank or execute:
-- probability_publishable, rank_eligible and can_execute are pinned false.
-- Nothing reads these tables into a terminal label. Apply only with Greg's
-- approval of the exact PR head.

create table if not exists public.wow_v17_forward_evidence_registrations (
  registration_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default clock_timestamp(),
  sport text not null check (sport ~ '^[A-Z0-9_]{2,20}$'),
  league text not null check (league ~ '^[A-Z0-9_]{2,40}$'),
  market_family text not null check (market_family ~ '^[A-Z0-9_]{2,40}$'),
  cohort_role text not null check (cohort_role in ('EVALUATION', 'CHALLENGER')),
  outcome_space text not null check (outcome_space in ('BINARY_HOME_AWAY', 'THREE_WAY_1X2')),
  model_family text not null check (length(model_family) between 3 and 120),
  candidate_table text not null check (candidate_table in ('wow_d1_candidate_artifacts')),
  candidate_id text not null check (length(candidate_id) between 1 and 80),
  model_artifact_version text not null check (length(model_artifact_version) between 3 and 200),
  artifact_checksum text not null check (artifact_checksum ~ '^[0-9a-f]{64}$'),
  training_dataset_hash text check (training_dataset_hash is null or training_dataset_hash ~ '^[0-9a-f]{64}$'),
  freeze_policy text not null check (freeze_policy = 'FIRST_ELIGIBLE_CANDIDATE_FROZEN_V1'),
  probability_publishable boolean not null default false check (probability_publishable is false),
  can_execute boolean not null default false check (can_execute is false)
);

create unique index if not exists wow_v17_forward_evidence_one_evaluation
  on public.wow_v17_forward_evidence_registrations (sport, league, market_family)
  where cohort_role = 'EVALUATION';
create unique index if not exists wow_v17_forward_evidence_registration_identity
  on public.wow_v17_forward_evidence_registrations (sport, league, market_family, artifact_checksum);

create table if not exists public.wow_v17_forward_evidence_captures (
  capture_id uuid primary key default gen_random_uuid(),
  registration_id uuid not null references public.wow_v17_forward_evidence_registrations (registration_id),
  created_at timestamptz not null default clock_timestamp(),
  sport text not null,
  league text not null,
  official_event_id text not null check (length(official_event_id) between 1 and 80),
  event_start_time_utc timestamptz not null,
  home_participant text not null check (length(home_participant) between 1 and 120),
  away_participant text not null check (length(away_participant) between 1 and 120),
  identity_provider text not null check (length(identity_provider) between 3 and 80),
  model_artifact_version text not null,
  artifact_checksum text not null check (artifact_checksum ~ '^[0-9a-f]{64}$'),
  probabilities jsonb not null,
  features jsonb not null check (jsonb_typeof(features) = 'object'),
  feature_payload_sha256 text not null check (feature_payload_sha256 ~ '^[0-9a-f]{64}$'),
  source_history_digest text not null check (source_history_digest ~ '^[0-9a-f]{64}$'),
  probability_publishable boolean not null default false check (probability_publishable is false),
  rank_eligible boolean not null default false check (rank_eligible is false),
  can_execute boolean not null default false check (can_execute is false),
  constraint wow_v17_forward_evidence_capture_pregame check (created_at < event_start_time_utc),
  constraint wow_v17_forward_evidence_capture_unique unique (registration_id, official_event_id, feature_payload_sha256)
);

create index if not exists wow_v17_forward_evidence_captures_event
  on public.wow_v17_forward_evidence_captures (registration_id, official_event_id, created_at desc);

create table if not exists public.wow_v17_forward_evidence_settlements (
  settlement_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default clock_timestamp(),
  sport text not null check (sport ~ '^[A-Z0-9_]{2,20}$'),
  league text not null check (league ~ '^[A-Z0-9_]{2,40}$'),
  official_event_id text not null check (length(official_event_id) between 1 and 80),
  event_start_time_utc timestamptz not null,
  outcome text not null check (outcome in ('HOME', 'AWAY', 'DRAW', 'VOID')),
  home_score numeric,
  away_score numeric,
  settlement_source text not null check (length(settlement_source) between 3 and 80),
  source_retrieved_at timestamptz not null,
  can_execute boolean not null default false check (can_execute is false),
  constraint wow_v17_forward_evidence_settlement_after_start check (created_at > event_start_time_utc),
  constraint wow_v17_forward_evidence_settlement_source_after_start check (source_retrieved_at > event_start_time_utc),
  constraint wow_v17_forward_evidence_settlement_unique unique (sport, league, official_event_id)
);

create or replace function public.wow_v17_forward_evidence_registration_guard()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  new.created_at := pg_catalog.clock_timestamp();
  return new;
end;
$function$;

create or replace function public.wow_v17_forward_evidence_capture_guard()
returns trigger
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_v17_forward_evidence_registrations%rowtype;
  v_keys text[];
  v_expected text[];
  v_sum double precision := 0;
  v_value double precision;
  v_key text;
begin
  -- Caller-supplied time and identity are never authoritative. BEFORE INSERT
  -- runs ahead of the pregame check constraint.
  new.created_at := pg_catalog.clock_timestamp();
  select * into r from public.wow_v17_forward_evidence_registrations
   where registration_id = new.registration_id;
  if not found then
    raise exception 'FORWARD_EVIDENCE_REGISTRATION_NOT_FOUND';
  end if;
  if new.artifact_checksum is distinct from r.artifact_checksum
     or new.model_artifact_version is distinct from r.model_artifact_version then
    raise exception 'FORWARD_EVIDENCE_CAPTURE_IDENTITY_MISMATCH';
  end if;
  new.sport := r.sport;
  new.league := r.league;
  if jsonb_typeof(new.probabilities) <> 'object' then
    raise exception 'FORWARD_EVIDENCE_PROBABILITIES_INVALID';
  end if;
  select array_agg(k order by k) into v_keys from jsonb_object_keys(new.probabilities) k;
  v_expected := case r.outcome_space
    when 'BINARY_HOME_AWAY' then array['AWAY', 'HOME']
    else array['AWAY', 'DRAW', 'HOME'] end;
  if v_keys is distinct from v_expected then
    raise exception 'FORWARD_EVIDENCE_PROBABILITY_KEYS_INVALID';
  end if;
  foreach v_key in array v_keys loop
    if jsonb_typeof(new.probabilities -> v_key) <> 'number' then
      raise exception 'FORWARD_EVIDENCE_PROBABILITIES_INVALID';
    end if;
    v_value := (new.probabilities ->> v_key)::double precision;
    if not (v_value > 0 and v_value < 1) then
      raise exception 'FORWARD_EVIDENCE_PROBABILITY_DOMAIN_INVALID';
    end if;
    v_sum := v_sum + v_value;
  end loop;
  if abs(v_sum - 1) > 1e-6 then
    raise exception 'FORWARD_EVIDENCE_PROBABILITIES_NOT_NORMALIZED';
  end if;
  return new;
end;
$function$;

create or replace function public.wow_v17_forward_evidence_settlement_guard()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  -- Settlement time is server clock time and must be after kickoff.
  new.created_at := pg_catalog.clock_timestamp();
  return new;
end;
$function$;

drop trigger if exists trg_wow_v17_forward_evidence_registration_guard
  on public.wow_v17_forward_evidence_registrations;
create trigger trg_wow_v17_forward_evidence_registration_guard
  before insert on public.wow_v17_forward_evidence_registrations
  for each row execute function public.wow_v17_forward_evidence_registration_guard();

drop trigger if exists trg_wow_v17_forward_evidence_capture_guard
  on public.wow_v17_forward_evidence_captures;
create trigger trg_wow_v17_forward_evidence_capture_guard
  before insert on public.wow_v17_forward_evidence_captures
  for each row execute function public.wow_v17_forward_evidence_capture_guard();

drop trigger if exists trg_wow_v17_forward_evidence_settlement_guard
  on public.wow_v17_forward_evidence_settlements;
create trigger trg_wow_v17_forward_evidence_settlement_guard
  before insert on public.wow_v17_forward_evidence_settlements
  for each row execute function public.wow_v17_forward_evidence_settlement_guard();

drop trigger if exists trg_wow_v17_forward_evidence_registrations_immutable
  on public.wow_v17_forward_evidence_registrations;
create trigger trg_wow_v17_forward_evidence_registrations_immutable
  before update or delete on public.wow_v17_forward_evidence_registrations
  for each row execute function public.wow_reject_immutable_mutation();

drop trigger if exists trg_wow_v17_forward_evidence_captures_immutable
  on public.wow_v17_forward_evidence_captures;
create trigger trg_wow_v17_forward_evidence_captures_immutable
  before update or delete on public.wow_v17_forward_evidence_captures
  for each row execute function public.wow_reject_immutable_mutation();

drop trigger if exists trg_wow_v17_forward_evidence_settlements_immutable
  on public.wow_v17_forward_evidence_settlements;
create trigger trg_wow_v17_forward_evidence_settlements_immutable
  before update or delete on public.wow_v17_forward_evidence_settlements
  for each row execute function public.wow_reject_immutable_mutation();

create or replace view public.wow_v17_forward_evidence_graded
with (security_invoker = true) as
with latest as (
  select distinct on (c.registration_id, c.official_event_id) c.*
    from public.wow_v17_forward_evidence_captures c
   where c.created_at < c.event_start_time_utc
   order by c.registration_id, c.official_event_id, c.created_at desc, c.capture_id desc
)
select
  l.registration_id,
  r.sport,
  r.league,
  r.market_family,
  r.cohort_role,
  r.outcome_space,
  r.model_family,
  r.model_artifact_version,
  r.artifact_checksum,
  l.capture_id,
  l.official_event_id,
  l.event_start_time_utc,
  l.created_at as prediction_created_at,
  l.probabilities,
  s.settlement_id,
  s.outcome,
  s.settlement_source,
  (l.probabilities ->> s.outcome)::double precision as outcome_probability,
  case r.outcome_space
    when 'BINARY_HOME_AWAY' then
      power((l.probabilities ->> 'HOME')::double precision - (s.outcome = 'HOME')::int, 2)
    else (
      select sum(power((e.value #>> '{}')::double precision - (e.key = s.outcome)::int, 2))
        from jsonb_each(l.probabilities) e)
  end as brier,
  -ln(greatest((l.probabilities ->> s.outcome)::double precision, 1e-15)) as log_loss,
  false as probability_publishable,
  false as can_execute
from latest l
join public.wow_v17_forward_evidence_registrations r on r.registration_id = l.registration_id
join public.wow_v17_forward_evidence_settlements s
  on s.sport = r.sport and s.league = r.league and s.official_event_id = l.official_event_id
where s.outcome <> 'VOID'
  and l.artifact_checksum = r.artifact_checksum
  and not (r.outcome_space = 'BINARY_HOME_AWAY' and s.outcome = 'DRAW');

alter table public.wow_v17_forward_evidence_registrations enable row level security;
alter table public.wow_v17_forward_evidence_captures enable row level security;
alter table public.wow_v17_forward_evidence_settlements enable row level security;

revoke all on table public.wow_v17_forward_evidence_registrations from public, anon, authenticated;
revoke all on table public.wow_v17_forward_evidence_captures from public, anon, authenticated;
revoke all on table public.wow_v17_forward_evidence_settlements from public, anon, authenticated;
revoke all on table public.wow_v17_forward_evidence_graded from public, anon, authenticated;
revoke update, delete, truncate, references, trigger
  on table public.wow_v17_forward_evidence_registrations from service_role;
revoke update, delete, truncate, references, trigger
  on table public.wow_v17_forward_evidence_captures from service_role;
revoke update, delete, truncate, references, trigger
  on table public.wow_v17_forward_evidence_settlements from service_role;
grant select, insert on table public.wow_v17_forward_evidence_registrations to service_role;
grant select, insert on table public.wow_v17_forward_evidence_captures to service_role;
grant select, insert on table public.wow_v17_forward_evidence_settlements to service_role;
grant select on table public.wow_v17_forward_evidence_graded to service_role;

revoke all on function public.wow_v17_forward_evidence_registration_guard() from public, anon, authenticated;
revoke all on function public.wow_v17_forward_evidence_capture_guard() from public, anon, authenticated;
revoke all on function public.wow_v17_forward_evidence_settlement_guard() from public, anon, authenticated;
