-- V17 first-six team/event specialist ownership.
--
-- This migration establishes canonical routing ownership only. It deliberately
-- does not certify a model, activate a fitted artifact, publish a probability,
-- or enable execution. Unsupported numerical routes remain UNAVAILABLE until
-- their exact fitted-model + calibrator lifecycle proves otherwise.

insert into public.wow_specialist_registry
    (sport, market_family, controlling_specialist, active, precedence, can_execute)
values
    ('NCAAF', 'OUTRIGHT_WINNER', 'wow.ncaaf-game-win-probability-expert', true, 100, false),
    ('NBA', 'OUTRIGHT_WINNER', 'wow.nba-game-win-probability-expert', true, 100, false),
    ('WNBA', 'OUTRIGHT_WINNER', 'wow.wnba-game-win-probability-expert', true, 100, false),
    ('NCAAB', 'OUTRIGHT_WINNER', 'wow.ncaab-game-win-probability-expert', true, 100, false),
    ('SOCCER', 'OUTRIGHT_WINNER', 'wow.soccer-match-win-probability-expert', true, 100, false),
    ('TENNIS', 'OUTRIGHT_WINNER', 'wow.tennis-match-win-probability-expert', true, 100, false)
on conflict (sport, market_family) do update
set controlling_specialist = excluded.controlling_specialist,
    active = true,
    precedence = excluded.precedence,
    can_execute = false,
    updated_at = now();

insert into public.wow_runtime_capabilities
    (capability_key, capability_status, evidence, can_execute)
values
    ('NCAAF_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.ncaaf-game-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'probability_publishable', false,
        'can_execute', false
    ), false),
    ('NBA_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.nba-game-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'probability_publishable', false,
        'can_execute', false
    ), false),
    ('WNBA_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.wnba-game-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'probability_publishable', false,
        'can_execute', false
    ), false),
    ('NCAAB_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.ncaab-game-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'probability_publishable', false,
        'can_execute', false
    ), false),
    ('SOCCER_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.soccer-match-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'outcome_space', 'HOME_DRAW_AWAY',
        'probability_publishable', false,
        'can_execute', false
    ), false),
    ('TENNIS_EVENT_PROBABILITY', 'UNAVAILABLE', jsonb_build_object(
        'source_of_truth', 'ROUTE_OWNERSHIP_ONLY',
        'controlling_specialist', 'wow.tennis-match-win-probability-expert',
        'blocker', 'CERTIFIED_ARTIFACT_REQUIRED',
        'retirement_settlement_required', true,
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
