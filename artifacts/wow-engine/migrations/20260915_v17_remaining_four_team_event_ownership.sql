-- V17 baseline team/event specialist ownership completion.
--
-- Adds canonical routing ownership for the four baseline sport families that
-- previously had no live registry row. This is routing governance only: it does
-- not certify, activate, or promote a fitted model and cannot publish a model
-- probability. Each capability remains UNAVAILABLE until its own sport-specific
-- fitted artifact + calibrator lifecycle proves otherwise.

insert into public.wow_specialist_registry
    (sport, market_family, controlling_specialist, active, precedence, can_execute)
values
    ('NHL', 'OUTRIGHT_WINNER', 'wow.nhl-game-win-probability-expert', true, 100, false),
    ('GOLF', 'OUTRIGHT_WINNER', 'wow.golf-event-win-probability-expert', true, 100, false),
    ('MMA', 'OUTRIGHT_WINNER', 'wow.mma-fight-win-probability-expert', true, 100, false),
    ('BOXING', 'OUTRIGHT_WINNER', 'wow.boxing-fight-win-probability-expert', true, 100, false)
on conflict (sport, market_family) do update
set controlling_specialist = excluded.controlling_specialist,
    active = true,
    precedence = excluded.precedence,
    can_execute = false,
    updated_at = now();

insert into public.wow_runtime_capabilities
    (capability_key, capability_status, evidence, can_execute)
values
    ('NHL_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.nhl-game-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'probability_publishable', false,
        'can_execute', false
    ), false),
    ('GOLF_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.golf-event-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'probability_publishable', false,
        'can_execute', false
    ), false),
    ('MMA_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.mma-fight-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'probability_publishable', false,
        'can_execute', false
    ), false),
    ('BOXING_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.boxing-fight-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'probability_publishable', false,
        'can_execute', false
    ), false)
on conflict (capability_key) do update
set capability_status = case
        when public.wow_runtime_capabilities.capability_status = 'AVAILABLE'
            then public.wow_runtime_capabilities.capability_status
        else excluded.capability_status
    end,
    evidence = case
        when public.wow_runtime_capabilities.capability_status = 'AVAILABLE'
            then public.wow_runtime_capabilities.evidence
        else excluded.evidence
    end,
    can_execute = false,
    updated_at = now();
