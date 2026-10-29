"""Emit bounded, hash-verified public NCAA/ESPN outcome candidates.

This one-shot workflow-dispatch log transport is consumed by an authorized
read-only SQL auditor. No GitHub Actions secrets, Supabase credentials, private
database rows, player names, market prices, or model probabilities are logged.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

CHUNK_PREFIX = "WOW_NCAAF_CANDIDATE_CHUNK_V1="
META_PREFIX = "WOW_NCAAF_CANDIDATE_MANIFEST_V1="
MAX_ROWS = 100_000
ROWS_PER_LOG = 100
STATS = frozenset((
    "PASSING_YARDS", "COMPLETIONS", "PASS_ATTEMPTS",
    "RUSHING_YARDS", "RUSH_ATTEMPTS", "RECEIVING_YARDS", "RECEPTIONS",
))
CAN_EXECUTE = False


class FullAuditLogHold(ValueError):
    pass


def audited_lines(manifest: dict, candidate_rows: list) -> list[str]:
    if (manifest.get("status") != "NCAAF_NCAA_ESPN_SOURCE_CROSSWALK_RESEARCH_ONLY"
            or manifest.get("season") != 2024
            or manifest.get("can_execute") is not False
            or manifest.get("model_training_authorized_by_crosswalk") is not False
            or manifest.get("probability_publishable") is not False
            or not isinstance(candidate_rows, list)
            or not 1 <= len(candidate_rows) <= MAX_ROWS
            or manifest.get("full_candidate_stat_cells") != len(candidate_rows)):
        raise FullAuditLogHold("NCAAF_FULL_SOURCE_MANIFEST_INVALID")
    payload = json.dumps(candidate_rows, sort_keys=True, separators=(",", ":")).encode()
    digest = sha256(payload).hexdigest()
    if manifest.get("full_candidate_stat_rows_sha256") != digest:
        raise FullAuditLogHold("NCAAF_FULL_SOURCE_HASH_MISMATCH")
    seen: set[tuple[str, str, str]] = set()
    for row in candidate_rows:
        if not isinstance(row, dict) or set(row) != {
                "event_id", "athlete_id", "stat_type", "actual_value", "espn_team_id"}:
            raise FullAuditLogHold("NCAAF_FULL_SOURCE_ROW_SCHEMA_INVALID")
        e,p,stat,value,team=(row["event_id"],row["athlete_id"],
            row["stat_type"],row["actual_value"],row["espn_team_id"])
        if (not isinstance(e,str) or not re.fullmatch(r"[1-9][0-9]{0,19}",e)
                or not isinstance(p,str) or not re.fullmatch(r"[1-9][0-9]{0,19}",p)
                or stat not in STATS or type(value) is not int
                or not -1000 <= value <= 2000
                or (stat in {"COMPLETIONS","PASS_ATTEMPTS","RUSH_ATTEMPTS","RECEPTIONS"}
                    and value < 0)
                or (team is not None and (not isinstance(team,str) or
                    not re.fullmatch(r"[1-9][0-9]{0,19}", team)))):
            raise FullAuditLogHold("NCAAF_FULL_SOURCE_ROW_INVALID")
        key=(e,p,stat)
        if key in seen:
            raise FullAuditLogHold("NCAAF_FULL_SOURCE_DUPLICATE")
        seen.add(key)
    if candidate_rows != sorted(candidate_rows,
                                key=lambda r:(r["event_id"],r["athlete_id"],r["stat_type"])):
        raise FullAuditLogHold("NCAAF_FULL_SOURCE_ORDER_INVALID")
    meta = {
        "season": 2024,
        "sha256": digest,
        "rows": len(candidate_rows),
        "chunks": (len(candidate_rows)+ROWS_PER_LOG-1)//ROWS_PER_LOG,
        "ncaa_source_sha256": manifest.get("ncaa_source_sha256"),
        "espn_source_sha256": manifest.get("espn_source_sha256"),
        "source_independence_certified": False,
        "cfbd_team_identity_verified": False,
        "historical_pregame_features_verified": False,
        "model_training_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    for field in ("ncaa_source_sha256","espn_source_sha256"):
        if not re.fullmatch("[0-9a-f]{64}",str(meta[field])):
            raise FullAuditLogHold("NCAAF_FULL_SOURCE_PROVENANCE_MISSING")
    lines=[META_PREFIX+json.dumps(meta,sort_keys=True,separators=(",", ":"))]
    for i in range(meta["chunks"]):
        subset=candidate_rows[i*ROWS_PER_LOG:(i+1)*ROWS_PER_LOG]
        lines.append(CHUNK_PREFIX+json.dumps({
            "i":i,"rows":subset,
            "chunk_sha256":sha256(json.dumps(subset,sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
        },sort_keys=True,separators=(",", ":")))
    return lines


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--candidate-file",required=True)
    parser.add_argument("--manifest",required=True)
    args=parser.parse_args()
    manifest=json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    candidates=json.loads(Path(args.candidate_file).read_text(encoding="utf-8"))
    for line in audited_lines(manifest,candidates):
        print(line,flush=True)


if __name__=="__main__":
    main()
