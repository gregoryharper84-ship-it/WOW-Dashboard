-- WOW MLB V2D forward-shadow -> Calibration Health synchronization
-- Probability Contract & Specialist Routing completion, 2026-08-28
--
-- APPLIED to the wow-engine-validation Supabase project (2026-08-28) as
-- migration wow_forward_shadow_calibration_health_sync_20260828. Verified
-- live: wow_mlb_v2d_assess_calibration_health() correctly recomputed
-- forward_shadow_status=PREDICTIONS_AVAILABLE, forward_shadow_n=30,
-- eligible_shadow_n=6, graded_shadow_n=0, pending_shadow_n=6,
-- timestamped_pregame_provenance_status=AVAILABLE (replacing the stale
-- NOT_STARTED/0/UNAVAILABLE row), and wow_mlb_forward_grade_shadow_event()
-- correctly returns BLOCKED/EVENT_NOT_YET_STARTED (no write) against a
-- real still-pregame shadow event. This file is kept in git as the
-- source-controlled record of that migration, not as a pending draft.
--
-- Repairs the exact stale-ledger gap: wow_mlb_v2d_calibration_health
-- currently reports forward_shadow_status=NOT_STARTED / forward_shadow_n=0
-- / timestamped_pregame_provenance_status=UNAVAILABLE, while the real
-- collector tables (wow_mlb_forward_shadow_events, etc.) already hold 30
-- captured events, 6 of them scored via wow_mlb_forward_score_event(). The
-- ledger was never wired to read the collector's actual state.
--
-- Eligibility contract for "qualified frozen prediction" (deliberately
-- narrower than "a row exists"): PASS feature hydration, a timestamped
-- pregame starter/lineup provenance record, a snapshot strictly before
-- event_start_time (no post-event leakage), and an existing immutable
-- score snapshot. Rows that don't meet this are still counted in
-- forward_shadow_n (captured observations) but never in
-- eligible_shadow_n -- per the "preserve but don't falsely qualify"
-- requirement.

-- 1. Grading storage, separate from prediction creation. Never rewrites
--    wow_mlb_forward_score_snapshots (already immutable via
--    wow_mlb_forward_score_ledger_immutable()).
create table if not exists public.wow_mlb_forward_shadow_grades (
    grade_id uuid primary key default gen_random_uuid(),
    shadow_event_id uuid not null references public.wow_mlb_forward_shadow_events(shadow_event_id),
    score_snapshot_id uuid not null references public.wow_mlb_forward_score_snapshots(score_snapshot_id),
    spec_id uuid not null,

    prediction_probability double precision not null
        check (prediction_probability > 0 and prediction_probability < 1),
    predicted_side text not null check (predicted_side in ('HOME','AWAY')),
    official_winner text not null check (official_winner in ('HOME','AWAY')),
    actual_outcome boolean not null,   -- did the predicted side actually win

    home_score integer not null check (home_score >= 0),
    away_score integer not null check (away_score >= 0),

    brier_contribution double precision not null,
    log_loss_contribution double precision,
    calibration_bin integer,

    prediction_timestamp timestamptz not null,
    outcome_timestamp timestamptz not null,
    grade_timestamp timestamptz not null default now(),
    model_spec_id uuid not null,
    settlement_source text,

    can_execute boolean not null default false check (can_execute = false),

    constraint uq_wow_forward_grade_per_snapshot unique (score_snapshot_id)
);

alter table public.wow_mlb_forward_shadow_grades enable row level security;

comment on table public.wow_mlb_forward_shadow_grades is
'Forward-shadow grading results for frozen MLB V2D predictions. Stored separately from wow_mlb_forward_score_snapshots (immutable prediction ledger) -- grading never rewrites a historical prediction.';

-- Grading rows are themselves immutable once written -- one grade per
-- prediction, matching the "never rewrite historical prediction
-- probabilities during grading" requirement.
create or replace function public.wow_mlb_forward_shadow_grade_immutable()
returns trigger
language plpgsql
set search_path = ''
as $function$ begin raise exception 'wow_mlb_forward_shadow_grades is immutable'; end; $function$;

drop trigger if exists trg_wow_mlb_forward_shadow_grade_immutable_upd on public.wow_mlb_forward_shadow_grades;
create trigger trg_wow_mlb_forward_shadow_grade_immutable_upd
    before update on public.wow_mlb_forward_shadow_grades
    for each row execute function public.wow_mlb_forward_shadow_grade_immutable();

drop trigger if exists trg_wow_mlb_forward_shadow_grade_immutable_del on public.wow_mlb_forward_shadow_grades;
create trigger trg_wow_mlb_forward_shadow_grade_immutable_del
    before delete on public.wow_mlb_forward_shadow_grades
    for each row execute function public.wow_mlb_forward_shadow_grade_immutable();

