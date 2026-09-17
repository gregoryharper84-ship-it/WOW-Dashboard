from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from v17.fantasy_score_forward_cohort_runtime import (
    EVIDENCE_SOURCE_KIND,
    FantasyScoreForwardCohortRequest,
    LANE_SPECS,
    _build_prediction_payload,
    _lane_readiness,
    run_fantasy_score_forward_cohort,
)


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
MODEL_HASH = "a" * 64
SCORING_HASH = "b" * 64


def _snapshot(lane="NBA", suffix="1"):
    spec = LANE_SPECS[lane]
    return {
        "source_snapshot_id": f"00000000-0000-0000-0000-0000000000{suffix.zfill(2)}",
        "captured_at": (NOW - timedelta(minutes=10)).isoformat(),
        "event_id": f"event-{suffix}",
        "event_start_time": (NOW + timedelta(hours=3)).isoformat(),
        "sport": spec.sport,
        "player": "Player A",
        "team": "AAA",
        "opponent": "BBB",
        "stat_type": spec.stat_type,
        "line": 25.5,
        "hydration_status": "PASS",
        "blockers": [],
    }


def _candidate(lane="NBA", direction="MORE"):
    spec = LANE_SPECS[lane]
    p_more, p_less, p_push = 0.61, 0.37, 0.02
    raw = p_more if direction == "MORE" else p_less
    payload = {
        "market_family": spec.market_family,
        "controlling_specialist": spec.controlling_specialist,
        "model_version": f"{lane}_FANTASY_SCORE_EMPIRICAL_RESIDUAL_CANDIDATE_V1",
        "model_source_sha256": MODEL_HASH,
        "scoring_profile_id": f"PRIZEPICKS_{lane}_FANTASY_V1",
        "scoring_profile_sha256": SCORING_HASH,
        "simulation_count": 50_000,
        "seed": 17,
        "raw_candidate_probability": raw,
        "P(MORE)": p_more,
        "P(LESS)": p_less,
        "P(PUSH)": p_push,
        "model_timestamp": NOW.isoformat(),
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "blockers": ["BLOCKED_NO_CERTIFIED_EXACT_LINE_CALIBRATION_ARTIFACT"],
    }
    if lane == "NFL":
        payload["position"] = "WR"
    return payload


def test_every_current_fantasy_score_lane_has_exact_forward_contract():
    assert set(LANE_SPECS) == {"NFL", "NBA", "WNBA", "MLB_HITTER", "MLB_PITCHER"}
    assert LANE_SPECS["NFL"].market_family == "NFL_DFS_FANTASY_SCORE"
    assert LANE_SPECS["MLB_HITTER"].stat_type == "HITTER_FANTASY_SCORE"
    assert LANE_SPECS["MLB_PITCHER"].stat_type == "PITCHER_FANTASY_SCORE"


def test_valid_candidate_package_becomes_immutable_nonpublishable_prediction():
    spec = LANE_SPECS["NBA"]
    payload, blockers = _build_prediction_payload(
        spec=spec,
        snapshot=_snapshot("NBA"),
        direction="MORE",
        scored=_candidate("NBA", "MORE"),
        now=NOW,
    )
    assert blockers == []
    assert payload is not None
    assert payload["fantasy_score_lane"] == "NBA"
    assert payload["market_family"] == "NBA_FANTASY_SCORE"
    assert payload["controlling_specialist"] == spec.controlling_specialist
    assert payload["scoring_profile_id"] == "PRIZEPICKS_NBA_FANTASY_V1"
    assert payload["scoring_profile_sha256"] == SCORING_HASH
    assert payload["model_artifact_checksum"] == MODEL_HASH
    assert payload["evidence_source_kind"] == EVIDENCE_SOURCE_KIND
    assert payload["simulation_draws"] == 50_000
    assert payload["probability_publishable"] is False
    assert payload["locked_at"] == NOW.isoformat()


def test_wrong_specialist_is_rejected_before_persistence():
    scored = _candidate("NBA")
    scored["controlling_specialist"] = "wow.generic-fantasy-projection"
    payload, blockers = _build_prediction_payload(
        spec=LANE_SPECS["NBA"], snapshot=_snapshot("NBA"), direction="MORE", scored=scored, now=NOW
    )
    assert payload is None
    assert "FANTASY_SCORE_CONTROLLING_SPECIALIST_MISMATCH" in blockers


def test_missing_exact_scoring_profile_hash_is_rejected():
    scored = _candidate("WNBA")
    scored["scoring_profile_sha256"] = None
    payload, blockers = _build_prediction_payload(
        spec=LANE_SPECS["WNBA"], snapshot=_snapshot("WNBA"), direction="MORE", scored=scored, now=NOW
    )
    assert payload is None
    assert "FANTASY_SCORE_SCORING_PROFILE_HASH_MISSING_OR_INVALID" in blockers


def test_candidate_must_explicitly_remain_nonpublishable_nonrankable_nonexecutable():
    scored = _candidate("MLB_HITTER")
    scored["probability_publishable"] = True
    scored["rank_eligible"] = True
    scored["can_execute"] = True
    payload, blockers = _build_prediction_payload(
        spec=LANE_SPECS["MLB_HITTER"],
        snapshot=_snapshot("MLB_HITTER"),
        direction="MORE",
        scored=scored,
        now=NOW,
    )
    assert payload is None
    assert "FANTASY_SCORE_CANDIDATE_MUST_BE_NONPUBLISHABLE" in blockers
    assert "FANTASY_SCORE_CANDIDATE_MUST_BE_NONRANKABLE" in blockers
    assert "FANTASY_SCORE_CAN_EXECUTE_MUST_BE_FALSE" in blockers


