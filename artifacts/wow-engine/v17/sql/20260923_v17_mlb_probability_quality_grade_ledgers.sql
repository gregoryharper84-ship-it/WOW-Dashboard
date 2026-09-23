-- WOW V17 MLB immutable grading-ledger split.
-- Class B observability/governance only: this does not alter probability outputs,
-- publication qualification, terminal authority, or can_execute=false.

create table if not exists public.wow_mlb_v17_prediction_grade_ledger (
  grade_ledger_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  grade_kind text not null check (grade_kind in (
    'EARLY_PREGAME_MODEL_GRADE',
    'FINAL_PREGAME_PUBLISHED_GRADE',
    'DOMINANCE_DIAGNOSTIC'
  )),
  official_event_id text not null,
  event_prediction_id uuid references public.wow_event_predictions(event_prediction_id),
  score_snapshot_id uuid references public.wow_mlb_forward_score_snapshots(score_snapshot_id),
  source_shadow_grade_id uuid references public.wow_mlb_forward_shadow_grades(grade_id),
  final_refresh_snapshot_id uuid,
  prediction_timestamp timestamptz not null,
  event_start_time timestamptz not null,
  home_team text not null,
  away_team text not null,
  home_probability double precision not null check (home_probability > 0 and home_probability < 1),
  away_probability double precision not null check (away_probability > 0 and away_probability < 1),
  selected_side text not null check (selected_side in ('HOME','AWAY')),
  selected_participant text not null,
  official_winner text,
  home_score integer check (home_score is null or home_score >= 0),
  away_score integer check (away_score is null or away_score >= 0),
  selected_won boolean,
  selected_margin integer,
  lineup_status_at_score text,
  probability_publishable_at_prediction boolean not null,
  diagnostic_json jsonb not null default '{}'::jsonb,
  can_execute boolean not null default false check (can_execute = false),
  unique (grade_kind, official_event_id),
  check (abs((home_probability + away_probability) - 1.0) <= 0.000001)
);

create index if not exists idx_wow_mlb_v17_grade_ledger_event_prediction
  on public.wow_mlb_v17_prediction_grade_ledger(event_prediction_id)
  where event_prediction_id is not null;
create index if not exists idx_wow_mlb_v17_grade_ledger_score_snapshot
  on public.wow_mlb_v17_prediction_grade_ledger(score_snapshot_id)
  where score_snapshot_id is not null;
create index if not exists idx_wow_mlb_v17_grade_ledger_shadow_grade
  on public.wow_mlb_v17_prediction_grade_ledger(source_shadow_grade_id)
  where source_shadow_grade_id is not null;

alter table public.wow_mlb_v17_prediction_grade_ledger enable row level security;
revoke all on table public.wow_mlb_v17_prediction_grade_ledger from anon, authenticated;
do $$
begin
  if exists (select 1 from pg_roles where rolname='service_role') then
    grant select, insert on table public.wow_mlb_v17_prediction_grade_ledger to service_role;
  end if;
end $$;

create or replace function public.wow_mlb_v17_grade_ledger_reject_mutation()
returns trigger
language plpgsql
set search_path to ''
as $$
begin
  raise exception 'WOW_MLB_V17_GRADE_LEDGER_IMMUTABLE';
end;
$$;
revoke execute on function public.wow_mlb_v17_grade_ledger_reject_mutation() from public, anon, authenticated;

drop trigger if exists wow_mlb_v17_prediction_grade_ledger_immutable
  on public.wow_mlb_v17_prediction_grade_ledger;
create trigger wow_mlb_v17_prediction_grade_ledger_immutable
before update or delete on public.wow_mlb_v17_prediction_grade_ledger
for each row execute function public.wow_mlb_v17_grade_ledger_reject_mutation();

create or replace function public.wow_mlb_v17_dominance_diagnostic_research(
  p_score_snapshot_id uuid,
  p_selected_side text default null
)
returns jsonb
language plpgsql
stable
set search_path to ''
as $$
declare
  s public.wow_mlb_forward_score_snapshots%rowtype;
  d public.wow_mlb_v2b_distribution_state%rowtype;
  side text;
  mults double precision[];
  weights double precision[];
  ph double precision[];
  pa double precision[];
  ri integer;
  hi integer;
  ai integer;
  hm double precision;
  am double precision;
  cell double precision;
  mass double precision;
  diff integer;
  reg_win double precision;
  reg_tie double precision;
  win2 double precision;
  win4 double precision;
  win6 double precision;
  loss4 double precision;
  expdiff double precision;
  weighted_reg_win double precision := 0;
  weighted_tie double precision := 0;
  weighted_win2 double precision := 0;
  weighted_win4 double precision := 0;
  weighted_win6 double precision := 0;
  weighted_loss4 double precision := 0;
  weighted_expdiff double precision := 0;
  extra_selected double precision;
  outright_win double precision;
