"""End-to-end NFL prop acceptance replay through real fitted artifacts and Daily runtime.

Exercises the four runtime-scoreable NFL prop lanes (PASSING_YARDS, RUSHING_YARDS,
RECEIVING_YARDS, ANYTIME_TD) using the real fitted artifacts in
``data/wow_nfl_prop_artifacts_v1.json``, real ``NFL_PROP_ROLLING_FITTED_V1`` and
``NFL_PROP_PRECALIBRATION_BOOTSTRAP_V1`` adapters, real ``score_discrete_prop_end_to_end``
and ``api_prod_market._probability_qualification``, and real ``run_daily_snapshot``.

No live network or production database is contacted; can_execute remains false.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import api_prod_market
from prop_calibration_adapters import register as register_calibration_adapter
from prop_discrete_engine import (
    clear_prop_calibration_adapters,
    score_discrete_prop_end_to_end,
)
from prop_distribution_contract import PropInferenceRequest
from prop_fitted_provider import clear_model_family_adapters
from prop_model_adapters import register as register_model_adapter
from v17 import daily_snapshot_runtime as runtime

ARTIFACT_JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "data",
    "wow_nfl_prop_artifacts_v1.json",
)


def setup_function():
    clear_model_family_adapters()
    clear_prop_calibration_adapters()
    register_model_adapter()
    register_calibration_adapter()


def teardown_function():
    clear_model_family_adapters()
    clear_prop_calibration_adapters()


def _load_nfl_artifacts() -> dict[str, dict[str, Any]]:
    with open(ARTIFACT_JSON_PATH, encoding="utf-8") as handle:
        entries = json.load(handle)
    return {str(item["stat_type"]): dict(item) for item in entries}


def _rpc_artifact_payload(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "artifact_id": f"nfl-art-{entry['stat_type'].lower()}",
        "provider_identity": entry["provider_identity"],
        "model_family": entry["model_family"],
        "model_artifact_version": entry["model_artifact_version"],
        "calibrator_version": entry["calibrator_version"],
        "sport": "NFL",
        "stat_type": entry["stat_type"],
        "feature_schema_version": entry["feature_schema_version"],
        "feature_transform_version": entry["feature_transform_version"],
        "specialist_version": entry["specialist_version"],
        "certification_id": entry["certification_id"],
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "training_dataset_hash": entry["training_dataset_hash"],
        "training_code_sha": entry["training_code_sha"],
        "artifact_checksum": entry["artifact_checksum"],
        "artifact_format": entry["artifact_format"],
        "artifact_payload": entry["artifact_payload"],
        "supported_line_min": entry["supported_line_min"],
        "supported_line_max": entry["supported_line_max"],
        "training_rows": entry["training_rows"],
        "validation_metrics": entry["validation_metrics"],
        "probability_publishable": False,
        "can_execute": False,
    }


class _ArtifactClient:
    def __init__(self, artifacts: dict[str, dict[str, Any]]):
        self._artifacts = artifacts

    def rpc(self, name: str, params: dict[str, Any]):
        assert name == "wow_prop_certified_model_artifact"
        stat_type = str(params["p_stat_type"]).upper()
        payload = _rpc_artifact_payload(self._artifacts[stat_type])
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=payload))


class _Query:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = list(rows)
        self._range: tuple[int, int] | None = None
        self._limit: int | None = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start: int, end: int):
        self._range = (start, end)
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def upsert(self, *_args, **_kwargs):
        return self

    def execute(self):
        rows = list(self._rows)
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        elif self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class _DailyDB:
    def __init__(self, prop_rows: list[dict[str, Any]]):
        self._prop_rows = list(prop_rows)
        self.persisted_details: list[dict[str, Any]] = []

    def table(self, name: str):
        if name == "wow_prop_evidence_snapshots":
            return _Query(self._prop_rows)
        if name in {"wow_v17_daily_row_details", "wow_v17_daily_acquisition_details"}:
            return _Query([])
        raise AssertionError(f"unexpected table {name}")


def _real_score_prop_adapter(
    artifacts: dict[str, dict[str, Any]],
    features_by_snapshot: dict[str, dict[str, Any]],
):
    client = _ArtifactClient(artifacts)

    class _ScorePropRequest:
        def __init__(self, **kwargs: Any):
            for key, value in kwargs.items():
                setattr(self, key, value)

    def score_prop(req: Any, _identity: str) -> dict[str, Any]:
        features = features_by_snapshot[str(req.source_snapshot_id)]
        as_of = datetime.now(timezone.utc).isoformat()
        inf_req = PropInferenceRequest(
            event_id=str(req.event_id),
            player_id=f"nfl:{req.player.lower().replace(' ', '-')}",
            sport="NFL",
            league_season="2026",
            stat_type=str(req.stat_type),
            market_identity_id=f"{req.event_id}:{req.player}:{req.stat_type}:{req.line}",
            request_id=f"req-{req.source_snapshot_id}-{req.direction.lower()}",
            as_of_timestamp=as_of,
            evidence_snapshot_id=str(req.source_snapshot_id),
            feature_schema_version="PROP_FEATURES_V1",
        )
        result = score_discrete_prop_end_to_end(
            client=client,
            request=inf_req,
            event_start_time=str(req.event_start_time),
            player=str(req.player),
            line=float(req.line),
            direction=str(req.direction),
            source_snapshot_id=str(req.source_snapshot_id),
            features=features,
            seed=20261122,
            money_lane_status="PAYOUT_UNRESOLVED",
        )
        market_lane = {"status": "HOLD", "can_execute": False}
        money_lane = {"status": "HOLD", "can_execute": False}
        settlement_lane = {"status": "HOLD", "can_execute": False}
        qualification = api_prod_market._probability_qualification(
            result.row, market_lane, money_lane, settlement_lane
        )
        # Exact production return shape of api_prod_market.score_prop:
        # note rank_eligible lives under probability_qualification, NOT top-level.
        return {
            "ok": True,
            "prediction": {
                "prediction_id": f"pred-{req.source_snapshot_id}-{req.direction.lower()}",
                "sport": "NFL",
                "stat_type": req.stat_type,
                "direction": req.direction,
                "can_execute": False,
            },
            "model_evidence": api_prod_market._discrete_model_evidence(result),
            "probability_qualification": qualification,
            "terminal_label": qualification["terminal_label"],
            "pick_rejected": qualification["pick_rejected"],
            "evidence": {
                "ok": True,
                "code": "PROP_EVIDENCE_READY",
                "hydration_status": "PASS",
                "source_snapshot_id": req.source_snapshot_id,
            },
            "backend_traversal": {
                "requester_model": "WOW_BETTING_ENGINE",
                "render": "PASS",
                "supabase_capability": "PASS",
                "supabase_evidence": "PASS",
                "controlling_specialist": "PASS",
                "exact_route_artifact": "PASS",
                "governed_model": "PASS",
                "prediction_ledger_write": "PASS",
            },
            "route_preflight": {
                "status": "PASS",
                "certified_artifact_code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
                "evidence_hydration_attempted": True,
                "can_execute": False,
            },
            "model_path": "WOW_PROP_FITTED_MODEL_V1->RAW_DISCRETE_DISTRIBUTION->CALIBRATION->PERSISTENCE",
            "probability_publishable": bool(result.row.probability_publishable),
            "can_execute": False,
        }

    return SimpleNamespace(ScorePropRequest=_ScorePropRequest, score_prop=score_prop)


def test_nfl_four_certified_prop_lanes_e2e_daily_qualification_and_receipts():
    """Real fitted NFL prop artifacts flow through Daily without silently stripping rank_eligible."""
    artifacts = _load_nfl_artifacts()
    assert set(artifacts) == {"PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"}

    future_start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    slate_date = future_start.date().isoformat()
    captured_at = datetime.now(timezone.utc).isoformat()

    # Four pregame NFL rows (one per certified stat lane) with strong historical support
    # on a low line so the real fitted specialist + Phase-A bootstrap lower bound exceeds 0.65.
    snapshots = [
        (
            "snap-nfl-pass",
            "PASSING_YARDS",
            "Josh Allen",
            100.5,
            {
                "game_log": [295, 310, 280, 325, 305, 290, 315, 285, 300, 320],
                "box_score_log": [{"opportunity": v} for v in (35, 38, 34, 40, 36, 35, 37, 34, 36, 39)],
            },
        ),
        (
            "snap-nfl-rush",
            "RUSHING_YARDS",
            "Derrick Henry",
            25.5,
            {
                "game_log": [112, 98, 125, 104, 118, 95, 110, 121, 106, 115],
                "box_score_log": [{"opportunity": v} for v in (20, 19, 22, 18, 21, 18, 20, 22, 19, 21)],
            },
        ),
        (
            "snap-nfl-rec",
            "RECEIVING_YARDS",
            "Ja'Marr Chase",
            25.5,
            {
                "game_log": [108, 115, 96, 122, 104, 112, 99, 118, 105, 114],
                "box_score_log": [{"opportunity": v} for v in (11, 12, 10, 13, 11, 12, 10, 12, 11, 12)],
            },
        ),
        (
            "snap-nfl-atd",
            "ANYTIME_TD",
            "Backup Fullback",
            0.5,
            {
                "game_log": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                "box_score_log": [{"opportunity": v} for v in (2, 1, 2, 1, 2, 1, 2, 1, 2, 1)],
            },
        ),
    ]

    prop_rows = []
    features_by_snapshot: dict[str, dict[str, Any]] = {}
    for snap_id, stat_type, player, line, features in snapshots:
        prop_rows.append(
            {
                "source_snapshot_id": snap_id,
                "captured_at": captured_at,
                "event_id": "NFL:2026-11-22:BUF-KC",
                "event_start_time": future_start.isoformat(),
                "sport": "NFL",
                "player": player,
                "stat_type": stat_type,
                "line": line,
                "hydration_status": "PASS",
                "blockers": [],
            }
        )
        features_by_snapshot[snap_id] = features

    db = _DailyDB(prop_rows)
    market_api = _real_score_prop_adapter(artifacts, features_by_snapshot)
    req = runtime.DailySnapshotRequest(
        requested_slate_date=slate_date,
        requested_timezone="UTC",
        lanes=["PROPS"],
        max_props=6,
        max_team_events=0,
        response_mode="FULL",
    )

    result = runtime.run_daily_snapshot(req, db=db, market_api=market_api, event_api=None)

    assert result["can_execute"] is False
    assert result["reconciliation"]["balanced"] is True
    assert result["reconciliation"]["rows_in"] == 4
    assert result["reconciliation"]["rows_completed"] >= 3

    by_stat = {row["identity"]["stat_type"]: row for row in result["rows"]}
    for stat_type in ("PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS"):
        row = by_stat[stat_type]
        assert row["row_status"] == "COMPLETED"
        assert row["probability_publishable"] is True
        assert row["rank_eligible"] is True
        assert row["can_execute"] is False
        assert row["terminal_reduction"]["approved_stage_exception_applied"] is True
        more = next(o for o in row["result"]["outcomes"] if o["direction"] == "MORE")
        assert more["payload"]["probability_qualification"]["rank_eligible"] is True
        assert more["payload"]["model_evidence"]["calibrated_probability_lower_bound"] >= 0.60
        assert more["payload"]["prediction"]["prediction_id"].startswith("pred-")

    # ANYTIME_TD with 0/10 TDs has LESS lower bound >= 0.60 (two-sided prop).
    atd_row = by_stat["ANYTIME_TD"]
    assert atd_row["row_status"] == "COMPLETED"
    assert atd_row["probability_publishable"] is True
    assert atd_row["rank_eligible"] is True
    less = next(o for o in atd_row["result"]["outcomes"] if o["direction"] == "LESS")
    assert less["payload"]["probability_qualification"]["rank_eligible"] is True
    assert less["payload"]["model_evidence"]["calibrated_probability_lower_bound"] >= 0.60

    # Stage progression monitor must accurately reflect hydrated, evaluated, certified, publishable.
    progression = result["stage_progression"]
    assert progression["discovered"] == 4
    assert progression["hydrated"] == 4
    assert progression["model_evaluated"] == 4
    assert progression["certified"] == 4
    assert progression["publishable"] == 4
    assert result["scoring_run_status"] == "ALL_PUBLISHABLE"
    assert result["stage_progression_by_sport"]["NFL"]["publishable"] == 4


def test_nfl_prop_coin_flip_line_fails_closed_below_65_lower_bound():
    """A line near the player's median must not qualify when lower bound < 0.65."""
    artifacts = _load_nfl_artifacts()
    future_start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    slate_date = future_start.date().isoformat()
    captured_at = datetime.now(timezone.utc).isoformat()

    snap_id = "snap-nfl-pass-coinflip"
    prop_rows = [
        {
            "source_snapshot_id": snap_id,
            "captured_at": captured_at,
            "event_id": "NFL:2026-11-22:BUF-KC",
            "event_start_time": future_start.isoformat(),
            "sport": "NFL",
            "player": "Median QB",
            "stat_type": "PASSING_YARDS",
            "line": 225.5,
            "hydration_status": "PASS",
            "blockers": [],
        }
    ]
    features = {
        snap_id: {
            "game_log": [220, 230, 215, 235, 225, 228, 222, 232, 218, 227],
            "box_score_log": [{"opportunity": v} for v in (32, 31, 33, 32, 31, 32, 33, 31, 32, 32)],
        }
    }

    db = _DailyDB(prop_rows)
    market_api = _real_score_prop_adapter(artifacts, features)
    req = runtime.DailySnapshotRequest(
        requested_slate_date=slate_date,
        requested_timezone="UTC",
        lanes=["PROPS"],
        max_props=6,
        max_team_events=0,
        response_mode="FULL",
    )

    result = runtime.run_daily_snapshot(req, db=db, market_api=market_api, event_api=None)
    row = result["rows"][0]
    assert row["row_status"] == "REJECTED"
    assert row["probability_publishable"] is False
    assert row["rank_eligible"] is False
    assert row["can_execute"] is False
    assert result["stage_progression"]["hydrated"] == 1
    assert result["stage_progression"]["model_evaluated"] == 1
    assert result["stage_progression"]["certified"] == 0
    assert result["stage_progression"]["publishable"] == 0
    assert result["scoring_run_status"] == "ALL_HELD_ZERO_PUBLISHABLE"


def test_stage_monitor_rejects_forged_prop_certification_without_backend_receipts():
    """A PROPS row claiming rank_eligible=True without verified backend_traversal + prediction_id is not certified."""
    forged_row = {
        "lane": "PROPS",
        "row_status": "COMPLETED",
        "probability_publishable": True,
        "rank_eligible": True,
        "result": {
            "probability_publishable": True,
            "rank_eligible": True,
            "can_execute": False,
            "outcomes": [
                {
                    "direction": "MORE",
                    "status": "COMPLETED",
                    "payload": {
                        "probability_publishable": True,
                        "rank_eligible": True,
                        "can_execute": False,
                    },
                }
            ],
        },
    }
    assert runtime._is_row_certified(forged_row, evaluated=True) is False
