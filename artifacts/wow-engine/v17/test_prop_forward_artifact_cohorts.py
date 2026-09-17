from datetime import datetime, timedelta, timezone

import v17.fantasy_score_forward_cohort_runtime as fantasy_runtime
import v17.fantasy_score_forward_cohort_thesis_dedupe as fantasy_dedupe
import v17.prop_universal_forward_evidence as universal
import v17.prop_universal_forward_schema_repair  # install live/artifact guards


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


def _snapshot():
    return {
        "source_snapshot_id": "00000000-0000-0000-0000-000000000001",
        "captured_at": (NOW - timedelta(minutes=10)).isoformat(),
        "event_id": "WNBA:TEST",
        "event_start_time": (NOW + timedelta(hours=2)).isoformat(),
        "sport": "WNBA",
        "player": "Player A",
        "stat_type": "POINTS",
        "line": 20.5,
        "hydration_status": "PASS",
        "blockers": [],
    }


def _generic_scored(version):
    return {
        "probability_publishable": False,
        "research_only": True,
        "research_model_output": {
            "raw_specialist_probability": 0.61,
            "provider_identity": universal.PROVIDER,
            "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
            "model_artifact_version": version,
            "model_artifact_checksum": ("a" if version == "A" else "b") * 64,
            "feature_schema_version": "PROP_FEATURES_V1",
            "calibration_version": "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1",
            "model_timestamp": NOW.isoformat(),
        },
    }


def test_generic_prediction_identity_changes_when_exact_artifact_changes():
    a, blockers_a = universal._prediction_payload(
        sport="WNBA", stat_type="POINTS", snapshot=_snapshot(), direction="MORE",
        scored=_generic_scored("A"), now=NOW,
    )
    b, blockers_b = universal._prediction_payload(
        sport="WNBA", stat_type="POINTS", snapshot=_snapshot(), direction="MORE",
        scored=_generic_scored("B"), now=NOW,
    )
    assert blockers_a == ()
    assert blockers_b == ()
    assert a is not None and b is not None
    assert a["prediction_id"] != b["prediction_id"]
    assert a["calibration_version"] == "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1"
    assert b["calibration_version"] == "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1"
    assert "calibrator_version" not in a


def _fantasy_prediction(index, artifact):
    checksum = ("c" if artifact == "A" else "d") * 64
    return {
        "prediction_id": f"{artifact}-{index}",
        "source_snapshot_id": f"s-{artifact}-{index}",
        "event_id": f"NBA:{artifact}:{index}",
        "event_start_time": (NOW - timedelta(hours=1)).isoformat(),
        "model_timestamp": (NOW - timedelta(hours=4)).isoformat(),
        "player": f"Player {index}",
        "stat_type": "FANTASY_SCORE",
        "line": 30.5,
        "direction": "MORE",
        "fantasy_score_lane": "NBA",
        "market_family": "NBA_FANTASY_SCORE",
        "evidence_source_kind": fantasy_runtime.EVIDENCE_SOURCE_KIND,
        "model_family": "NBA_FANTASY_SCORE",
        "model_artifact_version": f"NBA_FS_{artifact}",
        "model_artifact_checksum": checksum,
        "scoring_profile_id": "PRIZEPICKS_NBA_FANTASY_V1",
        "scoring_profile_sha256": "e" * 64,
        "calibration_version": "UNAVAILABLE_CANDIDATE_ONLY",
    }


def test_fantasy_phase_threshold_cannot_be_reached_by_mixing_two_artifacts():
    # 150 settled observations on A + 150 on B is not a 300-row cohort.
    predictions = [
        *[_fantasy_prediction(i, "A") for i in range(150)],
        *[_fantasy_prediction(i, "B") for i in range(150)],
    ]
    outcomes = [
        {
            "prediction_id": row["prediction_id"],
            "actual_stat": 35.0,
            "settlement_timestamp": NOW.isoformat(),
            "void": False,
        }
        for row in predictions
    ]
    db = _Db({"wow_predictions": predictions, "wow_outcomes": outcomes})
    readiness = fantasy_dedupe._lane_readiness(db, fantasy_runtime.LANE_SPECS["NBA"])
    assert readiness["forward_settled_source_n"] == 300  # audit total only
    assert readiness["aggregate_counts_are_certification_authority"] is False
    assert readiness["strongest_single_artifact_settled_n"] == 150
    assert readiness["status"] == "PHASE_A_FORWARD_COHORT_BUILDING"
    assert readiness["remaining_to_phase_b"] == 50
    assert sorted(row["forward_settled_n"] for row in readiness["artifact_cohorts"]) == [150, 150]
    assert readiness["certification_counting_basis"] == "EXACT_ARTIFACT_COHORT_ONLY"
    assert readiness["can_execute"] is False


def _generic_prediction(index, artifact):
    return {
        "prediction_id": f"g-{artifact}-{index}",
        "event_id": f"WNBA:{artifact}:{index}",
        "player": f"Player {index}",
        "sport": "WNBA",
        "stat_type": "POINTS",
        "line": 20.5,
        "direction": "MORE",
        "model_provider_identity": universal.PROVIDER,
        "feature_schema_version": "PROP_FEATURES_V1",
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "model_artifact_version": f"WNBA_POINTS_{artifact}",
        "model_artifact_checksum": ("f" if artifact == "A" else "1") * 64,
        "calibration_version": "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1",
    }


def test_generic_readiness_exposes_artifact_cohorts_instead_of_mixed_certification_count():
    predictions = [
        *[_generic_prediction(i, "A") for i in range(3)],
        *[_generic_prediction(i, "B") for i in range(4)],
    ]
    outcomes = [
        {
            "prediction_id": row["prediction_id"],
            "actual_stat": 22.0,
            "settlement_timestamp": NOW.isoformat(),
            "void": False,
        }
        for row in predictions
    ]
    db = _Db({"wow_predictions": predictions, "wow_outcomes": outcomes})
    readiness = universal._route_readiness(db, "WNBA", "POINTS")
    assert readiness["forward_settled_n"] == 7
    assert readiness["aggregate_counts_are_certification_authority"] is False
    assert sorted(row["forward_settled_n"] for row in readiness["artifact_cohorts"]) == [3, 4]
    assert readiness["certification_counting_basis"] == "EXACT_ARTIFACT_COHORT_ONLY"
    assert readiness["can_execute"] is False
