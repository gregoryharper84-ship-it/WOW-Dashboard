"""One NHL pregame path: verified identity → reviewed fact → fitted rescore."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import v17.news_event_research_handoff as news_handoff_module
import v17.news_fitted_rescore_dispatch as rescore_dispatch_module
from v17.news_event_research_handoff import reconcile_news_handoff
from v17.news_fitted_rescore_dispatch import dispatch_news_rescores_for_handoff
from v17.news_reviewed_fact_producer import attach_reviewed_facts_bundle, produce_reviewed_facts
from v17.nhl_multiscout_identity import resolve_multiscout_nhl_identity
from v17.sport_scout_enrichment import _attach_sibling_news, enrich_handoff

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)


def _fixed_news_clock(instant):
    """Freeze only the research handoff clock; never extend news freshness."""
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is not None else instant.replace(tzinfo=None)
    return FixedDateTime


URL = "https://www.nhl.com/news/islanders-confirm-starting-goalie"


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        # Real requests always exposes response.content. Keep the original
        # bytes available to the read-once resolver without using .json().
        self.content = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def json(self):
        return self.payload


class _Session:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, **kwargs):
        return _Response(self.payload)


def _schedule():
    return {
        "games": [{
            "id": 2026020381,
            "season": 20262027,
            "gameType": 2,
            "gameState": "FUT",
            "startTimeUTC": "2026-11-24T00:30:00Z",
            "homeTeam": {"abbrev": "NYI"},
            "awayTeam": {"abbrev": "TOR"},
        }]
    }


def _ok_scorer(request):
    return {
        "ok": True,
        "code": "NEWS_FITTED_RESCORE_COMPLETE",
        "model_version": "nhl-llp-fixture-v17.e2e",
        "feature_snapshot_hash": request["feature_snapshot_hash"],
        "calibrated_probability": 0.58,
        "calibrated_lower_bound": 0.51,
        "immutable_prediction_id": "pred-nhl-news-e2e-001",
        "prior_immutable_prediction_id": "pred-nhl-original-001",
        "calibration_receipt_id": "calib-nhl-e2e-001",
        "controlling_specialist_route": request.get("controlling_specialist_route"),
        "terminal_readback": {
            "authority": "V17_TERMINAL_REDUCER",
            "status": "HOLD_RESEARCH_RESCORE",
            "can_execute": False,
        },
        "prediction_authority": False,
        "can_execute": False,
    }


def test_verified_nhl_news_path_produces_fitted_rescore_receipt(tmp_path: Path, monkeypatch):
    # A league-official claim has a real 12h freshness ceiling. A test dated
    # Nov 22 must not silently expire when CI runs on Nov 23 or later.
    monkeypatch.setattr(news_handoff_module, "datetime", _fixed_news_clock(NOW))
    # enrich_handoff dispatches the rescore with the wall clock; freeze it to
    # the same instant so the fixed 2026-11-24T00:30Z start never expires.
    monkeypatch.setattr(rescore_dispatch_module, "datetime", _fixed_news_clock(NOW))
    identity = resolve_multiscout_nhl_identity(
        {
            "sport_key": "icehockey_nhl",
            "official_event_id": "espn-401999999",
            "provider_event_id": "espn-401999999",
            "provider_event_source": "ODDS_API",
            "official_event_identity_verified": False,
            "canonical_identity_status": "PROVIDER_EVENT_ID_UNVERIFIED",
            "commence_time": "2026-11-24T00:30:00Z",
            "home_team": "New York Islanders",
            "away_team": "Toronto Maple Leafs",
        },
        session=_Session(_schedule()),
    )
    assert identity["official_event_id"] == "2026020381"
    assert identity["provider_event_id"] == "espn-401999999"
    assert identity["official_event_identity_verified"] is True

    discovery = {
        "schema_version": "wow.v17.public_news_receipt.v1",
        "source_status": "OK",
        "source_code": "SOURCE_FETCH_OK",
        "sport_key": "icehockey_nhl",
        "source_observed_at": "2026-11-22T17:00:00Z",
        "articles": [{
            "source_url": URL,
            "title": "Islanders confirm Sorokin starts",
            "official_event_identity_verified": False,
        }],
        "prediction_authority": False,
        "can_execute": False,
    }
    source = tmp_path / "model-handoff.json"
    raw = json.dumps(discovery, separators=(",", ":")).encode("utf-8")
    (tmp_path / "public-gdelt-receipt.json").write_bytes(raw)
    digest = __import__("hashlib").sha256(raw).hexdigest()

    claim = {
        "source_url": URL,
        "sport_key": "icehockey_nhl",
        "official_event_id": "2026020381",
        "publisher_published_at_utc": "2026-11-22T16:00:00Z",
        "independently_reviewed_at_utc": "2026-11-22T17:00:00Z",
        "independent_review_ref": "sirt-islanders-goalie-20261122",
        "source_group": "nhl-official",
        "source_class": "LEAGUE_OFFICIAL",
        "confirmed": True,
        "evidence_kind": "STARTING_GOALIE_STATUS",
        "subject_id": "NYI-G",
        "fact_value": "SOROKIN",
        "material_change": True,
    }
    bundle = produce_reviewed_facts(
        discovery, [claim], observed_at=NOW, source_receipt_sha256=digest,
    )
    assert bundle["producer_status"] == "NEWS_REVIEW_FACTS_ACCEPTED"
    (tmp_path / "news-reviewed-facts.json").write_text(json.dumps(bundle), encoding="utf-8")

    payload = {
        "schema_version": "wow.v17.nightly_multiscout.v1",
        "status": "DISCOVERY_COMPLETE",
        "run_id": "news-e2e",
        "can_execute": False,
        "governance": {"can_execute": False},
        "model_handoff": {
            "team_event_candidates": [{
                **identity,
                "event_start_utc": "2026-11-24T00:30:00Z",
                "controlling_specialist_route": "LLP_TEAM_BETTING_ENGINE",
                "baseline_features": {"goalie_status": "UNCONFIRMED"},
            }],
            "prop_candidates": [],
        },
    }
    attached = _attach_sibling_news(payload, source)
    assert attached["public_news_discovery_bridge"]["source_sha256"] == digest
    attached = attach_reviewed_facts_bundle(attached, source)
    assert attached["news_reviewed_facts_bridge"]["status"] == "NEWS_REVIEWED_FACTS_ATTACHED"
    assert attached["news_reviewed_facts_bridge"]["candidates_attached"] == 1

    # Default enrich_handoff binds the production registry; without a registered
    # adapter the route remains typed MODEL_UNAVAILABLE (not a synthetic score).
    enriched = enrich_handoff(attached)
    candidate = enriched["model_handoff"]["team_event_candidates"][0]
    assert candidate["news_research_handoff"]["research_status"] == "NEWS_RESEARCH_RESCORE_REQUESTED"
    assert candidate["news_research_rescore_requested"] is True
    assert candidate["news_fitted_rescore_receipt"]["dispatch_status"] == "NEWS_RESCORE_MODEL_UNAVAILABLE"
    assert "MODEL_UNAVAILABLE" in candidate["news_fitted_rescore_receipt"]["blockers"]
    assert candidate["news_model_rescore_verified"] is False
    assert candidate["can_execute"] is False

    verified = dispatch_news_rescores_for_handoff(
        enriched, score_fn=_ok_scorer, observed_at=NOW,
    )
    row = verified["model_handoff"]["team_event_candidates"][0]
    receipt = row["news_fitted_rescore_receipt"]
    assert receipt["dispatch_status"] == "NEWS_RESCORE_VERIFIED"
    assert receipt["model_rescore_verified"] is True
    assert receipt["fitted_model_version"] == "nhl-llp-fixture-v17.e2e"
    assert receipt["before_feature_snapshot_hash"] != receipt["after_feature_snapshot_hash"]
    assert receipt["calibrated_probability"] == 0.58
    assert receipt["calibrated_lower_bound"] == 0.51
    assert receipt["immutable_prediction_id"] == "pred-nhl-news-e2e-001"
    assert receipt["prior_immutable_prediction_id"] == "pred-nhl-original-001"
    assert receipt["calibration_receipt_id"] == "calib-nhl-e2e-001"
    assert receipt["terminal_readback"]["authority"] == "V17_TERMINAL_REDUCER"
    assert receipt["can_execute"] is False
    assert verified["news_fitted_rescore_summary"]["rescore_verified"] == 1
    assert verified["can_execute"] is False

    # Red/green regression: when time truly passes the official 12h limit,
    # the same article must lose rescore eligibility even before puck drop.
    monkeypatch.setattr(news_handoff_module, "datetime", _fixed_news_clock(NOW + timedelta(hours=13)))
    monkeypatch.setattr(rescore_dispatch_module, "datetime", _fixed_news_clock(NOW + timedelta(hours=13)))
    expired = enrich_handoff(attached)
    expired_row = expired["model_handoff"]["team_event_candidates"][0]
    assert expired_row["news_research_handoff"]["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"
    assert expired_row["news_research_rescore_requested"] is False
    assert expired_row["can_execute"] is False


def test_adversarial_forged_id_and_duplicate_syndication_never_qualify():
    # Forged: provider id stamped as official without verification.
    forged_event = {
        "sport_key": "icehockey_nhl",
        "official_event_id": "espn-forged",
        "provider_event_id": "espn-forged",
        "official_event_identity_verified": False,
        "canonical_identity_status": "PROVIDER_EVENT_ID_UNVERIFIED",
        "event_start_utc": "2026-11-24T00:30:00Z",
    }
    discovery = {
        "source_status": "OK",
        "source_code": "SOURCE_FETCH_OK",
        "sport_key": "icehockey_nhl",
        "source_observed_at": "2026-11-22T17:00:00Z",
        "articles": [
            {"source_url": URL, "title": "Wire copy"},
            {"source_url": URL + "-mirror", "title": "Wire copy"},
        ],
        "can_execute": False,
        "prediction_authority": False,
    }
    handoff = reconcile_news_handoff(
        discovery,
        forged_event,
        [{
            "source_url": URL,
            "sport_key": "icehockey_nhl",
            "official_event_id": "espn-forged",
            "publisher_published_at_utc": "2026-11-22T16:00:00Z",
            "independently_reviewed_at_utc": "2026-11-22T17:00:00Z",
            "independent_review_ref": "x",
            "source_group": "g1",
            "source_class": "LEAGUE_OFFICIAL",
            "confirmed": True,
            "evidence_kind": "STARTING_GOALIE_STATUS",
            "subject_id": "NYI-G",
            "fact_value": "SOROKIN",
            "material_change": True,
        }],
        observed_at=NOW,
    )
    assert handoff["research_status"] == "NEWS_CANONICAL_EVENT_UNVERIFIED"
    assert handoff["research_rescore_requested"] is False

    # Syndicated secondary copies without official confirmation.
    producer = produce_reviewed_facts(
        discovery,
        [
            {
                "source_url": URL,
                "sport_key": "icehockey_nhl",
                "official_event_id": "2026020381",
                "publisher_published_at_utc": "2026-11-22T16:00:00Z",
                "independently_reviewed_at_utc": "2026-11-22T17:00:00Z",
                "independent_review_ref": "a",
                "source_group": "beat-1",
                "source_class": "PRIMARY_BEAT_REPORTER",
                "confirmed": True,
                "evidence_kind": "STARTING_GOALIE_STATUS",
                "subject_id": "NYI-G",
                "fact_value": "SOROKIN",
                "material_change": True,
            },
            {
                "source_url": URL + "-mirror",
                "sport_key": "icehockey_nhl",
                "official_event_id": "2026020381",
                "publisher_published_at_utc": "2026-11-22T16:05:00Z",
                "independently_reviewed_at_utc": "2026-11-22T17:05:00Z",
                "independent_review_ref": "b",
                "source_group": "beat-2",
                "source_class": "SECONDARY_MEDIA",
                "confirmed": True,
                "evidence_kind": "STARTING_GOALIE_STATUS",
                "subject_id": "NYI-G",
                "fact_value": "SOROKIN",
                "material_change": True,
            },
        ],
        observed_at=NOW,
        source_receipt_sha256="c" * 64,
    )
    assert producer["producer_status"] == "NEWS_REVIEW_HIGH_IMPACT_CONFIRMATION_REQUIRED"
    assert producer["can_execute"] is False
