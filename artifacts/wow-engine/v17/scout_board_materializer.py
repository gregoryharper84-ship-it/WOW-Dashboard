"""Materialize research-only Scout boards from durable state.

Scout probability remains NULL by contract. Publishable probability shown on a
board is read at render time from the controlling specialist's governed ledger.
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import psycopg

from v17.scout_brain_persistence import database_url
from v17.scout_governed_probability_lookup import lookup_governed_probability

SPORTS = {
    "americanfootball_ncaaf": "CFB",
    "americanfootball_nfl": "NFL",
    "baseball_mlb": "MLB",
    "basketball_nba": "NBA",
    "basketball_wnba": "WNBA",
}


def _candidate(row: tuple) -> dict:
    return {
        "candidate_id": row[0],
        "research_status": row[1],
        "research_priority_score": float(row[2]) if row[2] is not None else None,
        "controlling_specialist": row[3],
        "commence_time": row[4],
        "thesis": row[5],
        "edge_classes": row[6],
        "contradictory_evidence": row[7],
        "red_team_flags": row[8],
        "event_id": row[9],
        "canonical_event_id": row[10],
        "home_team": row[11],
        "away_team": row[12],
        "can_execute": row[13],
        "selection": row[14],
        "market_type": row[15],
        "sport_key": row[16],
    }


def _display_row(cur, candidate: dict) -> dict:
    governed = lookup_governed_probability(cur, candidate)
    probability = governed.get("governed_probability")
    lower = governed.get("calibrated_lower_bound")
    return {
        "candidate_id": candidate["candidate_id"],
        "pick": governed.get("governed_selection") or candidate.get("selection"),
        "status": candidate["research_status"],
        "research_priority_score": candidate["research_priority_score"],
        "controlling_specialist": candidate["controlling_specialist"],
        "commence_time": candidate["commence_time"].isoformat() if candidate["commence_time"] else None,
        "home_team": candidate.get("home_team"),
        "away_team": candidate.get("away_team"),
        "thesis": candidate["thesis"],
        "edge_classes": candidate["edge_classes"],
        "contradictory_evidence": candidate["contradictory_evidence"],
        "red_team_flags": candidate["red_team_flags"],
        # Deliberately preserve the Scout probability field as NULL. The fields
        # below come only from a publishable controlling-specialist record.
        "probability": None,
        **governed,
        "governed_probability_pct": round(float(probability) * 100.0, 2) if probability is not None else None,
        "calibrated_lower_bound_pct": round(float(lower) * 100.0, 2) if lower is not None else None,
        "probability_display": f"{float(probability) * 100.0:.2f}%" if probability is not None else "—",
        "lower_bound_display": f"{float(lower) * 100.0:.2f}%" if lower is not None else "—",
        "can_execute": False,
    }


def materialize(slate_date: date) -> dict:
    out = {"slate_date": slate_date.isoformat(), "boards": [], "can_execute": False}
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            for sport_key, label in SPORTS.items():
                cur.execute("""
                    select candidate_id,research_status,research_priority_score,controlling_specialist,commence_time,
                           coalesce(thesis,''),edge_classes,contradictory_evidence,red_team_flags,
                           event_id,canonical_event_id,home_team,away_team,can_execute,selection,market_type,sport_key
                    from wow_scout.candidates
                    where sport_key=%s and commence_time::date=%s
                    order by research_priority_score desc nulls last, updated_at desc
                """, (sport_key, slate_date))
                candidates = [_candidate(row) for row in cur.fetchall()]
                active = [row for row in candidates if row["research_status"] in ('RESEARCH_INTEREST_LOW','RESEARCH_INTEREST_MEDIUM','RESEARCH_INTEREST_HIGH')]
                quarantined = [row for row in candidates if row["research_status"] == 'QUARANTINED']
                unresolved = [row for row in candidates if row["research_status"] == 'WATCH']
                research_interest = [_display_row(cur, row) for row in active]
                payload = {
                    "sport": label,
                    "sport_key": sport_key,
                    "research_interest": research_interest,
                    "quarantined_candidate_ids": [row["candidate_id"] for row in quarantined],
                    "unresolved_candidate_ids": [row["candidate_id"] for row in unresolved],
                    "probability_authority": "CONTROLLING_SPECIALIST_ONLY",
                    "market_probability_is_governed_probability": False,
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
                """, (
                    board_id, sport_key, slate_date,
                    [row["candidate_id"] for row in active],
                    [row["candidate_id"] for row in quarantined],
                    [row["candidate_id"] for row in unresolved],
                    bool(active), json.dumps(payload),
                ))
                published = sum(1 for row in research_interest if row.get("probability_publishable") is True)
                out["boards"].append({
                    "sport": label,
                    "research_interest": len(active),
                    "governed_probabilities_published": published,
                    "quarantined": len(quarantined),
                    "unresolved": len(unresolved),
                })
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
