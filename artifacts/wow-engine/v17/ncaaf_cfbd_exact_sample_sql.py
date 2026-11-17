"""Build a read-only, bounded SQL receipt for exact-key CFBD research checks.

No DB credentials, writes, wagers or model authorization. Input is a public
source manifest carrying <=28 stat candidates admitted by NCAA/ESPN parity.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

ALLOWED_STATS = frozenset((
    "PASSING_YARDS", "COMPLETIONS", "PASS_ATTEMPTS", "RUSHING_YARDS",
    "RUSH_ATTEMPTS", "RECEPTIONS", "RECEIVING_YARDS",
))
MANIFEST_SCHEMA = "WOW_V17_NCAAF_NCAA_ESPN_PLAYER_CROSSWALK_RESEARCH_V1"
MAX_SAMPLE = 28
CAN_EXECUTE = False


class ExactSampleHold(ValueError):
    pass


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value):
        raise ExactSampleHold(f"NCAAF_CFBD_{name}_INVALID")
    if int(value) <= 0:
        raise ExactSampleHold(f"NCAAF_CFBD_{name}_INVALID")
    return str(int(value))


def build_read_only_sql(receipt: Mapping[str, Any], *, season: int = 2024) -> str:
    if (type(season) is not int or not 2010 <= season <= 2030
            or receipt.get("season") != season or receipt.get("schema") != MANIFEST_SCHEMA
            or receipt.get("status") != "NCAAF_NCAA_ESPN_SOURCE_CROSSWALK_RESEARCH_ONLY"
            or receipt.get("can_execute") is not False
            or receipt.get("model_training_authorized_by_crosswalk") is not False):
        raise ExactSampleHold("NCAAF_CFBD_RECEIPT_SCOPE_INVALID")
    sample = receipt.get("cfbd_exact_match_sample")
    if not isinstance(sample, list) or not 1 <= len(sample) <= MAX_SAMPLE:
        raise ExactSampleHold("NCAAF_CFBD_SAMPLE_SIZE_INVALID")
    canonical = json.dumps(sample, sort_keys=True, separators=(",", ":")).encode()
    if receipt.get("cfbd_exact_match_sample_sha256") != sha256(canonical).hexdigest():
        raise ExactSampleHold("NCAAF_CFBD_SAMPLE_HASH_MISMATCH")
    if receipt.get("cfbd_exact_match_sample_size") != len(sample):
        raise ExactSampleHold("NCAAF_CFBD_SAMPLE_COUNT_MISMATCH")
    if not all(re.fullmatch(r"[a-f0-9]{64}", str(receipt.get(k, "")))
               for k in ("ncaa_source_sha256", "espn_source_sha256")):
        raise ExactSampleHold("NCAAF_CFBD_SOURCE_HASH_INVALID")

    lines = []
    seen = set()
    for record in sample:
        if not isinstance(record, dict) or set(record) != {
                "event_id", "athlete_id", "stat_type", "actual_value", "espn_team_id"}:
            raise ExactSampleHold("NCAAF_CFBD_SAMPLE_ROW_INVALID")
        event_id = _id(record["event_id"], "EVENT_ID")
        athlete_id = _id(record["athlete_id"], "ATHLETE_ID")
        stat = record["stat_type"]
        actual = record["actual_value"]
        if stat not in ALLOWED_STATS or type(actual) is not int or not -1000 <= actual <= 2000:
            raise ExactSampleHold("NCAAF_CFBD_STAT_INVALID")
        if stat in {"COMPLETIONS", "PASS_ATTEMPTS", "RECEPTIONS", "RUSH_ATTEMPTS"} and actual < 0:
            raise ExactSampleHold("NCAAF_CFBD_COUNT_NEGATIVE")
        team_id = record["espn_team_id"]
        if team_id is not None:
            _id(team_id, "ESPN_TEAM_ID")
        key = (event_id, athlete_id, stat)
        if key in seen:
            raise ExactSampleHold("NCAAF_CFBD_SAMPLE_DUPLICATE_KEY")
        seen.add(key)
        lines.append(f"  ('{event_id}','{athlete_id}','{stat}',{actual})")
    values = ",\n".join(lines)
    # Caller executes via a privileged, read-only Supabase session. This
    # query never returns the original player names or long event-level rows.
    return f"""-- RESEARCH ONLY | NCAA+ESPN source parity; exact CFBD key/value check.
-- No training admission, sportsbook market, DDL or write operations.
-- Sample checksum: {receipt["cfbd_exact_match_sample_sha256"]}
-- NCAA source SHA: {receipt["ncaa_source_sha256"]}
-- ESPN source SHA: {receipt["espn_source_sha256"]}
WITH sample(event_id, athlete_id, stat_type, candidate_value) AS (
 VALUES
{values}
), compared AS (
 SELECT s.stat_type, s.event_id, s.athlete_id, s.candidate_value,
        count(o.outcome_id) AS cfbd_row_count,
        min(o.actual_value) AS cfbd_value,
        min(o.event_start_time) AS earliest_event_start,
        max(o.observed_at) AS latest_observed_at
 FROM sample s
 LEFT JOIN wow_prop_history.ncaaf_player_game_outcomes o
   ON o.season = {season}
  AND o.official_event_id = s.event_id
  AND o.official_player_id = s.athlete_id
  AND o.stat_type = s.stat_type
  AND o.source_provider = 'CFBD'
  AND o.source_status = 'NORMALIZED_RESEARCH_ONLY'
 GROUP BY s.stat_type,s.event_id,s.athlete_id,s.candidate_value
), classified AS (
 SELECT stat_type,event_id,athlete_id,
 CASE
   WHEN cfbd_row_count=0 THEN 'CFBD_EXACT_KEY_NOT_FOUND'
   WHEN cfbd_row_count<>1 THEN 'CFBD_DUPLICATE_SOURCE_KEY'
   WHEN cfbd_value<>candidate_value THEN 'CFBD_STAT_CONTRADICTION'
   WHEN latest_observed_at<=earliest_event_start THEN 'CFBD_OUTCOME_TIMESTAMP_INVALID'
   ELSE 'CFBD_EXACT_EVENT_PLAYER_STAT_VALUE_MATCH_TEAM_UNVERIFIED'
 END AS audit_status
 FROM compared
)
SELECT stat_type,audit_status,
 count(*)::int AS sample_stat_rows,
 count(DISTINCT event_id)::int AS distinct_events,
 count(DISTINCT (event_id,athlete_id))::int AS distinct_player_games,
 false AS team_crosswalk_verified,
 false AS model_training_eligible,
 false AS probability_publishable,
 false AS can_execute
FROM classified
GROUP BY stat_type,audit_status
ORDER BY stat_type,audit_status;
"""


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--season", type=int, default=2024)
    parser.add_argument("--output-sql", required=True)
    args = parser.parse_args()
    report = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    query = build_read_only_sql(report, season=args.season)
    Path(args.output_sql).write_text(query, encoding="utf-8")
    print(json.dumps({
        "status": "CFBD_READ_ONLY_EXACT_KEY_SQL_READY",
        "sample_n": len(report["cfbd_exact_match_sample"]),
        "sql_sha256": sha256(query.encode()).hexdigest(),
        "model_training_eligible": False,
        "can_execute": False,
    }, sort_keys=True))
