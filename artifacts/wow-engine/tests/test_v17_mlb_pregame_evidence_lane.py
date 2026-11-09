"""MLB event-specific prelock evidence lane.

MLB previously had no per-event pregame table, so the lane reported
``MODEL_BUNDLE_PREGAME_CORPUS_SOURCE_NOT_CONFIGURED`` and could never complete.
These tests prove the declared reader now reads a genuine event-specific corpus
and that the lane reaches a complete, boundary-sealed bundle once governed rows
exist. They fabricate no governed row, certify nothing and promote nothing: the
database doubles below only exercise the read path, and the receipt-shaped
fixture stands in for the immutable certification receipt the boundary demands.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from v17 import model_evidence_boundary as boundary
from v17 import model_evidence_registry_readers as readers
from v17.model_evidence_bundle_contract import ModelBundleScope
from v17.team_event_official_publication_guard import (
    evaluate_team_event_official_publication,
)

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20261109_mlb_pregame_evidence.sql"
)

EVENT_ID = "MLB:TEST-1"
EVENT_START = "2026-11-12T19:00:00+00:00"
MODEL_AT = "2026-11-12T18:30:00+00:00"
SOURCE_AT = "2026-11-12T17:00:00+00:00"
FEATURE_AT = "2026-11-12T18:00:00+00:00"
FIT_END = "2026-11-01T00:00:00+00:00"

FAMILY = "MLB_EVENT_WIN_PROBABILITY"
VERSION = "MLB_EVENT_V1"
SCHEMA = "MLB_EVENT_FEATURES_V1"

ARTIFACT_SHA = "a" * 64
DATASET_SHA = "b" * 64
CODE_SHA = "c" * 40
CALIBRATOR_SHA = "d" * 64
COHORT_SHA = "e" * 64
HOLDOUT_SHA = "f" * 64
SOURCE_SHA = "1" * 64
FEATURE_SHA = "2" * 64
FOREIGN_SHA = "9" * 64


class _Query:
    """Minimal PostgREST-shaped double: select/eq/order/limit/execute."""

    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        wanted = str(value).strip().lower()
        self.rows = [
            row
            for row in self.rows
            if str(row.get(column)).strip().lower() == wanted
        ]
        return self

    def order(self, column, desc=False):
        self.rows.sort(key=lambda row: str(row.get(column) or ""), reverse=bool(desc))
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Db:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Query(self.tables.get(name, []))


def _scope() -> ModelBundleScope:
    return ModelBundleScope(
        model_kind="TEAM_EVENT",
        sport="MLB",
        model_family=FAMILY,
        model_artifact_version=VERSION,
        feature_schema_version=SCHEMA,
        official_event_id=EVENT_ID,
        event_start_time=EVENT_START,
        prediction_timestamp=MODEL_AT,
    )


def _artifact_row(**overrides):
    row = {
        "artifact_id": "art-mlb-1",
        "model_family": FAMILY,
        "model_artifact_version": VERSION,
        "feature_schema_version": SCHEMA,
        "artifact_checksum": ARTIFACT_SHA,
        "training_dataset_hash": DATASET_SHA,
        "training_code_sha": CODE_SHA,
        "training_rows": 9900,
        "calibrator_id": "cal-mlb-1",
        "created_at": "2026-11-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _calibrator_row(**overrides):
    row = {
        "calibrator_id": "cal-mlb-1",
        "model_family": FAMILY,
        "model_artifact_version": VERSION,
        "feature_schema_version": SCHEMA,
        "model_artifact_checksum": ARTIFACT_SHA,
        "calibrator_checksum": CALIBRATOR_SHA,
        "calibration_method": "PLATT",
        "bounds_method_version": "LOWER_BOUND_V2",
        "calibration_cohort_sha256": COHORT_SHA,
        "holdout_evidence_sha256": HOLDOUT_SHA,
        "calibration_training_n": 6944,
        "holdout_overlap_rows": 0,
        "brier": 0.19,
        "log_loss": 0.55,
        "calibration_bias": -0.01,
        "calibration_fit_end": FIT_END,
        "created_at": "2026-11-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _pregame_row(**overrides):
    row = {
        "model_family": FAMILY,
        "model_artifact_version": VERSION,
        "feature_schema_version": SCHEMA,
        "model_artifact_checksum": ARTIFACT_SHA,
        "official_event_id": EVENT_ID,
        "event_start_time": EVENT_START,
        "event_status": "SCHEDULED",
        "source_snapshot_id": "src-snap-1",
        "source_snapshot_timestamp": SOURCE_AT,
        "source_payload_sha256": SOURCE_SHA,
        "feature_snapshot_id": "feat-snap-1",
        "feature_snapshot_timestamp": FEATURE_AT,
        "feature_snapshot_sha256": FEATURE_SHA,
        "is_current": True,
        "created_at": "2026-11-12T18:00:00+00:00",
    }
    row.update(overrides)
    return row


def _receipt(**overrides):
    row = {
        "sport": "MLB",
        "model_version": VERSION,
        "artifact_sha256": ARTIFACT_SHA,
        "feature_schema_version": SCHEMA,
        "certification_status": "CERTIFIED",
        "independent_verification_status": "PASS",
        "calibration_sha256": CALIBRATOR_SHA,
        "calibration_artifact_id": "cal-mlb-1",
        "created_at": "2026-11-09T00:00:00+00:00",
        "verification_receipt": {
            "artifact_payload_checksum_verified": True,
            "training_provenance_verified": True,
            "calibrator_sha256": CALIBRATOR_SHA,
            "calibrator_id": "cal-mlb-1",
            "calibrator_chronological_replay_verified": True,
            "calibrated_lower_bound_verified": True,
            "official_event_id": EVENT_ID,
            "feature_snapshot_sha256": FEATURE_SHA,
            "source_payload_sha256": SOURCE_SHA,
            "feature_snapshot_id": "feat-snap-1",
            "source_snapshot_id": "src-snap-1",
            "event_start_time": EVENT_START,
            "pregame_source_payload_verified": True,
            "pregame_feature_replay_verified": True,
            "pregame_official_event_identity_verified": True,
            "pregame_settlement_basis_verified": True,
        },
    }
    row.update(overrides)
    return row


def _tables(*, pregame=(), calibrator=None, artifact=None, receipt=None):
    return {
        "wow_mlb_event_fitted_model_artifacts": [
            artifact if artifact is not None else _artifact_row()
        ],
        "wow_mlb_event_calibrator_evidence": [
            calibrator if calibrator is not None else _calibrator_row()
        ],
        "wow_mlb_event_pregame_feature_rows": list(pregame),
        readers.VERIFICATION_TABLE: [receipt if receipt is not None else _receipt()],
    }


def _approved_payload() -> dict:
    return {
        "sport": "MLB",
        "official_event_id": EVENT_ID,
        "model_artifact_version": VERSION,
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "terminal_label": "FINAL_APPROVED",
        "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "calibrated_probability": 0.62,
        "calibrated_lower_bound": 0.58,
        "llp_governance": {
            "probability_publishable": True,
            "rank_eligible": True,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
            "probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "event_mutex_status": "PASS",
            "postmodel_gates_status": "PASS",
            "final_gates_status": "PASS",
            "terminal_label": "FINAL_APPROVED",
        },
    }


def test_the_mlb_lane_declares_an_event_specific_pregame_reader():
    lane = readers.lane_for(_scope())

    assert lane is not None
    assert lane.pregame_corpus is not None
    assert lane.pregame_corpus.table == "wow_mlb_event_pregame_feature_rows"
    report = readers.assess_registry_model_bundle(_Db(_tables()), _scope())
    # Declared, so the lane reads it. With no rows the honest verdict is MISSING,
    # not "unconfigured" and not a fabricated pass.
    assert "MODEL_BUNDLE_PREGAME_CORPUS_SOURCE_NOT_CONFIGURED" not in report["blockers"]
    assert "MODEL_BUNDLE_PREGAME_CORPUS_MISSING" in report["blockers"]
    assert report["bundle_complete"] is False


def test_a_receipted_mlb_bundle_completes_and_the_guard_publishes():
    db = _Db(_tables(pregame=[_pregame_row()]))

    report = readers.assess_registry_model_bundle(db, _scope())
    assert report["bundle_complete"] is True
    assert report["blockers"] == []
    assert report["status"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert report["component_sources"]["pregame_corpus"]["status"] == readers.FOUND

    triad = boundary.evaluate_market_evidence_triad(
        model_kind="TEAM_EVENT",
        sport="MLB",
        official_event_id=EVENT_ID,
        event_start_time=EVENT_START,
        prediction_timestamp=MODEL_AT,
        model_family=FAMILY,
        model_artifact_version=VERSION,
        feature_schema_version=SCHEMA,
        db=db,
    )
    assert triad["status"] == boundary.COMPLETE
    assert triad["bundle_complete"] is True
    assert triad["blockers"] == []

    sealed = boundary.seal_report(triad)
    assert boundary.verify_sealed_report(
        sealed,
        sport="MLB",
        official_event_id=EVENT_ID,
        model_artifact_version=VERSION,
    )

    guard = evaluate_team_event_official_publication(
        {**_approved_payload(), "model_evidence_triad": sealed}
    )
    assert guard["status"] == "PASS"
    assert guard["blockers"] == []
    assert guard["can_execute"] is False


def test_a_snapshot_captured_after_first_pitch_is_held():
    db = _Db(_tables(pregame=[_pregame_row(feature_snapshot_timestamp=EVENT_START)]))

    report = readers.assess_registry_model_bundle(db, _scope())

    assert report["bundle_complete"] is False
    assert "MODEL_BUNDLE_PREGAME_CORPUS_INVALID_OR_UNVERIFIED" in report["blockers"]


def test_pregame_evidence_must_bind_the_served_artifact_checksum():
    db = _Db(_tables(pregame=[_pregame_row(model_artifact_checksum=FOREIGN_SHA)]))

    report = readers.assess_registry_model_bundle(db, _scope())

    assert report["bundle_complete"] is False
    assert "MODEL_BUNDLE_PREGAME_CORPUS_INVALID_OR_UNVERIFIED" in report["blockers"]


def test_a_superseded_snapshot_is_ignored_but_two_current_rows_fail_closed():
    superseded = _pregame_row(
        is_current=False, feature_snapshot_id="feat-snap-old", created_at="2026-11-12T17:30:00+00:00"
    )
    current = _pregame_row()
    refreshed = readers.assess_registry_model_bundle(
        _Db(_tables(pregame=[superseded, current])), _scope()
    )
    assert refreshed["bundle_complete"] is True

    ambiguous = readers.assess_registry_model_bundle(
        _Db(_tables(pregame=[current, dict(current)])), _scope()
    )
    assert ambiguous["bundle_complete"] is False
    assert "MODEL_BUNDLE_PREGAME_CORPUS_AMBIGUOUS" in ambiguous["blockers"]


def test_migration_declares_the_pregame_and_calibrator_tables_additively():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table if not exists public.wow_mlb_event_pregame_feature_rows" in sql
    assert "create table if not exists public.wow_mlb_event_calibrator_evidence" in sql
    assert "official_event_id" in sql
    assert "source_payload_sha256" in sql
    assert "feature_snapshot_sha256" in sql
    assert "is_current" in sql
    # Prelock ordering and a single current snapshot are enforced, not assumed.
    assert "source_snapshot_timestamp <= feature_snapshot_timestamp" in sql
    assert "feature_snapshot_timestamp < event_start_time" in sql
    assert "where is_current" in sql
    assert "enable row level security" in sql

    statements = [
        line.strip()
        for line in sql.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert not any(
        line.startswith(("grant ", "revoke ", "update ", "delete ", "truncate "))
        for line in statements
    )
