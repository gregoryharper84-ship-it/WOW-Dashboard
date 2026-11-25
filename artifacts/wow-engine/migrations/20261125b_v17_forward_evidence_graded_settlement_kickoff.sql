-- Follow-up to 20261125_v17_forward_evidence_lifecycle (kept separate so the
-- approved file SHA is unchanged). A capture is graded only if it preceded the
-- kickoff recorded on the settlement itself. Apply only after that migration.

create or replace view public.wow_v17_forward_evidence_graded
with (security_invoker = true) as
with latest as (
  select distinct on (c.registration_id, c.official_event_id) c.*
    from public.wow_v17_forward_evidence_captures c
   where c.created_at < c.event_start_time_utc
   order by c.registration_id, c.official_event_id, c.created_at desc, c.capture_id desc
)
select
  l.registration_id,
  r.sport,
  r.league,
  r.market_family,
  r.cohort_role,
  r.outcome_space,
  r.model_family,
  r.model_artifact_version,
  r.artifact_checksum,
  l.capture_id,
  l.official_event_id,
  l.event_start_time_utc,
  l.created_at as prediction_created_at,
  l.probabilities,
  s.settlement_id,
  s.outcome,
  s.settlement_source,
  (l.probabilities ->> s.outcome)::double precision as outcome_probability,
  case r.outcome_space
    when 'BINARY_HOME_AWAY' then
      power((l.probabilities ->> 'HOME')::double precision - (s.outcome = 'HOME')::int, 2)
    else (
      select sum(power((e.value #>> '{}')::double precision - (e.key = s.outcome)::int, 2))
        from jsonb_each(l.probabilities) e)
  end as brier,
  -ln(greatest((l.probabilities ->> s.outcome)::double precision, 1e-15)) as log_loss,
  false as probability_publishable,
  false as can_execute
from latest l
join public.wow_v17_forward_evidence_registrations r on r.registration_id = l.registration_id
join public.wow_v17_forward_evidence_settlements s
  on s.sport = r.sport and s.league = r.league and s.official_event_id = l.official_event_id
where s.outcome <> 'VOID'
  and l.created_at < s.event_start_time_utc
  and l.artifact_checksum = r.artifact_checksum
  and not (r.outcome_space = 'BINARY_HOME_AWAY' and s.outcome = 'DRAW');

-- Least privilege: the view inherited default write grants for service_role.
revoke insert, update, delete, truncate, references, trigger
  on table public.wow_v17_forward_evidence_graded from service_role;