-- 2. Grading function. Fails closed: no-ops (BLOCKED, not an error) until
--    the game has actually started and a frozen prediction exists to
--    grade; never invents a result, never re-scores.
create or replace function public.wow_mlb_forward_grade_shadow_event(
    p_shadow_event_id uuid,
    p_official_winner text,     -- 'HOME' or 'AWAY'
    p_home_score integer,
    p_away_score integer,
    p_settlement_source text default null
) returns jsonb
language plpgsql
set search_path = ''
as $function$
declare
  e public.wow_mlb_forward_shadow_events%rowtype;
  s public.wow_mlb_forward_score_snapshots%rowtype;
  v_home_win boolean;
  v_predicted_side text;
  v_actual_outcome boolean;
  v_brier double precision;
  v_logloss double precision;
  v_grade_id uuid;
begin
  if p_official_winner not in ('HOME','AWAY') then
    raise exception 'official_winner must be HOME or AWAY';
  end if;
  if p_home_score is null or p_away_score is null or p_home_score < 0 or p_away_score < 0 then
    raise exception 'scores must be non-negative';
  end if;

  select * into e from public.wow_mlb_forward_shadow_events where shadow_event_id = p_shadow_event_id;
  if not found then raise exception 'shadow event not found'; end if;

  if now() < e.event_start_time then
    return jsonb_build_object('status','BLOCKED','reason','EVENT_NOT_YET_STARTED','can_execute',false);
  end if;

  -- Grade the earliest frozen prediction for this event -- never a
  -- re-scored/later one -- matching "preserve the original frozen
  -- prediction."
  select * into s from public.wow_mlb_forward_score_snapshots
   where shadow_event_id = p_shadow_event_id
   order by created_at asc limit 1;
  if not found then
    return jsonb_build_object('status','BLOCKED','reason','NO_FROZEN_PREDICTION_TO_GRADE','can_execute',false);
  end if;

  if exists (select 1 from public.wow_mlb_forward_shadow_grades where score_snapshot_id = s.score_snapshot_id) then
    return jsonb_build_object('status','BLOCKED','reason','ALREADY_GRADED','can_execute',false);
  end if;

  v_home_win := (p_official_winner = 'HOME');
  v_predicted_side := case when s.calibrated_home_probability >= s.calibrated_away_probability then 'HOME' else 'AWAY' end;
  v_actual_outcome := (v_predicted_side = p_official_winner);
  v_brier := power((case when v_home_win then 1.0 else 0.0 end) - s.calibrated_home_probability, 2);
  if s.calibrated_home_probability > 0 and s.calibrated_home_probability < 1 then
    v_logloss := - ( (case when v_home_win then 1.0 else 0.0 end) * ln(s.calibrated_home_probability)
                    + (case when v_home_win then 0.0 else 1.0 end) * ln(1 - s.calibrated_home_probability) );
  else
    v_logloss := null;
  end if;

  insert into public.wow_mlb_forward_shadow_grades (
    shadow_event_id, score_snapshot_id, spec_id, prediction_probability, predicted_side,
    official_winner, actual_outcome, home_score, away_score, brier_contribution, log_loss_contribution,
    prediction_timestamp, outcome_timestamp, model_spec_id, settlement_source
  ) values (
    p_shadow_event_id, s.score_snapshot_id, s.spec_id, s.calibrated_home_probability, v_predicted_side,
    p_official_winner, v_actual_outcome, p_home_score, p_away_score, v_brier, v_logloss,
    s.model_timestamp, now(), s.spec_id, p_settlement_source
  ) returning grade_id into v_grade_id;

  -- Only the outcome/result fields are touched here -- never the
  -- prediction fields (model_probability_*, calibrated_probability_*),
  -- which stay exactly as scored.
  update public.wow_mlb_forward_shadow_events set
    result_status = 'GRADED',
    home_win = v_home_win
  where shadow_event_id = p_shadow_event_id;

  return jsonb_build_object(
    'status','GRADED','grade_id',v_grade_id,'predicted_side',v_predicted_side,
    'actual_outcome',v_actual_outcome,'brier_contribution',v_brier,'log_loss_contribution',v_logloss,
    'can_execute',false
  );
end;
$function$;

-- 3. Calibration Health now derivable from real evidence. Adds the three
--    count columns the completion packet asks for that don't exist yet
--    (forward_shadow_n already exists and becomes "all captured
--    observations for this spec"; the new columns narrow to the
--    contract-qualifying subset).
alter table public.wow_mlb_v2d_calibration_health
  add column if not exists eligible_shadow_n integer,
  add column if not exists graded_shadow_n integer,
  add column if not exists pending_shadow_n integer;

create or replace function public.wow_mlb_v2d_assess_calibration_health(p_spec_id uuid default null)
returns jsonb
language plpgsql
set search_path = ''
as $function$
declare
  v_spec_id uuid;
  v_captured_n integer;
  v_eligible_n integer;
  v_graded_n integer;
  v_pending_n integer;
  v_provenance_status text;
  v_forward_status text;
  v_blockers text[] := '{}';
  v_health_status text;
