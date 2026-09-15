-- WOW V17 — establish exact NHL team/event route ownership.
--
-- This migration assigns one controlling specialist for NHL outright-winner
-- probability. It does not create or certify a model. Runtime capability stays
-- UNAVAILABLE until a promoted certified artifact/calibrator exists.

insert into public.wow_specialist_registry(
    sport,
    market_family,
    controlling_specialist,
    active,
    precedence,
    updated_at,
    can_execute
) values (
    'NHL',
    'OUTRIGHT_WINNER',
    'wow.nhl-game-win-probability-expert',
    true,
    100,
    now(),
    false
)
on conflict (sport, market_family) do update
set controlling_specialist = excluded.controlling_specialist,
    active = true,
    precedence = excluded.precedence,
    updated_at = now(),
    can_execute = false;

insert into public.wow_runtime_capabilities(
    capability_key,
    capability_status,
    evidence,
    can_execute,
    updated_at
) values (
    'NHL_EVENT_PROBABILITY',
    'UNAVAILABLE',
    jsonb_build_object(
        'provider_identity', 'WOW_NHL_EVENT_FITTED_MODEL_V1',
        'controlling_specialist', 'wow.nhl-game-win-probability-expert',
        'model_family', 'NHL_REGULAR_SEASON_LOGISTIC_V1',
        'feature_schema_version', 'NHL_REGULAR_SEASON_FEATURES_V1',
        'candidate_pipeline_ready', true,
        'certified_artifact_ready', false,
        'source_review_required', true,
        'terminal_label_if_scored_now', 'MODEL_UNAVAILABLE',
        'probability_publishable', false,
        'can_execute', false
    ),
    false,
    now()
)
on conflict (capability_key) do update
set capability_status = 'UNAVAILABLE',
    evidence = coalesce(public.wow_runtime_capabilities.evidence, '{}'::jsonb) || excluded.evidence,
    can_execute = false,
    updated_at = now();
