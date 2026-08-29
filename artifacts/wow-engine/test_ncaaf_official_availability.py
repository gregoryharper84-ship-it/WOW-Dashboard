from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ncaaf_official_availability import (
    RAW_EVIDENCE_KIND,
    NCAAvailabilityUnavailable,
    candidate_provider_rows,
    normalize_player_availability,
)


def _base(**overrides):
    kickoff = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    data = {
        "conference": "BIG12",
        "official_event_id": "cfb-official-1",
        "event_start_time": kickoff.isoformat(),
        "report_timestamp": (kickoff - timedelta(hours=6)).isoformat(),
        "report_phase": "PRE_GAME",
        "team": "Texas Tech",
        "player": "Player Example",
        "status": "PROBABLE",
        "position": "QB",
    }
    data.update(overrides)
    return data


def test_big12_probable_preserves_official_75_percent_semantics():
    row = normalize_player_availability(**_base())
    assert row["evidence_kind"] == RAW_EVIDENCE_KIND
    assert row["scope"] == "EVENT"
    assert row["team"] is None
    assert row["payload"]["team"] == "Texas Tech"
    assert row["payload"]["source_defined_play_probability"] == 0.75
    assert row["payload"]["probability_source"] == "OFFICIAL_CONFERENCE_POLICY"
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_big12_gameday_decision_does_not_invent_probability():
    row = normalize_player_availability(
        **_base(report_phase="GAME_DAY", status="GAME TIME DECISION")
    )
    assert row["payload"]["source_defined_play_probability"] is None
    assert row["payload"]["probability_source"] is None


def test_acc_questionable_preserves_official_50_percent_semantics():
    row = normalize_player_availability(
        **_base(conference="ACC", status="QUESTIONABLE")
    )
    assert row["payload"]["source_defined_play_probability"] == 0.50
    assert row["payload"]["probability_source"] == "OFFICIAL_CONFERENCE_POLICY"


def test_big_ten_status_stays_categorical_and_records_unlisted_policy():
    row = normalize_player_availability(
        **_base(conference="BIGTEN", status="PROBABLE")
    )
    assert row["payload"]["source_defined_play_probability"] is None
    assert row["payload"]["probability_source"] is None
    assert row["payload"]["not_listed_means_available"] is True


def test_sun_belt_status_stays_categorical():
    row = normalize_player_availability(
        **_base(conference="SUNBELT", status="DOUBTFUL")
    )
    assert row["payload"]["source_defined_play_probability"] is None
    assert row["payload"]["not_listed_means_available"] is False


@pytest.mark.parametrize("conference", ["SEC", "MOUNTAINWEST", "AAC", "MAC"])
def test_unverified_policy_conference_fails_closed(conference: str):
    with pytest.raises(NCAAvailabilityUnavailable) as exc:
        normalize_player_availability(**_base(conference=conference))
    assert exc.value.code == "NCAAF_AVAILABILITY_POLICY_UNVERIFIED"


def test_post_kickoff_report_fails_closed():
    kickoff = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    with pytest.raises(NCAAvailabilityUnavailable) as exc:
        normalize_player_availability(
            **_base(report_timestamp=(kickoff + timedelta(minutes=1)).isoformat())
        )
    assert exc.value.code == "NCAAF_AVAILABILITY_NOT_PREGAME"


def test_unknown_status_fails_closed():
    with pytest.raises(NCAAvailabilityUnavailable) as exc:
        normalize_player_availability(**_base(status="MAYBE"))
    assert exc.value.code == "NCAAF_AVAILABILITY_STATUS_UNRECOGNIZED"


def test_raw_availability_never_masquerades_as_model_ready_evidence():
    row = normalize_player_availability(**_base())
    assert row["evidence_kind"] == "PLAYER_AVAILABILITY_REPORT"
    assert row["evidence_kind"] not in {"QB_STATUS", "QB_CERTAINTY", "SKILL_AVAILABILITY"}


def test_candidate_official_providers_are_review_only():
    rows = candidate_provider_rows()
    assert {row["provider_key"] for row in rows} == {
        "BIG12_OFFICIAL_AVAILABILITY",
        "ACC_OFFICIAL_AVAILABILITY",
        "BIGTEN_OFFICIAL_AVAILABILITY",
        "SUNBELT_OFFICIAL_AVAILABILITY",
    }
    assert all(row["provider_class"] == "OFFICIAL_CONFERENCE" for row in rows)
    assert all(row["allowed_evidence_kinds"] == [RAW_EVIDENCE_KIND] for row in rows)
    assert all(row["active"] is False for row in rows)
    assert all(row["can_execute"] is False for row in rows)


def test_hash_is_deterministic_for_same_report_observation():
    first = normalize_player_availability(**_base())
    second = normalize_player_availability(**_base())
    assert first["payload_sha256"] == second["payload_sha256"]
    assert len(first["payload_sha256"]) == 64
