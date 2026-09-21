from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import nfl_prop_auto_hydration as nfl
import pick_request_runtime as runtime


def _espn_event(*, home: str = "LAR", away: str = "NYG", season: int = 2026, week: int = 2) -> dict:
    return {
        "id": "401872947",
        "season": {"year": season},
        "week": {"number": week},
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": home}},
                    {"homeAway": "away", "team": {"abbreviation": away}},
                ]
            }
        ],
    }


def _evidence(*, canonical_event_id: str, verified_canonical_event_id: str) -> runtime.RawPropEvidence:
    now = datetime.now(timezone.utc)
    captured = now - timedelta(minutes=2)
    return runtime.RawPropEvidence.model_validate(
        {
            "captured_at": captured.isoformat(),
            "game_log": [90.0] * 10,
            "box_score_log": [{"game_id": f"game-{i}"} for i in range(10)],
            "role_status": {
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "player": "Puka Nacua",
                "espn_athlete_id": "4426515",
                "team": "LAR",
                "opponent": "NYG",
                "event_id": "401872947",
                "provider_event_ids": {"ESPN": "401872947"},
                "canonical_event_id": canonical_event_id,
                "verified_canonical_event_id": verified_canonical_event_id,
                "identity_binding_status": "PASS",
            },
            "role_timestamp": captured.isoformat(),
            "opportunity_ledger": {"status": "PASS"},
            "source_timestamps": {"ESPN_NFL_IDENTITY_SCOREBOARD": captured.isoformat()},
            "evidence_version": "PROP_EVIDENCE_V1",
            "rate_provenance": "TEST_ONLY",
        }
    )


def _row(event_id: str, verified_canonical_event_id: str) -> runtime.PickRequestRow:
    event_start = datetime.now(timezone.utc) + timedelta(days=1)
    return runtime.PickRequestRow(
        event_id=event_id,
        event_start_time=event_start.isoformat(),
        sport="NFL",
        league="NFL",
        player="Puka Nacua",
        stat_type="RECEIVING_YARDS",
        line=84.5,
        direction="MORE",
        opponent="New York Giants",
        evidence=_evidence(
            canonical_event_id=event_id,
            verified_canonical_event_id=verified_canonical_event_id,
        ),
    )


def test_espn_event_derives_nflverse_canonical_identity_for_rams() -> None:
    metadata = nfl._canonical_event_metadata(_espn_event())

    assert metadata["provider_home_team"] == "LAR"
    assert metadata["provider_away_team"] == "NYG"
    assert metadata["canonical_home_team"] == "LA"
    assert metadata["canonical_away_team"] == "NYG"
    assert metadata["verified_canonical_event_id"] == "2026_02_NYG_LA"


def test_espn_team_aliases_are_identity_only_and_deterministic() -> None:
    metadata = nfl._canonical_event_metadata(_espn_event(home="WSH", away="JAC"))

    assert metadata["verified_canonical_event_id"] == "2026_02_JAX_WAS"


def test_synthetic_nfl_canonical_id_is_rejected_before_snapshot_persistence() -> None:
    row = _row(
        "SYNTHETIC-NFL-NYG-LAR-IDENTITY-PROBE",
        "2026_02_NYG_LA",
    )

    with pytest.raises(ValueError, match="NFL_CANONICAL_EVENT_ID_MISMATCH"):
        runtime._validate_evidence(row, "RECEIVING_YARDS")


def test_verified_nfl_canonical_id_passes_evidence_validation() -> None:
    row = _row("2026_02_NYG_LA", "2026_02_NYG_LA")

    normalized = runtime._validate_evidence(row, "RECEIVING_YARDS")

    assert normalized["event_id"] == "2026_02_NYG_LA"
    assert normalized["sport"] == "NFL"


def test_provider_backed_nfl_evidence_without_verified_canonical_id_fails_closed() -> None:
    row = _row("2026_02_NYG_LA", "")

    with pytest.raises(ValueError, match="NFL_CANONICAL_EVENT_ID_UNVERIFIED"):
        runtime._validate_evidence(row, "RECEIVING_YARDS")
