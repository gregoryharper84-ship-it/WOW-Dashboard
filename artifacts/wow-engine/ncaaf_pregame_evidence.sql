-- WOW v16 Clean Core — provider-agnostic NCAAF pregame evidence registry.
-- Stores timestamped source evidence only. It does not score, calibrate, publish,
-- or authorize execution.

create table if not exists public.wow_ncaaf_pregame_evidence (
    evidence_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    official_event_id text not null,
    event_start_time timestamptz not null,
    evidence_kind text not null,
    scope text not null,
    team text,
    player text,
    source_provider text not null,
    source_record_id text,
    source_uri text,
    evidence_timestamp timestamptz not null,
    retrieved_at timestamptz not null default now(),
    provenance_grade text not null default 'UNVERIFIED',
    payload jsonb not null,
    payload_sha256 text not null,
    blocker_codes jsonb not null default '[]'::jsonb,
    can_execute boolean not null default false,

    constraint wow_ncaaf_evidence_kind check (evidence_kind in (
        'TEAM_POWER','OFF_EPA','DEF_EPA','SUCCESS_RATE','EXPLOSIVENESS',
        'QB_STATUS','QB_VALUE','QB_CERTAINTY','OL_HEALTH','DEF_FRONT_HEALTH',
        'SKILL_AVAILABILITY','REST_TRAVEL','TEMPO','TURNOVER_VOLATILITY',
        'SPECIAL_TEAMS','WEATHER','MARKET_NO_VIG'
    )),
    constraint wow_ncaaf_evidence_scope check (scope in ('HOME','AWAY','EVENT')),
    constraint wow_ncaaf_evidence_scope_team check (
        (scope = 'EVENT' and team is null) or
        (scope in ('HOME','AWAY') and team is not null)
    ),
    constraint wow_ncaaf_evidence_payload_object check (jsonb_typeof(payload) = 'object'),
    constraint wow_ncaaf_evidence_blockers_array check (jsonb_typeof(blocker_codes) = 'array'),
    constraint wow_ncaaf_evidence_hash_format check (payload_sha256 ~ '^[0-9a-f]{64}$'),
    constraint wow_ncaaf_evidence_provenance check (provenance_grade in ('A','B','C','UNVERIFIED')),
    constraint wow_ncaaf_evidence_strictly_pregame check (evidence_timestamp < event_start_time),
    constraint wow_ncaaf_evidence_never_execute check (can_execute = false),
    unique (official_event_id, evidence_kind, scope, source_provider, payload_sha256)
);

create index if not exists idx_wow_ncaaf_pregame_evidence_lookup
    on public.wow_ncaaf_pregame_evidence
    (official_event_id, evidence_kind, scope, evidence_timestamp desc);

alter table public.wow_ncaaf_pregame_evidence enable row level security;
revoke all on table public.wow_ncaaf_pregame_evidence from anon, authenticated;
grant all on table public.wow_ncaaf_pregame_evidence to service_role;

create or replace function public.wow_ncaaf_latest_pregame_evidence(
    p_official_event_id text,
    p_evidence_kind text,
    p_scope text
) returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
    v_row public.wow_ncaaf_pregame_evidence%rowtype;
begin
    select * into v_row
      from public.wow_ncaaf_pregame_evidence
     where official_event_id = p_official_event_id
       and evidence_kind = p_evidence_kind
       and scope = p_scope
       and evidence_timestamp < event_start_time
       and provenance_grade in ('A','B','C')
       and jsonb_array_length(blocker_codes) = 0
     order by evidence_timestamp desc
     limit 1;

    if not found then
        return jsonb_build_object(
            'ok', false,
            'code', 'NCAAF_PREGAME_EVIDENCE_NOT_FOUND',
            'official_event_id', p_official_event_id,
            'evidence_kind', p_evidence_kind,
            'scope', p_scope,
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    return to_jsonb(v_row) || jsonb_build_object(
        'ok', true,
        'code', 'NCAAF_PREGAME_EVIDENCE_READY',
        'probability_publishable', false,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_ncaaf_latest_pregame_evidence(text,text,text) from public, anon, authenticated;
grant execute on function public.wow_ncaaf_latest_pregame_evidence(text,text,text) to service_role;

comment on table public.wow_ncaaf_pregame_evidence is
  'Provider-agnostic timestamped NCAAF pregame evidence. Strictly pre-kickoff, service-role only, no scoring/publication/execution authority.';
