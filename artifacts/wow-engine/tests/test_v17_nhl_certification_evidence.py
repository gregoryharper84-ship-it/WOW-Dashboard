"""NHL-specific candidate certification evidence — Fix C contract tests."""
from __future__ import annotations

from copy import deepcopy

from v17.candidate_certification_evidence import _hash
from v17 import nhl_certification_evidence as nhl


FEATURE_NAMES = (
    "elo_delta",
    "win_pct_l10_delta",
    "goal_diff_l10_delta",
    "goals_for_l10_delta",
    "goals_against_l10_delta",
    "season_win_pct_delta",
    "rest_days_delta",
    "home_back_to_back",
    "away_back_to_back",
    "neutral_site",
)


def _manifest() -> dict:
    return {
        "source": nhl.SOURCE_ID,
        "policy": nhl.SOURCE_POLICY,
        "market_features_used": False,
        "outcome_space": "HOME_AWAY_INCLUDING_OT_SO",
    }


def _row(*, event_id: str = "NHL:2023020001") -> dict:
    manifest = _manifest()
    return {
        "training_row_id": event_id,
        "created_at": "2026-01-01T00:00:00+00:00",
        "official_event_id": event_id,
        "event_start_time": "2026-01-02T00:00:00+00:00",
        "feature_as_of": "2026-01-01T23:59:00+00:00",
        "features": {name: 0.1 for name in FEATURE_NAMES},
        "outcome_json": {"home_win": True},
        "source_manifest": manifest,
        "source_manifest_sha256": _hash(manifest),
        "market_features_used": False,
        "can_execute": False,
    }


