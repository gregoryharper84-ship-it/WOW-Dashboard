from datetime import datetime, timezone

import v17.prop_forward_cohort_runtime as runtime
from v17.prop_forward_cohort_thesis_dedupe import _cohort_counts, thesis_key


def test_thesis_key_normalizes_player_case_whitespace_and_line():
    a = {"event_id": "MLB:1", "player": " Cam   Schlittler ", "stat_type": "pitcher_strikeouts", "line": 5.5}
    b = {"event_id": "MLB:1", "player": "cam schlittler", "stat_type": "PITCHER_STRIKEOUTS", "line": "5.50"}
    assert thesis_key(a) == thesis_key(b)


def test_more_less_and_refreshed_snapshots_count_as_one_independent_thesis():
    rows = [
        {
            "prediction_id": "s1-more", "source_snapshot_id": "s1", "direction": "MORE",
            "event_id": "MLB:1", "player": "Cam Schlittler", "stat_type": "PITCHER_STRIKEOUTS", "line": 5.5,
        },
        {
            "prediction_id": "s1-less", "source_snapshot_id": "s1", "direction": "LESS",
            "event_id": "MLB:1", "player": "Cam Schlittler", "stat_type": "PITCHER_STRIKEOUTS", "line": 5.5,
        },
        {
            "prediction_id": "s2-more", "source_snapshot_id": "s2", "direction": "MORE",
            "event_id": "MLB:1", "player": "Cam Schlittler", "stat_type": "PITCHER_STRIKEOUTS", "line": "5.50",
        },
        {
            "prediction_id": "s3-less", "source_snapshot_id": "s3", "direction": "LESS",
            "event_id": "MLB:1", "player": " cam  schlittler ", "stat_type": "PITCHER_STRIKEOUTS", "line": 5.5,
        },
    ]
    assert _cohort_counts(rows, {"s1-more", "s3-less"}) == (1, 1)


def test_distinct_event_or_line_remains_distinct_observation():
    rows = [
        {"prediction_id": "a", "direction": "MORE", "event_id": "MLB:1", "player": "P", "stat_type": "PITCHER_STRIKEOUTS", "line": 5.5},
        {"prediction_id": "b", "direction": "MORE", "event_id": "MLB:1", "player": "P", "stat_type": "PITCHER_STRIKEOUTS", "line": 6.5},
        {"prediction_id": "c", "direction": "MORE", "event_id": "MLB:2", "player": "P", "stat_type": "PITCHER_STRIKEOUTS", "line": 5.5},
    ]
    assert _cohort_counts(rows, {"a", "b", "c"}) == (3, 3)


def test_missing_thesis_identity_is_not_counted_toward_readiness():
    rows = [{"prediction_id": "bad", "direction": "MORE", "event_id": "MLB:1", "player": "P", "stat_type": "PITCHER_STRIKEOUTS", "line": None}]
    assert _cohort_counts(rows, {"bad"}) == (0, 0)


def test_runtime_overrides_are_installed():
    assert runtime._cohort_counts is _cohort_counts
    assert runtime._reconcile_capability.__module__ == "v17.prop_forward_cohort_thesis_dedupe"
    assert runtime._eligible_snapshots.__module__ == "v17.prop_forward_cohort_thesis_dedupe"


def test_same_thesis_written_as_integer_and_decimal_line_collapses_to_one_key():
    """A line carried as 5 and as "5.0" is one thesis, not two observations."""
    integer_line = {"event_id": "MLB:1", "player": "Cam Schlittler", "stat_type": "PITCHER_STRIKEOUTS", "line": 5}
    decimal_line = {"event_id": "MLB:1", "player": "Cam Schlittler", "stat_type": "PITCHER_STRIKEOUTS", "line": "5.0"}
    assert thesis_key(integer_line) == thesis_key(decimal_line)
    assert _cohort_counts(
        [
            {"prediction_id": "a", "direction": "MORE", **integer_line},
            {"prediction_id": "b", "direction": "MORE", **decimal_line},
        ],
        {"a", "b"},
    ) == (1, 1)


def test_nearby_lines_are_not_collapsed_by_line_normalisation():
    """Normalisation must not merge genuinely different lines."""
    base = {"event_id": "MLB:1", "player": "Cam Schlittler", "stat_type": "PITCHER_STRIKEOUTS"}
    assert thesis_key({**base, "line": 5.5}) != thesis_key({**base, "line": 5.0})
    assert thesis_key({**base, "line": "5.50"}) != thesis_key({**base, "line": "6.5"})
