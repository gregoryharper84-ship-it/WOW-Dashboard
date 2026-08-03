"""
Tests for the five source reconciliation doctrines in source_grade.py.

Doctrine 1: StatMuse/B-grade stat sites → reconciliation required for exact-stat roles
Doctrine 2: ESPN blurb → stale averages, live game-log takes priority
Doctrine 3: Odds aggregators → exact-line audit required for line_price role
Doctrine 4: Consumer weather sites → cannot replace official Kalshi settlement station
Doctrine 5: Screenshots (incl. PrizePicks) → line active status unconfirmed for line_price
"""
import pytest
from gate_engine.source_grade import (
    run,
    grade_source,
    _check_reconciliation_rules,
    STAT_RECONCILIATION_REQUIRED_SOURCES,
    ODDS_AGGREGATOR_SOURCES,
    CONSUMER_WEATHER_SOURCES,
    OFFICIAL_WEATHER_SOURCES,
    SCREENSHOT_SOURCES,
    SOURCE_TYPE_GRADES,
)
from gate_engine.labels import PropLabel


def _row():
    return {"blockers": [], "gates": {}, "terminal_label": None}


def _src(stype, role, corroborated=False, has_ts=True, name=None):
    return {
        "name":          name or stype,
        "source_type":   stype,
        "role":          role,
        "has_timestamp": has_ts,
        "corroborated":  corroborated,
    }


# ─────────────────────────────────────────────────────────────────
# Source type registry — new types present in SOURCE_TYPE_GRADES
# ─────────────────────────────────────────────────────────────────

class TestNewSourceTypes:
    def test_espn_blurb_is_C(self):
        assert grade_source("espn_blurb") == "C"

    def test_espn_article_is_C(self):
        assert grade_source("espn_article") == "C"

    def test_espn_api_is_A_minus(self):
        assert grade_source("espn_api") == "A-"

    def test_odds_aggregator_is_B(self):
        assert grade_source("odds_aggregator") == "B"

    def test_action_network_is_B(self):
        assert grade_source("action_network") == "B"

    def test_donbest_is_B(self):
        assert grade_source("donbest") == "B"

    def test_covers_is_B(self):
        assert grade_source("covers") == "B"

    def test_vegasinsider_is_B(self):
        assert grade_source("vegasinsider") == "B"

    def test_consumer_weather_site_is_C(self):
        assert grade_source("consumer_weather_site") == "C"

    def test_weather_dot_com_is_C(self):
        assert grade_source("weather_dot_com") == "C"

    def test_nws_cli_is_A(self):
        assert grade_source("nws_cli") == "A"

    def test_official_weather_station_is_A(self):
        assert grade_source("official_weather_station") == "A"

    def test_prizepicks_screenshot_is_D(self):
        assert grade_source("prizepicks_screenshot") == "D"

    def test_board_capture_is_D(self):
        assert grade_source("board_capture") == "D"

    def test_web_search_is_C(self):
        assert grade_source("web_search") == "C"

    def test_news_article_is_C(self):
        assert grade_source("news_article") == "C"

    def test_official_feed_is_A(self):
        assert grade_source("official_feed") == "A"


# ─────────────────────────────────────────────────────────────────
# Doctrine 1: StatMuse / B-grade stat sites — reconciliation required
# ─────────────────────────────────────────────────────────────────

