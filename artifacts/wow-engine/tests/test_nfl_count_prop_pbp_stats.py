"""PBP play flag comparison remains an offline disagreement diagnostic."""
from __future__ import annotations

import gzip
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_nfl_count_prop_pbp_stats as mod
from audit_nfl_count_prop_pbp_presence import PBP_GZ_SHA256
from acquire_nfl_count_prop_sources import OFFICIAL_SIZE_BYTES


def _synthetic(tmp_path, monkeypatch, frame):
    blob = gzip.compress(frame.to_csv(index=False).encode(), mtime=0)
    path = tmp_path / "play_by_play_2024.csv.gz"
    path.write_bytes(blob)
    monkeypatch.setitem(PBP_GZ_SHA256, 2024, hashlib.sha256(blob).hexdigest())
    monkeypatch.setitem(OFFICIAL_SIZE_BYTES["pbp"], 2024, len(blob))
    return path


def _play(**changes):
    row = {
        "game_id": "2024_01_BAL_KC", "season": 2024, "week": 1,
        "season_type": "REG", "play_id": 42, "play_type": "pass",
        "pass_attempt": 1, "complete_pass": 1, "pass_touchdown": 0,
        "rush_attempt": 0, "sack": 0, "qb_spike": 0,
        "two_point_attempt": 0, "passer_player_id": "00-0032222",
        "penalty": 0, "penalty_type": "", "penalty_team": "",
        "desc": "Synthetic test play",
        "receiver_player_id": "00-0031111", "rusher_player_id": "",
    }
    row.update(changes)
    return row


def test_single_completed_pass_has_one_attempt_reception_and_target(tmp_path, monkeypatch):
    path = _synthetic(tmp_path, monkeypatch, pd.DataFrame([_play()]))
    counts, games, quality = mod.read_pinned_pbp_signals(path, 2024)
    assert games == {"2024_01_BAL_KC"}
    assert counts["attempts"][("2024_01_BAL_KC", "00-0032222")] == 1
    assert counts["completions"][("2024_01_BAL_KC", "00-0032222")] == 1
    assert counts["receptions"][("2024_01_BAL_KC", "00-0031111")] == 1
    assert counts["targets"][("2024_01_BAL_KC", "00-0031111")] == 1
    assert not counts["passing_tds"]
    assert not counts["carries"]
    assert quality["flag_nulls"]["pass_attempt"] == 0


@pytest.mark.parametrize("change", [
    {"sack": 1}, {"qb_spike": 1}, {"two_point_attempt": 1},
    {"play_type": "no_play"},
])
def test_excluded_pass_events_never_count_as_box_score_attempts(
    tmp_path, monkeypatch, change,
):
    path = _synthetic(tmp_path, monkeypatch, pd.DataFrame([_play(**change)]))
    counts, _, _ = mod.read_pinned_pbp_signals(path, 2024)
    assert sum(counts["attempts"].values()) == 0
    assert sum(counts["completions"].values()) == 0


def test_qb_spike_source_passing_attempt_remains_a_count_candidate(
    tmp_path, monkeypatch,
):
    """A spike is an NFL incompletion and must not vanish from attempt audit."""
    row = _play(play_type="qb_spike", qb_spike=1, complete_pass=0,
                receiver_player_id="")
    counts, _, info = mod.read_pinned_pbp_signals(
        _synthetic(tmp_path, monkeypatch, pd.DataFrame([row])), 2024,
    )
    assert counts["attempts"][("2024_01_BAL_KC", "00-0032222")] == 1
    assert not counts["completions"]
    assert info["administrative_play_rows"]["qb_spike_attributed"] == 1


def test_qb_kneel_is_included_only_in_research_carry_candidate(
    tmp_path, monkeypatch,
):
    """Weekly official carries include verified quarterback kneeldowns."""
    row = _play(play_id=45, play_type="qb_kneel",
                pass_attempt=0, complete_pass=0, rush_attempt=1,
                passer_player_id="", receiver_player_id="",
                rusher_player_id="00-0032222")
    counts, _, info = mod.read_pinned_pbp_signals(
        _synthetic(tmp_path, monkeypatch, pd.DataFrame([row])), 2024,
    )
    assert counts["carries"][("2024_01_BAL_KC", "00-0032222")] == 1
    assert not counts["attempts"]
    assert info["administrative_play_rows"]["qb_kneel_attributed"] == 1


def test_rush_event_counts_only_rusher(tmp_path, monkeypatch):
    row = _play(play_id=44, play_type="run", pass_attempt=0, complete_pass=0,
                rush_attempt=1, passer_player_id="", receiver_player_id="",
                rusher_player_id="00-0031111")
    counts, _, _ = mod.read_pinned_pbp_signals(
        _synthetic(tmp_path, monkeypatch, pd.DataFrame([row])), 2024,
    )
    assert counts["carries"][("2024_01_BAL_KC", "00-0031111")] == 1
    assert not counts["attempts"]