def test_nfl_requires_real_position_identity():
    scored = _candidate("NFL")
    scored.pop("position")
    payload, blockers = _build_prediction_payload(
        spec=LANE_SPECS["NFL"], snapshot=_snapshot("NFL"), direction="MORE", scored=scored, now=NOW
    )
    assert payload is None
    assert "NFL_FANTASY_POSITION_MISSING_OR_INVALID" in blockers


def test_more_and_less_must_match_their_exact_directional_probability():
    scored = _candidate("NBA", "LESS")
    scored["raw_candidate_probability"] = scored["P(MORE)"]
    payload, blockers = _build_prediction_payload(
        spec=LANE_SPECS["NBA"], snapshot=_snapshot("NBA"), direction="LESS", scored=scored, now=NOW
    )
    assert payload is None
    assert "FANTASY_SCORE_RAW_SIDE_PROBABILITY_MISMATCH" in blockers


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table, rows):
        self.db = db
        self.table = table
        self.rows = list(rows)
        self._limit = None
        self._upsert = None

    def select(self, *_args): return self
    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]
        return self
    def gt(self, field, value):
        self.rows = [row for row in self.rows if str(row.get(field) or "") > str(value)]
        return self
    def in_(self, field, values):
        allowed = set(values)
        self.rows = [row for row in self.rows if row.get(field) in allowed]
        return self
    def order(self, field):
        self.rows.sort(key=lambda row: str(row.get(field) or ""))
        return self
    def limit(self, value):
        self._limit = value
        return self
    def range(self, start, end):
        self.rows = self.rows[start:end + 1]
        return self
    def upsert(self, payload, **_kwargs):
        self._upsert = dict(payload)
        return self
    def execute(self):
        if self._upsert is not None:
            self.db.tables.setdefault(self.table, []).append(self._upsert)
            return _Result([self._upsert])
        data = self.rows[: self._limit] if self._limit is not None else self.rows
        return _Result(data)


class _Db:
    def __init__(self, tables):
        self.tables = {name: list(rows) for name, rows in tables.items()}
    def table(self, name):
        return _Query(self, name, self.tables.setdefault(name, []))


class _Req:
    def __init__(self, **kwargs): self.payload = kwargs


class _Market:
    ScorePropRequest = _Req
    def score_prop(self, req, _identity):
        lane = next(
            lane for lane, spec in LANE_SPECS.items()
            if spec.sport == req.payload["sport"] and spec.stat_type == req.payload["stat_type"]
        )
        return _candidate(lane, req.payload["direction"])


def test_forward_run_captures_both_directions_but_readiness_counts_one_source():
    snapshot = _snapshot("NBA")
    db = _Db({
        "wow_prop_evidence_snapshots": [snapshot],
        "wow_predictions": [],
        "wow_outcomes": [],
    })
    result = run_fantasy_score_forward_cohort(
        FantasyScoreForwardCohortRequest(lanes=["NBA"], max_snapshots_per_lane=5),
        db=db,
        market_api=_Market(),
        now=NOW,
    )
    lane = result["lanes"][0]
    assert result["run_status"] == "COMPLETED"
    assert lane["captured_forward_predictions"] == 2
    assert len(db.tables["wow_predictions"]) == 2
    assert lane["calibration_readiness"]["forward_prediction_source_n"] == 1
    assert lane["calibration_readiness"]["forward_settled_source_n"] == 0
    assert lane["calibration_readiness"]["remaining_to_phase_b"] == 200
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_settled_directional_twins_still_count_as_one_independent_source():
    snapshot_id = _snapshot("NBA")["source_snapshot_id"]
    predictions = []
    outcomes = []
    for direction in ("MORE", "LESS"):
        prediction_id = f"pred-{direction.lower()}"
        predictions.append({
            "prediction_id": prediction_id,
            "source_snapshot_id": snapshot_id,
            "event_id": "event-legacy-twins",
            "event_start_time": (NOW - timedelta(hours=2)).isoformat(),
            "model_timestamp": (NOW - timedelta(hours=5)).isoformat(),
            "player": "Player A",
            "stat_type": "FANTASY_SCORE",
            "line": 25.5,
            "direction": direction,
            "fantasy_score_lane": "NBA",
            "market_family": "NBA_FANTASY_SCORE",
            "evidence_source_kind": EVIDENCE_SOURCE_KIND,
            "model_family": "NBA_FANTASY_SCORE",
            "model_artifact_version": "NBA_FANTASY_SCORE_EMPIRICAL_RESIDUAL_CANDIDATE_V1",
            "model_artifact_checksum": MODEL_HASH,
            "scoring_profile_id": "PRIZEPICKS_NBA_FANTASY_V1",
            "scoring_profile_sha256": SCORING_HASH,
            "calibration_version": "UNAVAILABLE_CANDIDATE_ONLY",
        })
        outcomes.append({
            "prediction_id": prediction_id,
            "actual_stat": 31.0,
            "settlement_timestamp": NOW.isoformat(),
            "void": False,
        })
    db = _Db({"wow_predictions": predictions, "wow_outcomes": outcomes})
    readiness = _lane_readiness(db, LANE_SPECS["NBA"])
    assert readiness["forward_prediction_source_n"] == 1
    assert readiness["forward_settled_source_n"] == 1
    assert readiness["remaining_to_phase_b"] == 199
    assert readiness["counting_basis"] in {
        "UNIQUE_SOURCE_SNAPSHOT",
        "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS",
    }
    if "certification_counting_basis" in readiness:
        assert readiness["certification_counting_basis"] == "EXACT_ARTIFACT_COHORT_ONLY"
        assert readiness["strongest_single_artifact_settled_n"] == 1
