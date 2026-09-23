-- WOW V17 MLB Calibration Health V2 shadow governance
-- 2026-09-23
--
-- CLASS B / RESEARCH-SHADOW ONLY.
-- DO NOT APPLY TO PRODUCTION AS A publication gate without governed review.
-- This migration does not alter sporting probabilities, champion artifacts,
-- publication thresholds, V17 terminal authority, or can_execute=false.
--
-- Purpose:
--   1. make quantitative calibration metrics explicit;
--   2. keep EARLY_PREGAME_MODEL_GRADE separate from
--      FINAL_PREGAME_PUBLISHED_GRADE;
--   3. make PASS impossible unless a versioned policy is explicitly RATIFIED;
--   4. preserve immutable metric receipts;
--   5. expose the legacy lifecycle-only PASS beside V2 rather than silently
--      treating it as quantitative calibration evidence.

create table if not exists public.wow_mlb_v2d_calibration_health_v2_policy_shadow (
  policy_version text primary key,
  created_at timestamptz not null default now(),
  status text not null check (status in ('DRAFT','RATIFIED','RETIRED')),
  min_n integer,
  max_brier double precision,
  max_log_loss double precision,
  max_ece double precision,
  max_bin_gap double precision,
  max_abs_calibration_intercept double precision,
  min_calibration_slope double precision,
  max_calibration_slope double precision,
  rationale text not null,
  can_execute boolean not null default false check (can_execute=false),
  check (
    status <> 'RATIFIED' or (
      min_n is not null and min_n >= 30 and
      max_brier is not null and max_brier > 0 and
      max_log_loss is not null and max_log_loss > 0 and
      max_ece is not null and max_ece >= 0 and
      max_bin_gap is not null and max_bin_gap >= 0 and
      max_abs_calibration_intercept is not null and max_abs_calibration_intercept >= 0 and
      min_calibration_slope is not null and max_calibration_slope is not null and
      min_calibration_slope > 0 and max_calibration_slope >= min_calibration_slope
    )
  )
);

create table if not exists public.wow_mlb_v2d_calibration_health_v2_shadow (
  assessment_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  spec_id uuid not null references public.wow_mlb_v2d_frozen_spec(spec_id),
  cohort_kind text not null check (
    cohort_kind in ('EARLY_PREGAME_MODEL_GRADE','FINAL_PREGAME_PUBLISHED_GRADE')
  ),
  n integer not null check (n >= 0),
  brier double precision,
  log_loss double precision,
  ece_equal_count double precision,
  max_equal_count_gap double precision,
  calibration_intercept double precision,
  calibration_slope double precision,
  roc_auc double precision,
  selected_side_hit_rate double precision,
  probability_stddev double precision,
  policy_version text references public.wow_mlb_v2d_calibration_health_v2_policy_shadow(policy_version),
  policy_status text not null,
  health_status text not null check (
    health_status in ('PASS','REVIEW_REQUIRED','INSUFFICIENT_SAMPLE','POLICY_UNRATIFIED','NOT_EVALUATED')
  ),
  blockers text[] not null default '{}',
  legacy_health_status text,
  probability_publishable boolean not null default false check (probability_publishable=false),
  can_execute boolean not null default false check (can_execute=false)
);

create index if not exists idx_wow_mlb_cal_health_v2_spec_cohort_time
  on public.wow_mlb_v2d_calibration_health_v2_shadow(spec_id,cohort_kind,created_at desc);
create index if not exists idx_wow_mlb_cal_health_v2_policy_version
  on public.wow_mlb_v2d_calibration_health_v2_shadow(policy_version);

alter table public.wow_mlb_v2d_calibration_health_v2_policy_shadow enable row level security;
alter table public.wow_mlb_v2d_calibration_health_v2_shadow enable row level security;
revoke all on public.wow_mlb_v2d_calibration_health_v2_policy_shadow from anon, authenticated;
revoke all on public.wow_mlb_v2d_calibration_health_v2_shadow from anon, authenticated;

create or replace function public.wow_mlb_v2d_calibration_health_v2_immutable()
returns trigger
language plpgsql
set search_path=''
as $function$
begin
  raise exception 'wow_mlb_v2d_calibration_health_v2_shadow is immutable';
end;
$function$;

drop trigger if exists trg_wow_mlb_v2d_calibration_health_v2_upd
  on public.wow_mlb_v2d_calibration_health_v2_shadow;
create trigger trg_wow_mlb_v2d_calibration_health_v2_upd
before update on public.wow_mlb_v2d_calibration_health_v2_shadow
for each row execute function public.wow_mlb_v2d_calibration_health_v2_immutable();

drop trigger if exists trg_wow_mlb_v2d_calibration_health_v2_del
  on public.wow_mlb_v2d_calibration_health_v2_shadow;
create trigger trg_wow_mlb_v2d_calibration_health_v2_del
before delete on public.wow_mlb_v2d_calibration_health_v2_shadow
for each row execute function public.wow_mlb_v2d_calibration_health_v2_immutable();

