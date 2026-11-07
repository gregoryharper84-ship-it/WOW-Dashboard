-- Schedule geometry may write only REST_TRAVEL. Kickoff and venue identity come
-- from a /games snapshot retrieved before the target kickoff. Venue coordinates
-- come from a /venues snapshot also retrieved before that kickoff. Scores,
-- ratings, box scores, forecasts, and market prices are not inputs.
insert into public.wow_ncaaf_evidence_sources (
  provider_key, provider_class, max_provenance_grade, allowed_evidence_kinds,
  active, approval_reference, notes, can_execute
) values
('CFBD_SCHEDULE_GEOMETRY_V1','RESEARCH_SOURCE','B',array['REST_TRAVEL'],true,'CODE:ncaaf_pregame_feature_acquisition.py@V1','Derived rest days and away travel from archived pre-kickoff CFBD schedule and venue snapshots. Prior game start must be strictly before the target kickoff. No score, rating, box score, forecast, or market input. Source SQL only; not a production migration.',false)
on conflict (provider_key) do update set
  provider_class=excluded.provider_class,
  max_provenance_grade=excluded.max_provenance_grade,
  allowed_evidence_kinds=excluded.allowed_evidence_kinds,
  active=excluded.active,
  approval_reference=excluded.approval_reference,
  notes=excluded.notes,
  can_execute=false;
