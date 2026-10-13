"""Pure evidence-only tests for #1689; no external provider or credentials."""
from datetime import datetime, timedelta, timezone
import pytest
from v17.cross_sport_acceptance_factory import (
    ACCEPTANCE_CONTRACT_VERSION, CohortPolicy, Fixture, Lane, SourceInventory,
    evaluate_cohort, reconcile_lane,
)

NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
SHA = "a" * 40
SOURCE_HASH = "b" * 64


def _lane(sport="NFL", market="MONEYLINE", research_only=False):
    return Lane(sport, market, "scoreWowTeamEventRequest", "NFL_GAME_WIN_V17", research_only=research_only)


def _fixtures(count=13, *, sport="NFL", start=None, state="SCHEDULED"):
    start = start or NOW + timedelta(days=3)
    return tuple(Fixture(sport, f"{sport}-id-{i}", start + timedelta(hours=i * 4), state) for i in range(count))


def _policy(*, minimum=13):
    return CohortPolicy(minimum, NOW + timedelta(days=2), NOW + timedelta(days=8))


def _inventory(fixtures=None):
    return SourceInventory("CANONICAL_FIXTURE_TEST_SOURCE", SOURCE_HASH, NOW - timedelta(minutes=20), fixtures if fixtures is not None else _fixtures())


def _result(lane=None, inventory=None, policy=None):
    return evaluate_cohort(lane or _lane(), inventory or _inventory(), policy or _policy(), now=NOW)


def test_source_verified_multiday_nfl_week_cohort_passes_no_static_date():
    row = _result()
    assert row.status == "COHORT_ELIGIBLE_FOR_TEST"
    assert row.eligible_count == 13
    assert row.blockers == ()
    assert len(set(row.event_ids)) == 13
    assert row.as_dict()["probability_publishable"] is False
    assert row.as_dict()["can_execute"] is False


def test_previous_week_fixture_never_auto_passes_full_slate():
    old = _fixtures(start=NOW - timedelta(days=10))
    result = _result(inventory=_inventory(old))
    assert result.status == "COHORT_HELD"
    assert "ACCEPTANCE_ELIGIBLE_COHORT_INSUFFICIENT" in result.blockers
    assert result.eligible_count == 0


def test_byes_and_thin_schedule_cannot_weaken_minimum():
    result = _result(inventory=_inventory(_fixtures(12)))
    assert result.eligible_count == 12
    assert "ACCEPTANCE_ELIGIBLE_COHORT_INSUFFICIENT" in result.blockers


def test_not_yet_eligible_cancelled_and_started_games_are_excluded():
    rows = _fixtures(12) + (Fixture("NFL", "cancelled", NOW + timedelta(days=4), "CANCELLED"),)
    result = _result(inventory=_inventory(rows))
    assert result.eligible_count == 12
    assert "ACCEPTANCE_ELIGIBLE_COHORT_INSUFFICIENT" in result.blockers


def test_duplicate_canonical_id_fails_even_when_row_count_would_pass():
    a = _fixtures(13)
    result = _result(inventory=_inventory(a + (a[0],)))
    assert "ACCEPTANCE_CANONICAL_EVENT_DUPLICATE" in result.blockers
    assert result.status == "COHORT_HELD"


def test_stale_unverified_source_fails_without_inventing_games():
    r = _result(inventory=SourceInventory("verified", SOURCE_HASH, NOW - timedelta(hours=8), _fixtures()))
    assert "ACCEPTANCE_SOURCE_STALE_OR_FUTURE" in r.blockers


def test_missing_source_checksum_is_hold_even_if_count_satisfied():
    r = _result(inventory=SourceInventory("provider", "", NOW, _fixtures()))
    assert "ACCEPTANCE_SOURCE_PROVENANCE_MISSING" in r.blockers


def test_cross_sport_rows_cannot_masquerade_as_nfl_slate():
    r = _result(inventory=_inventory(_fixtures(sport="MLB")))
    assert "ACCEPTANCE_INVENTORY_CROSS_SPORT" in r.blockers
    assert r.eligible_count == 0


