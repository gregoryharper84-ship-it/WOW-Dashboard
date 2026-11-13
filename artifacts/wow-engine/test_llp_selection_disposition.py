from datetime import datetime, timezone

import pytest

from v17.llp_selection_disposition import (
    ExternalResearchSupport,
    classify_selection_disposition,
)

NOW = datetime(2026, 11, 8, 16, 59, tzinfo=timezone.utc)
START = datetime(2026, 11, 8, 22, 0, tzinfo=timezone.utc)
MODEL_TIME = datetime(2026, 11, 8, 15, 30, tzinfo=timezone.utc)
SNAPSHOT_TIME = datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc)


def _research(*, second_domain="espn.com", second_stance="SUPPORTS_SELECTED_SIDE", captured_at=NOW):
    return ExternalResearchSupport.model_validate({
        "sport": "NBA",
        "official_event_id": "nba-2026-1108-bos-mil",
        "home_team": "Boston Celtics",
        "away_team": "Milwaukee Bucks",
        "event_start_time_utc": START.isoformat(),
        "selected_side": "Boston Celtics",
        "captured_at_utc": captured_at.isoformat(),
        "sources": [
            {
                "source_id": "official-injury-report",
                "url": "https://www.nba.com/injuryreport",
                "domain": "nba.com",
                "source_type": "OFFICIAL",
                "stance": "SUPPORTS_SELECTED_SIDE",
                "observed_at_utc": (NOW.replace(minute=50)).isoformat(),
                "summary": "The official availability update supports the selected side's matchup outlook."
            },
            {
                "source_id": "independent-preview",
                "url": f"https://www.{second_domain}/nba/game-preview",
                "domain": second_domain,
                "source_type": "ANALYSIS",
                "stance": second_stance,
                "observed_at_utc": (NOW.replace(minute=55)).isoformat(),
                "summary": "Independent matchup analysis supports the selected side."
            }
        ],
        "supporting_factors": [
            "The verified availability report supports the selected side.",
            "Independent matchup analysis supports the same side."
        ],
        "contradicting_factors": []
    })


def _event(*, receipt_verified=False, market_status="HELD_EVIDENCE_ONLY"):
    market = {
        "market_family": "OUTRIGHT_WINNER",
        "status": market_status,
        "reason_codes": ["MODEL_CLASS_NOT_CERTIFIED_FITTED"],
        "selected_side": "Boston Celtics",
        "model_class": "RESEARCH_ONLY",
        "model_version": "nba-candidate-v3",
        "source_receipt_id": None,
    }
    if market_status == "GOVERNED_RECEIPT_VISIBLE":
        market.update({
            "evidence_visible": True,
            "model_class": "CERTIFIED_FITTED",
            "model_version": "nba-fitted-v3",
            "calibration_version": "nba-cal-v2",
            "source_receipt_id": "00000000-0000-4000-8000-000000000001",
            "calibrated_probability": 0.66,
            "calibrated_probability_lower_bound": 0.59,
            "calibrated_probability_upper_bound": 0.72,
        })
    return {
        "sport": "NBA",
        "official_event_id": "nba-2026-1108-bos-mil",
        "home_team": "Boston Celtics",
        "away_team": "Milwaukee Bucks",
        "event_start_time_utc": START.isoformat(),
        "markets": [market],
        "source_diagnostics": {
            "event_prediction_receipt_verified": receipt_verified,
            "source_reason_codes": [],
        },
    }


