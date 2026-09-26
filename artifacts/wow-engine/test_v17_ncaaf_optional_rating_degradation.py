from __future__ import annotations

from dataclasses import dataclass

import pytest

import ncaaf_cfbd_hydrator as hydrator
from ncaaf_cfbd_client import CFBDResponse, CFBDUnavailable
import v17.ncaaf_model_maintenance as maintenance


class RatingFailureClient:
    def games(self, *, year: int, week: int, classification: str | None = None) -> CFBDResponse:
        return CFBDResponse(
            endpoint="/games",
            params={"year": year, "week": week, "classification": classification},
            rows=[{"id": f"game-{year}-{week}"}],
        )

    def ratings(self, family: str, *, year: int, week: int | None = None) -> CFBDResponse:
        raise CFBDUnavailable("CFBD_HTTP_ERROR", f"rating unavailable:{family}:{year}:{week}")


class GamesFailureClient(RatingFailureClient):
    def games(self, *, year: int, week: int, classification: str | None = None) -> CFBDResponse:
        raise CFBDUnavailable("CFBD_HTTP_ERROR", f"games unavailable:{year}:{week}")


def test_optional_rating_failure_preserves_valid_games_snapshot():
    snapshots = hydrator.hydrate_cfbd_season(
        RatingFailureClient(),
        season=2026,
        weeks=[4],
        rating_families=("elo",),
        classification="fbs",
        allow_optional_rating_failures=True,
    )

    assert len(snapshots) == 2
    assert snapshots[0].endpoint == "/games"
    assert snapshots[0].acquisition_status == "AVAILABLE"
    assert snapshots[0].response_row_count == 1
    assert snapshots[0].blocker_codes == []
    assert snapshots[1].endpoint == "/ratings/elo"
    assert snapshots[1].acquisition_status == "BLOCKED"
    assert snapshots[1].response_row_count == 0
    assert snapshots[1].blocker_codes == ["CFBD_HTTP_ERROR"]
    assert snapshots[1].can_execute is False


def test_optional_rating_failure_remains_fail_closed_by_default():
    with pytest.raises(CFBDUnavailable) as exc_info:
        hydrator.hydrate_cfbd_season(
            RatingFailureClient(),
            season=2026,
            weeks=[4],
            rating_families=("elo",),
            classification="fbs",
        )
    assert exc_info.value.code == "CFBD_HTTP_ERROR"


def test_games_failure_is_mandatory_even_when_rating_degradation_is_allowed():
    with pytest.raises(CFBDUnavailable) as exc_info:
        hydrator.hydrate_cfbd_season(
            GamesFailureClient(),
            season=2026,
            weeks=[4],
            rating_families=("elo",),
            classification="fbs",
            allow_optional_rating_failures=True,
        )
    assert exc_info.value.code == "CFBD_HTTP_ERROR"


@dataclass
class FakeSnapshot:
    blocker_codes: list[str]


@dataclass
class FakeGames:
    candidate_rows: int = 7
    persisted_rows: int = 7
    skipped_rows: int = 0
    blocker_codes: tuple[str, ...] = ()
    can_execute: bool = False


def test_maintenance_reports_optional_rating_failure_as_degradation(monkeypatch):
    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", classmethod(lambda cls: object()))
    monkeypatch.setattr(
        maintenance,
        "hydrate_cfbd_season",
        lambda *args, **kwargs: [FakeSnapshot(blocker_codes=["CFBD_HTTP_ERROR"])],
    )
    monkeypatch.setattr(maintenance, "persist_source_snapshots", lambda db, rows: 1)
    monkeypatch.setattr(maintenance, "materialize_training_games", lambda db, rows: FakeGames())
    monkeypatch.setattr(
        maintenance,
        "materialize_complete_training_features",
        lambda db: {
            "status": "BLOCKED",
            "complete_feature_rows": 0,
            "market_features_used": False,
            "probability_publishable": False,
            "can_execute": False,
        },
    )
    monkeypatch.setattr(
        maintenance,
        "train_result_form_candidate",
        lambda db, *, training_code_sha: {
            "ok": True,
            "lifecycle_state": "CANDIDATE",
            "probability_publishable": False,
            "can_execute": False,
        },
    )

    result = maintenance.run_ncaaf_model_maintenance(
        object(), seasons=[2026], weeks=[4], training_code_sha="a" * 40
    )

    assert result["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert result["fresh_acquisition_complete"] is False
    assert result["maintenance_degraded"] is True
    assert "CFBD_HTTP_ERROR" in result["blockers"]
    assert result["acquisition"][0]["status"] == "UPDATED_WITH_DEGRADATION"
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