def _candidate(**overrides) -> dict:
    row = {
        "candidate_id": "nhl-cand-1",
        "created_at": "2026-02-01T00:00:00+00:00",
        "sport": "NHL",
        "league": "NHL",
        "model_family": nhl.MODEL_FAMILY,
        "model_artifact_version": f"{nhl.MODEL_FAMILY}_{'a' * 16}_{'b' * 12}",
        "feature_schema_version": nhl.FEATURE_SCHEMA,
        "source_policy_id": nhl.SOURCE_POLICY,
        "training_dataset_hash": "c" * 64,
        "training_code_sha": "d" * 40,
        "artifact_checksum": "e" * 64,
        "artifact_payload": {"feature_names": list(FEATURE_NAMES)},
        "calibrator_payload": {
            "method": "EMPIRICAL_WILSON_BINS_V1",
            "bins": [{"raw_mean": 0.5, "calibrated_probability": 0.5, "wilson_lower": 0.4, "wilson_upper": 0.6}],
            "training_n": 1226,
            "selection_version": "FORWARD_CALIBRATION_SELECTION_V2",
        },
        "validation_metrics": {},
        "training_rows": nhl.REQUIRED_TRAINING_ROWS,
        "calibration_rows": nhl.REQUIRED_CALIBRATION_ROWS,
        "test_rows": nhl.REQUIRED_TEST_ROWS,
        "research_screen_pass": True,
        "source_review_status": "REQUIRED",
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    row.update(overrides)
    return row


class _Query:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.filters: list[tuple[str, object]] = []
        self._limit = None
        self.mode = "select"
        self.payload = None

    def select(self, *_):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def lte(self, key, value):
        self.filters.append(("lte", key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = dict(payload)
        return self

    def execute(self):
        if self.mode == "insert":
            self.db.writes.append(self.payload)
            row = dict(self.payload)
            row.setdefault("receipt_id", f"receipt-{len(self.db.writes)}")
            self.db.tables.setdefault(self.name, []).append(row)
            return type("R", (), {"data": [row]})()
        rows = [dict(r) for r in self.db.tables.get(self.name, [])]
        for op, key, value in self.filters:
            if op == "eq":
                rows = [r for r in rows if r.get(key) == value]
            elif op == "lte":
                rows = [r for r in rows if str(r.get(key) or "") <= str(value)]
        if self._limit is not None:
            rows = rows[: self._limit]
        return type("R", (), {"data": rows})()


class _Database:
    def __init__(self, *, candidates=None, training_rows=None):
        self.tables = {
            "wow_d1_candidate_artifacts": list(candidates or []),
            "wow_d1_training_rows": list(training_rows or []),
            "wow_d1_certification_evidence_receipts": [],
        }
        self.writes: list[dict] = []

    def table(self, name):
        return _Query(self, name)


def test_research_verifier_never_authorizes_execution_or_publication():
    assert nhl.CAN_EXECUTE is False
    assert nhl.PROBABILITY_PUBLISHABLE is False
    assert nhl.REQUIRED_TRAINING_ROWS == 3676
    assert nhl.REQUIRED_CALIBRATION_ROWS == 1226
    assert nhl.REQUIRED_TEST_ROWS == 1226
    assert nhl.REQUIRED_TRAINING_ROWS + nhl.REQUIRED_CALIBRATION_ROWS + nhl.REQUIRED_TEST_ROWS == 6128


def test_absent_candidate_blocks_without_receipt_write():
    db = _Database(candidates=[])
    result = nhl.verify_nhl_candidate_certification_evidence(db, "absent")
    assert result["status"] == "CERTIFICATION_EVIDENCE_BLOCKED"
    assert result["code"] == "CANDIDATE_ARTIFACT_NOT_FOUND"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert db.writes == []


def test_wrong_family_is_unsupported_contract():
    db = _Database(candidates=[_candidate(model_family="OTHER_FAMILY")])
    result = nhl.verify_nhl_candidate_certification_evidence(db, "nhl-cand-1")
    assert result["status"] == "CERTIFICATION_EVIDENCE_FAILED"
    assert "CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_split_must_be_3676_1226_1226_for_historical_target():
    db = _Database(candidates=[_candidate(training_rows=3000, calibration_rows=1000, test_rows=1000)])
    result = nhl.verify_nhl_candidate_certification_evidence(db, "nhl-cand-1")
    assert "NHL_HISTORICAL_TARGET_SPLIT_MISMATCH" in result["blockers"]
    assert result["probability_publishable"] is False


def test_identity_calibrator_forbidden_for_certification_evidence_pass():
    db = _Database(
        candidates=[
            _candidate(
                calibrator_payload={"method": nhl.IDENTITY_CALIBRATION_METHOD, "bins": []},
                source_review_status="PASS",
            )
        ]
    )
    result = nhl.verify_nhl_candidate_certification_evidence(db, "nhl-cand-1")
    assert "NHL_IDENTITY_CALIBRATOR_FORBIDDEN" in result["blockers"]
    assert result["status"] != "CERTIFICATION_EVIDENCE_PASS"
    assert result["probability_publishable"] is False


def test_source_review_required_blocks_overall_pass_without_minting_review():
    db = _Database(candidates=[_candidate(source_review_status="REQUIRED")])
    result = nhl.verify_nhl_candidate_certification_evidence(db, "nhl-cand-1")
    assert "SOURCE_REVIEW_REQUIRED" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_validated_row_rejects_temporal_leakage_and_bad_outcome():
    leak = _row()
    leak["feature_as_of"] = leak["event_start_time"]
    value, blockers = nhl._validated_row(leak, FEATURE_NAMES)
    assert value is None
    assert "BINARY_TEMPORAL_LEAKAGE" in blockers

    bad = _row()
    bad["outcome_json"] = {"home_win": "yes"}
    value, blockers = nhl._validated_row(bad, FEATURE_NAMES)
    assert value is None
    assert "TRAINING_ROW_SHAPE_INVALID" in blockers


def test_dispatch_reuses_nhl_verifier(monkeypatch):
    from v17 import candidate_certification_evidence as entry

    expected = {
        "status": "CERTIFICATION_EVIDENCE_FAILED",
        "can_execute": False,
        "probability_publishable": False,
    }
    monkeypatch.setattr(
        nhl,
        "verify_nhl_candidate_certification_evidence",
        lambda db, candidate_id: expected,
    )
    db = _Database(
        candidates=[
            {
                "candidate_id": "candidate-id",
                "sport": "NHL",
                "model_family": "NHL_REGULAR_SEASON_LOGISTIC_V1",
            }
        ]
    )
    assert entry.verify_candidate_certification_evidence(db, "candidate-id") == expected


def test_generic_path_still_rejects_non_nhl_unsupported_family():
    from v17 import candidate_certification_evidence as entry

    db = _Database(
        candidates=[
            {
                "sport": "NBA",
                "model_family": "SOMETHING_ELSE",
                "source_policy_id": "X",
                "research_screen_pass": True,
                "lifecycle_state": "CANDIDATE",
                "can_execute": False,
                "promoted": False,
                "active": False,
                "automatic_certification": False,
                "automatic_promotion": False,
                "probability_publishable": False,
                "candidate_id": "x",
                "created_at": "2026-01-01T00:00:00+00:00",
                "league": "NBA",
                "feature_schema_version": "F",
                "training_dataset_hash": "a" * 64,
                "artifact_checksum": "b" * 64,
                "model_artifact_version": "v",
                "training_code_sha": "c" * 40,
                "artifact_payload": {"feature_names": ["x"]},
                "calibrator_payload": {},
                "validation_metrics": {},
                "training_rows": 0,
                "calibration_rows": 0,
                "test_rows": 0,
            }
        ]
    )
    result = entry.verify_candidate_certification_evidence(db, "x")
    assert "CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED" in result["blockers"]