def _row(*, model_class="RESEARCH_ONLY", artifact=True, status="HELD"):
    result = {
        "model_class": model_class,
        "model_version": "nba-candidate-v3",
        "model_artifact_id": "artifact-nba-2026-03",
        "model_artifact_sha256": "a" * 64,
        "calibration_artifact_id": "calibration-nba-2026-02",
        "calibration_artifact_sha256": "b" * 64,
        "model_timestamp": MODEL_TIME.isoformat(),
        "source_snapshot_timestamp": SNAPSHOT_TIME.isoformat(),
        "can_execute": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    if not artifact:
        result.pop("model_artifact_id")
        result.pop("model_artifact_sha256")
    if model_class == "CERTIFIED_FITTED":
        result.update({
            "probability_publishable": True,
            "rank_eligible": True,
            "calibrated_probability": 0.66,
            "calibrated_probability_lower_bound": 0.59,
            "calibrated_probability_upper_bound": 0.72,
            "source_receipt_id": "00000000-0000-4000-8000-000000000001",
        })
    return {
        "row_index": 0,
        "row_status": status,
        "lane": "MONEYLINE",
        "detail": {
            "identity": {
                "sport": "NBA",
                "official_event_id": "nba-2026-1108-bos-mil",
                "home_team": "Boston Celtics",
                "away_team": "Milwaukee Bucks",
                "commence_time_utc": START.isoformat(),
            },
            "result": result,
        },
    }


def test_research_supported_candidate_is_provisional_without_model_probability():
    disposition = classify_selection_disposition(
        event=_event(), raw_row=_row(), research_context=_research(), now_utc=NOW,
    )
    assert disposition["status"] == "PROVISIONAL"
    assert disposition["selected_side"] == "Boston Celtics"
    assert disposition["basis"] == "EXTERNAL_RESEARCH_ONLY"
    assert disposition["probability_publishable"] is False
    assert disposition["rank_eligible"] is False
    assert disposition["can_execute"] is False
    assert disposition["model_recheck_required"] is True
    assert disposition["source_content_verified_by_backend"] is False
    assert "calibrated_probability" not in disposition
    assert "calibrated_probability_lower_bound" not in disposition
    assert "model_probability" not in disposition


def test_missing_model_artifact_is_held_even_when_external_research_supports_a_side():
    disposition = classify_selection_disposition(
        event=_event(), raw_row=_row(artifact=False),
        research_context=_research(), now_utc=NOW,
    )
    assert disposition["status"] == "HELD"
    assert "MODEL_ARTIFACT_MISSING" in disposition["reason_codes"]
    assert disposition["selected_side"] is None
    assert disposition["can_execute"] is False


def test_unresolved_identity_is_held_even_when_research_supports_a_side():
    event = _event()
    event["official_event_id"] = ""
    disposition = classify_selection_disposition(
        event=event, raw_row=_row(), research_context=_research(), now_utc=NOW,
    )
    assert disposition["status"] == "HELD"
    assert "EVENT_IDENTITY_UNRESOLVED" in disposition["reason_codes"]


def test_market_context_alone_cannot_create_a_provisional_selection():
    research = _research()
    research_data = research.model_dump(mode="json")
    for source in research_data["sources"]:
        source["source_type"] = "MARKET_CONTEXT"
    research = ExternalResearchSupport.model_validate(research_data)
    disposition = classify_selection_disposition(
        event=_event(), raw_row=_row(), research_context=research, now_utc=NOW,
    )
    assert disposition["status"] == "HELD"
    assert "EXTERNAL_RESEARCH_INSUFFICIENT_INDEPENDENT_SOURCES" in disposition["reason_codes"]


def test_conflicting_external_source_holds_instead_of_selecting_a_side():
    disposition = classify_selection_disposition(
        event=_event(), raw_row=_row(),
        research_context=_research(second_stance="CONTRADICTS_SELECTED_SIDE"),
        now_utc=NOW,
    )
    assert disposition["status"] == "HELD"
    assert "EXTERNAL_RESEARCH_CONFLICT" in disposition["reason_codes"]


def test_qualified_requires_independent_ledger_verification_and_full_fitted_package():
    event = _event(receipt_verified=True, market_status="GOVERNED_RECEIPT_VISIBLE")
    disposition = classify_selection_disposition(
        event=event,
        raw_row=_row(model_class="CERTIFIED_FITTED", status="COMPLETED"),
        research_context=None,
        now_utc=NOW,
    )
    assert disposition["status"] == "QUALIFIED"
    assert disposition["basis"] == "V17_CERTIFIED_FITTED_RECEIPT"
    assert disposition["selected_side"] == "Boston Celtics"
    assert disposition["calibrated_probability"] == 0.66
    assert disposition["calibrated_probability_lower_bound"] == 0.59
    assert disposition["source_receipt_id"] == "00000000-0000-4000-8000-000000000001"
    assert disposition["can_execute"] is False


def test_daily_claim_without_ledger_verification_never_becomes_qualified():
    event = _event(receipt_verified=False, market_status="GOVERNED_RECEIPT_VISIBLE")
    disposition = classify_selection_disposition(
        event=event,
        raw_row=_row(model_class="CERTIFIED_FITTED", status="COMPLETED"),
        research_context=None,
        now_utc=NOW,
    )
    assert disposition["status"] == "HELD"
    assert "EVENT_PREDICTION_RECEIPT_UNVERIFIED" in disposition["reason_codes"]


def test_external_research_context_rejects_probability_fields():
    data = _research().model_dump(mode="json")
    data["sources"][0]["probability"] = 0.91
    with pytest.raises(Exception):
        ExternalResearchSupport.model_validate(data)
