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


def test_verified_nfl_identity_is_not_game_availability() -> None:
    row = _row("2026_02_NYG_LA", "2026_02_NYG_LA")

    # Before P0 #2131 this exact ESPN roster-only fixture passed validation.
    # Identity, L10 and provider timestamps are not proof of participation.
    with pytest.raises(ValueError, match="PLAYER_AVAILABILITY_UNVERIFIED"):
        runtime._validate_evidence(row, "RECEIVING_YARDS")


def test_provider_backed_nfl_evidence_without_verified_canonical_id_fails_closed() -> None:
    row = _row("2026_02_NYG_LA", "")

    with pytest.raises(ValueError, match="NFL_CANONICAL_EVENT_ID_UNVERIFIED"):
        runtime._validate_evidence(row, "RECEIVING_YARDS")


@pytest.mark.parametrize("availability", ["OUT", "IR", "injured reserve", "inactive", "suspended"])
def test_nfl_known_unavailable_is_rejected_before_scoring(availability: str) -> None:
    row = _row("2026_02_NYG_LA", "2026_02_NYG_LA")
    row.evidence.role_status["game_availability_status"] = availability
    with pytest.raises(ValueError, match="PLAYER_UNAVAILABLE"):
        runtime._validate_evidence(row, "RECEIVING_YARDS")


@pytest.mark.parametrize("availability", ["ACTIVE", "AVAILABLE", "PROBABLE", "QUESTIONABLE", "DOUBTFUL"])
def test_nfl_caller_claim_of_playability_cannot_bypass_trusted_source(availability: str) -> None:
    row = _row("2026_02_NYG_LA", "2026_02_NYG_LA")
    row.evidence.role_status["game_availability_status"] = availability
    with pytest.raises(ValueError, match="PLAYER_AVAILABILITY_UNVERIFIED"):
        runtime._validate_evidence(row, "RECEIVING_YARDS")


def test_nfl_terminal_reducer_keeps_event_and_unknown_availability_distinct() -> None:
    from prop_terminal_reducer_v2 import reduce_prop_terminal

    unavailable = reduce_prop_terminal(
        proposed_label="NO_PLAY", blockers=["PLAYER_UNAVAILABLE"], model_evaluated=False
    )
    unknown = reduce_prop_terminal(
        proposed_label="MODEL_INPUTS_INSUFFICIENT",
        blockers=["PLAYER_AVAILABILITY_UNVERIFIED"], model_evaluated=False
    )
    assert (unavailable.terminal_label, unavailable.verdict_class, unavailable.model_evaluated) == (
        "NO_PLAY", "EVENT_INVALIDATED", False
    )
    assert (unknown.terminal_label, unknown.verdict_class, unknown.model_evaluated) == (
        "MODEL_INPUTS_INSUFFICIENT", "ACQUISITION_BLOCKED", False
    )
    assert not unavailable.pick_rejected  # Existing reducer NO_PLAY semantics.
    assert not unknown.pick_rejected



def test_auto_hydrated_nfl_role_proves_identity_but_not_game_participation(monkeypatch) -> None:
    # The original IR leak was a falsely ACTIVE role label produced even when
    # only ESPN athlete/team/scoreboard identity and prior nflverse logs existed.
    monkeypatch.setattr(
        nfl, "_resolve_espn_athlete",
        lambda _name, *, http_get: ("4040715", "Jalen Hurts"),
    )
    monkeypatch.setattr(nfl, "_athlete_team", lambda *_a, **_k: "PHI")
    monkeypatch.setattr(
        nfl, "_target_event",
        lambda **_kw: {
            "event_id": "401873009",
            "opponent": "DAL",
            "provider_season": 2026,
            "provider_week": 7,
            "provider_home_team": "PHI",
            "provider_away_team": "DAL",
            "canonical_home_team": "PHI",
            "canonical_away_team": "DAL",
            "verified_canonical_event_id": "2026_07_DAL_PHI",
        },
    )
    monkeypatch.setattr(
        nfl, "_history",
        lambda **_kw: (
            [0.0] * 10,
            [{"opportunity": 1.0}] * 10,
            {"2026": "testdigest"},
            "NFLVERSE_NAME:jalen_hurts",
        ),
    )
    now = datetime.now(timezone.utc)
    evidence = nfl.hydrate_nfl_prop_evidence(
        player="Jalen Hurts",
        stat_type="ANYTIME_TD",
        event_start_time=(now + timedelta(days=1)).isoformat(),
        now=now,
    )
    role = evidence["role_status"]
    assert role["espn_athlete_id"] == "4040715"
    assert role["verified_canonical_event_id"] == "2026_07_DAL_PHI"
    assert role["status"] == "ROSTER_IDENTITY_VERIFIED_AVAILABILITY_UNVERIFIED"
    assert "game_availability_status" not in role
    assert evidence["opportunity_ledger"]["status"] == "PASS"
