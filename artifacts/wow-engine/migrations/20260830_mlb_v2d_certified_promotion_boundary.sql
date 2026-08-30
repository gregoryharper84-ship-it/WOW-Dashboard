-- WOW v16 Clean Core — MLB V2D certified promotion boundary hardening.
--
-- The forward-shadow V2D object is explicitly RESEARCH_FROZEN and the current
-- wow_mlb_v2d_frozen_spec schema enforces production_feature_ready = false.
-- Therefore it cannot itself satisfy the controlling-model requirement for a
-- production AVAILABLE MLB event probability lane.
--
-- This migration removes the unsafe state transition that could have changed
-- MLB_EVENT_PROBABILITY to AVAILABLE solely from forward-shadow completion.
-- A future migration may replace this function only after a distinct certified
-- MLB event fitted-model artifact registry + certified event calibrator are
-- implemented and verified.
--
-- Non-execution invariants remain binding.

create or replace function public.wow_mlb_promote_runtime_capability_if_eligible(
    p_spec_id uuid
) returns jsonb
language plpgsql
set search_path to ''
as $$
declare
    fs public.wow_mlb_v2d_frozen_spec%rowtype;
begin
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('wow_mlb_v2d_promote:' || p_spec_id::text, 0)
    );

    select * into fs
    from public.wow_mlb_v2d_frozen_spec
    where spec_id = p_spec_id;

    if not found then
        return jsonb_build_object(
            'status', 'BLOCKED',
            'code', 'FROZEN_SPEC_NOT_FOUND',
            'runtime_capability_status', 'UNAVAILABLE',
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    -- Fail closed by design. RESEARCH_FROZEN forward-shadow evidence may clear
    -- Calibration Health, but it is not a certified production fitted artifact.
    -- Do not mutate wow_runtime_capabilities here.
    return jsonb_build_object(
        'status', 'BLOCKED',
        'code', 'MLB_CERTIFIED_EVENT_ARTIFACT_UNAVAILABLE',
        'spec_id', fs.spec_id,
        'spec_status', fs.status,
        'production_feature_ready', fs.production_feature_ready,
        'required_next_gate', 'CERTIFIED_MLB_EVENT_FITTED_ARTIFACT_AND_EVENT_CALIBRATOR',
        'runtime_capability_status', 'UNAVAILABLE',
        'probability_publishable', false,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_mlb_promote_runtime_capability_if_eligible(uuid)
from anon, authenticated;
