from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

import nhl_candidate_pipeline as nhl
import v17.nhl_model_maintenance as maintenance
from nhl_candidate_pipeline import (
    FEATURE_NAMES,
    FEATURE_RECONSTRUCTION_VERSION,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    SOURCE_ID,
    game_record,
    normalize_game,
)
from v17.nhl_candidate_preflight import (
    NHLCandidatePreflightError,
    preflight_nhl_candidate_package,
    validate_nhl_goalie_status_handoff,
)
from v17.binary_candidate_lifecycle import BinaryTrainingRow, train_binary_candidate


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.filters = {}
        self.mode = "select"
        self.payload = None
        self.on_conflict = None
        self.ignore_duplicates = False

    def select(self, *args):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, value):
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = dict(payload)
        return self

    def upsert(self, payload, *, on_conflict=None, ignore_duplicates=False):
        self.mode = "upsert"
        self.payload = [dict(item) for item in payload]
        self.on_conflict = on_conflict
        self.ignore_duplicates = ignore_duplicates
        return self

    def execute(self):
        rows = self.db.rows.setdefault(self.table_name, [])
        if self.mode == "insert":
            payload = dict(self.payload or {})
            if self.table_name == "wow_d1_source_events":
                payload.setdefault("source_event_id", "source-1")
            elif self.table_name == "wow_d1_training_rows":
                payload.setdefault("training_row_id", "training-1")
            elif self.table_name == "wow_d1_candidate_artifacts":
                payload.setdefault("candidate_id", "candidate-1")
            rows.append(payload)
            return SimpleNamespace(data=[payload])
        if self.mode == "upsert":
            inserted = []
            keys = tuple((self.on_conflict or "").split(",")) if self.on_conflict else ()
            for payload in self.payload or []:
                duplicate = any(all(existing.get(key) == payload.get(key) for key in keys) for existing in rows) if keys else False
                if duplicate and self.ignore_duplicates:
                    continue
                rows.append(dict(payload))
                inserted.append(dict(payload))
            return SimpleNamespace(data=inserted)
        return SimpleNamespace(data=[row for row in rows if all(row.get(k) == v for k, v in self.filters.items())])