class TestDoctrine1StatMuseReconciliation:
    def test_statmuse_l5_l10_uncorroborated_triggers_blocker(self):
        blockers = _check_reconciliation_rules("statmuse", "l5_l10", corroborated=False)
        assert any("RECONCILIATION_REQUIRED" in b for b in blockers)
        assert any("statmuse" in b for b in blockers)
        assert any("needs_official_log" in b for b in blockers)

    def test_statmuse_exact_stat_triggers_blocker(self):
        blockers = _check_reconciliation_rules("statmuse", "exact_stat", corroborated=False)
        assert any("RECONCILIATION_REQUIRED" in b for b in blockers)

    def test_statmuse_game_log_triggers_blocker(self):
        blockers = _check_reconciliation_rules("statmuse", "game_log", corroborated=False)
        assert any("RECONCILIATION_REQUIRED" in b for b in blockers)

    def test_statmuse_corroborated_no_reconciliation(self):
        blockers = _check_reconciliation_rules("statmuse", "l5_l10", corroborated=True)
        assert not any("RECONCILIATION_REQUIRED" in b for b in blockers)

    def test_bbref_l5_l10_triggers_blocker(self):
        blockers = _check_reconciliation_rules("bbref", "l5_l10", corroborated=False)
        assert any("RECONCILIATION_REQUIRED" in b for b in blockers)

    def test_her_hoop_stats_triggers_blocker(self):
        blockers = _check_reconciliation_rules("her_hoop_stats", "l5_l10", corroborated=False)
        assert any("RECONCILIATION_REQUIRED" in b for b in blockers)

    def test_statmuse_non_stat_role_no_reconciliation(self):
        # StatMuse used for context/other role shouldn't trigger reconciliation
        blockers = _check_reconciliation_rules("statmuse", "context", corroborated=False)
        assert not any("RECONCILIATION_REQUIRED" in b for b in blockers)

    def test_run_stamps_reconciliation_blocker_on_row(self):
        row = _row()
        sources = [_src("statmuse", "l5_l10", corroborated=False)]
        result = run(row, sources=sources)
        assert any("RECONCILIATION_REQUIRED" in b for b in row["blockers"])

    def test_all_stat_reconciliation_sources_are_registered(self):
        for stype in STAT_RECONCILIATION_REQUIRED_SOURCES:
            assert stype in SOURCE_TYPE_GRADES, f"{stype} not in SOURCE_TYPE_GRADES"

    def test_reconciliation_blockers_in_result(self):
        row = _row()
        sources = [_src("statmuse", "l5_l10", corroborated=False)]
        result = run(row, sources=sources)
        assert "reconciliation_blockers" in result
        assert len(result["reconciliation_blockers"]) >= 1


# ─────────────────────────────────────────────────────────────────
# Doctrine 2: ESPN blurb — stale averages, game-log priority
# ─────────────────────────────────────────────────────────────────

class TestDoctrine2EspnBlurb:
    def test_espn_blurb_l5_l10_triggers_blocker(self):
        blockers = _check_reconciliation_rules("espn_blurb", "l5_l10", corroborated=False)
        assert any("ESPN_BLURB_STALE_AVERAGES" in b for b in blockers)
        assert any("live_game_log_required" in b for b in blockers)

    def test_espn_article_game_log_triggers_blocker(self):
        blockers = _check_reconciliation_rules("espn_article", "game_log", corroborated=False)
        assert any("ESPN_BLURB_STALE_AVERAGES" in b for b in blockers)

    def test_espn_blurb_non_stat_role_no_blocker(self):
        blockers = _check_reconciliation_rules("espn_blurb", "context", corroborated=False)
        assert not any("ESPN_BLURB_STALE_AVERAGES" in b for b in blockers)

    def test_espn_api_no_blurb_blocker(self):
        # espn_api is the real endpoint (event identity), not a blurb
        blockers = _check_reconciliation_rules("espn_api", "l5_l10", corroborated=False)
        assert not any("ESPN_BLURB_STALE_AVERAGES" in b for b in blockers)

    def test_run_stamps_espn_blurb_blocker_on_row(self):
        row = _row()
        sources = [_src("espn_blurb", "l5_l10")]
        run(row, sources=sources)
        assert any("ESPN_BLURB_STALE_AVERAGES" in b for b in row["blockers"])


# ─────────────────────────────────────────────────────────────────
# Doctrine 3: Odds aggregators — exact-line audit required
# ─────────────────────────────────────────────────────────────────

class TestDoctrine3OddsAggregator:
    def test_odds_aggregator_line_price_triggers_audit(self):
        blockers = _check_reconciliation_rules("odds_aggregator", "line_price", corroborated=False)
        assert any("EXACT_LINE_AUDIT_REQUIRED" in b for b in blockers)
        assert any("odds_aggregator" in b for b in blockers)

    def test_action_network_line_price_triggers_audit(self):
        blockers = _check_reconciliation_rules("action_network", "line_price", corroborated=False)
        assert any("EXACT_LINE_AUDIT_REQUIRED" in b for b in blockers)

    def test_donbest_line_price_triggers_audit(self):
        blockers = _check_reconciliation_rules("donbest", "line_price", corroborated=False)
        assert any("EXACT_LINE_AUDIT_REQUIRED" in b for b in blockers)

    def test_covers_line_price_triggers_audit(self):
        blockers = _check_reconciliation_rules("covers", "line_price", corroborated=False)
        assert any("EXACT_LINE_AUDIT_REQUIRED" in b for b in blockers)

    def test_odds_aggregator_non_line_role_no_audit(self):
        # Odds aggregator used for context doesn't need line audit
        blockers = _check_reconciliation_rules("odds_aggregator", "context", corroborated=False)
        assert not any("EXACT_LINE_AUDIT_REQUIRED" in b for b in blockers)

    def test_direct_api_no_audit_required(self):
        # Official odds_api doesn't trigger audit requirement
        blockers = _check_reconciliation_rules("odds_api", "line_price", corroborated=False)
        assert not any("EXACT_LINE_AUDIT_REQUIRED" in b for b in blockers)

    def test_run_stamps_audit_blocker_on_row(self):
        row = _row()
        sources = [_src("action_network", "line_price")]
        run(row, sources=sources)
        assert any("EXACT_LINE_AUDIT_REQUIRED" in b for b in row["blockers"])

    def test_all_odds_aggregator_sources_registered(self):
        for stype in ODDS_AGGREGATOR_SOURCES:
            assert stype in SOURCE_TYPE_GRADES, f"{stype} not in SOURCE_TYPE_GRADES"


