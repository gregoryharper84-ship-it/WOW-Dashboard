"""Offline adversarial coverage for NBA official report link discovery."""
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.nba_official_injury_index import NBAInjuryIndexHold, select_injury_report_link

INDEX = "https://official.nba.com/nba-injury-report-2026-27-season/"
PREFIX = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-18_"
CAPTURE = datetime(2026, 11, 18, 16, 0, tzinfo=timezone.utc)  # 11 AM ET
TIPOFF = datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc)


def html(*hrefs):
    return ("<html><body>"+"".join(f'<a href="{href}">official PDF</a>' for href in hrefs)+"</body></html>").encode()


def select(content, **kw):
    return select_injury_report_link(content, target_date=date(2026, 11, 18),
                                    captured_at=CAPTURE, event_start_utc=TIPOFF,
                                    **kw)


def holds(code, action):
    with pytest.raises(NBAInjuryIndexHold) as exc:
        action()
    assert exc.value.code == code


def test_selects_latest_not_future_report_with_readonly_evidence():
    raw = html(PREFIX + "09_15AM.pdf", PREFIX + "10_30AM.pdf", PREFIX + "01_00PM.pdf")
    result = select(raw)
    assert result["status"] == "NBA_OFFICIAL_REPORT_LINK_DISCOVERED_RESEARCH_ONLY"
    assert result["report_pdf_url"] == PREFIX + "10_30AM.pdf"
    assert result["report_time_inferred_utc"] == "2026-11-18T15:30:00Z"
    assert result["index_html_sha256"] == sha256(raw).hexdigest()
    assert result["source_index_url"] == INDEX
    assert result["discovered_report_count"] == 3
    for key in ("report_content_verified", "player_status_verified", "lineup_verified",
                "pregame_timestamp_attested", "training_authorized",
                "probability_publishable", "production_write_authorized", "can_execute"):
        assert result[key] is False
    assert result["timestamp_attestation"] == "NOT_ESTABLISHED"


def test_excludes_post_tipoff_even_when_prior_report_exists():
    raw = html(PREFIX + "09_15AM.pdf", PREFIX + "10_30AM.pdf")
    report = select_injury_report_link(raw, target_date=date(2026, 11, 18),
        captured_at=CAPTURE, event_start_utc=datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc))
    assert report["report_pdf_url"] == PREFIX + "09_15AM.pdf"


def test_all_future_reports_hold_with_no_fake_empty_injury_status():
    holds("NBA_WEB_INJURY_REPORT_NOT_CAPTURED_YET", lambda: select(html(PREFIX + "01_00PM.pdf")))


def test_no_matching_date_is_missing_link_not_no_injuries():
    holds("NBA_WEB_INJURY_REPORT_LINK_MISSING", lambda: select(html(
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-17_09_15AM.pdf")))


def test_untrusted_lookalike_report_url_is_rejected():
    for bad in ("http://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-18_10_30AM.pdf",
                "https://ak-static.cms.nba.com.evil.test/referee/injury/Injury-Report_2026-11-18_10_30AM.pdf",
                "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-18_10_30AM.pdf?token=1",
                "https://user@ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-18_10_30AM.pdf"):
        holds("NBA_WEB_INJURY_INDEX_URL_UNTRUSTED", lambda bad=bad: select(html(bad)))


def test_duplicate_link_is_idempotent_and_multiple_different_paths_at_same_time_hold():
    url = PREFIX + "10_30AM.pdf"
    assert select(html(url, url))["discovered_report_count"] == 1
    # Invalid link with a path/query suffix must fail rather than create an ambiguity.
    holds("NBA_WEB_INJURY_INDEX_URL_UNTRUSTED", lambda: select(html(url, url + "#download")))


def test_invalid_capture_time_and_inconsistent_event_clock_fail():
    raw = html(PREFIX + "10_30AM.pdf")
    holds("NBA_WEB_INJURY_INDEX_CLOCK_INVALID", lambda: select_injury_report_link(
        raw, target_date=date(2026, 11, 18), captured_at=datetime(2026, 11, 18), event_start_utc=TIPOFF))
    holds("NBA_WEB_INJURY_INDEX_CLOCK_INVALID", lambda: select_injury_report_link(
        raw, target_date=date(2026, 11, 18), captured_at=CAPTURE,
        event_start_utc=datetime(2026, 11, 17, 23, 0, tzinfo=timezone.utc)))


def test_invalid_or_oversized_html_fails_before_parsing():
    holds("NBA_WEB_INJURY_INDEX_CAPTURE_INVALID", lambda: select(b""))
    holds("NBA_WEB_INJURY_INDEX_CAPTURE_INVALID", lambda: select(b"x" * 600_001))
    holds("NBA_WEB_INJURY_INDEX_CAPTURE_INVALID", lambda: select(b"\xff\xfe"))


def test_no_current_pretip_report_holds_not_invents_status():
    raw = html(PREFIX + "10_30AM.pdf")
    holds("NBA_WEB_INJURY_REPORT_NOT_PREGAME", lambda: select_injury_report_link(
        raw, target_date=date(2026, 11, 18),
        captured_at=datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc),
        event_start_utc=datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc)))
