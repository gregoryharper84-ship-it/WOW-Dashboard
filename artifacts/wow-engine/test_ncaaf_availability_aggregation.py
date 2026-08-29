from __future__ import annotations

import pytest

from ncaaf_availability_aggregation import (
    NCAAvailabilityAggregationUnavailable,
    build_qb_evidence,
    build_skill_availability,
)

KICKOFF = "2026-09-05T23:00:00+00:00"


def raw(*, team: str, player: str, status: str, p, provider: str = "BIG12_OFFICIAL_AVAILABILITY"):
    return {
        "official_event_id": "evt-1",
        "event_start_time": KICKOFF,
        "evidence_kind": "PLAYER_AVAILABILITY_REPORT",
        "scope": "EVENT",
        "team": None,
        "player": player,
        "source_provider": provider,
        "source_record_id": "r1",
        "source_uri": "https://official.example/report",
        "evidence_timestamp": "2026-09-05T20:00:00+00:00",
        "provenance_grade": "A",
        "payload": {"team": team, "status": status, "source_defined_play_probability": p},
        "payload_sha256": (player.lower().replace(" ", "") + "0" * 64)[:64],
        "blocker_codes": [],
        "can_execute": False,
    }


def test_qb_status_and_certainty_require_exact_starter_row_and_source_defined_probability():
    rows = build_qb_evidence(
        official_event_id="evt-1",
        event_start_time=KICKOFF,
        scope="HOME",
        team="Texas",
        starter_qb="QB One",
        depth_chart_as_of="2026-09-05T18:00:00+00:00",
        depth_chart_source="OFFICIAL_TEAM_DEPTH_CHART",
        raw_rows=[raw(team="Texas", player="QB One", status="PROBABLE", p=0.75)],
    )
    assert [row["evidence_kind"] for row in rows] == ["QB_STATUS", "QB_CERTAINTY"]
    assert rows[1]["payload"]["value"] == 0.75
    assert rows[0]["can_execute"] is False


def test_qb_status_categorical_only_does_not_invent_certainty():
    rows = build_qb_evidence(
        official_event_id="evt-1",
        event_start_time=KICKOFF,
        scope="AWAY",
        team="Iowa",
        starter_qb="QB Two",
        depth_chart_as_of="2026-09-05T18:00:00+00:00",
        depth_chart_source="OFFICIAL_TEAM_DEPTH_CHART",
        raw_rows=[raw(team="Iowa", player="QB Two", status="QUESTIONABLE", p=None, provider="BIGTEN_OFFICIAL_AVAILABILITY")],
    )
    assert [row["evidence_kind"] for row in rows] == ["QB_STATUS"]


def test_qb_fails_closed_when_starter_not_explicitly_reported():
    with pytest.raises(NCAAvailabilityAggregationUnavailable) as exc:
        build_qb_evidence(
            official_event_id="evt-1",
            event_start_time=KICKOFF,
            scope="HOME",
            team="Texas",
            starter_qb="QB Missing",
            depth_chart_as_of="2026-09-05T18:00:00+00:00",
            depth_chart_source="OFFICIAL_TEAM_DEPTH_CHART",
            raw_rows=[],
        )
    assert exc.value.code == "NCAAF_STARTER_AVAILABILITY_NOT_PROVEN"


def test_skill_availability_requires_explicit_weights_and_source_defined_probabilities():
    row = build_skill_availability(
        official_event_id="evt-1",
        event_start_time=KICKOFF,
        scope="HOME",
        team="Texas",
        player_weights={"WR One": 0.6, "RB One": 0.4},
        raw_rows=[
            raw(team="Texas", player="WR One", status="PROBABLE", p=0.75),
            raw(team="Texas", player="RB One", status="AVAILABLE", p=1.0),
        ],
    )
    assert row["evidence_kind"] == "SKILL_AVAILABILITY"
    assert row["payload"]["value"] == pytest.approx(0.85)
    assert row["can_execute"] is False


def test_skill_availability_rejects_categorical_status_without_numeric_policy_semantics():
    with pytest.raises(NCAAvailabilityAggregationUnavailable) as exc:
        build_skill_availability(
            official_event_id="evt-1",
            event_start_time=KICKOFF,
            scope="AWAY",
            team="Iowa",
            player_weights={"WR Two": 1.0},
            raw_rows=[raw(team="Iowa", player="WR Two", status="QUESTIONABLE", p=None, provider="BIGTEN_OFFICIAL_AVAILABILITY")],
        )
    assert exc.value.code == "NCAAF_SKILL_PROBABILITY_NOT_SOURCE_DEFINED"
