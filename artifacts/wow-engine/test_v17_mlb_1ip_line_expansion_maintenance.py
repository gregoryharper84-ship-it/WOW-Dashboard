from copy import deepcopy
from types import SimpleNamespace

from mlb_1ip_player_conditioned import MODEL_FAMILY
from v17 import mlb_1ip_line_expansion_maintenance as maintenance


class _Query:
    def __init__(self, db):
        self.db = db
        self.filters = []
        self.mode = "select"
        self.payload = None
        self.limit_n = None

    def select(self, _fields):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_n = int(value)
        return self

    def update(self, payload):
        self.mode = "update"
        self.payload = dict(payload)
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = dict(payload)
        return self

    def _matches(self, row):
        return all(row.get(key) == value for key, value in self.filters)

    def execute(self):
        if self.mode == "insert":
            row = deepcopy(self.payload)
            self.db.sequence += 1
            row.setdefault("artifact_id", f"artifact-{self.db.sequence}")
            self.db.rows.append(row)
            return SimpleNamespace(data=[deepcopy(row)])
        matched = [row for row in self.db.rows if self._matches(row)]
        if self.mode == "update":
            for row in matched:
                row.update(self.payload)
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        return SimpleNamespace(data=[deepcopy(row) for row in matched])


class _Table:
    def __init__(self, db):
        self.db = db

    def select(self, fields):
        return _Query(self.db).select(fields)

    def update(self, payload):
        return _Query(self.db).update(payload)

    def insert(self, payload):
        return _Query(self.db).insert(payload)


class _Db:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.sequence = len(rows)

    def table(self, name):
        assert name == "wow_prop_fitted_model_artifacts"
        return _Table(self)


def _active_row():
    return {
        "artifact_id": "artifact-1",
        "provider_identity": maintenance.PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": "MLB_1IP_PLAYER_CONDITIONED_BF_MIXTURE_V1_TEST",
        "calibrator_version": "MLB_1IP_PLAYER_CONDITIONED_TEMPORAL_CAL_V1",
        "sport": "MLB",
        "stat_type": maintenance.STAT_TYPE,
        "feature_schema_version": maintenance.FEATURE_SCHEMA_VERSION,
        "feature_transform_version": "MLB_1IP_PLAYER_CONDITIONED_BF_MIXTURE_V1",
        "specialist_version": "wow.mlb-first-inning-pitch-count-expert@1",
        "certification_id": "PROP-CERT-OLD",
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "training_dataset_hash": "a" * 64,
        "training_code_sha": "b" * 40,
        "artifact_checksum": "c" * 64,
        "artifact_format": "JSON_PLAYER_CONDITIONED_BF_MIXTURE_V1",
        "artifact_payload": {
            "model_family": MODEL_FAMILY,
            "aggregate_artifact_checksum": maintenance.AGGREGATE_ARTIFACT_CHECKSUM,
            "probability_publishable": False,
            "can_execute": False,
        },
        "supported_line_min": 11.5,
        "supported_line_max": 21.5,
        "training_rows": 1332,
        "validation_metrics": {"validated_lines": list(maintenance.OLD_VALIDATED_LINES)},
        "promoted": True,
        "active": True,
        "probability_publishable": False,
        "can_execute": False,
    }


def test_line_expansion_clones_fitted_payload_and_activates_eight_line_certification():
    db = _Db([_active_row()])
    result = maintenance.run_mlb_1ip_line_expansion_maintenance(db)
    assert result["status"] == "PROMOTED"
    assert result["model_payload_changed"] is False
    assert result["validated_lines"] == list(maintenance.EXPANDED_VALIDATED_LINES)
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False

    active = [row for row in db.rows if row.get("active") is True]
    assert len(active) == 1
    assert active[0]["model_family"] == MODEL_FAMILY
    assert active[0]["artifact_checksum"] == "c" * 64
    assert active[0]["artifact_payload"] == _active_row()["artifact_payload"]
    assert active[0]["validation_metrics"]["validated_lines"] == list(
        maintenance.EXPANDED_VALIDATED_LINES
    )
    assert db.rows[0]["active"] is False

    rerun = maintenance.run_mlb_1ip_line_expansion_maintenance(db)
    assert rerun["status"] == "ALREADY_ACTIVE"
    assert len([row for row in db.rows if row.get("active") is True]) == 1


def test_line_expansion_fails_closed_if_player_conditioned_artifact_is_not_active():
    row = _active_row()
    row["model_family"] = "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1"
    db = _Db([row])
    result = maintenance.run_mlb_1ip_line_expansion_maintenance(db)
    assert result["status"] == "BLOCKED"
    assert result["code"] == "MLB_1IP_PLAYER_CONDITIONED_ARTIFACT_NOT_ACTIVE"
    assert db.rows[0]["active"] is True
    assert result["can_execute"] is False
