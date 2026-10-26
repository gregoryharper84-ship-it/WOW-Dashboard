"""Read-only replay validator for NCAAF 44-event CFBD + ESPN forward research features.

Uses frozen 2026-10-22 evidence, not betting markets, not rich NCAAF_FEATURES_V1.
Does not score, calibrate, promote, certify, publish, persist or execute.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from math import isfinite, log1p
from pathlib import Path
from typing import Any

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
EXPECTED_FAMILY = "NCAAF_RESULT_FORM_LOGIT_V1"
EXPECTED_SCHEMA = "NCAAF_RESULT_FORM_PRIOR_V1"
EXPECTED_SOURCE_POLICY = "NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1"
FEATURE_NAMES = (
    "home_win_rate_prior", "away_win_rate_prior",
    "home_point_diff_prior", "away_point_diff_prior",
    "home_games_prior_log", "away_games_prior_log",
    "home_rest_days_capped", "away_rest_days_capped", "neutral_site",
)


class FrozenInputError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _time(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise FrozenInputError("NCAAF_FORWARD_TIME_INVALID") from exc
    if dt.tzinfo is None:
        raise FrozenInputError("NCAAF_FORWARD_TIME_INVALID")
    return dt.astimezone(timezone.utc)


def _fail_if(condition: bool, code: str) -> None:
    if condition:
        raise FrozenInputError(code)


def audit(payload: dict[str, Any], espn_fixture: dict[str, Any]) -> dict[str, Any]:
    _fail_if(payload.get("sport") != "NCAAF" or
             payload.get("candidate_model_family") != EXPECTED_FAMILY or
             payload.get("feature_schema_version") != EXPECTED_SCHEMA or
             payload.get("source_policy_id") != EXPECTED_SOURCE_POLICY,
             "NCAAF_FORWARD_MODEL_CONTRACT_MISMATCH")
    _fail_if(payload.get("can_execute") is not False or
             payload.get("probability_publishable") is not False or
             payload.get("historical_reconstruction") is not True or
             payload.get("model_artifact_certified") is not False,
             "NCAAF_FORWARD_UNSUPPORTED_AUTHORITY")
    meta = payload.get("source_snapshot") or {}
    _fail_if(meta.get("provider") != "CFBD" or meta.get("endpoint") != "/games" or
             len(str(meta.get("payload_sha256") or "")) != 64,
             "NCAAF_FORWARD_CFBD_SOURCE_INVALID")
    source_time = _time(meta.get("retrieved_at"))
    frozen = (espn_fixture.get("sports") or {}).get("NCAAF") or {}
    captured = frozen.get("events") or []
    expected = {str(e["espn_event_id"]): e for e in captured}
    events = payload.get("events")
    _fail_if(not isinstance(events, list) or len(events) != 44 or len(expected) != 44,
             "NCAAF_FORWARD_EVENT_COUNT_INVALID")
    seen = set()
    min_games = 9999
    for row in events:
        event_id = str(row.get("official_event_id") or "")
        origin = expected.get(event_id)
        _fail_if(not event_id or event_id in seen or origin is None or
                 event_id != row.get("espn_event_id") or
                 str(row.get("espn_home_team_id")) != str(origin.get("home_espn_team_id")) or
                 str(row.get("espn_away_team_id")) != str(origin.get("away_espn_team_id")),
                 "NCAAF_FORWARD_EVENT_IDENTITY_MISMATCH")
        seen.add(event_id)
        start = _time(row.get("event_start_time"))
        asof = _time(row.get("feature_as_of"))
        _fail_if(start != _time(origin["event_start_utc"]) or
                 asof >= start or source_time >= start or
                 asof > source_time,
                 "NCAAF_FORWARD_TEMPORAL_CONTRADICTION")
        _fail_if(not row.get("home_team") or not row.get("away_team") or
                 row["home_team"] == row["away_team"] or
                 type(row.get("neutral_site_verified")) is not bool,
                 "NCAAF_FORWARD_SCHOOL_IDENTITY_INVALID")
        _fail_if(row.get("status") != "RESEARCH_FORWARD_FEATURES_ONLY" or
                 row.get("can_execute") is not False or
                 row.get("probability_publishable") is not False or
                 row.get("model_probability") is not None or
                 row.get("calibrated_probability") is not None or
                 row.get("calibrated_lower_bound") is not None,
                 "NCAAF_FORWARD_UNSUPPORTED_AUTHORITY")
        features = row.get("features")
        _fail_if(not isinstance(features, dict) or
                 tuple(features) != FEATURE_NAMES or
                 any(type(v) not in (int, float) or not isfinite(v)
                     for v in features.values()),
                 "NCAAF_FORWARD_NUMERIC_VECTOR_INVALID")
        expected_asof = []
        for side, prefix in (("home", "home"), ("away", "away")):
            h = (row.get("history") or {}).get(side) or {}
            n, wins, points, ids = h.get("games"), h.get("wins"), h.get("point_diff_sum"), h.get("prior_event_ids")
            _fail_if(type(n) is not int or n < 3 or
                     type(wins) is not int or wins < 0 or wins > n or
                     type(points) is not int or
                     not isinstance(ids, list) or len(ids) != n or len(set(ids)) != n or
                     event_id in ids,
                     "NCAAF_FORWARD_PRIOR_HISTORY_INVALID")
            last_start = _time(h.get("last_event_time"))
            known_at = _time(h.get("last_source_retrieved_at"))
            _fail_if(last_start >= known_at or known_at >= start or
                     last_start >= start or known_at > source_time,
                     "NCAAF_FORWARD_PRIOR_PROVENANCE_INVALID")
            rest = max(0.0, min(28.0, (start - last_start).total_seconds() / 86400.0))
            expected_values = {
                prefix + "_win_rate_prior": wins / n,
                prefix + "_point_diff_prior": points / n,
                prefix + "_games_prior_log": log1p(n),
                prefix + "_rest_days_capped": rest,
            }
            for key, expected_value in expected_values.items():
                _fail_if(abs(float(features[key]) - expected_value) > 1e-10,
                         "NCAAF_FORWARD_FEATURE_REPLAY_MISMATCH")
            expected_asof.append(known_at)
            min_games = min(min_games, n)
        _fail_if(asof != max(expected_asof) or
                 features["neutral_site"] != (1.0 if row["neutral_site_verified"] else 0.0),
                 "NCAAF_FORWARD_FEATURE_REPLAY_MISMATCH")
    _fail_if(seen != set(expected), "NCAAF_FORWARD_EVENT_SET_MISMATCH")
    return {
        "status": "NCAAF_RESULT_FORM_FORWARD_FEATURE_REPLAY_PASS",
        "sport": "NCAAF",
        "events_verified": len(seen),
        "minimum_prior_games_per_team": min_games,
        "candidate_feature_schema_version": EXPECTED_SCHEMA,
        "source_verified": "CFBD_SNAPSHOT_AND_ESPN_IDENTITY_RECEIPTS",
        "model_probability": None,
        "probability_publishable": False,
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--espn-fixture", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = audit(json.loads(args.features.read_text()), json.loads(args.espn_fixture.read_text()))
    except (FrozenInputError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason_code": getattr(exc, "code", "NCAAF_FORWARD_PARSE_INVALID"), "can_execute": False}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
