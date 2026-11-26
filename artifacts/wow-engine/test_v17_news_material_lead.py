"""Material-lead query/title preference for GDELT discovery relevance."""
from __future__ import annotations

from datetime import datetime, timezone

from v17.news_material_lead import (
    is_material_lead_title,
    material_lead_count,
    material_query_for,
    prefer_material_leads,
)
from v17.public_gdelt_discovery import query_for, selection


def test_fashion_and_schedule_headlines_are_not_material_leads():
    assert is_material_lead_title("Eight best-dressed NFL players") is False
    assert is_material_lead_title("Thanksgiving NFL marathon schedule") is False
    assert is_material_lead_title("Colts QB ruled out vs Texans") is True
    assert is_material_lead_title("Islanders confirm starting goalie") is True


def test_prefer_material_leads_displaces_noise_within_limit():
    rows = [
        {"title": "Eight best-dressed NFL players", "source_url": "https://a.example/1"},
        {"title": "Thanksgiving NFL marathon", "source_url": "https://a.example/2"},
        {"title": "Colts QB ruled out vs Texans", "source_url": "https://a.example/3"},
    ]
    ranked = prefer_material_leads(rows, limit=2)
    assert ranked[0]["title"].startswith("Colts QB")
    assert ranked[0]["material_lead"] is True
    assert material_lead_count(ranked) == 1
    assert len(ranked) == 2


def test_doc_queries_rotate_material_topics_by_slot():
    assert "injury" in material_query_for("NFL", slot_index=0).lower()
    assert "lineup" in material_query_for("NFL", slot_index=1).lower()
    assert "ruled out" in material_query_for("NFL", slot_index=2).lower()
    at = datetime(2026, 11, 26, 0, tzinfo=timezone.utc)
    sport, query = selection(at)
    assert sport in {"americanfootball_nfl", "basketball_nba", "icehockey_nhl",
                     "americanfootball_ncaaf", "basketball_ncaab", "soccer",
                     "mma", "boxing", "tennis", "golf", "cricket"}
    assert query == query_for(sport, at)
    assert "(" in query and ")" in query


def test_query_for_rejects_naive_datetime():
    import pytest

    with pytest.raises(ValueError, match="GDELT_SLOT_REQUIRES_UTC_AWARE_TIME"):
        query_for("americanfootball_nfl", datetime(2026, 11, 26, 0))


def test_topics_rotate_independently_when_active_count_divisible_by_three():
    """February has 9 active sports (divisible by 3). Same sport must visit all topics."""
    from datetime import timedelta

    from v17.news_material_lead import MATERIAL_TOPIC_CLAUSES
    from v17.public_gdelt_discovery import active_sport_keys

    start = datetime(2026, 2, 1, 0, tzinfo=timezone.utc)
    active = active_sport_keys(start)
    assert len(active) % len(MATERIAL_TOPIC_CLAUSES) == 0
    # Pick NHL if present; otherwise the first active sport.
    target = "icehockey_nhl" if "icehockey_nhl" in active else active[0]
    topics: set[str] = set()
    # Walk enough 6h slots to cover multiple full sport cycles.
    for i in range(len(active) * len(MATERIAL_TOPIC_CLAUSES) * 2):
        at = start + timedelta(hours=6 * i)
        sport, query = selection(at)
        if sport != target:
            continue
        topics.add(query)
    assert len(topics) == len(MATERIAL_TOPIC_CLAUSES)
    joined = " | ".join(sorted(topics)).lower()
    assert "injury" in joined
    assert "lineup" in joined
    assert "ruled out" in joined
