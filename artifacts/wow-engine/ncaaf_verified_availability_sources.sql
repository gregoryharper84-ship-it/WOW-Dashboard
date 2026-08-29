-- Reviewed official-conference raw availability sources only.
-- These providers may write PLAYER_AVAILABILITY_REPORT evidence; they do not
-- directly authorize QB certainty, skill availability, model scoring, publication,
-- or execution. Downstream aggregation remains separately governed.
insert into public.wow_ncaaf_evidence_sources (
  provider_key, provider_class, max_provenance_grade, allowed_evidence_kinds,
  active, approval_reference, notes, can_execute
) values
('BIG12_OFFICIAL_AVAILABILITY','OFFICIAL_CONFERENCE','A',array['PLAYER_AVAILABILITY_REPORT'],true,'https://big12sports.com/sports/2025/8/21/availability-reporting.aspx','Verified official Big 12 availability policy; raw report evidence only; parser/source semantics reviewed 2026-08-29.',false),
('ACC_OFFICIAL_AVAILABILITY','OFFICIAL_CONFERENCE','A',array['PLAYER_AVAILABILITY_REPORT'],true,'https://theacc.com/sports/2025/8/28/availability-reporting.aspx','Verified official ACC availability policy; raw report evidence only; parser/source semantics reviewed 2026-08-29.',false),
('BIGTEN_OFFICIAL_AVAILABILITY','OFFICIAL_CONFERENCE','A',array['PLAYER_AVAILABILITY_REPORT'],true,'https://bigten.org/fb/article/60284/','Verified official Big Ten availability policy; categorical raw report evidence only; no invented numeric probability.',false),
('SUNBELT_OFFICIAL_AVAILABILITY','OFFICIAL_CONFERENCE','A',array['PLAYER_AVAILABILITY_REPORT'],true,'https://sunbeltsports.org/news/2025/8/20/sun-belt-to-institute-availability-reporting-for-2025-football-season.aspx','Verified official Sun Belt availability policy; categorical raw report evidence only; no invented numeric probability.',false)
on conflict (provider_key) do update set
  provider_class=excluded.provider_class,
  max_provenance_grade=excluded.max_provenance_grade,
  allowed_evidence_kinds=excluded.allowed_evidence_kinds,
  active=excluded.active,
  approval_reference=excluded.approval_reference,
  notes=excluded.notes,
  can_execute=false;
