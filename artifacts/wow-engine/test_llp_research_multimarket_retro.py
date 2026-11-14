"""Synthetic regression cases only; no real Nov 13 certified receipt is asserted."""
from copy import deepcopy
from v17.llp_research_multimarket_retro import audit_multimarket_retro

START = "2026-11-13T19:00:00Z"
FREEZE = "2026-11-13T17:00:00Z"
SETTLED = "2026-11-14T01:00:00Z"
BASIS = {
    "NBA": "FULL_GAME_INCLUDING_OVERTIME",
    "NCAAB": "FULL_GAME_INCLUDING_OVERTIME",
    "NCAAF": "FULL_GAME_INCLUDING_OVERTIME",
    "NHL": "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT",
    "SOCCER": "REGULATION",
    "TENNIS": "MATCH_COMPLETED",
}


def pick(sport="NBA", event="cavs-heat", market="MONEYLINE", side="AWAY",
         line=None, cohort="ML_TOP10", **changes):
    row = dict(sport=sport, official_event_id=event, home_team="Home",
               away_team="Away", event_start_time_utc=START,
               selection_id=f"{cohort}-{sport}-{event}-{market}",
               pregame_receipt_id=f"pre-{cohort}-{sport}-{event}-{market}",
               immutable_pregame=True, pregame_receipt_verified=True,
               frozen_at_utc=FREEZE, available_to_user_at_utc=FREEZE,
               source_as_of_utc="2026-11-13T16:00:00Z",
               market_family=market, selected_side=side,
               selected_side_line=line, cohort=cohort,
               settlement_basis=BASIS[sport])
    row.update(changes)
    return row


def final(sport="NBA", event="cavs-heat", home=133, away=121, **changes):
    row = dict(sport=sport, official_event_id=event, home_team="Home",
               away_team="Away", event_start_time_utc=START,
               settlement_basis=BASIS[sport], home_score=home,
               away_score=away, final_status="FINAL",
               independently_verified=True, settled_at_utc=SETTLED,
               settlement_receipt_id=f"official-{sport}-{event}",
               settlement_source="OFFICIAL_LEAGUE")
    row.update(changes)
    return row


def grade(picks, finals, **kwargs):
    return audit_multimarket_retro(picks, finals, **kwargs)


def verified_manifest(*picks):
    by_cohort = {}
    for item in picks:
        by_cohort.setdefault(item["cohort"], []).append(item["selection_id"])
    return dict(cohort_manifest=by_cohort, cohort_manifest_verified=True,
                cohort_manifest_receipt_id="frozen-slate-audited")


def test_original_nov13_upset_examples_are_only_synthetic_settlement_shapes():
    selections = [
        pick("NCAAB", "clemson-georgetown", side="AWAY", cohort="UPSETS"),
        pick("NCAAF", "pitt-fsu", side="AWAY", cohort="UPSETS"),
        pick("NHL", "vegas-nashville", side="AWAY", cohort="UPSETS"),
        pick("NBA", "heat-cavs", side="AWAY", cohort="UPSETS"),
        pick("SOCCER", "lux-est", side="AWAY", cohort="UPSETS"),
    ]
    finals = [
        final("NCAAB", "clemson-georgetown", 78, 85),
        final("NCAAF", "pitt-fsu", 10, 16),
        final("NHL", "vegas-nashville", 2, 3),
        final("NBA", "heat-cavs", 133, 121),
        final("SOCCER", "lux-est", 0, 1),
    ]
    before = deepcopy((selections, finals))
    result = grade(selections, finals, **verified_manifest(*selections))
    assert [r["grade_status"] for r in result["rows"]] == [
        "WIN", "WIN", "WIN", "LOSS", "WIN"
    ]
    assert result["by_cohort"][0]["headline_accuracy"] == 0.8
    assert result["fitted_model_samples_added"] == 0
    assert result["cross_market_accuracy"] is None
    assert result["can_execute"] is False
    assert (selections, finals) == before