@pytest.mark.parametrize("change,code", [
    ({"week": 2}, "NFL_PROP_PBP_EVENT_SCHEMA_INVALID"),
    ({"pass_attempt": 2}, "NFL_PROP_PBP_FLAG_INVALID"),
    ({"pass_attempt": "bad"}, "NFL_PROP_PBP_FLAG_INVALID"),
    ({"play_type": ""}, "NFL_PROP_PBP_EVENT_SCHEMA_INVALID"),
])
def test_invalid_source_event_fails_closed(tmp_path, monkeypatch, change, code):
    path = _synthetic(tmp_path, monkeypatch, pd.DataFrame([_play(**change)]))
    with pytest.raises(ValueError, match=code):
        mod.read_pinned_pbp_signals(path, 2024)


def test_duplicate_same_game_play_id_fails_closed(tmp_path, monkeypatch):
    path = _synthetic(tmp_path, monkeypatch, pd.DataFrame([_play(), _play()]))
    with pytest.raises(ValueError, match="NFL_PROP_PBP_EVENT_DUPLICATE"):
        mod.read_pinned_pbp_signals(path, 2024)


def test_weekly_parity_is_diagnostic_not_a_zero_label():
    weekly = pd.DataFrame([
        {"game_id": "2024_01_BAL_KC", "player_id": "00-0032222", "position": "QB",
         "attempts": 2, "completions": 1, "passing_tds": 0,
         "receptions": 0, "targets": 0, "carries": 0},
    ])
    from collections import Counter
    signals = {name: Counter() for name in mod.SOURCE_ROLES}
    signals["attempts"][("2024_01_BAL_KC", "00-0032222")] = 1
    signals["completions"][("2024_01_BAL_KC", "00-0032222")] = 1
    result = mod.compare_weekly_source_to_pbp_signals(
        weekly, signals, {"2024_01_BAL_KC"},
    )
    assert result["by_source_stat"]["attempts"]["pbp_signal_lower"] == 1
    assert result["by_source_stat"]["completions"]["pbp_signal_equal"] == 1
    assert result["status"] == "HOLD_PBP_STAT_SIGNALS_NOT_SETTLEMENT"
    assert result["no_missing_weekly_label_imputed"] is True
    assert result["fitted_specialist_certified"] is False
    assert result["can_execute"] is False


def test_negative_weekly_count_rejected():
    from collections import Counter
    weekly = pd.DataFrame([{
        "game_id": "2024_01_BAL_KC", "player_id": "00-0032222", "position": "QB",
        **{name: ( -1 if name == "attempts" else 0) for name in mod.SOURCE_ROLES},
    }])
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_COUNT_INVALID:attempts"):
        mod.compare_weekly_source_to_pbp_signals(
            weekly, {name: Counter() for name in mod.SOURCE_ROLES},
            {"2024_01_BAL_KC"},
        )


def test_quality_aggregation_retains_new_administrative_play_dimensions():
    """Regression: real source receipt must not HOLD on new quality keys."""
    aggregate = {"flag_nulls": {}, "unattributed": {}}
    mod.add_quality_totals(aggregate, {
        "flag_nulls": {"pass_attempt": 0},
        "administrative_play_rows": {"qb_spike_rows": 1, "qb_kneel_rows": 2},
    })
    mod.add_quality_totals(aggregate, {
        "administrative_play_rows": {"qb_spike_rows": 4, "qb_kneel_rows": 3},
        "unattributed": {"targets": 2},
    })
    assert aggregate["administrative_play_rows"] == {
        "qb_spike_rows": 5, "qb_kneel_rows": 5,
    }
    assert aggregate["unattributed"]["targets"] == 2


def test_actorless_flagged_opportunity_is_a_typed_source_hold(tmp_path, monkeypatch):
    """Never omit a flagged pass/rush solely because all actor IDs are blank."""
    row = _play(passer_player_id="", receiver_player_id="", rusher_player_id="")
    source = _synthetic(tmp_path, monkeypatch, pd.DataFrame([row]))
    with pytest.raises(ValueError, match="NFL_PROP_PBP_ATTRIBUTION_INCOMPLETE:2024:1"):
        mod.read_pinned_pbp_signals(source, 2024)


def test_actorless_no_play_is_not_an_observed_stat_event(tmp_path, monkeypatch):
    """Wiped-out plays remain excluded without manufacturing an opportunity."""
    row = _play(play_type="no_play", passer_player_id="",
                receiver_player_id="", rusher_player_id="")
    source = _synthetic(tmp_path, monkeypatch, pd.DataFrame([row]))
    signals, games, quality = mod.read_pinned_pbp_signals(source, 2024)
    assert games == {"2024_01_BAL_KC"}
    assert sum(sum(count.values()) for count in signals.values()) == 0
    assert quality["unattributed"]["attempts"] == 0


def test_whitespace_equivalent_event_ids_are_rejected_as_duplicates(
    tmp_path, monkeypatch,
):
    """Normalized game+play identity must be exact once even across whitespace."""
    rows = [_play(), _play(game_id=" 2024_01_BAL_KC ", play_id=" 42 ")]
    source = _synthetic(tmp_path, monkeypatch, pd.DataFrame(rows))
    with pytest.raises(ValueError, match="NFL_PROP_PBP_EVENT_DUPLICATE"):
        mod.read_pinned_pbp_signals(source, 2024)


