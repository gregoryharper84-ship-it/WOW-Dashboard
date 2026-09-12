"""Bootstrap provider team mappings using exact full-name reconciliation only."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import psycopg

try:
    from v17.scout_brain_persistence import database_url
except ModuleNotFoundError:
    from scout_brain_persistence import database_url


def normalize_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def provider_full_name(row: dict[str, Any]) -> str | None:
    for field in ("FullName", "TeamName", "School"):
        value = row.get(field)
        if value and str(value).strip():
            return str(value).strip()
    city = str(row.get("City") or "").strip()
    name = str(row.get("Name") or "").strip()
    combined = " ".join(x for x in (city, name) if x).strip()
    return combined or None


def provider_team_id(row: dict[str, Any]) -> str | None:
    for field in ("TeamID", "GlobalTeamID"):
        value = row.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return None


def canonical_team_id(sport_key: str, team_name: str) -> str:
    digest = hashlib.sha256((sport_key + "|" + normalize_name(team_name)).encode("utf-8")).hexdigest()[:18]
    return f"team:{sport_key}:{digest}"


def candidate_team_names(sport_key: str) -> list[str]:
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select team_name from (
                  select home_team as team_name from wow_scout.candidates where sport_key=%s and home_team is not null
                  union
                  select away_team as team_name from wow_scout.candidates where sport_key=%s and away_team is not null
                ) q order by team_name
                """,
                (sport_key, sport_key),
            )
            return [str(r[0]) for r in cur.fetchall()]


def bootstrap(provider: str, sport_key: str, rows: list[dict[str, Any]], *, verification_source: str) -> dict[str, Any]:
    names = candidate_team_names(sport_key)
    exact_index: dict[str, list[str]] = {}
    for name in names:
        exact_index.setdefault(normalize_name(name), []).append(name)

    verified_rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            for row in rows:
                pid = provider_team_id(row)
                pname = provider_full_name(row)
                if not pid or not pname:
                    unresolved.append({"provider_entity_id": pid, "provider_name": pname, "reason_code": "PROVIDER_TEAM_ID_OR_FULL_NAME_MISSING"})
                    continue
                matches = exact_index.get(normalize_name(pname), [])
                if not matches:
                    unresolved.append({"provider_entity_id": pid, "provider_name": pname, "reason_code": "EXACT_CANONICAL_NAME_MATCH_MISSING"})
                    continue
                if len(matches) != 1:
                    ambiguous.append({"provider_entity_id": pid, "provider_name": pname, "matches": matches, "reason_code": "EXACT_NAME_AMBIGUOUS"})
                    continue
                canonical_name = matches[0]
                canonical_id = canonical_team_id(sport_key, canonical_name)
                provider_key = row.get("Key") or row.get("Team")
                cur.execute(
                    """
                    insert into wow_scout.provider_entity_map
                    (provider,sport_key,entity_type,provider_entity_id,provider_entity_key,canonical_entity_id,canonical_name,aliases,
                     verified,verification_source,verified_at,updated_at)
                    values (%s,%s,'TEAM',%s,%s,%s,%s,%s,true,%s,now(),now())
                    on conflict (provider,sport_key,entity_type,provider_entity_id) do update set
                      provider_entity_key=excluded.provider_entity_key,
                      canonical_entity_id=excluded.canonical_entity_id,
                      canonical_name=excluded.canonical_name,
                      aliases=excluded.aliases,
                      verified=true,
                      verification_source=excluded.verification_source,
                      verified_at=now(),updated_at=now()
                    """,
                    (provider,sport_key,pid,str(provider_key) if provider_key else None,canonical_id,canonical_name,[pname],verification_source),
                )
                verified_rows.append({"provider_entity_id": pid, "provider_name": pname, "canonical_entity_id": canonical_id, "canonical_name": canonical_name})
        conn.commit()
    return {
        "provider": provider,
        "sport_key": sport_key,
        "verified": verified_rows,
        "unresolved": unresolved,
        "ambiguous": ambiguous,
        "matching_policy": "EXACT_NORMALIZED_FULL_NAME_ONLY",
        "prediction_authority": False,
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="SPORTSDATAIO")
    parser.add_argument("--sport", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--verification-source", default="PROVIDER_TEAM_PROFILE_EXACT_MATCH")
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    result = bootstrap(args.provider, args.sport, [r for r in rows if isinstance(r, dict)], verification_source=args.verification_source)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
