"""NHL Nov 3 postmortem lesson guard: inputs are not model probabilities."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from v17.nhl_pregame_decision_audit import audit_nhl_pregame_decision
from v17.llp_team_intelligence_readback import project_llp_intelligence_page

START = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
EVENT = dict(
    sport="NHL", official_event_id="2026020245",
    home_team="NSH", away_team="CAR",
    event_start_time_utc=START.isoformat(),
)


def _at(hours=1):
    return (START - timedelta(hours=hours)).isoformat()


def _evidence():
    return {
        "starting_goalies": {
            "home": {"goalie_id": "111", "starting_status": "CONFIRMED",
                     "source_id": "official-confirmation",
                     "source_snapshot_id": "home-goalie-v1", "as_of": _at(1)},
            "away": {"goalie_id": "222", "starting_status": "CONFIRMED",
                     "source_id": "official-confirmation",
                     "source_snapshot_id": "away-goalie-v1", "as_of": _at(1)},
        },
        "injury_report": {
            "status": "CURRENT", "source_id": "injury-board",
            "source_snapshot_id": "injury-v1", "as_of": _at(3),
            "team_coverage": ["NSH", "CAR"],
            "replacement_review": {"home": True, "away": True},
        },
        "rest_travel": {
            "source_id": "official-schedule",
            "source_snapshot_id": "rest-v1", "as_of": _at(6),
            "home": {"rest_days": 1, "back_to_back": False},
            "away": {"rest_days": 0, "back_to_back": True},
        },
        "matchup_context": {
            "source_id": "frozen-prior-results",
            "source_snapshot_id": "matchup-v1", "as_of": _at(20),
            "home_elo": 1520, "away_elo": 1580,
            "home_goalie_sv_pct": .913, "away_goalie_sv_pct": .905,
            "home_pp_pct": .21, "away_pp_pct": .24,
            "home_pk_pct": .80, "away_pk_pct": .79,
            "home_home_win_pct": .58, "away_away_win_pct": .55,
        },
    }


def _row(evidence=None):
    return {
        "lane": "MONEYLINE",
        "row_status": "HELD",
        "row_index": 3,
        "identity": {**EVENT, "commence_time_utc": EVENT["event_start_time_utc"]},
        "detail": {
            "result": {
                "model_class": "RESEARCH_ONLY", "sport_specific_evidence": evidence,
                "probability_publishable": False, "selected_side": "CAR",
                "external_probability": .99, "positive_ev": True,
                "moneyline_quote": {"odds": +500, "verified": True},
                "can_execute": False,
            },
            "probability_publishable": False,
        },
    }


def _audit(evidence=None, markets=()):
    return audit_nhl_pregame_decision(
        event=EVENT, row=_row(evidence), bundle={"markets": list(markets)}
    )


def test_missing_inputs_show_all_concrete_postmortem_gaps():
    result = _audit()
    assert result["status"] == "PREGAME_CONTEXT_INCOMPLETE"
    assert result["context_blockers"] == [
        "NHL_STARTING_GOALIES_NOT_CONFIRMED_AND_SOURCE_PINNED",
        "NHL_INJURY_AND_REPLACEMENT_REVIEW_INCOMPLETE",
        "NHL_REST_BACK_TO_BACK_CONTEXT_UNVERIFIED",
        "NHL_TWO_SIDED_MATCHUP_CONTEXT_INCOMPLETE",
    ]
    assert result["winner_receipt_status"] == "NO_CERTIFIED_FITTED_WINNER_RECEIPT"
    assert result["value_claim_allowed"] is False
    assert result["can_execute"] is False


def test_complete_structured_context_is_not_fitted_certification_or_value():
    evidence = _evidence()
    result = _audit(evidence)
    assert result["status"] == "STRUCTURED_PREGAME_CONTEXT_PRESENT_NOT_CERTIFIED"
    assert result["context_blockers"] == []
    assert result["back_to_back_sides"] == ["AWAY"]
    assert result["winner_receipt_status"] == "NO_CERTIFIED_FITTED_WINNER_RECEIPT"
    assert result["value_status"] == "INDEPENDENT_PRICE_AND_EDGE_VERIFICATION_REQUIRED"
    assert result["value_claim_allowed"] is False
    assert result["confidence_ranking_generated"] is False
    assert result["probability_modified"] is False


@pytest.mark.parametrize("change", [
    {"starting_status": "PROJECTED"},
    {"goalie_id": ""},
    {"source_snapshot_id": ""},
    {"as_of": _at(6)},
    {"as_of": (START + timedelta(seconds=1)).isoformat()},
])
def test_goalie_projected_missing_or_stale_cannot_pass(change):
    evidence = _evidence()
    evidence["starting_goalies"]["away"].update(change)
    result = _audit(evidence)
    assert "NHL_STARTING_GOALIES_NOT_CONFIRMED_AND_SOURCE_PINNED" in result["context_blockers"]


def test_injury_coverage_normalizes_official_team_names_to_nhl_codes():
    evidence = _evidence()
    evidence["injury_report"]["team_coverage"] = [
        "Nashville Predators", "Carolina Hurricanes",
    ]
    assert _audit(evidence)["context_blockers"] == []


def test_duplicate_goalie_id_cannot_pass():
    evidence = _evidence()
    evidence["starting_goalies"]["away"]["goalie_id"] = "111"
    assert _audit(evidence)["status"] == "PREGAME_CONTEXT_INCOMPLETE"


@pytest.mark.parametrize("mutation", [
    lambda injury: injury.update(team_coverage=["NSH"]),
    lambda injury: injury["replacement_review"].update(away=False),
    lambda injury: injury.update(as_of=_at(30)),
    lambda injury: injury.update(status="UNKNOWN"),
])
def test_both_team_injury_and_replacement_coverage_required(mutation):
    evidence = _evidence()
    mutation(evidence["injury_report"])
    assert "NHL_INJURY_AND_REPLACEMENT_REVIEW_INCOMPLETE" in _audit(evidence)["context_blockers"]


@pytest.mark.parametrize("mutation", [
    lambda rest: rest.update(as_of=(START+timedelta(minutes=1)).isoformat()),
    lambda rest: rest.update(away={"back_to_back": "maybe", "rest_days": 1}),
    lambda rest: rest.update(away={"back_to_back": True, "rest_days": 1}),
    lambda rest: rest.update(away={"back_to_back": False, "rest_days": 0}),
    lambda rest: rest.update(source_snapshot_id=""),
])
def test_rest_and_back_to_back_require_structured_pregame_proof(mutation):
    evidence = _evidence()
    mutation(evidence["rest_travel"])
    assert "NHL_REST_BACK_TO_BACK_CONTEXT_UNVERIFIED" in _audit(evidence)["context_blockers"]


@pytest.mark.parametrize("field,value", [
    ("home_goalie_sv_pct", True), ("away_goalie_sv_pct", float("nan")),
    ("away_pk_pct", "0.81"), ("home_home_win_pct", 1.5),
    ("away_away_win_pct", None), ("home_elo", 10**400),
])
def test_two_sided_goalie_special_teams_and_venue_context_are_finite(field, value):
    evidence = _evidence()
    evidence["matchup_context"][field] = value
    assert "NHL_TWO_SIDED_MATCHUP_CONTEXT_INCOMPLETE" in _audit(evidence)["context_blockers"]


def test_governed_receipt_visibility_does_not_create_positive_value():
    governed = {
        "market_family": "OUTRIGHT_WINNER",
        "status": "GOVERNED_RECEIPT_VISIBLE", "evidence_visible": True,
    }
    result = _audit(_evidence(), [governed])
    assert result["winner_receipt_status"] == "GOVERNED_RECEIPT_PRESENT"
    assert result["value_claim_allowed"] is False
    assert result["confidence_ranking_generated"] is False


def test_other_sports_do_not_receive_nhl_overlay_and_daily_is_immutable():
    row = _row()
    before = deepcopy(row)
    page = {"run_id":"test", "status":"OK", "rows":[row],
            "returned":1, "next_offset":None}
    view = project_llp_intelligence_page(page)
    event = view["events"][0]
    assert event["markets"][0]["status"] == "HELD_EVIDENCE_ONLY"
    assert "calibrated_probability" not in event["markets"][0]
    assert event["nhl_pregame_decision_audit"]["status"] == "PREGAME_CONTEXT_INCOMPLETE"
    assert row == before
    row["identity"]["sport"] = "NBA"
    assert "nhl_pregame_decision_audit" not in project_llp_intelligence_page(page)["events"][0]


def test_wrong_sport_rejected_and_issuer_cannot_fake_context():
    with pytest.raises(ValueError, match="WRONG_SPORT"):
        audit_nhl_pregame_decision(
            event={**EVENT, "sport": "NFL"}, row=_row(_evidence()), bundle={}
        )
    row = _row()
    row["detail"]["result"]["goalie_status"] = "CONFIRMED"
    row["detail"]["result"]["bookmaker_confidence"] = .99
    projected = project_llp_intelligence_page({
        "run_id": "r", "status": "OK", "rows": [row], "returned": 1
    })["events"][0]["nhl_pregame_decision_audit"]
    assert projected["status"] == "PREGAME_CONTEXT_INCOMPLETE"
    assert projected["value_claim_allowed"] is False