class FakeDB:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        return FakeQuery(self, name)


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def fake_package(rows_n=400):
    games = []
    feature_rows = []
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for i in range(rows_n):
        event_id = f"evt-{i}"
        start = base + timedelta(days=i)
        event_start = start.isoformat()
        source_sha = f"{i + 1:064x}"
        positive_outcome = i % 2 == 0
        manifest = {
            "source_id": SOURCE_ID,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "reconstruction_version": FEATURE_RECONSTRUCTION_VERSION,
            "historical_reconstruction": True,
            "reconstruction_basis": "PRIOR_SETTLED_GAMES_ONLY",
            "archive_pregame_snapshot_claimed": False,
            "market_features_used": False,
            "home_prior_history_hash": "a" * 64,
            "away_prior_history_hash": "b" * 64,
            "game_source_payload_sha256": source_sha,
            "can_execute": False,
        }
        features = {name: float(i % 5) for name in FEATURE_NAMES}
        games.append({
            "sport": "NHL", "league": "NHL", "official_event_id": event_id,
            "season": "20202021", "event_start_time": event_start,
            "home_team": "BOS", "away_team": "NYR",
            "home_score": 3 if positive_outcome else 2,
            "away_score": 2 if positive_outcome else 3,
            "positive_outcome": positive_outcome,
            "source_provider": SOURCE_ID,
            "source_uri": "https://api-web.nhle.com/v1/club-schedule-season/BOS/20202021",
            "source_retrieved_at": "2026-09-15T00:00:00+00:00",
            "source_payload_sha256": source_sha,
            "historical_reconstruction": True,
            "can_execute": False,
        })
        feature_rows.append({
            "sport": "NHL", "league": "NHL", "official_event_id": event_id,
            "season": "20202021", "event_start_time": event_start,
            "feature_as_of": (start - timedelta(seconds=1)).isoformat(),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "features": features,
            "source_manifest": manifest,
            "source_manifest_sha256": _hash(manifest),
            "historical_reconstruction": True,
            "archived_pregame_snapshot": False,
            "market_features_used": False,
            "can_execute": False,
            "positive_outcome": positive_outcome,
        })
    train_n = int(rows_n * 0.60)
    calibration_end = int(rows_n * 0.80)
    calibration_n = calibration_end - train_n
    test_n = rows_n - calibration_end
    hash_rows = [
        {
            "event_id": row["official_event_id"],
            "event_start_time": row["event_start_time"],
            "feature_as_of": row["feature_as_of"],
            "positive_outcome": row["positive_outcome"],
            "features": {name: float(row["features"][name]) for name in FEATURE_NAMES},
            "source_manifest_sha256": row["source_manifest_sha256"],
        }
        for row in feature_rows
    ]
    dataset_hash = _hash({
        "model_family": MODEL_FAMILY,
        "feature_names": list(FEATURE_NAMES),
        "rows": hash_rows,
    })
    artifact_payload = {
        "model_family": MODEL_FAMILY,
        "feature_names": list(FEATURE_NAMES),
        "split_policy": "CHRONOLOGICAL_60_20_20",
        "train_end_index": train_n,
        "calibration_end_index": calibration_end,
    }
    return {
        "status": "CANDIDATE_BUILT",
        "games": games,
        "feature_rows": feature_rows,
        "candidate": {
            "sport": "NHL", "league": "NHL", "model_family": MODEL_FAMILY,
            "model_artifact_version": "nhl-v1", "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "source_policy_id": SOURCE_ID, "training_dataset_hash": dataset_hash,
            "training_code_sha": "d" * 40, "artifact_checksum": _hash(artifact_payload),
            "artifact_payload": artifact_payload,
            "calibrator_payload": {"method": "EMPIRICAL_WILSON_BINS_V1"},
            "validation_metrics": {
                "train_n": train_n, "calibration_n": calibration_n, "test_n": test_n,
                "market_features_used": False,
                "can_execute": False, "probability_publishable": False,
            },
            "training_rows": train_n, "calibration_rows": calibration_n, "test_rows": test_n,
            "research_screen_pass": True, "source_review_status": "REQUIRED",
            "lifecycle_state": "CANDIDATE", "promoted": False, "active": False,
            "probability_publishable": False, "can_execute": False,
        },
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
    }


def test_default_years_stop_before_current_season_start():
    from datetime import datetime, timezone
    assert maintenance.default_start_years(datetime(2026, 9, 15, tzinfo=timezone.utc)) == (2021, 2022, 2023, 2024, 2025)


def test_official_final_score_keeps_full_game_shootout_winner():
    parsed = normalize_game(
        {
            "id": 2021020001,
            "season": 20212022,
            "gameType": 2,
            "gameState": "FINAL",
            "periodDescriptor": {"periodType": "SO"},
            "startTimeUTC": "2021-10-02T00:00:00Z",
            "homeTeam": {"abbrev": "BOS", "score": 3},
            "awayTeam": {"abbrev": "NYR", "score": 2},
        },
        source_uri="https://api-web.nhle.com/v1/gamecenter/2021020001/landing",
        retrieved_at="2026-09-15T00:00:00+00:00",
    )
    assert parsed is not None
    assert game_record(parsed)["positive_outcome"] is True