def test_separate_cohorts_and_spread_does_not_follow_winner():
    selections = [
        pick("NBA", "magic-bucks", side="HOME", cohort="ML"),
        pick("NBA", "magic-bucks", market="SPREAD", side="HOME",
             line=-8.5, cohort="ATS"),
        pick("NBA", "magic-bucks", market="TOTAL", side="UNDER",
             line=220.5, cohort="OU"),
    ]
    finals = [final("NBA", "magic-bucks", 107, 99)]
    report = grade(selections, finals, **verified_manifest(*selections))
    assert [r["grade_status"] for r in report["rows"]] == ["WIN", "LOSS", "WIN"]
    assert {x["cohort"]: x["headline_accuracy"] for x in report["by_cohort"]} == {
        "ATS": 0.0, "ML": 1.0, "OU": 1.0
    }
    assert report["cross_market_accuracy"] is None


def test_push_is_not_a_win_or_loss():
    row = pick("NHL", "vegas-nashville", market="TOTAL", side="UNDER", line=5,
               cohort="NHL_TOTAL")
    report = grade([row], [final("NHL", "vegas-nashville", 2, 3)],
                   **verified_manifest(row))
    assert report["rows"][0]["grade_status"] == "PUSH"
    assert report["by_cohort"][0]["graded"] == 0
    assert report["by_cohort"][0]["headline_accuracy"] is None


def test_soccer_regulation_draw_is_distinct_from_home_away_win():
    row = pick("SOCCER", "sui-slo", side="AWAY")
    report = grade([row], [final("SOCCER", "sui-slo", 0, 0)],
                   **verified_manifest(row))
    assert report["rows"][0]["grade_status"] == "LOSS"
    row2 = pick("SOCCER", "sui-slo", side="DRAW")
    report2 = grade([row2], [final("SOCCER", "sui-slo", 0, 0)],
                    **verified_manifest(row2))
    assert report2["rows"][0]["grade_status"] == "WIN"


def test_tennis_prematch_withdrawal_void_not_a_prediction_win():
    row = pick("TENNIS", "andreeva-muchova")
    outcome = final("TENNIS", "andreeva-muchova", 0, 0,
                    final_status="VOID_PREMATCH_WITHDRAWAL",
                    void_reason="PREMATCH_WITHDRAWAL")
    report = grade([row], [outcome], **verified_manifest(row))
    assert report["rows"][0]["grade_status"] == "VOID"
    assert report["by_cohort"][0]["graded"] == 0


def test_tennis_completed_requires_explicit_official_winner():
    row = pick("TENNIS", "fritz-musetti", side="HOME")
    outcome = final("TENNIS", "fritz-musetti")
    assert grade([row], [outcome])["rows"][0]["grade_status"] == "HELD"
    outcome["winner_side"] = "HOME"
    assert grade([row], [outcome])["rows"][0]["grade_status"] == "WIN"


def test_unverified_or_late_pregame_fails_closed():
    row = pick(pregame_receipt_verified=False)
    report = grade([row], [final()])
    assert report["rows"][0]["grade_status"] == "HELD"
    assert "PREGAME_SELECTION_RECEIPT_INVALID" in report["rows"][0]["blockers"]
    row = pick(source_as_of_utc="2026-11-13T18:00:00Z")
    report = grade([row], [final()])
    assert "PREGAME_CHRONOLOGY_INVALID" in report["rows"][0]["blockers"]


def test_unverified_official_final_cannot_grade():
    row = pick()
    report = grade([row], [final(independently_verified=False)],
                   **verified_manifest(row))
    assert report["rows"][0]["grade_status"] == "HELD"
    assert report["by_cohort"][0]["headline_accuracy"] is None


def test_falsely_reused_pregame_receipt_held_for_both_events():
    a = pick()
    b = pick("NBA", "another", pregame_receipt_id=a["pregame_receipt_id"])
    report = grade([a, b], [final(), final("NBA", "another")])
    assert [r["grade_status"] for r in report["rows"]] == ["HELD", "HELD"]


def test_duplicate_official_final_holds():
    row = pick()
    report = grade([row], [final(), final()], **verified_manifest(row))
    assert report["rows"][0]["grade_status"] == "HELD"


def test_no_card_manifest_no_headline_even_if_outcome_is_known():
    row = pick()
    report = grade([row], [final()])
    assert report["rows"][0]["grade_status"] == "LOSS"
    assert report["by_cohort"][0]["headline_accuracy"] is None


