-- V17 fitted team/event specialist certification registry.
--
-- This registry is deliberately separate from candidate/research artifacts.
-- A candidate cannot become publishable by mutating its research row. Governed
-- promotion writes a new immutable certification record only after fitted model,
-- calibration, independent replay/verification and exact input-contract review.
-- can_execute remains false at every layer.

create table if not exists public.wow_team_event_specialist_certifications (
    certification_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    sport text not null,
    league_scope text not null,
    controlling_specialist text not null,
    model_family text not null,
    model_version text not null,
    artifact_id text not null,
    artifact_sha256 text not null check (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    feature_schema_version text not null,
    input_contract_version text not null,
    calibration_artifact_id text not null,
    calibration_sha256 text not null check (calibration_sha256 ~ '^[0-9a-f]{64}$'),
    calibration_method text not null,
    independent_verification_status text not null check (independent_verification_status in ('PASS','FAIL')),
    verification_receipt jsonb not null default '{}'::jsonb check (jsonb_typeof(verification_receipt) = 'object'),
    certification_status text not null check (certification_status in ('CERTIFIED','REVOKED')),
    certified_at timestamptz not null,
    source_candidate_id uuid,
    supersedes_certification_id uuid references public.wow_team_event_specialist_certifications(certification_id),
    probability_publishable boolean not null default false,
    can_execute boolean not null default false check (can_execute = false),
    constraint wow_team_event_certification_publish_gate check (
        probability_publishable = false
        or (
            certification_status = 'CERTIFIED'
            and independent_verification_status = 'PASS'
        )
    )
);

create index if not exists idx_wow_team_event_certification_lookup
    on public.wow_team_event_specialist_certifications (sport, league_scope, created_at desc);

create unique index if not exists uq_wow_team_event_certification_model_receipt
    on public.wow_team_event_specialist_certifications (
        sport, league_scope, controlling_specialist, model_version, artifact_id, certification_status
    );

alter table public.wow_team_event_specialist_certifications enable row level security;
revoke all on public.wow_team_event_specialist_certifications from public, anon, authenticated;
grant select, insert on public.wow_team_event_specialist_certifications to service_role;

create trigger wow_team_event_specialist_certifications_immutable
before update or delete on public.wow_team_event_specialist_certifications
for each row execute function public.wow_reject_immutable_mutation();

create or replace function public.wow_v17_active_team_event_certification(
    p_sport text,
    p_league_scope text default null
) returns jsonb
language plpgsql
stable
security definer
set search_path=''
as $$
declare
    v_row public.wow_team_event_specialist_certifications%rowtype;
begin
    select * into v_row
      from public.wow_team_event_specialist_certifications c
     where c.sport = upper(trim(p_sport))
       and (p_league_scope is null or c.league_scope = upper(trim(p_league_scope)))
     order by c.created_at desc, c.certification_id desc
     limit 1;

    if not found then
        return jsonb_build_object(
            'ok', false,
            'code', 'TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED',
            'sport', upper(trim(p_sport)),
            'league_scope', p_league_scope,
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    if v_row.certification_status <> 'CERTIFIED'
       or v_row.independent_verification_status <> 'PASS'
       or v_row.probability_publishable is not true then
        return jsonb_build_object(
            'ok', false,
            'code', case
                when v_row.certification_status = 'REVOKED' then 'TEAM_EVENT_SPECIALIST_CERTIFICATION_REVOKED'
                else 'TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED'
            end,
            'sport', v_row.sport,
            'league_scope', v_row.league_scope,
            'certification_id', v_row.certification_id,
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    return jsonb_build_object(
        'ok', true,
        'code', 'TEAM_EVENT_SPECIALIST_CERTIFICATION_READY',
        'certification_id', v_row.certification_id,
        'sport', v_row.sport,
        'league_scope', v_row.league_scope,
        'controlling_specialist', v_row.controlling_specialist,
        'model_family', v_row.model_family,
        'model_version', v_row.model_version,
        'artifact_id', v_row.artifact_id,
        'artifact_sha256', v_row.artifact_sha256,
        'feature_schema_version', v_row.feature_schema_version,
        'input_contract_version', v_row.input_contract_version,
        'calibration_artifact_id', v_row.calibration_artifact_id,
        'calibration_sha256', v_row.calibration_sha256,
        'calibration_method', v_row.calibration_method,
        'independent_verification_status', v_row.independent_verification_status,
        'certification_status', v_row.certification_status,
        'certified_at', v_row.certified_at,
        'probability_publishable', true,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_v17_active_team_event_certification(text,text) from public, anon, authenticated;
grant execute on function public.wow_v17_active_team_event_certification(text,text) to service_role;

comment on table public.wow_team_event_specialist_certifications is
'Immutable V17 promotion receipts for fitted sport-specific team/event specialists. Registration is not certification; can_execute=false.';
