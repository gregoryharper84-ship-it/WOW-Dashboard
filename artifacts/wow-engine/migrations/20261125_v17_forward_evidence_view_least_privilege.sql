-- Class B (least privilege) | Incident #1951 | migration candidate, NOT APPLIED
-- 20261125_v17_forward_evidence_lifecycle revoked client roles from the graded
-- view and granted service_role SELECT, but Supabase default privileges had
-- already given service_role every privilege on the new view (readback
-- 2026-11-25 15:37 UTC: DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE).
-- The view is not updatable (DISTINCT ON + joins, no INSTEAD OF triggers),
-- so this is hygiene, not a live write path. Leave SELECT only.
revoke insert, update, delete, truncate, references, trigger
  on table public.wow_v17_forward_evidence_graded from service_role;
grant select on table public.wow_v17_forward_evidence_graded to service_role;
