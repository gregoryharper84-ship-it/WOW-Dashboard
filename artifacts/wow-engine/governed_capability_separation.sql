-- WOW governed deployment-state separation repair — 2026-08-28
--
-- Deployment contract completion and governed probability availability are
-- separate states. G01-G11 may all PASS while publication/model capability
-- remains unavailable because calibration evidence or production-feature
-- readiness is still incomplete. This function preserves fail-closed behavior:
-- can_execute is always false and capability is AVAILABLE only when all three
-- independent prerequisites are true.

create or replace function public.wow_governed_deployment_state()
returns jsonb
language sql
stable
set search_path to ''
as $function$
with gate_summary as (
  select
    count(*) as gate_count,
    count(*) filter (where status = 'PASS') as pass_count,
    coalesce(
      jsonb_agg(
        jsonb_build_object('id', gate_id, 'status', status, 'reason', reason)
        order by gate_id
      ),
      '[]'::jsonb
    ) as deployment_gates
  from public.wow_governed_deployment_gates
),
latest_health as (
  select spec_id, calibration_health_status
  from public.wow_mlb_v2d_calibration_health
  order by assessed_at desc
  limit 1
),
active_spec as (
  select fs.spec_id, fs.production_feature_ready
  from public.wow_mlb_v2d_frozen_spec fs
  where fs.status = 'RESEARCH_FROZEN'
    and fs.spec_id = (select spec_id from latest_health)
  limit 1
),
state as (
  select
    g.deployment_gates,
    (g.gate_count = 11 and g.pass_count = 11) as deployment_contract_pass,
    coalesce(h.calibration_health_status, 'UNAVAILABLE') as calibration_health_status,
    coalesce(s.production_feature_ready, false) as production_feature_ready
  from gate_summary g
  left join latest_health h on true
  left join active_spec s on true
)
select jsonb_build_object(
  'governed_probability_capability',
    case
      when deployment_contract_pass
       and calibration_health_status = 'PASS'
       and production_feature_ready
      then 'AVAILABLE'
      else 'UNAVAILABLE'
    end,
  'governed_probability_status',
    case
      when deployment_contract_pass
       and calibration_health_status = 'PASS'
       and production_feature_ready
      then 'READY_FOR_PRODUCTION_GATE_REVIEW'
      else 'NOT_PRODUCED'
    end,
  'deployment_contract_status',
    case when deployment_contract_pass then 'PASS' else 'FAIL' end,
  'deployment_gates', deployment_gates,
  'calibration_health_status', calibration_health_status,
  'production_feature_ready', production_feature_ready,
  'can_execute', false
)
from state;
$function$;
