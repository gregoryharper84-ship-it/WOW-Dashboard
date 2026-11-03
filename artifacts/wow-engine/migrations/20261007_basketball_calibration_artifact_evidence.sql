-- Add immutable shadow calibration-artifact evidence for NBA/WNBA team-event fits.
-- Class C evidence generation only. No certification, promotion, or probability publication.
--
-- The digest is content-derived, not a label. Before this change the stored
-- `calibration_artifact_sha256` was only shape-checked, so a mutated payload
-- (changed Platt coefficient, residual or bounds claim) could keep its declared
-- fingerprint. `wow_canonical_json_text` gives PostgreSQL the same canonical
-- serialization the Python artifact builder uses
-- (v17/multisport_team_event_calibration.canonical_calibration_json): sorted
-- keys, compact separators, UTF-8 text preserved, jsonb numeric scale preserved.
-- The trigger then recomputes SHA-256 over the stored payload and rejects any
-- row whose declared digest does not match its content.

alter table public.wow_basketball_team_event_calibrators
  add column if not exists calibration_artifact_payload jsonb,
  add column if not exists calibration_artifact_sha256 text;

alter table public.wow_basketball_team_event_calibrators
  drop constraint if exists wow_basketball_calibration_artifact_sha256_shape;

alter table public.wow_basketball_team_event_calibrators
  add constraint wow_basketball_calibration_artifact_sha256_shape
  check (
    calibration_artifact_sha256 is null
    or calibration_artifact_sha256 ~ '^[0-9a-f]{64}$'
  );

-- Canonical JSON rendering. Object keys are ordered by UTF-8 byte sequence
-- (identical to Python's code-point ordering); number rendering matches jsonb
-- numeric text so `240.0`, `0.9` and `0.30000000000000004` round-trip verbatim.
create or replace function public.wow_canonical_json_text(value jsonb)
returns text
language plpgsql
immutable
set search_path=''
as $$
declare
  rendered text;
begin
  if value is null then
    return 'null';
  end if;
  case jsonb_typeof(value)
    when 'object' then
      select '{' || coalesce(
               string_agg(
                 public.wow_canonical_json_text(to_jsonb(entry.key)) || ':' ||
                 public.wow_canonical_json_text(entry.value),
                 ',' order by convert_to(entry.key, 'UTF8')
               ), '') || '}'
        into rendered
        from jsonb_each(value) as entry(key, value);
      return rendered;
    when 'array' then
      select '[' || coalesce(
               string_agg(
                 public.wow_canonical_json_text(element.value),
                 ',' order by element.ordinality
               ), '') || ']'
        into rendered
        from jsonb_array_elements(value) with ordinality as element(value, ordinality);
      return rendered;
    when 'string' then
      return to_jsonb(value #>> '{}')::text;
    when 'number' then
      return (value #>> '{}');
    when 'boolean' then
      return case when (value #>> '{}') = 'true' then 'true' else 'false' end;
    else
      return 'null';
  end case;
end;
$$;

revoke all on function public.wow_canonical_json_text(jsonb) from public, anon, authenticated;
-- The digest trigger executes as the inserting/updating role. Authorize the
-- trusted internal writer to compute canonical hashes, not public clients.
grant execute on function public.wow_canonical_json_text(jsonb) to service_role;

-- Fail closed on any client-supplied digest that does not describe the payload.
create or replace function public.wow_basketball_calibrator_artifact_digest()
returns trigger
language plpgsql
set search_path=''
as $$
declare
  computed text;
begin
  if new.calibration_artifact_payload is null then
    return new;
  end if;
  if new.calibration_artifact_sha256 is null then
    raise exception 'BASKETBALL_CALIBRATION_ARTIFACT_DIGEST_MISSING';
  end if;
  computed := encode(
    sha256(convert_to(public.wow_canonical_json_text(new.calibration_artifact_payload), 'UTF8')),
    'hex'
  );
  if computed <> lower(new.calibration_artifact_sha256) then
    raise exception 'BASKETBALL_CALIBRATION_ARTIFACT_DIGEST_MISMATCH';
  end if;
  return new;
end;
$$;

revoke all on function public.wow_basketball_calibrator_artifact_digest() from public, anon, authenticated;

drop trigger if exists wow_basketball_calibrator_artifact_digest_guard
  on public.wow_basketball_team_event_calibrators;

create trigger wow_basketball_calibrator_artifact_digest_guard
before insert or update on public.wow_basketball_team_event_calibrators
for each row execute function public.wow_basketball_calibrator_artifact_digest();