def test_maintenance_persists_candidate_and_reconciles_training_outcome(monkeypatch):
    package = fake_package()
    for row in package["feature_rows"]:
        row.pop("positive_outcome")
    monkeypatch.setattr(maintenance, "build_candidate", lambda years, **kwargs: package)
    db = FakeDB()
    result = maintenance.run_nhl_model_maintenance(db, start_years=(2021,), training_code_sha="f" * 40)
    assert result["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert result["candidate_preflight"]["status"] == "NHL_CANDIDATE_PREFLIGHT_READY"
    assert all(isinstance(row.get("positive_outcome"), bool) for row in package["feature_rows"])
    assert db.rows["wow_d1_training_rows"][0]["outcome_json"] == {"positive_outcome": True}
    assert db.rows["wow_d1_candidate_artifacts"][0]["lifecycle_state"] == "CANDIDATE"


def test_batch_persistence_is_idempotent_for_source_and_training_rows(monkeypatch):
    monkeypatch.setattr(maintenance, "build_candidate", lambda years, **kwargs: fake_package())
    db = FakeDB()
    first = maintenance.run_nhl_model_maintenance(db, start_years=(2021,), training_code_sha="f" * 40)
    second = maintenance.run_nhl_model_maintenance(db, start_years=(2021,), training_code_sha="f" * 40)
    assert first["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert second["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert len(db.rows["wow_d1_source_events"]) == 400
    assert len(db.rows["wow_d1_training_rows"]) == 400
    assert len(db.rows["wow_d1_candidate_artifacts"]) == 1


def test_route_install_is_idempotent():
    app = FastAPI()
    maintenance.install_nhl_model_maintenance_route(app, auth_dependency=lambda: None, db_client_fn=FakeDB)
    maintenance.install_nhl_model_maintenance_route(app, auth_dependency=lambda: None, db_client_fn=FakeDB)
    paths = [route.path for route in app.router.routes]
    assert paths.count("/internal/v17/nhl-model-maintenance") == 1


def test_candidate_preflight_rejects_historic_8580_split_against_6128_cohort():
    package = fake_package(rows_n=6128)
    package["candidate"].update({
        "training_rows": 6128,
        "calibration_rows": 1226,
        "test_rows": 1226,
    })
    package["candidate"]["validation_metrics"].update({
        "train_n": 3676,
        "calibration_n": 1226,
        "test_n": 1226,
    })

    try:
        preflight_nhl_candidate_package(package)
    except NHLCandidatePreflightError as exc:
        assert exc.code == "NHL_CANDIDATE_PARTITION_MISMATCH"
        assert "source_rows=6128" in str(exc)
    else:
        raise AssertionError("the historical 8,580 split must fail preflight")


def test_candidate_preflight_rejects_duplicate_official_event_identity():
    package = fake_package()
    package["games"].append(dict(package["games"][0]))
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_DUPLICATE_IDENTITY"


def test_candidate_preflight_rejects_dataset_hash_drift():
    package = fake_package()
    package["feature_rows"][100]["features"]["elo_delta"] += 1.0
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_DATASET_HASH_MISMATCH"


def test_candidate_preflight_rejects_source_payload_mutation():
    package = fake_package()
    package["games"][50]["source_payload_sha256"] = "f" * 64
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_SOURCE_IDENTITY_MISMATCH"


def test_candidate_preflight_rejects_feature_time_at_or_after_event_start():
    package = fake_package()
    package["feature_rows"][50]["feature_as_of"] = package["feature_rows"][50]["event_start_time"]
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_TEMPORAL_LEAKAGE"


def test_candidate_preflight_hash_matches_binary_lifecycle():
    package = fake_package()
    rows = [
        BinaryTrainingRow(
            event_id=row["official_event_id"],
            event_start_time=row["event_start_time"],
            feature_as_of=row["feature_as_of"],
            positive_outcome=row["positive_outcome"],
            features=row["features"],
            source_manifest_sha256=row["source_manifest_sha256"],
        )
        for row in package["feature_rows"]
    ]
    fitted = train_binary_candidate(
        rows,
        model_family=MODEL_FAMILY,
        feature_names=FEATURE_NAMES,
    )
    package["candidate"].update({
        "training_dataset_hash": fitted.dataset_hash,
        "training_rows": fitted.metrics.train_n,
        "calibration_rows": fitted.metrics.calibration_n,
        "test_rows": fitted.metrics.test_n,
        "artifact_payload": fitted.artifact_payload,
        "artifact_checksum": _hash(fitted.artifact_payload),
        "validation_metrics": {
            "train_n": fitted.metrics.train_n,
            "calibration_n": fitted.metrics.calibration_n,
            "test_n": fitted.metrics.test_n,
            "market_features_used": False,
        },
    })
    result = preflight_nhl_candidate_package(package)
    assert result["training_dataset_hash"] == fitted.dataset_hash
    assert result["feature_schema_version"] == FEATURE_SCHEMA_VERSION


def test_6128_cohort_accepts_true_disjoint_3676_1226_1226_partitions():
    package = fake_package(rows_n=6128)
    candidate = package["candidate"]
    assert (
        candidate["training_rows"],
        candidate["calibration_rows"],
        candidate["test_rows"],
    ) == (3676, 1226, 1226)
    result = preflight_nhl_candidate_package(package)
    assert result["training_feature_n"] == 6128
    assert result["can_execute"] is False
    assert result["probability_publishable"] is False


def _deterministic_nhl_games():
    teams = ("BOS", "NYR", "TOR", "MTL", "TBL", "FLA", "CAR", "NJD")
    games = []
    game_index = 0
    for season_offset, season_id in enumerate((20212022, 20222023)):
        first_year = 2021 + season_offset
        base = datetime(first_year, 10, 1, tzinfo=timezone.utc)
        local_index = 0
        for cycle in range(15):
            for home_index in range(len(teams)):
                for away_index in range(home_index + 1, len(teams)):
                    home = teams[home_index] if (cycle + home_index + away_index) % 2 == 0 else teams[away_index]
                    away = teams[away_index] if home == teams[home_index] else teams[home_index]
                    home_win = (game_index % 7) < 4
                    if game_index % 17 == 0:
                        home_win = not home_win
                    start = base + timedelta(hours=local_index * 6)
                    raw = {
                        "id": 20_000_000 + game_index,
                        "season": season_id,
                        "gameType": 2,
                        "gameState": "FINAL",
                        "neutralSite": False,
                        "startTimeUTC": start.isoformat().replace("+00:00", "Z"),
                        "homeTeam": {
                            "id": home_index + 1,
                            "abbrev": home,
                            "score": 4 if home_win else 2,
                        },
                        "awayTeam": {
                            "id": away_index + 1,
                            "abbrev": away,
                            "score": 2 if home_win else 4,
                        },
                    }
                    parsed = nhl.normalize_game(
                        raw,
                        source_uri=(
                            "https://api-web.nhle.com/v1/club-schedule-season/"
                            f"{home}/{season_id}"
                        ),
                        retrieved_at="2026-10-22T00:00:00+00:00",
                    )
                    assert parsed is not None
                    games.append(parsed)
                    game_index += 1
                    local_index += 1
    return sorted(games, key=lambda row: (row.event_start_time, row.game_id))


def test_real_build_candidate_shape_passes_after_partition_fix(monkeypatch):
    games = _deterministic_nhl_games()
    monkeypatch.setattr(
        nhl,
        "fetch_historical_games",
        lambda years, session=nhl.requests: games,
    )
    package = nhl.build_candidate(
        (2021, 2022), training_code_sha="a" * 40
    )
    candidate = package["candidate"]
    # The exact #1612 cohort fix is part of this stacked branch. Do not
    # reproduce the old total-as-training bug or rewrite persisted artifacts.
    assert candidate["training_rows"] == candidate["validation_metrics"]["train_n"]
    assert candidate["calibration_rows"] == candidate["validation_metrics"]["calibration_n"]
    assert candidate["test_rows"] == candidate["validation_metrics"]["test_n"]
    assert (
        candidate["training_rows"] + candidate["calibration_rows"] + candidate["test_rows"]
        == len(package["feature_rows"])
    )
    maintenance._attach_training_outcomes(package)
    result = preflight_nhl_candidate_package(package)
    assert result["training_feature_n"] == len(package["feature_rows"])
    assert result["training_dataset_hash"] == candidate["training_dataset_hash"]
    assert result["can_execute"] is False


def test_candidate_preflight_rejects_feature_schema_drift():
    package = fake_package()
    package["feature_rows"][0]["features"]["unexpected_feature"] = 1.0
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_FEATURE_SCHEMA_MISMATCH"


def test_candidate_preflight_rejects_artifact_checksum_drift():
    package = fake_package()
    package["candidate"]["artifact_checksum"] = "f" * 64
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_ARTIFACT_CHECKSUM_MISMATCH"


def test_candidate_preflight_reconciles_embedded_artifact_split_metadata():
    package = fake_package()
    artifact = package["candidate"]["artifact_payload"]
    artifact["calibration_end_index"] += 1
    package["candidate"]["artifact_checksum"] = _hash(artifact)
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_ARTIFACT_SPLIT_MISMATCH"

    package = fake_package()
    artifact = package["candidate"]["artifact_payload"]
    artifact["model_family"] = "OTHER_MODEL"
    package["candidate"]["artifact_checksum"] = _hash(artifact)
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_ARTIFACT_IDENTITY_MISMATCH"


def test_candidate_preflight_rejects_wrong_source_policy_and_reconstruction():
    package = fake_package()
    package["candidate"]["source_policy_id"] = "OTHER_SOURCE"
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_IDENTITY_MISMATCH"

    package = fake_package()
    package["feature_rows"][0]["source_manifest"]["reconstruction_version"] = "OTHER"
    package["feature_rows"][0]["source_manifest_sha256"] = _hash(
        package["feature_rows"][0]["source_manifest"]
    )
    with pytest.raises(NHLCandidatePreflightError) as caught:
        preflight_nhl_candidate_package(package)
    assert caught.value.code == "NHL_CANDIDATE_SOURCE_MANIFEST_INVALID"


def _goalie_snapshot(
    home_status="CONFIRMED_STARTER",
    away_status="CONFIRMED_STARTER",
    *,
    as_of="2026-10-22T17:00:00Z",
    kickoff="2026-10-22T20:00:00Z",
):
    return {
        "official_event_id": "nhl-event-1",
        "source_id": SOURCE_ID,
        "source_snapshot_id": f"official-snapshot-{as_of}",
        "source_payload_sha256": "c" * 64,
        "as_of": as_of,
        "event_start_time_utc": kickoff,
        "home": {"goalie_id": "goalie-home", "starting_status": home_status},
        "away": {"goalie_id": "goalie-away", "starting_status": away_status},
    }


def test_goalie_handoff_rejects_missing_starting_goalie():
    frozen = _goalie_snapshot()
    current = _goalie_snapshot(as_of="2026-10-22T18:00:00Z")
    current["away"]["goalie_id"] = ""
    with pytest.raises(NHLCandidatePreflightError) as caught:
        validate_nhl_goalie_status_handoff(frozen, current)
    assert caught.value.code == "NHL_GOALIE_MISSING"


def test_goalie_handoff_rejects_changed_starting_status():
    frozen = _goalie_snapshot()
    current = _goalie_snapshot(
        home_status="CONFIRMED", as_of="2026-10-22T18:00:00Z"
    )
    with pytest.raises(NHLCandidatePreflightError) as caught:
        validate_nhl_goalie_status_handoff(frozen, current)
    assert caught.value.code == "NHL_STARTING_STATUS_CHANGED"


@pytest.mark.parametrize("status", ["SCRATCHED", "OUT", "UNKNOWN", "QUESTIONABLE"])
def test_goalie_handoff_rejects_nonconfirmed_starter_even_when_both_snapshots_match(status):
    frozen = _goalie_snapshot(home_status=status)
    current = _goalie_snapshot(home_status=status, as_of="2026-10-22T18:00:00Z")
    with pytest.raises(NHLCandidatePreflightError) as caught:
        validate_nhl_goalie_status_handoff(frozen, current)
    assert caught.value.code == "NHL_STARTING_GOALIE_NOT_CONFIRMED"


def test_goalie_handoff_rejects_changed_official_starter_designation_for_same_player():
    frozen = _goalie_snapshot(home_status="CONFIRMED")
    current = _goalie_snapshot(
        home_status="OFFICIAL_STARTER",
        as_of="2026-10-22T18:00:00Z",
    )
    with pytest.raises(NHLCandidatePreflightError) as caught:
        validate_nhl_goalie_status_handoff(frozen, current)
    assert caught.value.code == "NHL_STARTING_STATUS_CHANGED"


@pytest.mark.parametrize(
    ("frozen_as_of", "current_as_of", "expected_code"),
    [
        ("2026-10-22T17:00:00Z", "2026-10-22T17:00:00Z", "NHL_GOALIE_CURRENT_RECEIPT_STALE"),
        ("2026-10-22T18:00:00Z", "2026-10-22T17:30:00Z", "NHL_GOALIE_CURRENT_RECEIPT_STALE"),
        ("2026-10-22T15:00:00Z", "2026-10-22T15:59:00Z", "NHL_GOALIE_CURRENT_RECEIPT_STALE"),
        ("2026-10-22T20:00:00Z", "2026-10-22T20:01:00Z", "NHL_GOALIE_EVIDENCE_AFTER_KICKOFF"),
        ("2026-10-22T17:00:00Z", "2026-10-22T20:00:00Z", "NHL_GOALIE_EVIDENCE_AFTER_KICKOFF"),
    ],
)
def test_goalie_handoff_rejects_stale_or_post_start_evidence(
    frozen_as_of, current_as_of, expected_code
):
    frozen = _goalie_snapshot(as_of=frozen_as_of)
    current = _goalie_snapshot(as_of=current_as_of)
    with pytest.raises(NHLCandidatePreflightError) as caught:
        validate_nhl_goalie_status_handoff(frozen, current)
    assert caught.value.code == expected_code


def test_goalie_handoff_accepts_current_receipt_at_four_hour_freshness_boundary():
    result = validate_nhl_goalie_status_handoff(
        _goalie_snapshot(as_of="2026-10-22T15:59:00Z"),
        _goalie_snapshot(as_of="2026-10-22T16:00:00Z"),
    )
    assert result["status"] == "NHL_GOALIE_STATUS_UNCHANGED"


def test_goalie_handoff_accepts_unchanged_source_pinned_inputs_without_authority():
    result = validate_nhl_goalie_status_handoff(
        _goalie_snapshot(),
        _goalie_snapshot(as_of="2026-10-22T18:00:00Z"),
    )
    assert result["status"] == "NHL_GOALIE_STATUS_UNCHANGED"
    assert result["as_of"] == "2026-10-22T18:00:00Z"
    assert result["can_execute"] is False


def test_goalie_handoff_rejects_relabelled_timestamp_on_identical_source_receipt():
    frozen = _goalie_snapshot()
    current = _goalie_snapshot(as_of="2026-10-22T18:00:00Z")
    current["source_snapshot_id"] = frozen["source_snapshot_id"]
    with pytest.raises(NHLCandidatePreflightError) as caught:
        validate_nhl_goalie_status_handoff(frozen, current)
    assert caught.value.code == "NHL_GOALIE_SOURCE_NOT_REFRESHED"


def test_goalie_handoff_rejects_unpinned_source():
    frozen = _goalie_snapshot()
    current = _goalie_snapshot(as_of="2026-10-22T18:00:00Z")
    current["source_id"] = "UNTRUSTED"
    with pytest.raises(NHLCandidatePreflightError) as caught:
        validate_nhl_goalie_status_handoff(frozen, current)
    assert caught.value.code == "NHL_GOALIE_SOURCE_PIN_MISSING"


def test_maintenance_blocks_invalid_candidate_before_persistence(monkeypatch):
    package = fake_package()
    package["candidate"]["training_rows"] = 6128
    monkeypatch.setattr(maintenance, "build_candidate", lambda years, **kwargs: package)
    db = FakeDB()
    result = maintenance.run_nhl_model_maintenance(
        db, start_years=(2021,), training_code_sha="f" * 40
    )
    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_CANDIDATE_PREFLIGHT_BLOCKED"
    assert result["blocked_stage"] == "CANDIDATE_PREFLIGHT"
    assert result["detail"]["code"] == "NHL_CANDIDATE_PARTITION_MISMATCH"
    assert db.rows == {}
