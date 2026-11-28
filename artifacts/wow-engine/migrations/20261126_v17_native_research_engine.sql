-- Class B (evidence persistence) | WOW Native Research Engine V1 | migration candidate, NOT APPLIED
-- Evidence-only research ledger and native knowledge index. Nothing here can
-- hold a probability, a calibration, an approval or execution authority.
--
-- wow_nre_research_tasks: one row per blocker-derived research task (mutable
--   status; identity, inert flags and the originating blocker are guarded).
-- wow_nre_research_attempts: immutable per-run receipts (strategies, outcome).
-- wow_nre_documents: immutable acquired documents. Bodies only where source
--   terms permit storage; every row carries its acquisition receipt digest.
-- wow_nre_source_rights: immutable source-rights evidence for independent
--   review. production_use_authorized can never be true here.
-- wow_nre_findings: immutable research findings against the originating task.
-- wow_nre_search(): PostgreSQL full-text search with structured filters.
-- wow_nre_claim_task(): single-writer lease per task.

create table if not exists public.wow_nre_research_tasks (
  task_id text primary key check (task_id ~ '^[0-9a-f]{64}$'),
  sport text not null,
  league text,
  season text,
  market_route text not null,
  specialist_id text not null,
  candidate_id text,
  blocker_code text not null,
  certification_status text not null,
  certification_blockers jsonb not null default '[]'::jsonb,
  missing_evidence_field text not null,
  research_objective text not null,
  required_source_type text not null,
  permitted_methods jsonb not null default '[]'::jsonb,
  priority smallint not null check (priority between 1 and 5),
  blocker_fingerprint text not null check (blocker_fingerprint ~ '^[0-9a-f]{64}$'),
  status text not null check (status in ('OPEN', 'HELD', 'NOT_RESEARCHABLE')),
  outcome text not null check (outcome in (
    'NOT_ATTEMPTED', 'ACQUISITION_FAILED', 'BUDGET_EXHAUSTED', 'NO_SOURCE_FOUND', 'CONFLICTING_EVIDENCE',
    'CANDIDATE_FOUND_UNVERIFIED', 'CANDIDATE_VERIFIED_RIGHTS_REVIEW_REQUIRED',
    'CANDIDATE_VERIFIED_PENDING_INDEPENDENT_REVIEW')),
  progress_stage smallint not null default 0 check (progress_stage between 0 and 4),
  best_progress_stage smallint not null default 0 check (best_progress_stage between 0 and 4),
  evidence_refs jsonb not null default '[]'::jsonb,
  remaining_blockers jsonb not null default '[]'::jsonb,
  attempt_count integer not null default 0 check (attempt_count >= 0),
  last_attempt_at timestamptz,
  next_eligible_at timestamptz,
  lease_owner text,
  lease_expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  evidence_only boolean not null default true check (evidence_only),
  prediction_authority boolean not null default false check (not prediction_authority),
  can_execute boolean not null default false check (not can_execute),
  -- Research never erases the blocker it was created for.
  check (status = 'NOT_RESEARCHABLE' or outcome = 'NOT_ATTEMPTED' or remaining_blockers ? blocker_code)
);

