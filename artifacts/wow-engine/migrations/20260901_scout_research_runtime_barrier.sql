-- WOW v16 Scout + mandatory Research Team runtime barrier.
--
-- Forward-only upgrade for the existing WOW Agent Runtime. This migration
-- deliberately does not alter probability, calibration, settlement, money,
-- portfolio, or execution semantics. Scout/Research workers are evidence-only
-- and their maximum authority remains RESEARCH_INTEREST.

begin;

-- The Python state machine now has an explicit mandatory research stage.
alter table public.wow_agent_runs
    drop constraint if exists wow_agent_runs_status;

alter table public.wow_agent_runs
    add constraint wow_agent_runs_status check (status in (
        'CREATED','VALIDATING_REQUEST','DISCOVERY_QUEUED','DISCOVERY_RUNNING',
        'ROUTING','RESEARCH_QUEUED','RESEARCH_RUNNING',
        'EVIDENCE_QUEUED','EVIDENCE_RUNNING','MODELING_QUEUED',
        'MODELING_RUNNING','AUDIT_QUEUED','AUDIT_RUNNING','FINAL_REFRESH',
        'RECONCILING','COMPLETED','COMPLETED_WITH_BLOCKERS','FAILED','CANCELED'
    ));

-- Canonical registry mirrors agent_runtime/registry.py exactly. The new Scout
-- and Research workers cannot originate a probability; only wow.controlling-model
-- remains FITTED_MODEL and therefore eligible to originate the controlling
-- distribution.
insert into public.wow_agent_worker_registry
    (worker_id, worker_version, contract_version, implementation_type, authority_ceiling,
     required_predecessors, timeout_seconds, max_retries, artifact_required, configuration, enabled)
values
    ('wow.parallel-discovery-router', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{}', 30, 2, false, '{}'::jsonb, true),
    ('wow.global-scout-coordinator', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.parallel-discovery-router}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.prop-scout-router', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.global-scout-coordinator}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.ml-event-scout-router', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.global-scout-coordinator}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.slate-integrity-expert', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'IDENTITY_VERIFIED',
     '{wow.global-scout-coordinator}', 20, 1, false, '{}'::jsonb, true),
    ('wow.source-provenance-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.participant-status-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.history-comparables-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 45, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.matchup-context-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.market-settlement-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.research-evidence-reconciler', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'RESEARCH_INTEREST',
     '{wow.source-provenance-researcher,wow.participant-status-researcher,wow.history-comparables-researcher,wow.matchup-context-researcher,wow.market-settlement-researcher}',
     20, 1, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.evidence-hydration', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'EVIDENCE_VERIFIED',
     '{wow.research-evidence-reconciler}', 45, 2, false, '{}'::jsonb, true),
    ('wow.controlling-model', '1.0.0', 'wow.agent-output.v1', 'FITTED_MODEL', 'MODEL_QUALIFIED_HOLD',
     '{wow.evidence-hydration}', 60, 1, true, '{}'::jsonb, true),
    ('wow.failure-path-framework', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'MODEL_QUALIFIED_HOLD',
     '{wow.controlling-model}', 30, 1, false, '{}'::jsonb, true),
    ('wow.dynamic-calibration-expert', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'MODEL_QUALIFIED_HOLD',
     '{wow.failure-path-framework}', 30, 1, true, '{}'::jsonb, true),
    ('wow.exact-line-market-auditor', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'MARKET_VERIFIED_HOLD',
     '{wow.dynamic-calibration-expert}', 30, 2, false, '{}'::jsonb, true),
    ('wow.structure-exposure-governor', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'STRUCTURE_VERIFIED_HOLD',
     '{wow.exact-line-market-auditor}', 20, 1, false, '{}'::jsonb, true),
    ('wow.final-refresh-governor', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'FINAL_REFRESH_HOLD',
     '{wow.structure-exposure-governor}', 30, 2, false, '{}'::jsonb, true),
    ('wow.terminal-ceiling-reducer', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'FINAL_APPROVED',
     '{wow.final-refresh-governor}', 15, 0, false, '{}'::jsonb, true)
on conflict (worker_id, worker_version) do update
set contract_version = excluded.contract_version,
    implementation_type = excluded.implementation_type,
    authority_ceiling = excluded.authority_ceiling,
    required_predecessors = excluded.required_predecessors,
    timeout_seconds = excluded.timeout_seconds,
    max_retries = excluded.max_retries,
    artifact_required = excluded.artifact_required,
    configuration = excluded.configuration,
    enabled = excluded.enabled;

-- Exact registry parity is a production readiness invariant. Retire any stale
-- enabled runtime-worker versions so /health/ready cannot pass with an
-- ungoverned parallel worker set.
update public.wow_agent_worker_registry
set enabled = false
where enabled = true
  and (worker_id, worker_version) not in (
    ('wow.parallel-discovery-router','1.0.0'),
    ('wow.global-scout-coordinator','1.0.0'),
    ('wow.prop-scout-router','1.0.0'),
    ('wow.ml-event-scout-router','1.0.0'),
    ('wow.slate-integrity-expert','1.0.0'),
    ('wow.source-provenance-researcher','1.0.0'),
    ('wow.participant-status-researcher','1.0.0'),
    ('wow.history-comparables-researcher','1.0.0'),
    ('wow.matchup-context-researcher','1.0.0'),
    ('wow.market-settlement-researcher','1.0.0'),
    ('wow.research-evidence-reconciler','1.0.0'),
    ('wow.evidence-hydration','1.0.0'),
    ('wow.controlling-model','1.0.0'),
    ('wow.failure-path-framework','1.0.0'),
    ('wow.dynamic-calibration-expert','1.0.0'),
    ('wow.exact-line-market-auditor','1.0.0'),
    ('wow.structure-exposure-governor','1.0.0'),
    ('wow.final-refresh-governor','1.0.0'),
    ('wow.terminal-ceiling-reducer','1.0.0')
  );

commit;