# ─────────────────────────────────────────────────────────────────
# Doctrine 4: Consumer weather — cannot replace Kalshi settlement station
# ─────────────────────────────────────────────────────────────────

class TestDoctrine4ConsumerWeather:
    def test_consumer_weather_kalshi_weather_triggers_blocker(self):
        blockers = _check_reconciliation_rules("consumer_weather_site", "kalshi_weather", corroborated=False)
        assert any("WEATHER_SOURCE_INVALID_FOR_SETTLEMENT" in b for b in blockers)
        assert any("consumer_weather_site" in b for b in blockers)

    def test_weather_dot_com_kalshi_triggers_blocker(self):
        blockers = _check_reconciliation_rules("weather_dot_com", "kalshi_weather", corroborated=False)
        assert any("WEATHER_SOURCE_INVALID_FOR_SETTLEMENT" in b for b in blockers)

    def test_wunderground_kalshi_triggers_blocker(self):
        blockers = _check_reconciliation_rules("wunderground", "kalshi_weather", corroborated=False)
        assert any("WEATHER_SOURCE_INVALID_FOR_SETTLEMENT" in b for b in blockers)

    def test_consumer_weather_non_kalshi_role_no_blocker(self):
        # Consumer weather for context (not Kalshi settlement) doesn't hard-block
        blockers = _check_reconciliation_rules("consumer_weather_site", "context", corroborated=False)
        assert not any("WEATHER_SOURCE_INVALID_FOR_SETTLEMENT" in b for b in blockers)

    def test_nws_cli_kalshi_no_blocker(self):
        # Official NWS CLI is valid for Kalshi settlement
        blockers = _check_reconciliation_rules("nws_cli", "kalshi_weather", corroborated=False)
        assert not any("WEATHER_SOURCE_INVALID_FOR_SETTLEMENT" in b for b in blockers)

    def test_official_weather_station_no_blocker(self):
        blockers = _check_reconciliation_rules("official_weather_station", "kalshi_weather", corroborated=False)
        assert not any("WEATHER_SOURCE_INVALID_FOR_SETTLEMENT" in b for b in blockers)

    def test_consumer_weather_kalshi_downgraded_to_D_in_run(self):
        row = _row()
        sources = [_src("consumer_weather_site", "kalshi_weather")]
        result = run(row, sources=sources)
        # Grade should be D for this role (downgraded from C)
        graded = result["source_grades"]
        assert graded[0]["grade"] == "D"

    def test_run_stamps_weather_blocker_on_row(self):
        row = _row()
        sources = [_src("weather_dot_com", "kalshi_weather")]
        run(row, sources=sources)
        assert any("WEATHER_SOURCE_INVALID_FOR_SETTLEMENT" in b for b in row["blockers"])

    def test_official_sources_set_is_valid(self):
        for stype in OFFICIAL_WEATHER_SOURCES:
            assert stype in SOURCE_TYPE_GRADES, f"{stype} not in SOURCE_TYPE_GRADES"
            assert SOURCE_TYPE_GRADES[stype] == "A", f"{stype} should be grade A"


# ─────────────────────────────────────────────────────────────────
# Doctrine 5: Screenshots / PrizePicks — line active unconfirmed
# ─────────────────────────────────────────────────────────────────