def test_offseason_and_historical_windows_fail_closed():
    p = CohortPolicy(13, NOW - timedelta(days=14), NOW - timedelta(days=7))
    r = _result(policy=p)
    assert "ACCEPTANCE_HISTORICAL_COHORT_NOT_PREGAME" in r.blockers


def test_timezone_and_window_policy_is_explicit():
    p = CohortPolicy(13, NOW.replace(tzinfo=None), NOW + timedelta(days=8))
    r = _result(policy=p)
    assert "ACCEPTANCE_TIMEZONE_UNVERIFIED" in r.blockers
    p2 = CohortPolicy(13, NOW, NOW + timedelta(days=25))
    assert "ACCEPTANCE_COHORT_WINDOW_INVALID" in _result(policy=p2).blockers


def test_unknown_event_state_is_typed_hold():
    r = _result(inventory=_inventory(_fixtures(13, state="UNCLEAR")))
    assert "ACCEPTANCE_EVENT_STATE_UNKNOWN" in r.blockers


def test_non_nfl_sport_uses_own_cohort_policy_not_13():
    lane = Lane("MLB", "MONEYLINE", "scoreWowTeamEventRequest", "MLB_GAME_WIN", research_only=False)
    p = CohortPolicy(1, NOW + timedelta(days=2), NOW + timedelta(days=8))
    r = evaluate_cohort(lane, _inventory(_fixtures(1, sport="MLB")), p, now=NOW)
    assert r.status == "COHORT_ELIGIBLE_FOR_TEST"


def test_one_controlling_specialist_required_and_props_identity_typed():
    with pytest.raises(ValueError, match="SPECIALIST_OR_ACTION_MISSING"):
        Lane("NBA", "MONEYLINE", "", "NBA_MODEL")
    with pytest.raises(ValueError, match="PROP_STAT_TYPE_MISSING"):
        Lane("NFL", "PROP", "scoreWowPickRequest", "NFL_PROP")
    with pytest.raises(ValueError, match="NON_PROP_IDENTITY_INVALID"):
        Lane("MLB", "MONEYLINE", "op", "MLB", stat_type="PITCHER_STRIKEOUTS")


def _checks():
    return {name: "PASS" for name in (
        "BACKEND_MODEL_PACKAGE", "MODEL_CALIBRATION_AND_BOUNDS", "MODEL_CERTIFICATION",
        "CANONICAL_IDENTITY", "FINAL_TERMINAL_REDUCER", "BACKEND_ACTION_CANARY",
        "LIVE_GPT_EDITOR_PARITY", "FULL_POOL_RECONCILIATION",
    )}


def _reconciled(lane=None, cohort=None, **overrides):
    payload = {
        "lane": lane or _lane(), "cohort": cohort or _result(), "checks": _checks(),
        "source_commit_sha": SHA, "deployed_commit_sha": SHA,
        "render_deploy_id": "dep-test-not-live", "artifact_checksum": SOURCE_HASH,
        "calibrator_version": "CERTIFIED_TEST_ONLY", "action_operation_id": "scoreWowTeamEventRequest",
        "terminal_receipt_id": "terminal-test-not-live",
    }
    payload.update(overrides)
    return reconcile_lane(**payload)


