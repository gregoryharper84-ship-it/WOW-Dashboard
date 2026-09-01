-- WOW v16 Clean Core — MLB Calibration Health settled-cohort semantics.
-- Future/ungraded shadow predictions remain visible as pending evidence but do
-- not erase an already-sufficient settled review cohort. This also prevents a
-- trivially small 1/1 cohort from passing merely because every row happened to
-- be graded.
--
-- Governance stays fail-closed: this migration does not publish probabilities,
-- does not change can_execute, and does not alter champion/ratification gates.

create or replace function public.wow_mlb_v2d_assess_calibration_health(
  p_spec_id uuid default null::uuid
) returns jsonb
language plpgsql
set search_path to ''
as $$
declare
  v_spec_id uuid;
  v_captured_n integer;
  v_eligible_n integer;
  v_graded_n integer;
  v_pending_n integer;
  v_min_graded_n integer := 30;
  v_provenance_status text;
  v_forward_status text;
  v_blockers text[] := '{}';
  v_health_status text;
begin
  v_spec_id := coalesce(
    p_spec_id,
    (
      select spec_id
      from public.wow_mlb_v2d_frozen_spec
      where status = 'RESEARCH_FROZEN'
      order by created_at desc
      limit 1
    )
  );
  if v_spec_id is null then
    raise exception 'no frozen v2d spec available';
  end if;

  select count(distinct official_event_id)
  into v_captured_n
  from public.wow_mlb_forward_shadow_events
  where spec_id = v_spec_id;

  with qualified as (
    select
      e.shadow_event_id,
      e.official_event_id,
      row_number() over (
        partition by e.official_event_id
        order by e.snapshot_timestamp asc, e.shadow_event_id::text asc
      ) as event_rank
    from public.wow_mlb_forward_shadow_events e
    where e.spec_id = v_spec_id
      and e.feature_hydration_status = 'PASS'
      and e.starter_identity_provenance = 'PREGAME_TIMESTAMPED'
      and e.snapshot_timestamp is not null
      and e.snapshot_timestamp < e.event_start_time
      and e.model_score_status like 'SHADOW_SCORED%'
      and exists (
        select 1
        from public.wow_mlb_forward_score_snapshots s
        where s.shadow_event_id = e.shadow_event_id
          and s.spec_id = v_spec_id
      )
  )
  select count(*)
  into v_eligible_n
  from qualified
  where event_rank = 1;

  with qualified as (
    select
      e.shadow_event_id,
      e.official_event_id,
      row_number() over (
        partition by e.official_event_id
        order by e.snapshot_timestamp asc, e.shadow_event_id::text asc
      ) as event_rank
    from public.wow_mlb_forward_shadow_events e
    where e.spec_id = v_spec_id
      and e.feature_hydration_status = 'PASS'
      and e.starter_identity_provenance = 'PREGAME_TIMESTAMPED'
      and e.snapshot_timestamp is not null
      and e.snapshot_timestamp < e.event_start_time
      and e.model_score_status like 'SHADOW_SCORED%'
      and exists (
        select 1
        from public.wow_mlb_forward_score_snapshots s
        where s.shadow_event_id = e.shadow_event_id
          and s.spec_id = v_spec_id
      )
  )
  select count(*)
  into v_graded_n
  from qualified q
  where q.event_rank = 1
    and exists (
      select 1
      from public.wow_mlb_forward_shadow_grades g
      join public.wow_mlb_forward_score_snapshots s2
        on s2.score_snapshot_id = g.score_snapshot_id
      where s2.shadow_event_id = q.shadow_event_id
        and s2.spec_id = v_spec_id
    );

  v_pending_n := greatest(v_eligible_n - v_graded_n, 0);
  v_provenance_status := case
    when v_eligible_n > 0 then 'AVAILABLE'
    else 'UNAVAILABLE'
  end;

  v_forward_status := case
    when v_captured_n = 0 then 'NOT_STARTED'
    when v_eligible_n = 0 then 'COLLECTING'
    when v_graded_n = 0 then 'PREDICTIONS_AVAILABLE'
    when v_graded_n < v_min_graded_n then 'PARTIALLY_GRADED'
    else 'SUFFICIENT_FOR_REVIEW'
  end;

  if v_graded_n < v_min_graded_n then
    -- Preserve the native blocker label for downstream compatibility; its
    -- semantics are now "settled review cohort is not yet sufficient," not
    -- "every eligible/future prediction must already be graded."
    v_blockers := array_append(v_blockers, 'FORWARD_SHADOW_NOT_COMPLETED');
  end if;
  if v_provenance_status <> 'AVAILABLE' then
    v_blockers := array_append(v_blockers, 'TIMESTAMPED_PREGAME_STARTER_PROVENANCE_UNAVAILABLE');
  end if;
  if v_graded_n = 0 then
    v_blockers := array_append(v_blockers, 'FRESH_POST_FREEZE_OUTCOME_HOLDOUT_UNAVAILABLE');
  end if;

  v_health_status := case
    when cardinality(v_blockers) = 0 then 'PASS'
    else 'BLOCKED'
  end;

  update public.wow_mlb_v2d_calibration_health
  set assessed_at = now(),
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
    'spec_id',v_spec_id,
    'captured_shadow_n',v_captured_n,
    'eligible_shadow_n',v_eligible_n,
    'graded_shadow_n',v_graded_n,
    'pending_shadow_n',v_pending_n,
    'minimum_graded_shadow_n',v_min_graded_n,
    'forward_shadow_status',v_forward_status,
    'timestamped_pregame_provenance_status',v_provenance_status,
    'calibration_health_status',v_health_status,
    'blockers',to_jsonb(v_blockers),
    'probability_publishable',false,
    'can_execute',false
  );
end;
$$;