begin
  select * into s
  from public.wow_mlb_forward_score_snapshots
  where score_snapshot_id=p_score_snapshot_id;
  if not found then
    return jsonb_build_object('status','BLOCKED','code','MLB_SCORE_SNAPSHOT_NOT_FOUND','can_execute',false);
  end if;

  select * into d
  from public.wow_mlb_v2b_distribution_state
  where distribution_id=s.distribution_id;
  if not found then
    return jsonb_build_object('status','BLOCKED','code','MLB_DISTRIBUTION_STATE_NOT_FOUND','can_execute',false);
  end if;

  side := upper(coalesce(
    p_selected_side,
    case when s.calibrated_home_probability>=s.calibrated_away_probability then 'HOME' else 'AWAY' end
  ));
  if side not in ('HOME','AWAY') then
    return jsonb_build_object('status','BLOCKED','code','MLB_DOMINANCE_SELECTED_SIDE_INVALID','can_execute',false);
  end if;

  mults := array[d.low_multiplier,d.center_multiplier,d.high_multiplier];
  weights := array[d.low_weight,d.center_weight,d.high_weight];
  for ri in 1..3 loop
    hm := s.home_mu*mults[ri];
    am := s.away_mu*mults[ri];
    ph := public.wow_nb_pmf_array(hm,d.home_alpha_independent,40);
    pa := public.wow_nb_pmf_array(am,d.away_alpha_independent,40);
    reg_win:=0; reg_tie:=0; win2:=0; win4:=0; win6:=0; loss4:=0; expdiff:=0; mass:=0;
    for hi in 0..40 loop
      for ai in 0..40 loop
        cell := ph[hi+1]*pa[ai+1];
        mass := mass+cell;
        diff := case when side='HOME' then hi-ai else ai-hi end;
        if diff>0 then reg_win:=reg_win+cell; end if;
        if diff=0 then reg_tie:=reg_tie+cell; end if;
        if diff>=2 then win2:=win2+cell; end if;
        if diff>=4 then win4:=win4+cell; end if;
        if diff>=6 then win6:=win6+cell; end if;
        if diff<=-4 then loss4:=loss4+cell; end if;
        expdiff:=expdiff+cell*diff;
      end loop;
    end loop;
    if mass<=0 then
      return jsonb_build_object('status','BLOCKED','code','MLB_DOMINANCE_PMF_MASS_INVALID','can_execute',false);
    end if;
    weighted_reg_win:=weighted_reg_win+weights[ri]*(reg_win/mass);
    weighted_tie:=weighted_tie+weights[ri]*(reg_tie/mass);
    weighted_win2:=weighted_win2+weights[ri]*(win2/mass);
    weighted_win4:=weighted_win4+weights[ri]*(win4/mass);
    weighted_win6:=weighted_win6+weights[ri]*(win6/mass);
    weighted_loss4:=weighted_loss4+weights[ri]*(loss4/mass);
    weighted_expdiff:=weighted_expdiff+weights[ri]*(expdiff/mass);
  end loop;

  extra_selected := case
    when side='HOME' then d.extra_inning_home_win_probability
    else 1-d.extra_inning_home_win_probability
  end;
  outright_win := weighted_reg_win + weighted_tie*extra_selected;

  return jsonb_build_object(
    'status','PASS',
    'schema_version','WOW_V17_MLB_DOMINANCE_DIAGNOSTIC_V1',
    'selected_side',side,
    'score_snapshot_id',p_score_snapshot_id,
    'distribution_id',s.distribution_id,
    'outright_win_probability',outright_win,
    'regulation_win_probability',weighted_reg_win,
    'tie_after_9_probability',weighted_tie,
    'extra_inning_selected_win_probability',extra_selected,
    'win_by_2_plus_probability',weighted_win2,
    'win_by_4_plus_probability',weighted_win4,
    'win_by_6_plus_probability',weighted_win6,
    'expected_run_differential',weighted_expdiff,
    'loss_by_4_plus_probability',weighted_loss4,
    'blowout_asymmetry_score',weighted_win4-weighted_loss4,
    'margin_scope','REGULATION_SCORE_DISTRIBUTION',
    'research_only',true,
    'probability_publishable',false,
    'can_execute',false
  );
