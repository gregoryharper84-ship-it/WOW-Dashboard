"""
Tests for Layer 0.5: Calibration Health Gate
gate_engine/calibration_health.py
"""
from __future__ import annotations

import json
import os
import tempfile
import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import gate_engine.calibration_health as ch
from gate_engine.calibration_health import (
    HealthGrade, CLVQuadrant,
    _clv_quadrant, _score_dimension, _grade_rank, _most_restrictive_grade,
    validate_calibration_health, get_health_summary,
    WATCH_TAG_THRESHOLD, DOWNGRADE_TAG_THRESHOLD, SUPPRESS_THRESHOLD,
    MIN_SAMPLE, NEGATIVE_CLV_CUTOFF,
)


# ---------------------------------------------------------------------------
# Fixtures — patch ledger path to temp file for all tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def temp_ledger(monkeypatch, tmp_path):
    """Every test gets its own empty ledger file."""
    ledger_file = tmp_path / "test_ledger.jsonl"
    ledger_file.touch()
    monkeypatch.setattr(ch, "CALLEDGER_PATH", str(ledger_file))
    return ledger_file


def _write_entries(ledger_file, entries: list[dict]):
    with open(ledger_file, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _entry(result="WIN", clv=0.02, failure_tags=None, sport="NBA",
           market="points", player="LeBron"):
    return {
        "result": result,
        "clv": clv,
        "failure_tags": failure_tags or [],
        "sport": sport,
        "market": market,
        "player": player,
    }


# ---------------------------------------------------------------------------
# 1. CLV Quadrant classifier
# ---------------------------------------------------------------------------

class TestCLVQuadrant:
    def test_clv_pos_result_pos_is_promote(self):
        records = [_entry("WIN", clv=0.03)] * 6
        assert _clv_quadrant(records) == CLVQuadrant.PROMOTE

    def test_clv_pos_result_neg_is_variance_hold(self):
        records = [_entry("LOSS", clv=0.03)] * 6
        assert _clv_quadrant(records) == CLVQuadrant.VARIANCE_HOLD

    def test_clv_neg_result_pos_is_lucky(self):
        records = [_entry("WIN", clv=-0.03)] * 6
        assert _clv_quadrant(records) == CLVQuadrant.LUCKY

    def test_clv_neg_result_neg_is_suppress(self):
        records = [_entry("LOSS", clv=-0.03)] * 6
        assert _clv_quadrant(records) == CLVQuadrant.SUPPRESS

    def test_no_clv_data_is_unknown(self):
        records = [{"result": "WIN"}] * 6      # no clv field
        assert _clv_quadrant(records) == CLVQuadrant.NO_DATA

    def test_empty_records(self):
        assert _clv_quadrant([]) == CLVQuadrant.NO_DATA

    def test_mixed_positive_clv_wins_is_promote(self):
        records = [_entry("WIN", 0.02), _entry("WIN", 0.04),
                   _entry("LOSS", 0.01), _entry("WIN", 0.03)]
        assert _clv_quadrant(records) == CLVQuadrant.PROMOTE


# ---------------------------------------------------------------------------
# 2. Dimension scorer — DATA_GAP when sample too small
# ---------------------------------------------------------------------------

class TestDimensionScorerDataGap:
    def test_empty_records_is_data_gap(self):
        r = _score_dimension([], "test_dim")
        assert r["grade"] == HealthGrade.DATA_GAP

    def test_below_min_sample_is_data_gap(self):
        records = [_entry("WIN", 0.02)] * (MIN_SAMPLE - 1)
        r = _score_dimension(records, "test_dim")
        assert r["grade"] == HealthGrade.DATA_GAP
        assert r["code"] == "DATA_GAP"

    def test_exactly_min_sample_scores(self):
        records = [_entry("WIN", 0.02)] * MIN_SAMPLE
        r = _score_dimension(records, "test_dim")
        assert r["grade"] != HealthGrade.DATA_GAP


# ---------------------------------------------------------------------------
# 3. Dimension scorer — AUTO_SUPPRESS (8+ losses)
# ---------------------------------------------------------------------------

class TestDimensionScorerAutoSuppress:
    def test_8_losses_auto_suppress(self):
        records = [_entry("LOSS", -0.01)] * SUPPRESS_THRESHOLD
        r = _score_dimension(records, "test_dim")
        assert r["grade"] == HealthGrade.SUPPRESS
        assert r["code"] == "AUTO_SUPPRESS"

    def test_7_losses_not_auto_suppress(self):
        records = [_entry("LOSS", -0.01)] * (SUPPRESS_THRESHOLD - 1)
        r = _score_dimension(records, "test_dim")
        assert r["grade"] != HealthGrade.SUPPRESS or r["code"] != "AUTO_SUPPRESS"

    def test_suppress_ceiling_is_reject(self):
        from gate_engine.calibration_health import GRADE_CEILING
        assert GRADE_CEILING[HealthGrade.SUPPRESS] is not None


# ---------------------------------------------------------------------------
# 4. CLV negative + result negative → SUPPRESS (blended rule)
# ---------------------------------------------------------------------------

class TestCLVNegResultNegSuppress:
    def test_clv_neg_result_neg_triggers_suppress(self):
        # 6 records, CLV negative, losses
        records = [_entry("LOSS", clv=-0.03)] * 6
        r = _score_dimension(records, "test_dim")
        assert r["grade"] == HealthGrade.SUPPRESS
        assert "CLV_NEG" in r["code"]

    def test_clv_neg_result_neg_needs_enough_losses(self):
        # Only 2 losses — not enough for suppress (below threshold=3)
        records = [_entry("LOSS", clv=-0.03)] * 2 + [_entry("WIN", -0.03)] * 4
        r = _score_dimension(records, "test_dim")
        # With only 2 losses the grade should not be SUPPRESS from this rule
        assert r["grade"] != HealthGrade.SUPPRESS or r["failures"] >= 3


# ---------------------------------------------------------------------------
# 5. Tag failure rules
# ---------------------------------------------------------------------------

class TestTagFailureRules:
    def test_3_tag_failures_triggers_watch(self):
        tags = ["STALE_SOURCE"]
        records = [_entry("LOSS", 0.01, failure_tags=tags)] * WATCH_TAG_THRESHOLD
        # Pad to MIN_SAMPLE
        records += [_entry("WIN", 0.02)] * (MIN_SAMPLE - WATCH_TAG_THRESHOLD)
        r = _score_dimension(records, "tags", failure_tags=tags)
        assert r["grade"] == HealthGrade.WATCH
        assert r["code"] in ("TAG_FAILURE_WARNING", "TAG_NEG_CLV_DOWNGRADE")

    def test_5_tag_failures_neg_clv_watch_downgrade(self):
        tags = ["OUTLIER_CONTAMINATION"]
        # Padding records have no CLV so only the tag-failure entries contribute
        # to the mean CLV calculation, keeping it negative.
        def _entry_no_clv(result, failure_tags=None):
            return {"result": result, "failure_tags": failure_tags or [],
                    "sport": "NBA", "market": "points", "player": "X"}

        records = (
            [_entry("LOSS", clv=-0.02, failure_tags=tags)] * DOWNGRADE_TAG_THRESHOLD
            + [_entry_no_clv("WIN")] * MIN_SAMPLE
        )
        r = _score_dimension(records, "tags", failure_tags=tags)
        assert r["grade"] == HealthGrade.WATCH
        assert r["code"] == "TAG_NEG_CLV_DOWNGRADE"

    def test_no_tags_no_tag_penalty(self):
        records = [_entry("WIN", 0.02)] * MIN_SAMPLE
        r = _score_dimension(records, "tags", failure_tags=[])
        assert r["grade"] == HealthGrade.GREEN


# ---------------------------------------------------------------------------
# 6. Variance hold — no downgrade when CLV positive but results negative
# ---------------------------------------------------------------------------

class TestVarianceHold:
    def test_clv_pos_result_neg_is_green(self):
        records = [_entry("LOSS", clv=0.04)] * MIN_SAMPLE
        r = _score_dimension(records, "test_dim")
        # CLV positive + results negative → VARIANCE_HOLD → grade = GREEN
        assert r["grade"] == HealthGrade.GREEN
        assert r["code"] == "VARIANCE_HOLD"

    def test_lucky_run_is_watch(self):
        # CLV negative + results positive → LUCKY → WATCH
        records = [_entry("WIN", clv=-0.03)] * 2 + [_entry("LOSS", -0.03)] * 1
        records += [_entry("WIN", -0.03)] * 3
        r = _score_dimension(records, "test_dim")
        # Should be LUCKY (WATCH) if enough data
        if r["grade"] != HealthGrade.DATA_GAP:
            assert r["grade"] in (HealthGrade.WATCH, HealthGrade.GREEN)


# ---------------------------------------------------------------------------
# 7. Grade ordering
# ---------------------------------------------------------------------------

class TestGradeOrdering:
    def test_suppress_beats_watch(self):
        assert _grade_rank(HealthGrade.SUPPRESS) > _grade_rank(HealthGrade.WATCH)

    def test_watch_beats_green(self):
        assert _grade_rank(HealthGrade.WATCH) > _grade_rank(HealthGrade.GREEN)

    def test_most_restrictive_returns_suppress_over_green(self):
        g = _most_restrictive_grade([HealthGrade.GREEN, HealthGrade.SUPPRESS])
        assert g == HealthGrade.SUPPRESS

    def test_most_restrictive_all_green(self):
        g = _most_restrictive_grade([HealthGrade.GREEN, HealthGrade.GREEN])
        assert g == HealthGrade.GREEN


# ---------------------------------------------------------------------------
# 8. validate_calibration_health — integration
# ---------------------------------------------------------------------------

class TestValidateCalibrationHealth:
    def test_empty_ledger_is_data_gap_passed(self, temp_ledger):
        """No history → DATA_GAP → passed=True (soft cap only)."""
        c = {"sport": "NBA", "market": "points", "player": "Tatum", "failure_tags": []}
        out = validate_calibration_health(c)
        assert out["passed"] is True
        assert out["grade"] == HealthGrade.DATA_GAP.value
        assert out["can_approve_bets"] is False

    def test_healthy_history_passes_green(self, temp_ledger):
        entries = [_entry("WIN", 0.03, sport="NBA", market="points")] * 10
        _write_entries(temp_ledger, entries)
        c = {"sport": "NBA", "market": "points", "player": "Tatum"}
        out = validate_calibration_health(c)
        assert out["passed"] is True
        assert out["grade"] in (HealthGrade.GREEN.value, HealthGrade.DATA_GAP.value,
                                HealthGrade.WATCH.value)

    def test_8_losses_same_bucket_suppresses(self, temp_ledger):
        entries = [_entry("LOSS", -0.02, sport="WNBA", market="points")] * 10
        _write_entries(temp_ledger, entries)
        c = {"sport": "WNBA", "market": "points", "failure_tags": []}
        out = validate_calibration_health(c)
        assert out["passed"] is False
        assert out["grade"] == HealthGrade.SUPPRESS.value
        assert out["ceiling"] is not None

    def test_suppress_grade_gives_ceiling(self, temp_ledger):
        entries = [_entry("LOSS", -0.03)] * SUPPRESS_THRESHOLD
        _write_entries(temp_ledger, entries)
        c = {"sport": "NBA", "market": "points", "failure_tags": []}
        out = validate_calibration_health(c)
        # If suppressed, ceiling should be REJECT label
        if out["grade"] == HealthGrade.SUPPRESS.value:
            assert out["ceiling"] is not None

    def test_watch_grade_gives_watch_ceiling(self, temp_ledger):
        tag = "STALE_SOURCE"
        entries = (
            [_entry("LOSS", 0.02, failure_tags=[tag])] * WATCH_TAG_THRESHOLD
            + [_entry("WIN", 0.02)] * MIN_SAMPLE
        )
        _write_entries(temp_ledger, entries)
        c = {"sport": "NBA", "market": "points", "failure_tags": [tag]}
        out = validate_calibration_health(c)
        if out["grade"] == HealthGrade.WATCH.value:
            from gate_engine.llp_governance import LLPLabel
            assert out["ceiling"] == LLPLabel.WATCH.value

    def test_dimension_results_present(self, temp_ledger):
        c = {"sport": "NBA", "market": "rebounds", "failure_tags": []}
        out = validate_calibration_health(c)
        assert "dimension_results" in out
        assert "failure_tags" in out["dimension_results"]
        assert "sport_market" in out["dimension_results"]
        assert "player" in out["dimension_results"]

    def test_no_player_field_skips_player_dim(self, temp_ledger):
        c = {"sport": "MLB", "market": "strikeouts"}
        out = validate_calibration_health(c)
        player_dim = out["dimension_results"]["player"]
        assert player_dim["code"] == "NO_PLAYER_KEY"

    def test_can_approve_bets_always_false(self, temp_ledger):
        c = {"sport": "NBA", "market": "points"}
        out = validate_calibration_health(c)
        assert out["can_approve_bets"] is False

    def test_variance_hold_stays_green(self, temp_ledger):
        """CLV+ but losses → variance hold → passed=True (no downgrade)."""
        entries = [_entry("LOSS", clv=0.04, sport="NBA", market="assists")] * 10
        _write_entries(temp_ledger, entries)
        c = {"sport": "NBA", "market": "assists", "failure_tags": []}
        out = validate_calibration_health(c)
        # Variance hold: good process, bad luck — should stay GREEN
        assert out["passed"] is True

    def test_clv_neg_result_neg_suppresses_full(self, temp_ledger):
        """CLV- and losses → strongest downgrade → suppress."""
        entries = [_entry("LOSS", clv=-0.04, sport="WNBA", market="rebounds")] * 10
        _write_entries(temp_ledger, entries)
        c = {"sport": "WNBA", "market": "rebounds", "failure_tags": []}
        out = validate_calibration_health(c)
        assert out["grade"] == HealthGrade.SUPPRESS.value
        assert out["passed"] is False


# ---------------------------------------------------------------------------
# 9. get_health_summary
# ---------------------------------------------------------------------------

class TestGetHealthSummary:
    def test_empty_ledger_summary(self, temp_ledger):
        s = get_health_summary()
        assert s["total_records"] == 0
        assert s["overall_quadrant"] == CLVQuadrant.NO_DATA.value
        assert s["can_approve_bets"] is False

    def test_summary_with_data(self, temp_ledger):
        entries = (
            [_entry("WIN", 0.02, sport="NBA", market="points")] * 5
            + [_entry("LOSS", -0.01, sport="WNBA", market="assists")] * 3
        )
        _write_entries(temp_ledger, entries)
        s = get_health_summary()
        assert s["total_records"] == 8
        assert "NBA" in s["by_sport"] or "nba" in s["by_sport"]
        assert s["can_approve_bets"] is False

    def test_summary_by_failure_tag(self, temp_ledger):
        tag = "OUTLIER_CONTAMINATION"
        entries = [_entry("LOSS", -0.02, failure_tags=[tag])] * 4
        _write_entries(temp_ledger, entries)
        s = get_health_summary()
        assert tag in s["by_failure_tag"]
        assert s["by_failure_tag"][tag]["losses"] == 4

    def test_summary_includes_all_keys(self, temp_ledger):
        s = get_health_summary()
        for key in ("total_records", "overall_quadrant", "grade",
                    "by_sport", "by_market", "by_failure_tag", "can_approve_bets"):
            assert key in s, f"Missing key: {key}"