begin
  v_spec_id := coalesce(
    p_spec_id,
    (select spec_id from public.wow_mlb_v2d_frozen_spec where status = 'RESEARCH_FROZEN' order by created_at desc limit 1)
  );
  if v_spec_id is null then
    raise exception 'no frozen v2d spec available';
  end if;

  select count(*) into v_captured_n
  from public.wow_mlb_forward_shadow_events
  where spec_id = v_spec_id;

  -- Eligible = actually satisfies the no-hindsight frozen-prediction
  -- contract: PASS feature hydration, timestamped pregame starter
  -- provenance, a snapshot strictly before event start, a real scored
  -- status, and an existing immutable score snapshot. A captured row that
  -- doesn't meet this stays counted in v_captured_n only -- it is
  -- preserved as an observation, never promoted to "qualified."
  select count(*) into v_eligible_n
  from public.wow_mlb_forward_shadow_events e
  where e.spec_id = v_spec_id
    and e.feature_hydration_status = 'PASS'
    and e.starter_identity_provenance = 'PREGAME_TIMESTAMPED'
    and e.snapshot_timestamp is not null
    and e.snapshot_timestamp < e.event_start_time
    and e.model_score_status like 'SHADOW_SCORED%'
    and exists (
      select 1 from public.wow_mlb_forward_score_snapshots s
      where s.shadow_event_id = e.shadow_event_id and s.spec_id = v_spec_id
    );

  select count(*) into v_graded_n
  from public.wow_mlb_forward_shadow_events e
  where e.spec_id = v_spec_id
    and e.feature_hydration_status = 'PASS'
    and e.starter_identity_provenance = 'PREGAME_TIMESTAMPED'
    and e.snapshot_timestamp is not null
    and e.snapshot_timestamp < e.event_start_time
    and e.model_score_status like 'SHADOW_SCORED%'
    and e.result_status <> 'PENDING'
    and exists (
      select 1
      from public.wow_mlb_forward_shadow_grades g
      join public.wow_mlb_forward_score_snapshots s2 on s2.score_snapshot_id = g.score_snapshot_id
      where s2.shadow_event_id = e.shadow_event_id
    );

  v_pending_n := v_eligible_n - v_graded_n;
  v_provenance_status := case when v_eligible_n > 0 then 'AVAILABLE' else 'UNAVAILABLE' end;

  -- Canonical lifecycle per the completion packet -- reused, not a new
  -- enum invented for this function.
  v_forward_status := case
    when v_captured_n = 0 then 'NOT_STARTED'
    when v_eligible_n = 0 then 'COLLECTING'
    when v_graded_n = 0 then 'PREDICTIONS_AVAILABLE'
    when v_graded_n < v_eligible_n then 'PARTIALLY_GRADED'
    else 'SUFFICIENT_FOR_REVIEW'   -- statistical/governance sufficiency is
                                   -- judged elsewhere; this function never
                                   -- asserts CERTIFIED on its own.
  end;

  if v_forward_status <> 'SUFFICIENT_FOR_REVIEW' then
    v_blockers := array_append(v_blockers, 'FORWARD_SHADOW_NOT_COMPLETED');
  end if;
  if v_provenance_status <> 'AVAILABLE' then
    v_blockers := array_append(v_blockers, 'TIMESTAMPED_PREGAME_STARTER_PROVENANCE_UNAVAILABLE');
  end if;
  if v_graded_n = 0 then
    v_blockers := array_append(v_blockers, 'FRESH_POST_FREEZE_OUTCOME_HOLDOUT_UNAVAILABLE');
  end if;

  -- calibration_health_status keeps the table's existing PASS/BLOCKED
  -- binary (never widened to a new CERTIFIED value here) -- certification
  -- is a separate governance decision, not something this sync function
  -- can grant just because forward evidence caught up.
  v_health_status := case when cardinality(v_blockers) = 0 then 'PASS' else 'BLOCKED' end;

  update public.wow_mlb_v2d_calibration_health set
    assessed_at = now(),
    forward_shadow_status = v_forward_status,
    forward_shadow_n = v_captured_n,
    eligible_shadow_n = v_eligible_n,
    graded_shadow_n = v_graded_n,
    pending_shadow_n = v_pending_n,
    timestamped_pregame_provenance_status = v_provenance_status,
    calibration_health_status = v_health_status,
    blockers = v_blockers,
    probability_publishable = false,
    can_execute = false
  where spec_id = v_spec_id;

  return jsonb_build_object(
    'spec_id', v_spec_id,
    'captured_shadow_n', v_captured_n,
    'eligible_shadow_n', v_eligible_n,
    'graded_shadow_n', v_graded_n,
    'pending_shadow_n', v_pending_n,
    'forward_shadow_status', v_forward_status,
    'timestamped_pregame_provenance_status', v_provenance_status,
    'calibration_health_status', v_health_status,
    'blockers', to_jsonb(v_blockers),
    'can_execute', false
  );
end;
$function$;
