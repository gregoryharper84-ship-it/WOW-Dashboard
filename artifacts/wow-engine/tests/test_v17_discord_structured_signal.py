"""Synthetic-only tests; do not load private Discord message content."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.discord_structured_signal import accept_structured_claim, SignalRejected

NOW = datetime(2026, 11, 20, 16, 0, tzinfo=timezone.utc)
ALLOWED = frozenset({("111", "222")})


def sample(**overrides):
    data = {
        "guild_id": "111", "channel_id": "222", "message_id": "333",
        "claim_index": 0, "source_created_at": "2026-11-20T15:00:00Z",
        "event_start_at": "2026-11-20T20:00:00Z",
        "sport": "NBA", "event_id": "nba:20261120:001",
        "kind": "PRE_GAME_PICK", "market": "PLAYER_PROP",
        "selection": "OVER", "line": "5.5", "player": "Test Player",
        "stat": "assists", "book": "Test Book", "american_odds": -110,
    }
    data.update(overrides)
    return data


def gate(data=None, authorized=True, allowlist=ALLOWED, observed_at=NOW):
    return accept_structured_claim(
        sample() if data is None else data,
        observed_at=observed_at,
        allowed_channels=allowlist,
        source_use_authorized=authorized,
    )


def rejected(data=None, *, authorized=True, allowlist=ALLOWED, observed_at=NOW, code):
    with pytest.raises(SignalRejected, match=code):
        gate(data, authorized=authorized, allowlist=allowlist, observed_at=observed_at)


def test_valid_pregame_research_not_certified_and_no_raw_message():
    record = gate()
    assert record["research_status"] == "UNVERIFIED_RESEARCH_ONLY"
    assert record["kind"] == "PRE_GAME_PICK"
    assert record["line"] == "5.5"
    assert record["verified_result"] is None
    assert record["probability_authority"] is False
    assert record["certified_model"] is False
    assert record["can_execute"] is False
    assert "content" not in record and "attachment" not in record
    assert len(record["claim_fingerprint"]) == 64


def test_explicit_source_permission_fail_closed():
    rejected(code="DISCORD_SOURCE_PERMISSION_REQUIRED", authorized=False)
    rejected(code="DISCORD_SOURCE_PERMISSION_REQUIRED", allowlist=frozenset())


def test_user_supplied_permissions_and_raw_text_rejected():
    rejected(sample(source_use_authorized=True), code="DISCORD_STRUCTURED_UNEXPECTED_FIELDS")
    rejected(sample(content="paid private Discord message"), code="DISCORD_STRUCTURED_UNEXPECTED_FIELDS")
    rejected(sample(model_probability=.99), code="DISCORD_STRUCTURED_UNEXPECTED_FIELDS")


def test_other_channels_blocked():
    rejected(sample(channel_id="223"), code="DISCORD_CHANNEL_NOT_ALLOWLISTED")


def test_pregame_capture_after_start_fails():
    rejected(sample(event_start_at="2026-11-20T15:59:00Z"), code="DISCORD_POST_START_NOT_PROSPECTIVE")


def test_past_posted_pick_cannot_be_promoted_to_pregame():
    rejected(sample(source_created_at="2026-11-20T22:00:00Z"), code="DISCORD_SOURCE_TIME_IN_FUTURE")


def test_edited_postgame_pick_not_backdated():
    rejected(sample(source_edited_at="2026-11-20T16:05:00Z"), code="DISCORD_SOURCE_REVISION_TIME_INVALID")


def test_recap_is_reported_only():
    record = gate(sample(
        kind="POST_GAME_RECAP", event_start_at="2026-11-19T20:00:00Z",
        reported_outcome="WIN"))
    assert record["reported_outcome_unverified"] == "WIN"
    assert record["verified_result"] is None


def test_recap_without_claimed_outcome_rejected():
    rejected(sample(kind="POST_GAME_RECAP", event_start_at="2026-11-19T20:00:00Z"),
             code="DISCORD_RECAP_OUTCOME_REQUIRED")


def test_pregame_cannot_claim_a_result():
    rejected(sample(reported_outcome="WIN"), code="DISCORD_FUTURE_RESULT_INVALID")


def test_player_prop_line_and_stat_required():
    rejected(sample(line=None), code="DISCORD_STRUCTURED_LINE_REQUIRED")
    rejected(sample(player=None), code="DISCORD_STRUCTURED_FIELD_INVALID")
    rejected(sample(stat="not a valid stat!"), code="DISCORD_STAT_INVALID")


def test_wrong_market_direction_or_probability_authority_rejected():
    rejected(sample(selection="TEAM_X"), code="DISCORD_PROP_DIRECTION_INVALID")
    rejected(sample(certified_model=True), code="DISCORD_STRUCTURED_UNEXPECTED_FIELDS")


def test_unsupported_sport_or_event_identity_rejected():
    rejected(sample(sport="FANTASY"), code="DISCORD_STRUCTURED_ROUTE_UNSUPPORTED")
    rejected(sample(event_id="an event with spaces"), code="DISCORD_EVENT_ID_INVALID")


def test_no_line_for_moneyline():
    rejected(sample(market="MONEYLINE"), code="DISCORD_MONEYLINE_HAS_LINE")
    record = gate(sample(market="MONEYLINE", line=None,
                         player=None, stat=None, selection="TEAM_A"))
    assert record["market"] == "MONEYLINE" and record["line"] is None


def test_outlier_odds_rejected():
    rejected(sample(american_odds=0), code="DISCORD_ODDS_INVALID")
    rejected(sample(american_odds=50), code="DISCORD_ODDS_INVALID")
    rejected(sample(american_odds="100"), code="DISCORD_ODDS_INVALID")


def test_fingerprint_is_stable_and_revision_sensitive():
    before = gate(sample())
    later = gate(sample(), observed_at=NOW + timedelta(minutes=1))
    assert before["claim_fingerprint"] == later["claim_fingerprint"]
    edited = gate(sample(source_edited_at="2026-11-20T15:30:00Z"))
    assert edited["claim_fingerprint"] != before["claim_fingerprint"]


def test_naive_source_clock_rejected():
    rejected(sample(source_created_at="2026-11-20T15:00:00"),
             code="DISCORD_STRUCTURED_TIME_INVALID")
