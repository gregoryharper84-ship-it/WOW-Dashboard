from datetime import datetime, timedelta, timezone

import v17.fantasy_score_forward_cohort_runtime as runtime
import v17.fantasy_score_forward_cohort_thesis_dedupe as dedupe


NOW = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, data): self.data = data


class _Query:
    def __init__(self, db, table, rows):
        self.db = db
        self.table = table
        self.rows = list(rows)
        self._limit = None
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
    def limit(self, value): self._limit = value; return self
    def range(self, start, end): self.rows = self.rows[start:end + 1]; return self
    def execute(self):
        data = self.rows[: self._limit] if self._limit is not None else self.rows
        return _Result(data)


class _Db:
    def __init__(self, tables): self.tables = {name: list(rows) for name, rows in tables.items()}
    def table(self, name): return _Query(self, name, self.tables.setdefault(name, []))


def _snapshot(snapshot_id, minutes):
    return {
        "source_snapshot_id": snapshot_id,
        "captured_at": (NOW - timedelta(minutes=minutes)).isoformat(),
        "event_id": "NBA:TEST-1",
        "event_start_time": (NOW + timedelta(hours=3)).isoformat(),
        "sport": "NBA",
        "player": "Player A",
        "team": "AAA",
        "opponent": "BBB",
        "stat_type": "FANTASY_SCORE",
        "line": 30.5,
        "hydration_status": "PASS",
        "blockers": [],
    }


def _artifact_identity():
    return {
        "model_family": "NBA_FANTASY_SCORE",
        "model_artifact_version": "NBA_FS_TEST_V1",
        "model_artifact_checksum": "a" * 64,
        "scoring_profile_id": "PRIZEPICKS_NBA_FANTASY_V1",
        "scoring_profile_sha256": "b" * 64,
        "calibration_version": "UNAVAILABLE_CANDIDATE_ONLY",
    }


def test_patch_is_installed_over_fantasy_runtime():
    assert runtime._eligible_snapshots is dedupe._eligible_snapshots
    assert runtime._lane_readiness is dedupe._lane_readiness


def test_refreshed_snapshots_of_same_fantasy_thesis_count_once_at_capture():
    db = _Db({
        "wow_prop_evidence_snapshots": [
            _snapshot("s1", 20),
            _snapshot("s2", 10),
        ]
    })
    rows = runtime._eligible_snapshots(db, runtime.LANE_SPECS["NBA"], 10, now=NOW)
    assert len(rows) == 1
    assert dedupe.thesis_key(rows[0]) == ("NBA:TEST-1", "player a", "FANTASY_SCORE", "30.5")


def test_refreshed_snapshots_and_direction_twins_count_as_one_calibration_observation():
    identity = _artifact_identity()
    predictions = [
        {
            "prediction_id": "p1-more",
            "source_snapshot_id": "s1",
            "event_id": "NBA:TEST-1",
            "event_start_time": (NOW - timedelta(hours=1)).isoformat(),
            "model_timestamp": (NOW - timedelta(hours=4)).isoformat(),
            "player": "Player A",
            "stat_type": "FANTASY_SCORE",
            "line": 30.5,
            "direction": "MORE",
            "fantasy_score_lane": "NBA",
            "market_family": "NBA_FANTASY_SCORE",
            "evidence_source_kind": runtime.EVIDENCE_SOURCE_KIND,
            **identity,
        },
        {
            "prediction_id": "p1-less",
            "source_snapshot_id": "s1",
            "event_id": "NBA:TEST-1",
            "event_start_time": (NOW - timedelta(hours=1)).isoformat(),
            "model_timestamp": (NOW - timedelta(hours=4)).isoformat(),
            "player": "Player A",
            "stat_type": "FANTASY_SCORE",
            "line": 30.5,
            "direction": "LESS",
            "fantasy_score_lane": "NBA",
            "market_family": "NBA_FANTASY_SCORE",
            "evidence_source_kind": runtime.EVIDENCE_SOURCE_KIND,
            **identity,
        },
        {
            "prediction_id": "p2-more",
            "source_snapshot_id": "s2",
            "event_id": "NBA:TEST-1",
            "event_start_time": (NOW - timedelta(hours=1)).isoformat(),
            "model_timestamp": (NOW - timedelta(hours=3)).isoformat(),
            "player": "Player A",
            "stat_type": "FANTASY_SCORE",
            "line": 30.5,
            "direction": "MORE",
            "fantasy_score_lane": "NBA",
            "market_family": "NBA_FANTASY_SCORE",
            "evidence_source_kind": runtime.EVIDENCE_SOURCE_KIND,
            **identity,
        },
    ]
    outcomes = [
        {
            "prediction_id": "p1-more",
            "actual_stat": 34.0,
            "settlement_timestamp": NOW.isoformat(),
            "void": False,
        },
        {
            "prediction_id": "p1-less",
            "actual_stat": 34.0,
            "settlement_timestamp": NOW.isoformat(),
            "void": False,
        },
        {
            "prediction_id": "p2-more",
            "actual_stat": 34.0,
            "settlement_timestamp": NOW.isoformat(),
            "void": False,
        },
    ]
    db = _Db({"wow_predictions": predictions, "wow_outcomes": outcomes})
    readiness = runtime._lane_readiness(db, runtime.LANE_SPECS["NBA"])
    assert readiness["forward_prediction_source_n"] == 1
    assert readiness["forward_settled_source_n"] == 1
    assert readiness["strongest_single_artifact_settled_n"] == 1
    assert readiness["remaining_to_phase_b"] == runtime.PHASE_B_MIN_N - 1
    assert readiness["certification_counting_basis"] == "EXACT_ARTIFACT_COHORT_ONLY"
    assert readiness["counting_basis"] == "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS"
    assert readiness["can_execute"] is False
