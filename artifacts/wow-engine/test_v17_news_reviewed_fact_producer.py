"""Reviewed-news producer: accept authentic claims, reject adversarial ones."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from v17.news_reviewed_fact_producer import (
    attach_reviewed_facts_bundle,
    produce_reviewed_facts,
)

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
URL = "https://www.nhl.com/news/islanders-confirm-starting-goalie"
URL_B = "https://www.nhl.com/islanders/news/goalie-confirmation"
DISCOVERY = {
    "source_status": "OK",
    "source_code": "SOURCE_FETCH_OK",
    "source_observed_at": "2026-11-22T17:00:00Z",
    "sport_key": "icehockey_nhl",
    "articles": [
        {"source_url": URL, "title": "Islanders confirm Sorokin starts tonight"},
        {"source_url": URL_B, "title": "Different headline confirms Sorokin"},
    ],
    "can_execute": False,
    "prediction_authority": False,
}
CLAIM = {
    "source_url": URL,
    "sport_key": "icehockey_nhl",
    "official_event_id": "2026020381",
    "publisher_published_at_utc": "2026-11-22T16:00:00Z",
    "independently_reviewed_at_utc": "2026-11-22T17:00:00Z",
    "independent_review_ref": "sirt-review-islanders-20261122",
    "source_group": "nhl-official",
    "source_class": "LEAGUE_OFFICIAL",
    "confirmed": True,
    "evidence_kind": "STARTING_GOALIE_STATUS",
    "subject_id": "NYI-G",
    "fact_value": "SOROKIN",
    "material_change": True,
}
DIGEST = "a" * 64


def test_authentic_league_claim_is_accepted_with_source_linkage():
    out = produce_reviewed_facts(DISCOVERY, [CLAIM], observed_at=NOW, source_receipt_sha256=DIGEST)
    assert out["producer_status"] == "NEWS_REVIEW_FACTS_ACCEPTED"
    assert out["source_receipt_sha256"] == DIGEST
    assert len(out["accepted_facts"]) == 1
    fact = out["accepted_facts"][0]
    assert fact["official_event_id"] == "2026020381"
    assert fact["source_receipt_sha256"] == DIGEST
    assert fact["independent_review_ref"] == "sirt-review-islanders-20261122"
    assert out["can_execute"] is False
    assert out["prediction_authority"] is False


def test_unsupported_publisher_and_url_not_in_discovery_are_rejected():
    bad_class = {**CLAIM, "source_class": "ANONYMOUS_BLOG"}
    out = produce_reviewed_facts(DISCOVERY, [bad_class], observed_at=NOW, source_receipt_sha256=DIGEST)
    assert out["producer_status"] == "NEWS_REVIEW_NO_ACCEPTED_FACTS"
    assert out["rejected_claims"][0]["reason"] == "UNSUPPORTED_PUBLISHER_CLASS"

    bad_url = {**CLAIM, "source_url": "https://evil.example/forged"}
    out = produce_reviewed_facts(DISCOVERY, [bad_url], observed_at=NOW, source_receipt_sha256=DIGEST)
    assert out["rejected_claims"][0]["reason"] == "SOURCE_URL_NOT_IN_DISCOVERY"


def test_syndicated_duplicate_high_impact_without_official_is_rejected():
    one = {
        **CLAIM,
        "source_class": "PRIMARY_BEAT_REPORTER",
        "source_group": "reporter-a",
        "independent_review_ref": "review-a",
    }
    # Same headline text as URL → syndicated, not independent corroboration.
    two = {
        **CLAIM,
        "source_url": URL_B,
        "source_class": "SECONDARY_MEDIA",
        "source_group": "reporter-b",
        "independent_review_ref": "review-b",
    }
    discovery = {
        **DISCOVERY,
        "articles": [
            {"source_url": URL, "title": "Sorokin starts"},
            {"source_url": URL_B, "title": "Sorokin starts"},
        ],
    }
    out = produce_reviewed_facts(discovery, [one, two], observed_at=NOW, source_receipt_sha256=DIGEST)
    assert out["producer_status"] == "NEWS_REVIEW_HIGH_IMPACT_CONFIRMATION_REQUIRED"
    assert "SYNDICATED_OR_UNATTRIBUTED_HEADLINES" in out["conflicts"]


def test_stale_or_unconfirmed_claims_are_rejected():
    stale = {**CLAIM, "publisher_published_at_utc": "2026-11-23T01:00:00Z"}
    out = produce_reviewed_facts(DISCOVERY, [stale], observed_at=NOW, source_receipt_sha256=DIGEST)
    assert out["rejected_claims"][0]["reason"] == "TIMESTAMP_INVALID"
    unconfirmed = {**CLAIM, "confirmed": False}
    out = produce_reviewed_facts(DISCOVERY, [unconfirmed], observed_at=NOW, source_receipt_sha256=DIGEST)
    assert out["rejected_claims"][0]["reason"] == "CLAIM_NOT_CONFIRMED"


def test_bundle_attaches_only_to_verified_matching_events(tmp_path: Path):
    bundle = produce_reviewed_facts(DISCOVERY, [CLAIM], observed_at=NOW, source_receipt_sha256=DIGEST)
    (tmp_path / "news-reviewed-facts.json").write_text(json.dumps(bundle), encoding="utf-8")
    payload = {
        "can_execute": False,
        "model_handoff": {
            "team_event_candidates": [
                {
                    "sport_key": "icehockey_nhl",
                    "official_event_id": "2026020381",
                    "official_event_identity_verified": True,
                },
                {
                    "sport_key": "icehockey_nhl",
                    "official_event_id": "2026020381",
                    "official_event_identity_verified": False,
                },
                {
                    "sport_key": "icehockey_nhl",
                    "official_event_id": "9999999999",
                    "official_event_identity_verified": True,
                },
            ],
            "prop_candidates": [],
        },
    }
    out = attach_reviewed_facts_bundle(payload, tmp_path / "model-handoff.json")
    assert out["news_reviewed_facts_bridge"]["status"] == "NEWS_REVIEWED_FACTS_ATTACHED"
    assert out["news_reviewed_facts_bridge"]["candidates_attached"] == 1
    rows = out["model_handoff"]["team_event_candidates"]
    assert len(rows[0]["news_reviewed_facts"]) == 1
    assert "news_reviewed_facts" not in rows[1]
    assert "news_reviewed_facts" not in rows[2]