def test_full_evidence_never_becomes_probability_or_release_authority():
    r = _reconciled()
    assert r["observed_state"] == "ACCEPTANCE_EVIDENCE_COMPLETE"
    assert r["probability_publishable"] is False
    assert r["can_execute"] is False
    assert r["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert r["acceptance_contract_version"] == ACCEPTANCE_CONTRACT_VERSION


def test_shadow_lane_never_qualifies_from_green_canaries():
    r = _reconciled(lane=_lane("NFL", "SPREAD", research_only=True))
    assert r["observed_state"] == "ACCEPTANCE_HELD"
    assert "ACCEPTANCE_RESEARCH_ONLY_NOT_PUBLISHABLE" in r["blockers"]


def test_sha_mismatch_stale_editor_missing_terminal_typed_independently():
    checks = _checks()
    checks["LIVE_GPT_EDITOR_PARITY"] = "NOT_CHECKED"
    r = _reconciled(checks=checks, deployed_commit_sha="c" * 40, terminal_receipt_id=None)
    assert "ACCEPTANCE_DEPLOY_SHA_MISMATCH" in r["blockers"]
    assert "ACCEPTANCE_GATE_NOT_CHECKED:LIVE_GPT_EDITOR_PARITY" in r["blockers"]
    assert "ACCEPTANCE_TERMINAL_RECEIPT_MISSING" in r["blockers"]


def test_cohort_hold_is_not_overwritten_by_healthy_other_gates():
    r = _reconciled(cohort=_result(inventory=_inventory(_fixtures(1))))
    assert "ACCEPTANCE_ELIGIBLE_COHORT_INSUFFICIENT" in r["blockers"]
    assert r["observed_state"] == "ACCEPTANCE_HELD"


def test_missing_model_calibration_and_canary_stay_typed():
    checks = _checks()
    checks["MODEL_CALIBRATION_AND_BOUNDS"] = "HOLD"
    checks["BACKEND_ACTION_CANARY"] = "DEFERRED"
    r = _reconciled(checks=checks)
    assert "ACCEPTANCE_GATE_HOLD:MODEL_CALIBRATION_AND_BOUNDS" in r["blockers"]
    assert "ACCEPTANCE_GATE_DEFERRED:BACKEND_ACTION_CANARY" in r["blockers"]


def test_malformed_policy_inputs_fail_closed_without_throwing():
    p = CohortPolicy(13, NOW, NOW + timedelta(days=5), max_window_days=None, max_source_age=None)
    r = _result(policy=p)
    assert "ACCEPTANCE_WINDOW_LIMIT_INVALID" in r.blockers
    assert "ACCEPTANCE_SOURCE_AGE_POLICY_INVALID" in r.blockers
    malformed = SourceInventory("provider", None, NOW, _fixtures())
    assert "ACCEPTANCE_SOURCE_PROVENANCE_MISSING" in _result(inventory=malformed).blockers


def test_malformed_deployment_sha_fails_closed():
    r = _reconciled(source_commit_sha=12345)
    assert "ACCEPTANCE_DEPLOY_SHA_INVALID" in r["blockers"]


# #1689 — week-aware selection is source-driven; it never manufactures a slate.
from v17.cross_sport_acceptance_factory import (
    SourceWeekWindow, choose_current_pregame_cohort,
)


def _week(week, start, end, sport="NFL"):
    return SourceWeekWindow(sport, "2026", week, start, end)


def test_verified_source_week_selection_skips_partly_played_current_week():
    weeks = (
        _week("05", NOW - timedelta(days=6), NOW + timedelta(days=1)),
        _week("06", NOW + timedelta(days=2), NOW + timedelta(days=8)),
    )
    result = choose_current_pregame_cohort(
        _lane(), _inventory(), weeks, now=NOW, minimum_unique_events=13,
    )
    assert result["status"] == "COHORT_ELIGIBLE_FOR_TEST"
    assert result["selected_week"]["week_key"] == "06"
    assert result["cohort"]["eligible_event_count"] == 13
    assert result["attempted_week_receipts"][0]["status"] == "COHORT_HELD"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_verified_week_cannot_lower_required_complete_slate_threshold():
    upcoming = _week("06", NOW + timedelta(days=2), NOW + timedelta(days=8))
    row = choose_current_pregame_cohort(
        _lane(), _inventory(_fixtures(12)), (upcoming,), now=NOW, minimum_unique_events=13,
    )
    assert row["status"] == "COHORT_HELD"
    assert row["selected_week"] is None
    assert "ACCEPTANCE_ELIGIBLE_COHORT_INSUFFICIENT" in row["attempted_week_receipts"][0]["blockers"]


def test_verified_week_no_current_events_is_typed_hold_not_empty_success():
    past = _week("04", NOW - timedelta(days=8), NOW - timedelta(days=1))
    row = choose_current_pregame_cohort(
        _lane(), _inventory(), (past,), now=NOW, minimum_unique_events=13,
    )
    assert row["blockers"] == ["ACCEPTANCE_NO_CURRENT_OR_FUTURE_VERIFIED_WEEK"]


def test_verified_week_rejects_duplicate_or_cross_sport_schedule():
    week = _week("06", NOW + timedelta(days=2), NOW + timedelta(days=8))
    base = dict(lane=_lane(), inventory=_inventory(), now=NOW, minimum_unique_events=13)
    assert "ACCEPTANCE_DUPLICATE_VERIFIED_WEEK" in choose_current_pregame_cohort(
        **base, source_weeks=(week, week),
    )["blockers"]
    assert "ACCEPTANCE_VERIFIED_WEEK_IDENTITY_INVALID" in choose_current_pregame_cohort(
        **base, source_weeks=(_week("06", week.window_start, week.window_end, "MLB"),),
    )["blockers"]


def test_verified_week_stale_provider_snapshot_never_promotes_acceptance():
    upcoming = _week("06", NOW + timedelta(days=2), NOW + timedelta(days=8))
    stale = SourceInventory("CFBD-INTERNAL-TEST", SOURCE_HASH, NOW - timedelta(hours=9), _fixtures())
    row = choose_current_pregame_cohort(
        _lane(), stale, (upcoming,), now=NOW, minimum_unique_events=13,
    )
    assert row["status"] == "COHORT_HELD"
    assert "ACCEPTANCE_SOURCE_STALE_OR_FUTURE" in row["attempted_week_receipts"][0]["blockers"]


def test_verified_week_does_not_accept_far_future_or_invalid_calendar():
    base = dict(lane=_lane(), inventory=_inventory(), now=NOW, minimum_unique_events=13)
    distant = _week("10", NOW + timedelta(days=16), NOW + timedelta(days=22))
    assert choose_current_pregame_cohort(
        **base, source_weeks=(distant,),
    )["status"] == "COHORT_HELD"
    invalid = _week("06", NOW + timedelta(days=2), NOW + timedelta(days=16))
    assert choose_current_pregame_cohort(
        **base, source_weeks=(invalid,),
    )["blockers"] == ["ACCEPTANCE_VERIFIED_WEEK_WINDOW_INVALID"]


# #1690 governance REJECT — a week with started/final games AND 13+ remaining
# pregame games must never be accepted as a complete-week cohort.
_W05 = (NOW - timedelta(days=3), NOW + timedelta(days=4))
_W06 = (NOW + timedelta(days=5), NOW + timedelta(days=11))


def _partial_week05(remaining=13):
    played = (
        Fixture("NFL", "w05-final-0", NOW - timedelta(days=2), "FINAL"),
        Fixture("NFL", "w05-final-1", NOW - timedelta(days=1), "FINAL"),
        Fixture("NFL", "w05-final-2", NOW - timedelta(hours=20), "FINAL"),
        Fixture("NFL", "w05-live-0", NOW - timedelta(hours=1), "IN_PROGRESS"),
    )
    pregame = tuple(
        Fixture("NFL", f"w05-pre-{i}", NOW + timedelta(hours=2 + i * 3), "SCHEDULED")
        for i in range(remaining)
    )
    return played + pregame


def _full_week06(count=13):
    return tuple(
        Fixture("NFL", f"w06-pre-{i}", _W06[0] + timedelta(hours=1 + i * 3), "SCHEDULED")
        for i in range(count)
    )


def test_partially_elapsed_week_with_13_remaining_is_held_directly():
    policy = CohortPolicy(13, *_W05)
    result = evaluate_cohort(_lane(), _inventory(_partial_week05(13)), policy, now=NOW)
    assert result.eligible_count == 13
    assert result.status == "COHORT_HELD"
    assert "ACCEPTANCE_WEEK_PARTIALLY_ELAPSED" in result.blockers
    assert result.as_dict()["probability_publishable"] is False


def test_partially_elapsed_week_with_more_than_13_remaining_is_still_held():
    policy = CohortPolicy(13, *_W05)
    result = evaluate_cohort(_lane(), _inventory(_partial_week05(15)), policy, now=NOW)
    assert result.eligible_count == 15
    assert result.status == "COHORT_HELD"
    assert "ACCEPTANCE_WEEK_PARTIALLY_ELAPSED" in result.blockers


def test_selector_skips_partial_week_with_13_remaining_and_selects_next_full_week():
    inventory = _inventory(_partial_week05(13) + _full_week06(13))
    weeks = (_week("05", *_W05), _week("06", *_W06))
    row = choose_current_pregame_cohort(_lane(), inventory, weeks, now=NOW, minimum_unique_events=13)
    assert row["status"] == "COHORT_ELIGIBLE_FOR_TEST"
    assert row["selected_week"]["week_key"] == "06"
    assert row["cohort"]["eligible_event_count"] == 13
    assert all(e.startswith("w06-") for e in row["cohort"]["canonical_event_ids"])
    first = row["attempted_week_receipts"][0]
    assert first["week_key"] == "05" and first["status"] == "COHORT_HELD"
    assert first["eligible_count"] == 13
    assert "ACCEPTANCE_WEEK_PARTIALLY_ELAPSED" in first["blockers"]
    assert row["can_execute"] is False


def test_selector_partial_week_without_full_next_week_is_typed_hold():
    inventory = _inventory(_partial_week05(14) + _full_week06(12))
    weeks = (_week("05", *_W05), _week("06", *_W06))
    row = choose_current_pregame_cohort(_lane(), inventory, weeks, now=NOW, minimum_unique_events=13)
    assert row["status"] == "COHORT_HELD"
    assert row["selected_week"] is None and row["cohort"] is None
    assert row["blockers"] == ["ACCEPTANCE_NO_FULL_VERIFIED_WEEK"]
    receipts = {r["week_key"]: r for r in row["attempted_week_receipts"]}
    assert "ACCEPTANCE_WEEK_PARTIALLY_ELAPSED" in receipts["05"]["blockers"]
    assert "ACCEPTANCE_ELIGIBLE_COHORT_INSUFFICIENT" in receipts["06"]["blockers"]
    assert "ACCEPTANCE_WEEK_PARTIALLY_ELAPSED" not in receipts["06"]["blockers"]


def test_pregame_status_with_passed_kickoff_counts_as_elapsed_fail_closed():
    stale_status = Fixture("NFL", "w05-stale", NOW - timedelta(minutes=5), "SCHEDULED")
    pregame = tuple(
        Fixture("NFL", f"w05-pre-{i}", NOW + timedelta(hours=2 + i), "PRE_GAME") for i in range(13)
    )
    result = evaluate_cohort(_lane(), _inventory((stale_status,) + pregame), CohortPolicy(13, *_W05), now=NOW)
    assert result.status == "COHORT_HELD"
    assert "ACCEPTANCE_WEEK_PARTIALLY_ELAPSED" in result.blockers


def test_postponed_or_cancelled_in_window_is_not_elapsed_and_full_week_passes():
    rows = _full_week06(13) + (
        Fixture("NFL", "w06-ppd", _W06[0] + timedelta(hours=2), "POSTPONED"),
        Fixture("NFL", "w06-can", _W06[0] + timedelta(hours=3), "CANCELLED"),
    )
    result = evaluate_cohort(_lane(), _inventory(rows), CohortPolicy(13, *_W06), now=NOW)
    assert result.status == "COHORT_ELIGIBLE_FOR_TEST"
    assert result.blockers == ()
    assert result.eligible_count == 13


def test_played_games_outside_window_do_not_taint_full_future_week():
    inventory = _inventory(_partial_week05(13) + _full_week06(13))
    result = evaluate_cohort(_lane(), inventory, CohortPolicy(13, *_W06), now=NOW)
    assert result.status == "COHORT_ELIGIBLE_FOR_TEST"
    assert result.eligible_count == 13
