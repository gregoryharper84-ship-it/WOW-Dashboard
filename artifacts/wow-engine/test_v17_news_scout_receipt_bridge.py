"""Offline same-run GDELT archive-to-Scout bridge: discovery != model evidence."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from v17.sport_scout_enrichment import _attach_sibling_news, enrich_handoff


def _fixture():
    now = datetime.now(timezone.utc)
    event = {
        "sport_key": "basketball_nba",
        "official_event_id": "NBA-FUTURE-EXAMPLE",
        "event_start_utc": (now + timedelta(hours=6)).isoformat(),
        "home_team": "Home", "away_team": "Away",
    }
    payload = {
        "schema_version": "wow.v17.nightly_multiscout.v1",
        "status": "DISCOVERY_COMPLETE",
        "run_id": "news-bridge-test",
        "can_execute": False,
        "governance": {},
        "model_handoff": {
            "team_event_candidates": [event],
            "prop_candidates": [
                {"sport_key": "americanfootball_nfl",
                 "official_event_id": "NFL-OTHER",
                 "event_start_utc": (now + timedelta(hours=7)).isoformat()}
            ],
        },
    }
    receipt = {
        "schema_version": "wow.v17.public_news_receipt.v1",
        "source_status": "OK",
        "source_code": "SOURCE_FETCH_OK",
        "sport_key": "basketball_nba",
        "source_observed_at": (now - timedelta(minutes=20)).isoformat(),
        "articles": [{
            "source_url": "https://reporter.example/sports/news",
            "title": "Unverified headline",
            "official_event_identity_verified": False,
            "publisher_claim_verified": False,
            "historical_pregame_eligible": False,
        }],
        "model_feature_eligible": False,
        "historical_pregame_eligible": False,
        "prediction_authority": False,
        "can_execute": False,
    }
    return payload, receipt


def _write(tmp_path: Path, receipt):
    source = tmp_path / "model-handoff.json"
    (tmp_path / "public-gdelt-receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    return source


def test_public_gdelt_news_is_attached_but_cannot_promote_model(tmp_path):
    payload, receipt = _fixture()
    source = _write(tmp_path, receipt)
    attached = _attach_sibling_news(payload, source)
    bridge = attached["public_news_discovery_bridge"]
    assert bridge["status"] == "NEWS_DISCOVERY_ATTACHED"
    assert bridge["candidates_touched"] == 1
    assert len(bridge["source_sha256"]) == 64
    assert bridge["model_feature_eligible"] is False
    assert attached["model_handoff"]["team_event_candidates"][0]["public_news_discovery_sha256"] == bridge["source_sha256"]
    assert "news_discovery_receipt" not in attached["model_handoff"]["team_event_candidates"][0]
    assert attached["public_news_discovery_receipt"]["articles"] == receipt["articles"]
    result = enrich_handoff(attached)
    nba = result["model_handoff"]["team_event_candidates"][0]
    nfl = result["model_handoff"]["prop_candidates"][0]
    assert nba["news_research_handoff"]["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"
    # Exact original artifact bytes are hashed by the sibling reader;
    # parsed/canonical JSON is separately hashed and never misnamed as raw.
    research = nba["news_research_handoff"]
    assert research["source_receipt_sha256"] == bridge["source_sha256"]
    assert len(research["discovery_canonical_sha256"]) == 64
    assert research["source_receipt_sha256"] != research["discovery_canonical_sha256"]
    assert research["event_start_utc"] == payload["model_handoff"]["team_event_candidates"][0]["event_start_utc"]
    assert nba["news_research_handoff"]["research_rescore_requested"] is False
    assert nba["news_research_handoff"]["model_rescore_verified"] is False
    assert nba["news_research_handoff"]["calibrated_probability"] is None
    assert nba["probability_authority"] is False
    assert nfl.get("news_research_handoff") is None
    assert result["can_execute"] is False


def test_blocked_news_source_poses_no_eligibility(tmp_path):
    payload, receipt = _fixture()
    receipt["source_status"] = "BLOCKED"
    receipt["source_code"] = "SOURCE_RATE_LIMITED"
    receipt["articles"] = []
    result = enrich_handoff(_attach_sibling_news(payload, _write(tmp_path, receipt)))
    news = result["model_handoff"]["team_event_candidates"][0]["news_research_handoff"]
    assert news["research_status"] == "NEWS_DISCOVERY_BLOCKED"
    assert result["public_news_discovery_bridge"]["status"] == "NEWS_SOURCE_BLOCKED"
    assert result["public_news_discovery_bridge"]["source_code"] == "SOURCE_RATE_LIMITED"
    assert result["public_news_discovery_bridge"]["article_count"] == 0
    assert result["model_handoff"]["team_event_candidates"][0]["news_intelligence_status"] == "NEWS_DISCOVERY_BLOCKED"
    assert result["model_handoff"]["team_event_candidates"][0]["news_latest_verified_at_utc"] is None
    assert result["model_handoff"]["team_event_candidates"][0]["news_model_rescore_verified"] is False
    assert news["research_rescore_requested"] is False
    assert result["can_execute"] is False


def test_cross_sport_news_does_not_leak_between_events(tmp_path):
    payload, receipt = _fixture()
    receipt["sport_key"] = "tennis"
    attached = _attach_sibling_news(payload, _write(tmp_path, receipt))
    assert attached["public_news_discovery_bridge"]["status"] == "NEWS_NO_MATCHING_SPORT"
    assert attached["public_news_discovery_bridge"]["candidates_touched"] == 0
    assert "public_news_discovery_sha256" not in attached["model_handoff"]["team_event_candidates"][0]


def test_malformed_forged_or_oversized_news_receipts_fail_closed(tmp_path):
    payload, receipt = _fixture()
    for change in (
        {"can_execute": True},
        {"prediction_authority": True},
        {"source_status": "CERTIFIED"},
        {"articles": "not-list"},
        {"sport_key": {"malformed": True}},
    ):
        candidate = {**receipt, **change}
        result = _attach_sibling_news(payload, _write(tmp_path, candidate))
        assert result["public_news_discovery_bridge"]["status"] == "NEWS_RECEIPT_INVALID"
        assert "public_news_discovery_sha256" not in result["model_handoff"]["team_event_candidates"][0]
    (tmp_path / "public-gdelt-receipt.json").write_text("{not-json", encoding="utf-8")
    assert _attach_sibling_news(payload, tmp_path / "model-handoff.json")[
        "public_news_discovery_bridge"]["status"] == "NEWS_RECEIPT_UNREADABLE"
    (tmp_path / "public-gdelt-receipt.json").write_text("x" * 600_000, encoding="utf-8")
    assert _attach_sibling_news(payload, tmp_path / "model-handoff.json")[
        "public_news_discovery_bridge"]["status"] == "NEWS_RECEIPT_OVERSIZED"


def test_existing_independent_news_review_is_not_overwritten(tmp_path):
    payload, receipt = _fixture()
    candidate = payload["model_handoff"]["team_event_candidates"][0]
    existing = {"source_status": "BLOCKED", "source_code": "PRESERVED"}
    candidate["news_discovery_receipt"] = existing
    attached = _attach_sibling_news(payload, _write(tmp_path, receipt))
    assert attached["public_news_discovery_bridge"]["candidates_touched"] == 0
    assert attached["model_handoff"]["team_event_candidates"][0]["news_discovery_receipt"] == existing


def test_news_artifact_absence_keeps_legacy_handoff_exact(tmp_path):
    payload, _ = _fixture()
    assert _attach_sibling_news(payload, tmp_path / "model-handoff.json") is payload


def test_many_candidates_reference_one_bounded_public_receipt(tmp_path):
    payload, receipt = _fixture()
    one = payload["model_handoff"]["team_event_candidates"][0]
    payload["model_handoff"]["team_event_candidates"] = [
        {**one, "official_event_id": f"NBA-SAMPLE-{i}"} for i in range(400)
    ]
    attached = _attach_sibling_news(payload, _write(tmp_path, receipt))
    assert attached["public_news_discovery_bridge"]["candidates_touched"] == 400
    digest = attached["public_news_discovery_bridge"]["source_sha256"]
    assert all(row["public_news_discovery_sha256"] == digest
               and "news_discovery_receipt" not in row
               for row in attached["model_handoff"]["team_event_candidates"])
    # Original article URL is serialized only once, not 400 times.
    assert json.dumps(attached).count("https://reporter.example/sports/news") == 1
    assert len(json.dumps(attached)) < 250_000
    assert attached["can_execute"] is False
