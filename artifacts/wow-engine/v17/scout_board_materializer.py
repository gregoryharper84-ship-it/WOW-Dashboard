"""Materialize research-only Scout boards from durable state."""
from __future__ import annotations

import argparse
import json
from datetime import date

import psycopg

from v17.scout_brain_persistence import database_url

SPORTS = {
    "americanfootball_ncaaf": "CFB",
    "americanfootball_nfl": "NFL",
    "baseball_mlb": "MLB",
    "basketball_nba": "NBA",
    "basketball_wnba": "WNBA",
}


def materialize(slate_date: date) -> dict:
    out = {"slate_date": slate_date.isoformat(), "boards": [], "can_execute": False}
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            for sport_key, label in SPORTS.items():
                cur.execute("""
                    select candidate_id,research_status,research_priority_score,controlling_specialist,commence_time,
                           coalesce(thesis,''),edge_classes,contradictory_evidence,red_team_flags
                    from wow_scout.candidates
                    where sport_key=%s and commence_time::date=%s
                    order by research_priority_score desc nulls last, updated_at desc
                """, (sport_key, slate_date))
                rows = cur.fetchall()
                active = [r for r in rows if r[1] in ('RESEARCH_INTEREST_LOW','RESEARCH_INTEREST_MEDIUM','RESEARCH_INTEREST_HIGH')]
                quarantined = [r for r in rows if r[1] == 'QUARANTINED']
                unresolved = [r for r in rows if r[1] == 'WATCH']
                payload = {
                    "sport": label,
                    "sport_key": sport_key,
                    "research_interest": [
                        {"candidate_id":r[0],"status":r[1],"research_priority_score":float(r[2]) if r[2] is not None else None,
                         "controlling_specialist":r[3],"commence_time":r[4].isoformat() if r[4] else None,"thesis":r[5],
                         "edge_classes":r[6],"contradictory_evidence":r[7],"red_team_flags":r[8],"probability":None}
                        for r in active
                    ],
                    "quarantined_candidate_ids": [r[0] for r in quarantined],
                    "unresolved_candidate_ids": [r[0] for r in unresolved],
                    "probability_authority": "CONTROLLING_SPECIALIST_ONLY",
                    "can_execute": False,
                }
                board_id = f"{sport_key}:{slate_date.isoformat()}:SCOUT_FINAL_BOARD"
                cur.execute("""
                    insert into wow_scout.final_boards
                    (board_id,sport_key,slate_date,board_type,status,candidate_ids,quarantined_candidate_ids,unresolved_candidate_ids,
                     specialist_handoff_ready,payload,can_execute)
                    values (%s,%s,%s,'SCOUT_FINAL_BOARD','MATERIALIZED',%s,%s,%s,%s,%s::jsonb,false)
                    on conflict (sport_key,slate_date,board_type) do update set
                      generated_at=now(),status='MATERIALIZED',candidate_ids=excluded.candidate_ids,
                      quarantined_candidate_ids=excluded.quarantined_candidate_ids,unresolved_candidate_ids=excluded.unresolved_candidate_ids,
                      specialist_handoff_ready=excluded.specialist_handoff_ready,payload=excluded.payload,can_execute=false
                """, (board_id,sport_key,slate_date,[r[0] for r in active],[r[0] for r in quarantined],[r[0] for r in unresolved],bool(active),json.dumps(payload)))
                out["boards"].append({"sport":label,"research_interest":len(active),"quarantined":len(quarantined),"unresolved":len(unresolved)})
        conn.commit()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    result = materialize(date.fromisoformat(args.date))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
