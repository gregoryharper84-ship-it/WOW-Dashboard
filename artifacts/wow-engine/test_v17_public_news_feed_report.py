"""Newsfeed output is honest about one sport, duplicates, and source blockers."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from v17.public_news_feed_report import SPORTS, build_feed, main, render_markdown


AT = datetime(2026, 11, 24, 7, 0, tzinfo=timezone.utc)


def _receipt():
    observed = AT - timedelta(minutes=5)
    return {
        "schema_version": "wow.v17.public_news_receipt.v1",
        "source_status": "OK", "source_code": "SOURCE_FETCH_OK",
        "sport_key": "icehockey_nhl",
        "source_observed_at": observed.isoformat(),
        "selection_policy": "ACTIVE_SLATE_ROTATION",
        "prediction_authority": False, "model_feature_eligible": False,
        "can_execute": False,
        "articles": [
            {
                "source_url": "https://example.com/nhl/goalie-news",
                "title": "Potential NHL goalie update",
                "source_provider": "GDELT",
                "source_discovered_at_claim": observed.isoformat(),
                "publisher_claim_verified": False,
                "official_event_identity_verified": False,
                "evidence_only": True,
            },
            {
                "source_url": "https://other.example/nhl/goalie-news",
                "title": "Potential NHL goalie update",
                "source_provider": "GDELT",
                "source_discovered_at_claim": observed.isoformat(),
                "publisher_claim_verified": False,
                "official_event_identity_verified": False,
                "evidence_only": True,
            },
        ],
    }


def test_one_sport_sample_deduplicates_syndication_with_provenance():
    raw = json.dumps(_receipt(), separators=(",", ":")).encode()
    feed = build_feed(raw, run_id="123-1", at=AT)
    assert feed["source_receipt_sha256"] == hashlib.sha256(raw).hexdigest()
    assert feed["status"] == "SOURCE_NONEMPTY_RESEARCH_ONLY"
    assert feed["sampled_sport"] == "icehockey_nhl"
    assert feed["article_count_in_receipt"] == 2
    assert len(feed["headlines"]) == 1
    assert len(feed["headlines"][0]["source_instances"]) == 2
    assert feed["duplicate_or_invalid_articles"] == 1
    assert feed["sport_coverage"]["icehockey_nhl"] == "SAMPLED_NONEMPTY_RESEARCH_ONLY"
    assert all(feed["sport_coverage"][s] == "NOT_SAMPLED_THIS_RUN"
               for s in SPORTS if s != "icehockey_nhl")
    assert feed["coverage_is_exhaustive"] is False
    assert feed["model_rescore_verified"] is False
    assert feed["publisher_facts_verified"] is False
    assert feed["probability_authority"] is False
    assert feed["can_execute"] is False


def test_blocked_source_is_not_reported_healthy():
    receipt = _receipt()
    receipt["source_status"] = "BLOCKED"
    receipt["source_code"] = "SOURCE_RATE_LIMITED"
    receipt["articles"] = []
    feed = build_feed(json.dumps(receipt).encode(), at=AT)
    assert feed["status"] == "SOURCE_DEGRADED"
    assert feed["source_code"] == "SOURCE_RATE_LIMITED"
    assert feed["headlines"] == []
    assert feed["sport_coverage"]["icehockey_nhl"] == "SAMPLED_SOURCE_BLOCKED"


def test_invalid_or_unavailable_receipt_fails_closed():
    assert build_feed(b"", at=AT)["status"] == "SOURCE_RECEIPT_MISSING"
    assert build_feed(b"[]", at=AT)["status"] == "SOURCE_RECEIPT_INVALID"
    assert build_feed(b"{broken", at=AT)["status"] == "SOURCE_RECEIPT_INVALID"
    assert build_feed(b"X" * 512_001, at=AT)["status"] == "SOURCE_RECEIPT_OVERSIZED"
    receipt = _receipt()
    receipt["can_execute"] = True
    assert build_feed(json.dumps(receipt).encode(), at=AT)["status"] == "SOURCE_RECEIPT_INVALID"


def test_missing_source_timestamp_and_staleness_are_visible():
    receipt = _receipt()
    receipt["source_observed_at"] = (AT - timedelta(days=2)).isoformat()
    assert build_feed(json.dumps(receipt).encode(), at=AT)["status"] == "SOURCE_RECEIPT_STALE_OR_TIME_INVALID"
    receipt["source_observed_at"] = (AT + timedelta(hours=1)).isoformat()
    assert build_feed(json.dumps(receipt).encode(), at=AT)["status"] == "SOURCE_RECEIPT_STALE_OR_TIME_INVALID"


def test_forged_event_or_verified_claim_does_not_become_public_feed_fact():
    receipt = _receipt()
    receipt["articles"][0]["publisher_claim_verified"] = True
    receipt["articles"][1]["official_event_identity_verified"] = True
    feed = build_feed(json.dumps(receipt).encode(), at=AT)
    assert feed["status"] == "SOURCE_NO_VALID_HEADLINES"
    assert feed["headlines"] == []


def test_external_title_is_escaped_and_not_treated_as_event_fact():
    receipt = _receipt()
    receipt["articles"] = [receipt["articles"][0]]
    receipt["articles"][0]["title"] = "Goalie *CONFIRMED* [fake](https://evil.example)"
    feed = build_feed(json.dumps(receipt).encode(), at=AT)
    report = render_markdown(feed)
    assert "Research headlines only" in report
    assert "NOT_SAMPLED_THIS_RUN" in report
    assert "\\*CONFIRMED\\*" in report
    assert "source instance(s)" in report
    assert "can_execute=false" in report


def test_cli_writes_both_artifacts_even_for_missing_source(monkeypatch, tmp_path):
    import sys
    j, m = tmp_path / "newsfeed.json", tmp_path / "newsfeed.md"
    monkeypatch.setattr(sys, "argv", ["feed", "--input", str(tmp_path / "absent"),
                                     "--output-json", str(j), "--output-md", str(m)])
    assert main() == 0
    result = json.loads(j.read_text())
    assert result["status"] == "SOURCE_RECEIPT_MISSING"
    assert "SOURCE_RECEIPT_MISSING" in m.read_text()
    assert result["can_execute"] is False


def test_cli_outputs_nonempty_on_real_shape_fixture(monkeypatch, tmp_path):
    import sys
    source = tmp_path / "source.json"
    receipt = _receipt()
    receipt["source_observed_at"] = datetime.now(timezone.utc).isoformat()
    for row in receipt["articles"]:
        row["source_discovered_at_claim"] = receipt["source_observed_at"]
    source.write_text(json.dumps(receipt))
    j, m = tmp_path / "newsfeed.json", tmp_path / "newsfeed.md"
    monkeypatch.setattr(sys, "argv", ["feed", "--input", str(source),
                                     "--output-json", str(j), "--output-md", str(m)])
    assert main() == 0
    assert json.loads(j.read_text())["status"] == "SOURCE_NONEMPTY_RESEARCH_ONLY"
    assert "Potential NHL goalie update" in m.read_text()



def test_workflow_module_invocation_runs_from_engine_root(tmp_path):
    # The production workflows run from artifacts/wow-engine and must use
    # -m, not a script path that shadows its parent package import.
    root = Path(__file__).resolve().parent
    j, m = tmp_path / "newsfeed.json", tmp_path / "newsfeed.md"
    result = subprocess.run(
        [sys.executable, "-m", "v17.public_news_feed_report",
         "--input", str(tmp_path / "missing"), "--output-json", str(j),
         "--output-md", str(m), "--run-id", "ci-test-1"],
        cwd=root, capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(j.read_text())["status"] == "SOURCE_RECEIPT_MISSING"
    assert "SOURCE_RECEIPT_MISSING" in m.read_text()
