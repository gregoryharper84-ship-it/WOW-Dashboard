"""Regression checks for read-only, time-correct schedule context (all 13 sports)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.schedule_fatigue_intelligence import (
    CAN_EXECUTE,
    EXPECTED_TEAM_EVENT_SPORTS,
    ScheduleContextError,
    build_schedule_matchup_context,
)
from v17.team_state_intelligence import paired_schedule_fatigue_context


BASE = datetime(2026, 11, 13, 1, tzinfo=timezone.utc)


def t(day=0, hour=0):
    return (BASE + timedelta(days=day, hours=hour)).isoformat()


def venue(lon=-96.8, tz="America/Chicago"):
    return {"latitude": 32.7, "longitude": lon, "time_zone": tz}


def source():
    return {"source_uri": "https://official.example/events", "source_payload_sha256": "a" * 64,
            "source_available_at": t(0, -6)}


def target(sport="NBA"):
    return {"official_event_id": "target", "sport": sport, "event_start_utc": t(),
            "as_of_utc": t(0, -1), "participants": ["A", "B"], "roles": {"A": "away", "B": "home"},
            "venue": venue(), **source()}


def prior(day, eid, *, teams=("A",), role="away", at=None, v=None, loads=None):
    return {"official_event_id": eid, "sport": "NBA", "event_start_utc": t(day),
            "event_end_utc": t(day, 2), "participants": list(teams),
            "roles": {s: role for s in teams}, "status": "FINAL", "venue": v or venue(),
            "workloads": loads or {}, **source(),
            "source_available_at": at or t(day, 3)}


def run(hist=(), **kwargs):
    return build_schedule_matchup_context(target=target(), history=hist,
        history_completeness={"A": True, "B": True}, **kwargs)


def test_all_13_sports_supported_without_certification():
    assert len(EXPECTED_TEAM_EVENT_SPORTS) == 13
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        result = build_schedule_matchup_context(target=target(sport), history=(),
            history_completeness={"A": False, "B": False})
        assert result["sport"] == sport
        assert result["status"] == "SCHEDULE_CONTEXT_INCOMPLETE"
        assert result["can_execute"] is False
        assert result["probability_publishable"] is False
        assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert CAN_EXECUTE is False


def test_back_to_back_density_and_three_consecutive_road_games():
    history = [prior(-3, "g1"), prior(-2, "g2"), prior(-1, "g3"), prior(-4, "b1", teams=("B",), role="home")]
    out = run(history)
    a, b = out["per_participant"]["A"], out["per_participant"]["B"]
    assert a["back_to_back"] is True
    assert a["games_in_4_days_including_target"] == 4
    assert a["games_in_6_days_including_target"] == 4
    assert a["consecutive_road_events_including_target"] == 4
    assert b["back_to_back"] is False
    assert out["matchup_differences"]["rest_hours_first_minus_second"] < 0
    assert out["probabilities"] is None


def test_workload_and_travel_are_measured_not_conflated():
    h = [prior(-2, "g1", v=venue(-75, "America/New_York"),
               loads={"A": {"value": 44, "unit": "minutes"}})]
    out = run(h)
    a = out["per_participant"]["A"]
    assert a["last_trip_miles"] > 1000
    assert a["last_trip_time_zone_offset_change_hours"] == -1
    assert a["workload_7d"] == 44
    assert a["workload_unit"] == "minutes"
    assert a["overnight_travel_verified"] is None
    assert a["rest_hours"] == pytest.approx(46)
    assert out["status"] == "SCHEDULE_CONTEXT_INCOMPLETE"  # no prior B, no inferred rest


def test_missing_venue_and_workload_remain_null():
    p = prior(-1, "p")
    p["venue"] = None
    result = run([p])
    a = result["per_participant"]["A"]
    assert a["last_trip_miles"] is None
    assert a["workload_7d"] is None
    assert a["workload_unit"] is None
    assert result["status"] == "SCHEDULE_CONTEXT_INCOMPLETE"


def test_late_source_rejected_even_if_old_game():
    with pytest.raises(ScheduleContextError) as e:
        run([prior(-3, "late", at=t(0))])
    assert e.value.code == "SCHEDULE_EVIDENCE_AFTER_FREEZE"


def test_duplicate_id_with_conflicting_opponent_rejected():
    p1 = prior(-2, "dupe", teams=("A",))
    p2 = prior(-2, "dupe", teams=("B",))
    with pytest.raises(ScheduleContextError) as e:
        run([p1, p2])
    assert e.value.code == "SCHEDULE_IDENTITY_CONFLICT"


def test_future_final_and_cross_sport_history_rejected():
    p = prior(1, "future")
    with pytest.raises(ScheduleContextError) as e:
        run([p])
    assert e.value.code == "SCHEDULE_EVIDENCE_INVALID"
    p = prior(-1, "wrong-sport")
    p["sport"] = "NHL"
    with pytest.raises(ScheduleContextError) as e:
        run([p])
    assert e.value.code == "SCHEDULE_EVIDENCE_INVALID"


def test_same_day_and_dst_dates_not_confused_with_b2b():
    # Early UTC morning spans a different local calendar date; calendar-day basis is target venue.
    p = prior(-1, "yesterday")
    out = run([p])
    assert out["per_participant"]["A"]["back_to_back"] is True
    same = prior(0, "earlier")
    same["event_start_utc"] = t(0, -4)
    same["event_end_utc"] = t(0, -2)
    same["source_available_at"] = t(0, -1)
    tar = target()
    tar["as_of_utc"] = t(0, -1)
    out = build_schedule_matchup_context(target=tar, history=[same],
                                         history_completeness={"A": True, "B": True})
    assert out["per_participant"]["A"]["back_to_back"] is False
    assert out["per_participant"]["A"]["games_in_4_days_including_target"] == 2


def test_no_history_does_not_mean_zero_fatigue_and_missing_attestation_holds():
    out = run([])
    a = out["per_participant"]["A"]
    assert a["rest_hours"] is None
    assert a["back_to_back"] is None
    assert a["games_in_4_days_including_target"] == 1  # observed, not asserted complete
    assert out["status"] == "SCHEDULE_CONTEXT_INCOMPLETE"
    out = build_schedule_matchup_context(target=target(), history=[], history_completeness={"A": False, "B": False})
    assert out["per_participant"]["A"]["games_in_4_days_including_target"] is None


def test_time_correct_hash_order_independent_and_existing_state_vector_unmodified():
    h = [prior(-3, "old"), prior(-1, "new")]
    direct = run(h)
    wrapped = paired_schedule_fatigue_context(target=target(), history=list(reversed(h)),
                                              history_completeness={"A": True, "B": True})
    assert direct == wrapped
    assert len(direct["evidence_sha256"]) == 64
    assert "fitted_probability" not in repr(direct)


def test_freeze_must_precede_event_and_source_integrity_is_required():
    tar = target()
    tar["as_of_utc"] = tar["event_start_utc"]
    with pytest.raises(ScheduleContextError) as e:
        build_schedule_matchup_context(target=tar, history=(), history_completeness={})
    assert e.value.code == "SCHEDULE_EVIDENCE_INVALID"
    tar = target()
    tar["source_payload_sha256"] = "not-hash"
    with pytest.raises(ScheduleContextError) as e:
        build_schedule_matchup_context(target=tar, history=(), history_completeness={})
    assert e.value.code == "SCHEDULE_EVIDENCE_INVALID"


def test_receipt_changes_when_trip_or_workload_changes():
    p = prior(-2, "event", loads={"A": {"value": 42, "unit": "minutes"}})
    baseline = run([p])["evidence_sha256"]
    changed = dict(p)
    changed["workloads"] = {"A": {"value": 44, "unit": "minutes"}}
    assert run([changed])["evidence_sha256"] != baseline
    changed = dict(p)
    changed["venue"] = venue(-75, "America/New_York")
    assert run([changed])["evidence_sha256"] != baseline


def test_duplicate_identity_includes_workload_and_venue():
    p = prior(-2, "dupe", loads={"A": {"value": 42, "unit": "minutes"}})
    p2 = dict(p)
    p2["workloads"] = {"A": {"value": 44, "unit": "minutes"}}
    with pytest.raises(ScheduleContextError) as e:
        run([p, p2])
    assert e.value.code == "SCHEDULE_IDENTITY_CONFLICT"


def test_bad_source_or_unknown_timezone_fails_closed():
    t1 = target()
    t1["source_uri"] = "http://bad.example/events"
    with pytest.raises(ScheduleContextError) as e:
        build_schedule_matchup_context(target=t1, history=(), history_completeness={})
    assert e.value.code == "SCHEDULE_EVIDENCE_INVALID"
    t1 = target()
    t1["venue"] = venue(-96.8, "Fake/TimeZone")
    with pytest.raises(ScheduleContextError) as e:
        build_schedule_matchup_context(target=t1, history=(), history_completeness={})
    assert e.value.code == "SCHEDULE_EVIDENCE_INVALID"


def test_two_complete_attestations_produce_research_only_comparison():
    a = prior(-1, "a-game", loads={"A": {"value": 36, "unit": "minutes"}})
    b = prior(-3, "b-game", teams=("B",), role="home", loads={"B": {"value": 31, "unit": "minutes"}})
    result = run([a, b])
    assert result["status"] == "RESEARCH_CONTEXT_ONLY"
    assert result["matchup_differences"]["workload_7d_first_minus_second"] == 5
    assert result["source_authenticity_independently_verified"] is False
    assert result["model_feature_consumption_verified"] is False


def test_malformed_workloads_never_crash_or_become_fake_zero():
    p = prior(-2, "bad-workload")
    p["workloads"] = [123]
    result = run([p])
    assert result["per_participant"]["A"]["workload_7d"] is None
    p["workloads"] = {"A": {"value": 30, "unit": ["minutes"]}}
    result = run([p])
    assert result["per_participant"]["A"]["workload_7d"] is None


def test_missing_road_role_cannot_claim_full_context():
    a = prior(-1, "a-game", loads={"A": {"value": 36, "unit": "minutes"}})
    b = prior(-3, "b-game", teams=("B",), role="home", loads={"B": {"value": 31, "unit": "minutes"}})
    tar = target()
    tar["roles"] = {"A": "away"}
    result = build_schedule_matchup_context(target=tar, history=[a, b],
                                             history_completeness={"A": True, "B": True})
    assert result["per_participant"]["B"]["consecutive_road_events_including_target"] is None
    assert result["status"] == "SCHEDULE_CONTEXT_INCOMPLETE"


def test_unplayed_intervening_fixture_holds_rest_and_changes_receipt():
    a = prior(-3, "a-final", loads={"A": {"value": 34, "unit": "minutes"}})
    b = prior(-4, "b-final", teams=("B",), role="home",
              loads={"B": {"value": 31, "unit": "minutes"}})
    tar = target()
    tar["as_of_utc"] = t(-1)
    tar["source_available_at"] = t(-2)
    before = build_schedule_matchup_context(target=tar, history=[a, b],
                                             history_completeness={"A": True, "B": True})
    incoming = {"official_event_id": "intervening", "sport": "NBA",
                "event_start_utc": t(0, -12), "participants": ["A"],
                "status": "SCHEDULED", "source_uri": "https://official.example/events",
                "source_payload_sha256": "b" * 64, "source_available_at": t(-2)}
    after = build_schedule_matchup_context(target=tar, history=[a, b, incoming],
                                            history_completeness={"A": True, "B": True})
    assert before["status"] == "RESEARCH_CONTEXT_ONLY"
    assert after["status"] == "SCHEDULE_CONTEXT_INCOMPLETE"
    assert after["per_participant"]["A"]["rest_hours"] is None
    assert after["per_participant"]["A"]["back_to_back"] is None
    assert after["per_participant"]["A"]["history_completeness_attested"] is False
    assert after["unresolved_prior_event_ids"] == ["intervening"]
    assert after["evidence_sha256"] != before["evidence_sha256"]


def test_duplicate_source_mirrors_deduplicate_counts_and_hash_independent_of_order():
    p = prior(-1, "same-official-event", loads={"A": {"value": 30, "unit": "minutes"}})
    mirror = dict(p)
    mirror["source_uri"] = "https://mirror.example/official"
    mirror["source_payload_sha256"] = "c" * 64
    first = run([p, mirror])
    second = run([mirror, p])
    assert first["per_participant"]["A"]["games_observed"] == 1
    assert first["per_participant"]["A"]["games_in_4_days_including_target"] == 2
    assert first["evidence_sha256"] == second["evidence_sha256"]
    assert first["evidence_sha256"] != run([p])["evidence_sha256"]