class TestDoctrine5ScreenshotLineActive:
    def test_screenshot_line_price_triggers_blocker(self):
        blockers = _check_reconciliation_rules("screenshot", "line_price", corroborated=False)
        assert any("LINE_ACTIVE_UNCONFIRMED" in b for b in blockers)
        assert any("screenshot" in b for b in blockers)

    def test_prizepicks_screenshot_line_price_triggers_blocker(self):
        blockers = _check_reconciliation_rules("prizepicks_screenshot", "line_price", corroborated=False)
        assert any("LINE_ACTIVE_UNCONFIRMED" in b for b in blockers)
        assert any("prizepicks_screenshot" in b for b in blockers)

    def test_board_capture_line_price_triggers_blocker(self):
        blockers = _check_reconciliation_rules("board_capture", "line_price", corroborated=False)
        assert any("LINE_ACTIVE_UNCONFIRMED" in b for b in blockers)

    def test_screenshot_non_line_role_no_blocker(self):
        # Screenshot used for status (not line) doesn't add LINE_ACTIVE blocker
        blockers = _check_reconciliation_rules("screenshot", "status_role", corroborated=False)
        assert not any("LINE_ACTIVE_UNCONFIRMED" in b for b in blockers)

    def test_api_feed_no_active_blocker(self):
        # Official API feed doesn't trigger line active check
        blockers = _check_reconciliation_rules("api_feed", "line_price", corroborated=False)
        assert not any("LINE_ACTIVE_UNCONFIRMED" in b for b in blockers)

    def test_run_stamps_line_active_blocker_on_row(self):
        row = _row()
        sources = [_src("prizepicks_screenshot", "line_price")]
        run(row, sources=sources)
        assert any("LINE_ACTIVE_UNCONFIRMED" in b for b in row["blockers"])

    def test_run_stamps_screenshot_blocker_on_row(self):
        row = _row()
        sources = [_src("screenshot", "line_price")]
        run(row, sources=sources)
        assert any("LINE_ACTIVE_UNCONFIRMED" in b for b in row["blockers"])

    def test_all_screenshot_sources_registered(self):
        for stype in SCREENSHOT_SOURCES:
            assert stype in SOURCE_TYPE_GRADES, f"{stype} not in SOURCE_TYPE_GRADES"
            assert SOURCE_TYPE_GRADES[stype] == "D", f"{stype} should be grade D"


# ─────────────────────────────────────────────────────────────────
# Multi-doctrine: multiple blockers fire on same row
# ─────────────────────────────────────────────────────────────────

class TestMultiDoctrineRow:
    def test_statmuse_plus_screenshot_line_both_fire(self):
        row = _row()
        sources = [
            _src("statmuse",    "l5_l10",    corroborated=False),
            _src("screenshot",  "line_price", corroborated=False),
        ]
        run(row, sources=sources)
        assert any("RECONCILIATION_REQUIRED" in b for b in row["blockers"])
        assert any("LINE_ACTIVE_UNCONFIRMED" in b for b in row["blockers"])

    def test_result_reconciliation_blockers_lists_all(self):
        row = _row()
        sources = [
            _src("statmuse",      "l5_l10",      corroborated=False),
            _src("action_network","line_price",   corroborated=False),
        ]
        result = run(row, sources=sources)
        assert len(result["reconciliation_blockers"]) >= 2

    def test_no_doctrine_violations_clean_result(self):
        row = _row()
        sources = [
            _src("odds_api",       "line_price"),
            _src("official_feed",  "l5_l10"),
            _src("nws_cli",        "kalshi_weather"),
        ]
        result = run(row, sources=sources)
        assert result["reconciliation_blockers"] == []
        assert result["passed"] is True


# ─────────────────────────────────────────────────────────────────
# Architecture tier validation
# ─────────────────────────────────────────────────────────────────

class TestSourceArchitectureTiers:
    """Grade ordering must reflect the source architecture priority order."""

    def test_official_api_outranks_research_site(self):
        from gate_engine.source_grade import GRADE_RANK
        assert GRADE_RANK["A"] > GRADE_RANK["B"]

    def test_research_site_outranks_article(self):
        from gate_engine.source_grade import GRADE_RANK
        assert GRADE_RANK["B"] > GRADE_RANK["C"]

    def test_article_outranks_screenshot(self):
        from gate_engine.source_grade import GRADE_RANK
        assert GRADE_RANK["C"] > GRADE_RANK["D"]

    def test_official_weather_is_tier_A(self):
        assert grade_source("nws_cli") == "A"
        assert grade_source("official_weather_station") == "A"

    def test_consumer_weather_is_below_official(self):
        from gate_engine.source_grade import GRADE_RANK
        official = GRADE_RANK[grade_source("nws_cli")]
        consumer = GRADE_RANK[grade_source("consumer_weather_site")]
        assert official > consumer