def test_residual_play_type_evidence_never_corrects_weekly_counts(
    tmp_path, monkeypatch,
):
    row = _play(play_id=45, play_type="no_play", pass_attempt=1,
                complete_pass=0)
    source = _synthetic(tmp_path, monkeypatch, pd.DataFrame([row]))
    result = mod.inspect_pinned_residual_actor_plays(
        source, 2024, "2024_01_BAL_KC", "00-0032222", "attempts",
    )
    assert result["actor_play_type_counts"] == {"no_play": 1}
    assert result["actor_flagged_event_rows"] == 1
    assert result["flagged_unmatched_play_types"] == {"no_play": 1}
    assert result["flagged_excluded_examples"][0]["penalty_flag"] == "0"
    assert result["flagged_excluded_examples"][0]["play_description_excerpt"] == "Synthetic test play"
    assert result["status"] == "HOLD_PBP_STAT_DISAGREEMENT_UNRESOLVED"
    assert result["missing_play_imputed"] is False
    assert result["can_execute"] is False


def test_residual_evidence_rejects_unapproved_stat(tmp_path):
    with pytest.raises(ValueError, match="NFL_PROP_PBP_STAT_COLUMNS_MISSING"):
        mod.inspect_pinned_residual_actor_plays(
            tmp_path / "does-not-matter.gz", 2024,
            "2024_01_BAL_KC", "00-0032222", "passing_tds",
        )


def test_residual_penalty_context_is_bounded_and_never_a_stat_fix(
    tmp_path, monkeypatch,
):
    """Pinned no_play penalties remain original evidence; no count correction."""
    row = _play(
        play_id=2274, play_type="no_play", pass_attempt=1, complete_pass=0,
        penalty=1, penalty_type="Offensive Holding", penalty_team="PIT",
        desc="Synthetic penalty nullifies the play; game count is not implied.",
    )
    source = _synthetic(tmp_path, monkeypatch, pd.DataFrame([row]))
    result = mod.inspect_pinned_residual_actor_plays(
        source, 2024, "2024_01_BAL_KC", "00-0032222", "attempts",
    )
    example = result["flagged_excluded_examples"][0]
    assert example["penalty_type"] == "Offensive Holding"
    assert example["penalty_team"] == "PIT"
    assert example["penalty_flag"] == "1"
    assert result["status"] == "HOLD_PBP_STAT_DISAGREEMENT_UNRESOLVED"
    assert result["missing_play_imputed"] is False
    assert result["can_execute"] is False


def test_spike_actor_does_not_create_receiver_targets_or_receptions(
    tmp_path, monkeypatch,
):
    """Spike is one pass attempt, never a receiver count even with actor ID."""
    row = _play(play_type="qb_spike", qb_spike=1, complete_pass=0,
                receiver_player_id="00-0031111")
    counts, _, _ = mod.read_pinned_pbp_signals(
        _synthetic(tmp_path, monkeypatch, pd.DataFrame([row])), 2024,
    )
    assert sum(counts["attempts"].values()) == 1
    assert sum(counts["targets"].values()) == 0
    assert sum(counts["receptions"].values()) == 0


def test_actorless_invalid_rushing_flag_rejected_before_filter(tmp_path, monkeypatch):
    row = _play(pass_attempt=0, rush_attempt=2, passer_player_id="",
                receiver_player_id="", rusher_player_id="")
    with pytest.raises(ValueError, match="NFL_PROP_PBP_FLAG_INVALID:rush_attempt"):
        mod.read_pinned_pbp_signals(
            _synthetic(tmp_path, monkeypatch, pd.DataFrame([row])), 2024,
        )


def test_whitespace_spike_passer_not_marked_attributed(tmp_path, monkeypatch):
    row = _play(play_type="qb_spike", qb_spike=1, complete_pass=0,
                passer_player_id="   ", receiver_player_id="00-0031111")
    counts, _, info = mod.read_pinned_pbp_signals(
        _synthetic(tmp_path, monkeypatch, pd.DataFrame([row])), 2024,
    )
    assert info["administrative_play_rows"]["qb_spike_rows"] == 1
    assert info["administrative_play_rows"]["qb_spike_attributed"] == 0
    assert info["unattributed"]["attempts"] == 1
    assert sum(counts["attempts"].values()) == 0


def test_residual_sack_is_recorded_as_excluded_flagged_pass(tmp_path, monkeypatch):
    row = _play(play_id=777, sack=1, play_type="pass", pass_attempt=1)
    result = mod.inspect_pinned_residual_actor_plays(
        _synthetic(tmp_path, monkeypatch, pd.DataFrame([row])),
        2024, "2024_01_BAL_KC", "00-0032222", "attempts",
    )
    assert result["flagged_unmatched_play_types"] == {"pass": 1}
    assert result["flagged_excluded_examples"][0]["sack"] == "1"
    assert result["missing_play_imputed"] is False
    assert result["status"] == "HOLD_PBP_STAT_DISAGREEMENT_UNRESOLVED"