create table if not exists public.wow_nre_research_attempts (
  attempt_id text primary key check (attempt_id ~ '^[0-9a-f]{64}$'),
  task_id text not null references public.wow_nre_research_tasks(task_id),
  required_source_type text not null,
  schedule_reason text not null check (schedule_reason in (
    'NEW_TASK', 'NEW_EVIDENCE', 'FRESHNESS_TRIGGER', 'AUTHORIZED_RETRY')),
  outcome text not null,
  progress_stage smallint not null check (progress_stage between 0 and 4),
  strategies jsonb not null default '[]'::jsonb,
  started_at timestamptz not null,
  finished_at timestamptz not null check (finished_at >= started_at),
  worker_owner text not null,
  receipt jsonb not null,
  receipt_sha256 text not null check (receipt_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  evidence_only boolean not null default true check (evidence_only),
  prediction_authority boolean not null default false check (not prediction_authority),
  production_use_authorized boolean not null default false check (not production_use_authorized),
  can_execute boolean not null default false check (not can_execute),
  check (receipt ? 'spend_usd' and (receipt ->> 'spend_usd')::numeric = 0)
);

create table if not exists public.wow_nre_source_rights (
  rights_id text primary key check (rights_id ~ '^[0-9a-f]{64}$'),
  source_id text not null,
  assessed_at timestamptz not null,
  terms_url text,
  terms_sha256 text,
  software_license_spdx text,
  data_license_label text not null,
  software_license_not_data_license boolean not null,
  research_access text not null,
  storage_permitted text not null,
  automated_acquisition_permitted text not null,
  model_training_permitted text not null,
  production_use_permitted text not null,
  attribution_required boolean not null,
  commercial_restrictions jsonb not null default '[]'::jsonb,
  upstream_sources jsonb not null default '[]'::jsonb,
  unresolved_upstream_rights boolean not null,
  evidence jsonb not null default '[]'::jsonb,
  review_status text not null default 'PENDING_INDEPENDENT_REVIEW'
    check (review_status = 'PENDING_INDEPENDENT_REVIEW'),
  -- Research may identify a source; it can never authorize production use.
  production_use_authorized boolean not null default false check (not production_use_authorized),
  created_at timestamptz not null default now()
);

create table if not exists public.wow_nre_documents (
  document_id text primary key check (document_id ~ '^[0-9a-f]{64}$'),
  canonical_url text not null check (canonical_url like 'https://%'),
  domain text not null,
  source_id text not null,
  content_sha256 text,
  content_type text,
  title text not null default '',
  body text,
  excerpt text not null default '' check (char_length(excerpt) <= 2000),
  keywords text not null default '',
  etag text,
  last_modified text,
  published_at timestamptz,
  observed_at timestamptz,
  retrieved_at timestamptz not null,
  sport text,
  league text,
  season text,
  teams text[] not null default '{}',
  players text[] not null default '{}',
  event_id text,
  market text,
  evidence_type text not null check (evidence_type in (
    'RESULTS_DATASET', 'SOURCE_TERMS', 'SCIENTIFIC_REFERENCE', 'NEWS_ARTICLE', 'FIXTURE_DATASET')),
  source_quality text not null,
  freshness text not null,
  rights_status text not null check (rights_status in (
    'STORAGE_PERMITTED', 'METADATA_ONLY_NON_COMMERCIAL', 'METADATA_ONLY_RIGHTS_UNRESOLVED')),
  task_id text references public.wow_nre_research_tasks(task_id),
  acquisition_receipt jsonb not null,
  acquisition_receipt_sha256 text not null check (acquisition_receipt_sha256 ~ '^[0-9a-f]{64}$'),
  search_vector tsvector generated always as (
    setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A')
    || setweight(to_tsvector('english'::regconfig, coalesce(keywords, '')), 'A')
    || setweight(to_tsvector('english'::regconfig, coalesce(excerpt, '')), 'B')
    || setweight(to_tsvector('english'::regconfig, coalesce(left(body, 200000), '')), 'C')) stored,
  created_at timestamptz not null default now(),
  -- Content is retained only where storage and reuse are permitted.
  check (body is null or rights_status = 'STORAGE_PERMITTED')
);

create table if not exists public.wow_nre_findings (
  finding_id text primary key check (finding_id ~ '^[0-9a-f]{64}$'),
  task_id text not null references public.wow_nre_research_tasks(task_id),
  attempt_id text not null references public.wow_nre_research_attempts(attempt_id),
  source_id text not null,
  finding_type text not null check (finding_type in (
    'CANDIDATE_SOURCE', 'INCUMBENT_SOURCE_STATUS', 'NEGATIVE_RESULT', 'SCIENTIFIC_REFERENCE')),
  outcome text not null,
  freshness text,
  conflict_status text,
  verification jsonb not null default '{}'::jsonb,
  rights_id text references public.wow_nre_source_rights(rights_id),
  document_ids jsonb not null default '[]'::jsonb,
  sport text not null,
  league text,
  season text,
  required_source_type text not null,
  -- Unverified, stale or conflicting evidence can never be handed off.
  accepted_for_handoff boolean not null default false
    check (not accepted_for_handoff or (freshness = 'FRESH' and conflict_status = 'CONSISTENT')),
  created_at timestamptz not null default now(),
  evidence_only boolean not null default true check (evidence_only),
  prediction_authority boolean not null default false check (not prediction_authority),
  production_use_authorized boolean not null default false check (not production_use_authorized),
  can_execute boolean not null default false check (not can_execute)
);

create index if not exists wow_nre_documents_search_idx on public.wow_nre_documents using gin (search_vector);
create index if not exists wow_nre_documents_filter_idx
  on public.wow_nre_documents (sport, league, season, evidence_type, retrieved_at desc);
create index if not exists wow_nre_documents_teams_idx on public.wow_nre_documents using gin (teams);
create index if not exists wow_nre_documents_url_idx on public.wow_nre_documents (canonical_url, retrieved_at desc);
create index if not exists wow_nre_attempts_task_idx on public.wow_nre_research_attempts (task_id, finished_at);
create index if not exists wow_nre_findings_lookup_idx
  on public.wow_nre_findings (sport, required_source_type, created_at desc);
create index if not exists wow_nre_rights_source_idx on public.wow_nre_source_rights (source_id, created_at desc);

-- Task guard: identity, originating blocker and inert flags are fixed;
-- best progress never goes backwards; server clock owns updated_at.
create or replace function public.wow_nre_task_guard()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  if tg_op = 'UPDATE' then
    if new.task_id is distinct from old.task_id or new.sport is distinct from old.sport
       or new.league is distinct from old.league or new.season is distinct from old.season
       or new.market_route is distinct from old.market_route or new.specialist_id is distinct from old.specialist_id
       or new.candidate_id is distinct from old.candidate_id or new.blocker_code is distinct from old.blocker_code
       or new.missing_evidence_field is distinct from old.missing_evidence_field
       or new.created_at is distinct from old.created_at then
      raise exception 'NRE_TASK_IDENTITY_IMMUTABLE' using errcode = '23514';
    end if;
    if new.best_progress_stage < old.best_progress_stage then
      raise exception 'NRE_TASK_PROGRESS_REGRESSION' using errcode = '23514';
    end if;
    if new.attempt_count < old.attempt_count then
      raise exception 'NRE_TASK_ATTEMPT_COUNT_REGRESSION' using errcode = '23514';
    end if;
  else
    new.created_at := pg_catalog.clock_timestamp();
  end if;
  new.updated_at := pg_catalog.clock_timestamp();
  return new;
end;
$function$;

create or replace function public.wow_nre_server_clock_guard()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  new.created_at := pg_catalog.clock_timestamp();
  return new;
end;
$function$;

drop trigger if exists trg_wow_nre_task_guard on public.wow_nre_research_tasks;
create trigger trg_wow_nre_task_guard
  before insert or update on public.wow_nre_research_tasks
  for each row execute function public.wow_nre_task_guard();

drop trigger if exists trg_wow_nre_tasks_no_delete on public.wow_nre_research_tasks;
create trigger trg_wow_nre_tasks_no_delete
  before delete on public.wow_nre_research_tasks
  for each row execute function public.wow_reject_immutable_mutation();

drop trigger if exists trg_wow_nre_attempts_clock on public.wow_nre_research_attempts;
create trigger trg_wow_nre_attempts_clock before insert on public.wow_nre_research_attempts
  for each row execute function public.wow_nre_server_clock_guard();
drop trigger if exists trg_wow_nre_attempts_immutable on public.wow_nre_research_attempts;
create trigger trg_wow_nre_attempts_immutable before update or delete on public.wow_nre_research_attempts
  for each row execute function public.wow_reject_immutable_mutation();

drop trigger if exists trg_wow_nre_documents_clock on public.wow_nre_documents;
create trigger trg_wow_nre_documents_clock before insert on public.wow_nre_documents
  for each row execute function public.wow_nre_server_clock_guard();
drop trigger if exists trg_wow_nre_documents_immutable on public.wow_nre_documents;
create trigger trg_wow_nre_documents_immutable before update or delete on public.wow_nre_documents
  for each row execute function public.wow_reject_immutable_mutation();

drop trigger if exists trg_wow_nre_rights_clock on public.wow_nre_source_rights;
create trigger trg_wow_nre_rights_clock before insert on public.wow_nre_source_rights
  for each row execute function public.wow_nre_server_clock_guard();
drop trigger if exists trg_wow_nre_rights_immutable on public.wow_nre_source_rights;
create trigger trg_wow_nre_rights_immutable before update or delete on public.wow_nre_source_rights
  for each row execute function public.wow_reject_immutable_mutation();

drop trigger if exists trg_wow_nre_findings_clock on public.wow_nre_findings;
create trigger trg_wow_nre_findings_clock before insert on public.wow_nre_findings
  for each row execute function public.wow_nre_server_clock_guard();
drop trigger if exists trg_wow_nre_findings_immutable on public.wow_nre_findings;
create trigger trg_wow_nre_findings_immutable before update or delete on public.wow_nre_findings
  for each row execute function public.wow_reject_immutable_mutation();

-- Single-writer lease per task (mirrors the engineering writer-lease model).
create or replace function public.wow_nre_claim_task(p_task_id text, p_owner text, p_ttl_seconds integer)
returns boolean
language plpgsql
set search_path to ''
as $function$
begin
  if p_owner is null or btrim(p_owner) = '' or p_ttl_seconds is null
     or p_ttl_seconds < 30 or p_ttl_seconds > 3600 then
    raise exception 'NRE_LEASE_REQUEST_INVALID' using errcode = '22023';
  end if;
  update public.wow_nre_research_tasks
     set lease_owner = p_owner,
         lease_expires_at = pg_catalog.clock_timestamp() + pg_catalog.make_interval(secs => p_ttl_seconds)
   where task_id = p_task_id
     and (lease_owner is null or lease_expires_at is null
          or lease_expires_at < pg_catalog.clock_timestamp() or lease_owner = p_owner);
  return found;
end;
$function$;

-- Native knowledge search: full-text plus structured filters.
create or replace function public.wow_nre_search(
  p_query text,
  p_limit integer default 20,
  p_sport text default null,
  p_league text default null,
  p_season text default null,
  p_evidence_type text default null,
  p_rights_status text default null,
  p_source_quality text default null,
  p_research_outcome text default null,
  p_team text default null,
  p_player text default null,
  p_event_id text default null,
  p_market text default null,
  p_published_after timestamptz default null,
  p_observed_after timestamptz default null,
  p_retrieved_after timestamptz default null)
returns table (
  document_id text, canonical_url text, title text, excerpt text, source_id text, sport text, league text,
  season text, evidence_type text, source_quality text, rights_status text, freshness text,
  research_outcome text, published_at timestamptz, observed_at timestamptz, retrieved_at timestamptz,
  acquisition_receipt_sha256 text, rank real)
language sql
stable
set search_path to ''
as $function$
  select d.document_id, d.canonical_url, d.title, d.excerpt, d.source_id, d.sport, d.league, d.season,
         d.evidence_type, d.source_quality, d.rights_status, d.freshness, t.outcome, d.published_at,
         d.observed_at, d.retrieved_at, d.acquisition_receipt_sha256,
         pg_catalog.ts_rank_cd(d.search_vector, q.query) as rank
    from public.wow_nre_documents d
    left join public.wow_nre_research_tasks t on t.task_id = d.task_id
    cross join (select pg_catalog.websearch_to_tsquery('english'::regconfig, coalesce(p_query, '')) as query) q
   where (coalesce(btrim(p_query), '') = '' or d.search_vector operator(pg_catalog.@@) q.query)
     and (p_sport is null or d.sport = p_sport)
     and (p_league is null or d.league = p_league)
     and (p_season is null or d.season = p_season)
     and (p_evidence_type is null or d.evidence_type = p_evidence_type)
     and (p_rights_status is null or d.rights_status = p_rights_status)
     and (p_source_quality is null or d.source_quality = p_source_quality)
     and (p_research_outcome is null or t.outcome = p_research_outcome)
     and (p_team is null or p_team = any (d.teams))
     and (p_player is null or p_player = any (d.players))
     and (p_event_id is null or d.event_id = p_event_id)
     and (p_market is null or d.market = p_market)
     and (p_published_after is null or d.published_at >= p_published_after)
     and (p_observed_after is null or d.observed_at >= p_observed_after)
     and (p_retrieved_after is null or d.retrieved_at >= p_retrieved_after)
   order by rank desc, d.retrieved_at desc
   limit least(greatest(coalesce(p_limit, 20), 1), 100);
$function$;

alter table public.wow_nre_research_tasks enable row level security;
alter table public.wow_nre_research_attempts enable row level security;
alter table public.wow_nre_source_rights enable row level security;
alter table public.wow_nre_documents enable row level security;
alter table public.wow_nre_findings enable row level security;

revoke all on table public.wow_nre_research_tasks from public, anon, authenticated;
revoke all on table public.wow_nre_research_attempts from public, anon, authenticated;
revoke all on table public.wow_nre_source_rights from public, anon, authenticated;
revoke all on table public.wow_nre_documents from public, anon, authenticated;
revoke all on table public.wow_nre_findings from public, anon, authenticated;
revoke delete, truncate, references, trigger on table public.wow_nre_research_tasks from service_role;
revoke update, delete, truncate, references, trigger on table public.wow_nre_research_attempts from service_role;
revoke update, delete, truncate, references, trigger on table public.wow_nre_source_rights from service_role;
revoke update, delete, truncate, references, trigger on table public.wow_nre_documents from service_role;
revoke update, delete, truncate, references, trigger on table public.wow_nre_findings from service_role;
grant select, insert, update on table public.wow_nre_research_tasks to service_role;
grant select, insert on table public.wow_nre_research_attempts to service_role;
grant select, insert on table public.wow_nre_source_rights to service_role;
grant select, insert on table public.wow_nre_documents to service_role;
grant select, insert on table public.wow_nre_findings to service_role;

revoke all on function public.wow_nre_task_guard() from public, anon, authenticated;
revoke all on function public.wow_nre_server_clock_guard() from public, anon, authenticated;
revoke all on function public.wow_nre_claim_task(text, text, integer) from public, anon, authenticated;
revoke all on function public.wow_nre_search(text, integer, text, text, text, text, text, text, text, text, text,
  text, text, timestamptz, timestamptz, timestamptz) from public, anon, authenticated;
grant execute on function public.wow_nre_claim_task(text, text, integer) to service_role;
grant execute on function public.wow_nre_search(text, integer, text, text, text, text, text, text, text, text, text,
  text, text, timestamptz, timestamptz, timestamptz) to service_role;