create or replace function public.wow_mlb_v2d_assess_calibration_health_v2_shadow(
  p_spec_id uuid default null,
  p_cohort_kind text default 'EARLY_PREGAME_MODEL_GRADE'
) returns jsonb
language plpgsql
set search_path=''
as $function$
declare
  v_spec_id uuid;
  v_probs double precision[];
  v_outcomes integer[];
  v_n integer := 0;
  v_brier double precision;
  v_log_loss double precision;
  v_ece double precision;
  v_max_gap double precision;
  v_auc double precision;
  v_hit double precision;
  v_stddev double precision;
  v_ci double precision := 0.0;
  v_cs double precision := 1.0;
  v_g0 double precision;
  v_g1 double precision;
  v_h00 double precision;
  v_h01 double precision;
  v_h11 double precision;
  v_det double precision;
  v_da double precision;
  v_db double precision;
  v_policy public.wow_mlb_v2d_calibration_health_v2_policy_shadow%rowtype;
  v_has_policy boolean := false;
  v_blockers text[] := '{}';
  v_health text := 'NOT_EVALUATED';
  v_legacy_status text;
  v_assessment_id uuid;
  i integer;
begin
  if p_cohort_kind not in ('EARLY_PREGAME_MODEL_GRADE','FINAL_PREGAME_PUBLISHED_GRADE') then
    raise exception 'unsupported cohort_kind: %', p_cohort_kind;
  end if;

  v_spec_id := coalesce(
    p_spec_id,
    (
      select spec_id
      from public.wow_mlb_v2d_frozen_spec
      where status='RESEARCH_FROZEN'
      order by created_at desc
      limit 1
    )
  );
  if v_spec_id is null then raise exception 'no frozen v2d spec available'; end if;

  select calibration_health_status
    into v_legacy_status
  from public.wow_mlb_v2d_calibration_health
  where spec_id=v_spec_id;

  if p_cohort_kind='EARLY_PREGAME_MODEL_GRADE' then
    -- Legacy forward grade storage names prediction_probability as though it
    -- were the selected-side probability, but the writer actually stored the
    -- calibrated HOME probability. Derive selected-side probability from the
    -- immutable predicted_side instead of rewriting historical rows.
    with canonical as (
      select distinct on (e.official_event_id)
        e.official_event_id,
        case
          when g.predicted_side='HOME' then g.prediction_probability
          else 1.0-g.prediction_probability
        end p,
        g.actual_outcome::int y,
        g.prediction_timestamp
      from public.wow_mlb_forward_shadow_grades g
      join public.wow_mlb_forward_shadow_events e
        on e.shadow_event_id=g.shadow_event_id
      where g.spec_id=v_spec_id
        and g.prediction_probability>0
        and g.prediction_probability<1
      order by e.official_event_id,g.prediction_timestamp,g.grade_id
    )
    select array_agg(p order by prediction_timestamp,official_event_id),
           array_agg(y order by prediction_timestamp,official_event_id)
      into v_probs,v_outcomes
    from canonical;
  else
    with canonical as (
      select official_event_id,
             case when selected_side='HOME' then home_probability else away_probability end p,
             selected_won::int y,
             prediction_timestamp
      from public.wow_mlb_v17_prediction_grade_ledger
      where grade_kind='FINAL_PREGAME_PUBLISHED_GRADE'
        and case when selected_side='HOME' then home_probability else away_probability end > 0
        and case when selected_side='HOME' then home_probability else away_probability end < 1
    )
    select array_agg(p order by prediction_timestamp,official_event_id),
           array_agg(y order by prediction_timestamp,official_event_id)
      into v_probs,v_outcomes
    from canonical;
  end if;

  v_n := coalesce(array_length(v_probs,1),0);

  if v_n>0 then
    with vals as (
      select v_probs[s] p,v_outcomes[s] y
      from generate_subscripts(v_probs,1) g(s)
    )
    select avg((p-y)^2),
           avg(-(y)*ln(greatest(1e-12,p))-(1-y)*ln(greatest(1e-12,1-p))),
           avg(y::double precision),
           stddev_pop(p)
      into v_brier,v_log_loss,v_hit,v_stddev
    from vals;

    with vals as (
      select v_probs[s] p,v_outcomes[s] y
      from generate_subscripts(v_probs,1) g(s)
    ), b as (
      select p,y,ntile(least(10,v_n)) over(order by p) bin
      from vals
    ), gaps as (
      select bin,count(*) n,abs(avg(p)-avg(y::double precision)) gap
      from b
      group by bin
    )
    select sum(n*gap)/sum(n),max(gap)
      into v_ece,v_max_gap
    from gaps;

    if exists (select 1 from unnest(v_outcomes) x(y) where y=1)
       and exists (select 1 from unnest(v_outcomes) x(y) where y=0) then
      with vals as (
        select v_probs[s] p,v_outcomes[s] y
        from generate_subscripts(v_probs,1) g(s)
      ), r as (
        select p,y,rank() over(order by p) rr
        from vals
      ), a as (
        select count(*) filter(where y=1) npos,
               count(*) filter(where y=0) nneg,
               sum(rr) filter(where y=1) rank_sum
        from r
      )
      select (rank_sum-npos*(npos+1)/2.0)/(npos*nneg)
        into v_auc
      from a;

      -- Logistic calibration fit: outcome ~ intercept + slope*logit(p).
      -- This is measurement only; it never remaps or republishes probabilities.
      for i in 1..25 loop
        with vals as (
          select v_probs[s] p,v_outcomes[s] y
          from generate_subscripts(v_probs,1) g(s)
        ), z as (
          select y,ln(p/(1-p)) x,
                 1.0/(1.0+exp(-(v_ci+v_cs*ln(p/(1-p))))) ph
          from vals
        )
        select sum(ph-y),
               sum((ph-y)*x),
               sum(ph*(1-ph)),
               sum(ph*(1-ph)*x),
               sum(ph*(1-ph)*x*x)
          into v_g0,v_g1,v_h00,v_h01,v_h11
        from z;
        v_det:=v_h00*v_h11-v_h01*v_h01;
        exit when v_det is null or abs(v_det)<1e-12;
        v_da:=(v_h11*v_g0-v_h01*v_g1)/v_det;
        v_db:=(-v_h01*v_g0+v_h00*v_g1)/v_det;
        v_ci:=v_ci-v_da;
        v_cs:=v_cs-v_db;
        exit when sqrt(v_g0*v_g0+v_g1*v_g1)<1e-8;
      end loop;
    else
      v_ci:=null;
      v_cs:=null;
      v_auc:=null;
    end if;
  end if;

  select * into v_policy
  from public.wow_mlb_v2d_calibration_health_v2_policy_shadow
  where status='RATIFIED'
  order by created_at desc
  limit 1;
  v_has_policy := found;

  if v_n < 30 then
    v_health:='INSUFFICIENT_SAMPLE';
    v_blockers:=array_append(v_blockers,'CALIBRATION_SAMPLE_INSUFFICIENT');
  elsif not v_has_policy then
    v_health:='POLICY_UNRATIFIED';
    v_blockers:=array_append(v_blockers,'QUANTITATIVE_CALIBRATION_POLICY_UNRATIFIED');
  else
    if v_n < v_policy.min_n then
      v_blockers:=array_append(v_blockers,'CALIBRATION_SAMPLE_INSUFFICIENT');
    end if;
    if v_brier > v_policy.max_brier then
      v_blockers:=array_append(v_blockers,'CALIBRATION_BRIER_EXCEEDS_LIMIT');
    end if;
    if v_log_loss > v_policy.max_log_loss then
      v_blockers:=array_append(v_blockers,'CALIBRATION_LOG_LOSS_EXCEEDS_LIMIT');
    end if;
    if v_ece > v_policy.max_ece then
      v_blockers:=array_append(v_blockers,'CALIBRATION_ECE_EXCEEDS_LIMIT');
    end if;
    if v_max_gap > v_policy.max_bin_gap then
      v_blockers:=array_append(v_blockers,'CALIBRATION_BIN_GAP_EXCEEDS_LIMIT');
    end if;
    if v_ci is null or abs(v_ci) > v_policy.max_abs_calibration_intercept then
      v_blockers:=array_append(v_blockers,'CALIBRATION_INTERCEPT_OUT_OF_RANGE');
    end if;
    if v_cs is null
       or v_cs < v_policy.min_calibration_slope
       or v_cs > v_policy.max_calibration_slope then
      v_blockers:=array_append(v_blockers,'CALIBRATION_SLOPE_OUT_OF_RANGE');
    end if;
    v_health:=case when cardinality(v_blockers)=0 then 'PASS' else 'REVIEW_REQUIRED' end;
  end if;

  insert into public.wow_mlb_v2d_calibration_health_v2_shadow(
    spec_id,cohort_kind,n,brier,log_loss,ece_equal_count,max_equal_count_gap,
    calibration_intercept,calibration_slope,roc_auc,selected_side_hit_rate,probability_stddev,
    policy_version,policy_status,health_status,blockers,legacy_health_status
  ) values (
    v_spec_id,p_cohort_kind,v_n,v_brier,v_log_loss,v_ece,v_max_gap,
    v_ci,v_cs,v_auc,v_hit,v_stddev,
    case when v_has_policy then v_policy.policy_version else null end,
    case when v_has_policy then v_policy.status else 'UNRATIFIED' end,
    v_health,v_blockers,v_legacy_status
  ) returning assessment_id into v_assessment_id;

  return jsonb_build_object(
    'assessment_id',v_assessment_id,
    'spec_id',v_spec_id,
    'cohort_kind',p_cohort_kind,
    'n',v_n,
    'brier',v_brier,
    'log_loss',v_log_loss,
    'ece_equal_count',v_ece,
    'max_equal_count_gap',v_max_gap,
    'calibration_intercept',v_ci,
    'calibration_slope',v_cs,
    'roc_auc',v_auc,
    'selected_side_hit_rate',v_hit,
    'probability_stddev',v_stddev,
    'legacy_health_status',v_legacy_status,
    'policy_status',case when v_has_policy then v_policy.status else 'UNRATIFIED' end,
    'health_status',v_health,
    'blockers',to_jsonb(v_blockers),
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;
