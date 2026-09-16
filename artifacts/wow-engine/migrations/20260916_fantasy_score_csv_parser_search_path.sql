-- V17 Fantasy Score CSV parser search_path hardening.
-- Security-only. No parsing/model/lifecycle behavior changes.

alter function public.wow_v17_csv_fields(text)
  set search_path = pg_catalog;

revoke all on function public.wow_v17_csv_fields(text) from public, anon, authenticated;
grant execute on function public.wow_v17_csv_fields(text) to service_role;

comment on function public.wow_v17_csv_fields(text) is
  'V17 Fantasy Score CSV parser. search_path pinned to pg_catalog; service-role-only execution. Parsing/model/publication/execution semantics unchanged.';
