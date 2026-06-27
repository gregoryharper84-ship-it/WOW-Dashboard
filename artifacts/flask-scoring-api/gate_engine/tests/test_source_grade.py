"""Tests for Module H: source_grade.py"""
import pytest
from gate_engine.source_grade import run, grade_source, worst_grade
from gate_engine.labels import PropLabel


def _row():
    return {"blockers": [], "gates": {}, "terminal_label": None}


def _src(name, stype, role="line_price", has_ts=True, corroborated=False, conflict=False, override=None):
    d = {"name": name, "source_type": stype, "role": role,
         "has_timestamp": has_ts, "corroborated": corroborated}
    if conflict:
        d["source_conflict"] = True
    if override:
        d["grade_override"] = override
    return d


class TestGradeSource:
    def test_api_feed_is_A(self):
        assert grade_source("api_feed") == "A"

    def test_statmuse_is_B(self):
        assert grade_source("statmuse") == "B"

    def test_screenshot_is_D(self):
        assert grade_source("screenshot") == "D"

    def test_no_timestamp_is_NT(self):
        assert grade_source("api_feed", has_timestamp=False) == "N/T"

    def test_unknown_type_defaults_to_C(self):
        assert grade_source("my_random_source") == "C"


class TestWorstGrade:
    def test_all_A_returns_A(self):
        assert worst_grade(["A", "A", "A-"]) == "A-"

    def test_B_worse_than_A(self):
        assert worst_grade(["A", "B"]) == "B"

    def test_D_worst(self):
        assert worst_grade(["A", "B", "D"]) == "D"

    def test_empty_returns_A(self):
        assert worst_grade([]) == "A"

    def test_NT_is_worst(self):
        assert worst_grade(["A", "N/T"]) == "N/T"


class TestRunNoCriticalSources:
    def test_no_sources_passes_no_cap(self):
        row = _row()
        result = run(row, sources=[])
        assert result["passed"] is True
        assert result["ceiling"] is None
        assert result["worst_critical"] == "A"

    def test_non_critical_sources_dont_cap(self):
        row = _row()
        sources = [_src("Blog", "article", role="context")]
        result = run(row, sources=sources)
        assert result["passed"] is True
        assert result["ceiling"] is None


class TestRunCriticalSources:
    def test_all_A_critical_passes(self):
        row = _row()
        sources = [
            _src("OddsAPI", "odds_api", role="line_price"),
            _src("StatFeed", "api_feed", role="l5_l10"),
        ]
        result = run(row, sources=sources)
        assert result["passed"] is True
        assert result["ceiling"] is None
        assert result["code"] == "SOURCE_GRADE_OK"

    def test_uncorroborated_B_caps_at_mq_hold(self):
        row = _row()
        sources = [_src("StatMuse", "statmuse", role="l5_l10", corroborated=False)]
        result = run(row, sources=sources)
        assert result["passed"] is False
        assert result["ceiling"] == PropLabel.MODEL_QUALIFIED_HOLD.value
        assert result["code"] == "SOURCE_GRADE_B_UNCORROBORATED"

    def test_corroborated_B_no_cap(self):
        row = _row()
        sources = [_src("StatMuse", "statmuse", role="l5_l10", corroborated=True)]
        result = run(row, sources=sources)
        assert result["passed"] is True
        assert result["ceiling"] is None

    def test_C_source_caps_at_watch(self):
        row = _row()
        sources = [_src("Article", "article", role="status_role")]
        result = run(row, sources=sources)
        assert result["passed"] is False
        assert result["ceiling"] == PropLabel.RESEARCH_INTEREST.value

    def test_D_screenshot_caps_at_watch(self):
        row = _row()
        sources = [_src("Screenshot", "screenshot", role="market_consensus")]
        result = run(row, sources=sources)
        assert result["passed"] is False
        assert result["ceiling"] == PropLabel.RESEARCH_INTEREST.value

    def test_no_timestamp_caps(self):
        row = _row()
        sources = [_src("StatMuse", "statmuse", role="l5_l10", has_ts=False)]
        result = run(row, sources=sources)
        assert result["worst_critical"] == "N/T"
        assert result["ceiling"] is not None


class TestSourceConflict:
    def test_conflict_blocks_money_labels(self):
        row = _row()
        sources = [_src("Feed1", "api_feed", role="line_price", conflict=True)]
        result = run(row, sources=sources)
        assert result["source_conflict"] is True
        assert result["ceiling"] == PropLabel.MODEL_QUALIFIED_HOLD.value
        assert result["code"] == "SOURCE_CONFLICT"
        assert row["terminal_label"] == PropLabel.SOURCE_CONFLICT.value

    def test_blocker_appended(self):
        row = _row()
        sources = [_src("StatMuse", "statmuse", role="l5_l10")]
        run(row, sources=sources)
        assert any("SOURCE_GRADE" in b for b in row["blockers"])


class TestGradeOverride:
    def test_grade_override_respected(self):
        row = _row()
        sources = [_src("Custom", "unknown_type", role="l5_l10", override="A")]
        result = run(row, sources=sources)
        assert result["passed"] is True