end;
$$;
revoke execute on function public.wow_mlb_v17_dominance_diagnostic_research(uuid,text)
  from public, anon, authenticated;
do $$
begin
  if exists(select 1 from pg_roles where rolname='service_role') then
    grant execute on function public.wow_mlb_v17_dominance_diagnostic_research(uuid,text) to service_role;
  end if;
end $$;

create or replace function public.wow_mlb_v17_capture_settled_grade_ledgers(
  p_official_event_id text
)
returns jsonb
language plpgsql
set search_path to ''
as $$
declare
  early record;
  finalp record;
  outcome record;
  v_selected_side text;
  v_selected_margin integer;
  v_early_inserted boolean:=false;
  v_final_inserted boolean:=false;
  v_dom_inserted boolean:=false;
  v_diag jsonb;
begin
  -- Early model evaluation is intentionally the earliest qualified immutable shadow.
  select
    e.official_event_id,e.event_start_time,e.home_team,e.away_team,
    g.grade_id,g.score_snapshot_id,g.predicted_side,g.official_winner,g.actual_outcome,
    g.home_score,g.away_score,s.model_timestamp,s.calibrated_home_probability,
    s.calibrated_away_probability,s.lineup_status_at_score
  into early
  from public.wow_mlb_forward_shadow_events e
  join public.wow_mlb_forward_shadow_grades g on g.shadow_event_id=e.shadow_event_id
  join public.wow_mlb_forward_score_snapshots s on s.score_snapshot_id=g.score_snapshot_id
  where e.official_event_id=p_official_event_id
    and e.feature_hydration_status='PASS'
    and e.starter_identity_provenance='PREGAME_TIMESTAMPED'
    and e.snapshot_timestamp<e.event_start_time
  order by e.snapshot_timestamp asc,e.shadow_event_id::text asc,s.created_at asc
  limit 1;

  if found then
    insert into public.wow_mlb_v17_prediction_grade_ledger(
      grade_kind,official_event_id,score_snapshot_id,source_shadow_grade_id,
      prediction_timestamp,event_start_time,home_team,away_team,home_probability,
      away_probability,selected_side,selected_participant,official_winner,home_score,
      away_score,selected_won,selected_margin,lineup_status_at_score,
      probability_publishable_at_prediction,diagnostic_json,can_execute
    ) values (
      'EARLY_PREGAME_MODEL_GRADE',early.official_event_id,early.score_snapshot_id,
      early.grade_id,early.model_timestamp,early.event_start_time,early.home_team,early.away_team,
      early.calibrated_home_probability,early.calibrated_away_probability,early.predicted_side,
      case when early.predicted_side='HOME' then early.home_team else early.away_team end,
      case when early.official_winner='HOME' then early.home_team else early.away_team end,
      early.home_score,early.away_score,early.actual_outcome,
      case when early.predicted_side='HOME' then early.home_score-early.away_score else early.away_score-early.home_score end,
      early.lineup_status_at_score,false,'{}'::jsonb,false
    ) on conflict (grade_kind,official_event_id) do nothing;
    get diagnostics v_early_inserted = row_count;
  end if;

  -- Final published evaluation is the latest publishable final refresh before first pitch.
  select
    ep.event_prediction_id,ep.official_event_id,ep.scoring_snapshot_id,
    ep.final_refresh_snapshot_id,ep.final_refresh_timestamp,ep.event_start_time,
    ep.home_team,ep.away_team,ep.calibrated_home_probability::double precision home_p,
    ep.calibrated_away_probability::double precision away_p,ep.selected_participant,
    s.lineup_status_at_score
  into finalp
  from public.wow_event_predictions ep
  join public.wow_mlb_forward_score_snapshots s on s.score_snapshot_id=ep.scoring_snapshot_id
  where ep.sport='MLB'
    and ep.official_event_id=p_official_event_id
    and ep.probability_publishable=true
    and ep.final_refresh_status='PASS'
    and ep.final_refresh_timestamp<ep.event_start_time
  order by ep.final_refresh_timestamp desc,ep.event_prediction_id::text desc
  limit 1;

  if found then
    select * into outcome
    from public.wow_event_outcomes o
    where o.event_prediction_id=finalp.event_prediction_id
      and o.void=false
      and o.official_winner is not null
      and o.home_score is not null
      and o.away_score is not null
    order by o.settlement_timestamp desc nulls last,o.created_at desc
    limit 1;

    if found then
      v_selected_side := case
        when finalp.selected_participant=finalp.home_team then 'HOME'
        when finalp.selected_participant=finalp.away_team then 'AWAY'
        else null
      end;
      if v_selected_side is null then
        return jsonb_build_object(
          'status','BLOCKED',
          'code','FINAL_PUBLISHED_SELECTED_PARTICIPANT_IDENTITY_INVALID',
          'official_event_id',p_official_event_id,
          'can_execute',false
        );
      end if;
      v_selected_margin := case
        when v_selected_side='HOME' then outcome.home_score-outcome.away_score
        else outcome.away_score-outcome.home_score
      end;

      insert into public.wow_mlb_v17_prediction_grade_ledger(
        grade_kind,official_event_id,event_prediction_id,score_snapshot_id,
        final_refresh_snapshot_id,prediction_timestamp,event_start_time,home_team,
        away_team,home_probability,away_probability,selected_side,selected_participant,
        official_winner,home_score,away_score,selected_won,selected_margin,
        lineup_status_at_score,probability_publishable_at_prediction,diagnostic_json,can_execute
      ) values (
        'FINAL_PREGAME_PUBLISHED_GRADE',finalp.official_event_id,finalp.event_prediction_id,
        finalp.scoring_snapshot_id,finalp.final_refresh_snapshot_id,finalp.final_refresh_timestamp,
        finalp.event_start_time,finalp.home_team,finalp.away_team,finalp.home_p,finalp.away_p,
        v_selected_side,finalp.selected_participant,outcome.official_winner,outcome.home_score,
        outcome.away_score,outcome.official_winner=finalp.selected_participant,v_selected_margin,
        finalp.lineup_status_at_score,true,'{}'::jsonb,false
      ) on conflict (grade_kind,official_event_id) do nothing;
      get diagnostics v_final_inserted = row_count;

      v_diag := public.wow_mlb_v17_dominance_diagnostic_research(
        finalp.scoring_snapshot_id,v_selected_side
      );
      if v_diag->>'status'='PASS' then
        insert into public.wow_mlb_v17_prediction_grade_ledger(
          grade_kind,official_event_id,event_prediction_id,score_snapshot_id,
          final_refresh_snapshot_id,prediction_timestamp,event_start_time,home_team,
          away_team,home_probability,away_probability,selected_side,selected_participant,
          official_winner,home_score,away_score,selected_won,selected_margin,
          lineup_status_at_score,probability_publishable_at_prediction,diagnostic_json,can_execute
        ) values (
          'DOMINANCE_DIAGNOSTIC',finalp.official_event_id,finalp.event_prediction_id,
          finalp.scoring_snapshot_id,finalp.final_refresh_snapshot_id,finalp.final_refresh_timestamp,
          finalp.event_start_time,finalp.home_team,finalp.away_team,finalp.home_p,finalp.away_p,
          v_selected_side,finalp.selected_participant,outcome.official_winner,outcome.home_score,
          outcome.away_score,outcome.official_winner=finalp.selected_participant,v_selected_margin,
          finalp.lineup_status_at_score,true,v_diag,false
        ) on conflict (grade_kind,official_event_id) do nothing;
        get diagnostics v_dom_inserted = row_count;
      end if;
    end if;
  end if;

  return jsonb_build_object(
    'status','PASS',
    'official_event_id',p_official_event_id,
    'early_grade_inserted',v_early_inserted,
    'final_grade_inserted',v_final_inserted,
    'dominance_inserted',v_dom_inserted,
    'can_execute',false
  );
end;
$$;
revoke execute on function public.wow_mlb_v17_capture_settled_grade_ledgers(text)
  from public, anon, authenticated;
do $$
begin
  if exists(select 1 from pg_roles where rolname='service_role') then
    grant execute on function public.wow_mlb_v17_capture_settled_grade_ledgers(text) to service_role;
  end if;
end $$;
