from __future__ import annotations

from datetime import datetime, timezone

from mlb_pitcher_k_risk_guard import (
    ROLE_COMPATIBLE,
    ROLE_INCOMPATIBLE,
    ROLE_SAMPLE_THIN,
    TAG_LOW_LINE_MORE_TAIL_CONTRADICTION,
    build_recent_role_profile,
    low_line_more_tail_risk,
)


def _outs(ip):
    whole, frac = str(ip).split(".")
    return int(whole) * 3 + int(frac)


def _int(value, *, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _split(date, *, started, ip, bf, pitches=20, strikeouts=0):
    return (
        2026,
        {
            "date": date,
            "stat": {
                "gamesStarted": 1 if started else 0,
                "inningsPitched": ip,
                "battersFaced": bf,
                "numberOfPitches": pitches,
                "strikeOuts": strikeouts,
            },
        },
    )


def test_newcomb_like_recent_opener_usage_is_incompatible_with_starter_k_model():
    # Six most recent official appearances are overwhelmingly opener/short-relief
    # shaped even though older full starts exist. The role diagnostic must inspect
    # all recent appearances rather than silently filtering to starts.
    splits = [
        _split("2026-09-13", started=False, ip="1.0", bf=3),
        _split("2026-09-11", started=False, ip="1.1", bf=5),
        _split("2026-09-09", started=False, ip="1.1", bf=6),
        _split("2026-09-07", started=False, ip="1.2", bf=7),
        _split("2026-09-05", started=False, ip="2.0", bf=9),
        _split("2026-09-03", started=True, ip="2.0", bf=9),
    ]
    # Older starts are deliberately present to prove they do not erase the
    # current-role change.
    for day in range(1, 11):
        splits.append(_split(f"2026-08-{day:02d}", started=True, ip="5.0", bf=21, strikeouts=5))

    profile = build_recent_role_profile(
        splits,
        event_start=datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc),
        outs_from_ip=_outs,
        int_value=_int,
    )

    assert profile["status"] == ROLE_INCOMPATIBLE
    assert profile["appearances_found"] == 6
    assert profile["start_share"] < 0.50
    assert profile["short_appearance_share"] >= 0.50
    assert profile["source_semantics"] == "OFFICIAL_ALL_RECENT_PITCHING_APPEARANCES_NOT_STARTS_ONLY"


def test_normal_recent_starter_usage_preserves_existing_starter_model_path():
    splits = [
        _split("2026-09-13", started=True, ip="6.0", bf=25),
        _split("2026-09-10", started=True, ip="5.2", bf=23),
        _split("2026-09-07", started=True, ip="6.1", bf=26),
        _split("2026-09-04", started=True, ip="5.0", bf=22),
        _split("2026-09-01", started=True, ip="6.0", bf=24),
        _split("2026-08-28", started=True, ip="5.1", bf=21),
    ]
    profile = build_recent_role_profile(
        splits,
        event_start=datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc),
        outs_from_ip=_outs,
        int_value=_int,
    )
    assert profile["status"] == ROLE_COMPATIBLE
    assert profile["start_share"] == 1.0
    assert profile["short_appearance_share"] == 0.0


def test_thin_recent_role_sample_does_not_invent_incompatibility():
    splits = [
        _split("2026-09-13", started=False, ip="1.0", bf=4),
        _split("2026-09-11", started=False, ip="1.0", bf=4),
        _split("2026-09-09", started=True, ip="5.0", bf=20),
    ]
    profile = build_recent_role_profile(
        splits,
        event_start=datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc),
        outs_from_ip=_outs,
        int_value=_int,
    )
    assert profile["status"] == ROLE_SAMPLE_THIN


def test_mize_like_low_line_more_zero_one_k_tail_caps_recommendation_confidence():
    # Mize-like recent-start shape: three of seven starts at 0/1 K. This is not
    # converted into a replacement probability; it is a contradiction against
    # advertising MORE 2.5 as HIGH/model-qualified.
    risk = low_line_more_tail_risk([1, 6, 3, 0, 1, 4, 7], line=2.5, direction="MORE")
    assert risk["applies"] is True
    assert risk["flag"] == TAG_LOW_LINE_MORE_TAIL_CONTRADICTION
    assert risk["zero_or_one_k_count"] == 3
    assert risk["zero_or_one_k_rate"] > 0.40


def test_low_k_tail_does_not_penalize_opposite_direction_or_higher_line_by_rule():
    history = [1, 6, 3, 0, 1, 4, 7]
    assert low_line_more_tail_risk(history, line=2.5, direction="LESS")["applies"] is False
    assert low_line_more_tail_risk(history, line=4.5, direction="MORE")["applies"] is False