def test_partial_card_suppresses_headline():
    row = pick()
    report = grade([row], [final()], cohort_manifest={"ML_TOP10": ["entry-a", "entry-b"]},
                   cohort_manifest_verified=True, cohort_manifest_receipt_id="manifest")
    assert report["by_cohort"][0]["headline_accuracy"] is None


def test_mismatched_settlement_and_invalid_score_hold():
    row = pick()
    report = grade([row], [final(settlement_basis="REGULATION")])
    assert "MARKET_SETTLEMENT_BASIS_INVALID" in report["rows"][0]["blockers"]
    report = grade([row], [final(home_score=-2)])
    assert "OFFICIAL_SCORE_INVALID" in report["rows"][0]["blockers"]


def test_unsupported_quarter_and_nonfinite_lines_hold():
    for line in (-2.25, float("nan"), float("inf"), True, 10**1000):
        row = pick(market="SPREAD", side="HOME", line=line, cohort="ATS")
        report = grade([row], [final()])
        assert report["rows"][0]["grade_status"] == "HELD"
        assert "SPREAD_SIDE_OR_LINE_INVALID" in report["rows"][0]["blockers"]


def test_same_event_market_cannot_be_counted_twice_in_cohort():
    a = pick()
    b = pick(selection_id="independent-extra-choice",
             pregame_receipt_id="independent-extra-receipt", selected_side="HOME")
    report = grade([a, b], [final()])
    assert all(r["grade_status"] == "HELD" for r in report["rows"])
    assert all("DUPLICATE_COHORT_EVENT_MARKET" in r["blockers"] for r in report["rows"])


def test_reused_official_settlement_receipt_cannot_grade_two_events():
    a = pick()
    b = pick(event="another")
    first = final()
    second = final(event="another", settlement_receipt_id=first["settlement_receipt_id"])
    report = grade([a, b], [first, second])
    assert all(r["grade_status"] == "HELD" for r in report["rows"])
    assert all("DUPLICATE_OFFICIAL_SETTLEMENT_RECEIPT" in r["blockers"]
               for r in report["rows"])


def test_missing_entire_cohort_must_remain_visible_and_unscored():
    report = grade([], [], cohort_manifest={"ML_TOP10": ["locked-pick-id"]},
                   cohort_manifest_verified=True, cohort_manifest_receipt_id="manifest")
    cohort = report["by_cohort"][0]
    assert cohort["cohort"] == "ML_TOP10"
    assert cohort["manifest_missing_selection_ids"] == ["locked-pick-id"]
    assert cohort["headline_accuracy"] is None


def test_mixed_market_same_cohort_cannot_publish_hit_rate():
    ml = pick(cohort="COMBINED")
    spread = pick(market="SPREAD", side="HOME", line=-7.5, cohort="COMBINED")
    report = grade([ml, spread], [final()], **verified_manifest(ml, spread))
    assert all(r["grade_status"] == "HELD" for r in report["rows"])
    assert all("MIXED_MARKET_COHORT_INVALID" in r["blockers"]
               for r in report["rows"])
    assert report["by_cohort"][0]["headline_accuracy"] is None


def test_official_prematch_void_can_settle_before_scheduled_start():
    row = pick("TENNIS", "andreeva-muchova")
    outcome = final("TENNIS", "andreeva-muchova",
                    final_status="VOID_PREMATCH_WITHDRAWAL",
                    void_reason="PREMATCH_WITHDRAWAL",
                    settled_at_utc="2026-11-13T18:30:00Z")
    report = grade([row], [outcome], **verified_manifest(row))
    assert report["rows"][0]["grade_status"] == "VOID"
    outcome["settled_at_utc"] = "2026-11-13T16:30:00Z"
    assert grade([row], [outcome])["rows"][0]["grade_status"] == "HELD"


def test_invalid_manifest_payload_does_not_crash_or_award_accuracy():
    row = pick()
    report = grade([row], [final()], cohort_manifest={"ML_TOP10": [["bad-nested-id"]]},
                   cohort_manifest_verified=True, cohort_manifest_receipt_id="manifest")
    assert report["by_cohort"][0]["headline_accuracy"] is None
