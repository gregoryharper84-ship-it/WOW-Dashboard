-- Security hardening for LLP-PATCH-2026-09-05-MLB-STARTER-SEQUENCING-DISPERSION
alter function public.wow_mlb_msd_v17_contract_status()
set search_path = public, pg_temp;
