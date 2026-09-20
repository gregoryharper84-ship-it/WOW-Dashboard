from v17.fantasy_score_pick_request_bridge import RESEARCH_ROUTES, _manifest_typed_absence
from v17.prop_capability_manifest import (
    BUILD_REQUIRED,
    CANDIDATE_ONLY,
    CERTIFIED_PRODUCTION,
    SUPPORTED_HOLD_ONLY,
    declared_prop_lane_manifest,
    normalize_prop_stat,
    prop_capability,
)


def test_nfl_direct_routes_preserve_governed_production_specialist():
    for stat in ("PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"):
        cap = prop_capability("NFL", stat)
        assert cap.lane_status == CERTIFIED_PRODUCTION
        assert cap.route_active is True
        assert cap.publication_allowed is True
        assert cap.controlling_specialist == "wow.nfl-player-prop-probability-expert"
        assert cap.can_execute is False


def test_wnba_components_are_hold_only_and_composites_candidate_only():
    for stat in ("POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"):
        cap = prop_capability("WNBA", stat)
        assert cap.lane_status == SUPPORTED_HOLD_ONLY
        assert cap.route_active is True
        assert cap.publication_allowed is False
        assert cap.blocker == "WNBA_PROP_PROSPECTIVE_NOT_PUBLISHABLE"
        assert cap.can_execute is False
    for stat in ("PRA", "POINTS_REBOUNDS", "POINTS_ASSISTS", "REBOUNDS_ASSISTS"):
        cap = prop_capability("WNBA", stat)
        assert cap.lane_status == CANDIDATE_ONLY
        assert cap.controlling_specialist == "wow.wnba-composite-prop-expert"
        assert cap.blocker == "WNBA_COMPOSITE_CANDIDATE_NOT_PROMOTED"
        assert cap.publication_allowed is False
        assert cap.can_execute is False


def test_nba_fitted_scalar_candidates_and_remaining_real_model_gaps():
    for stat in (
        "POINTS", "REBOUNDS", "ASSISTS", "PRA",
        "POINTS_REBOUNDS", "POINTS_ASSISTS", "REBOUNDS_ASSISTS",
    ):
        nba = prop_capability("NBA", stat)
        assert nba.lane_status == CANDIDATE_ONLY
        assert nba.controlling_specialist == "wow.nba-player-prop-probability-expert"
        assert nba.blocker == "NBA_SCALAR_CANDIDATE_NOT_PROMOTED"
        assert nba.publication_allowed is False
        assert nba.can_execute is False

    nba_threes = prop_capability("NBA", "THREE_POINTERS_MADE")
    assert nba_threes.lane_status == BUILD_REQUIRED
    assert nba_threes.blocker == "PROP_FITTED_SPECIALIST_BUILD_REQUIRED"
    assert nba_threes.publication_allowed is False

    nhl = prop_capability("NHL", "SHOTS_ON_GOAL")
    assert nhl.lane_status == BUILD_REQUIRED
    assert nhl.blocker == "NHL_SOG_REAL_MULTI_SEASON_CORPUS_REQUIRED"
    assert nhl.publication_allowed is False


def test_fantasy_score_candidates_are_visible_but_never_publishable():
    expected = {
        ("NFL", "FANTASY_SCORE"),
        ("NBA", "FANTASY_SCORE"),
        ("WNBA", "FANTASY_SCORE"),
        ("MLB", "HITTER_FANTASY_SCORE"),
        ("MLB", "PITCHER_FANTASY_SCORE"),
    }
    assert expected <= set(RESEARCH_ROUTES)
    for sport, stat in expected:
        cap = prop_capability(sport, stat)
        assert cap.lane_status == CANDIDATE_ONLY
        assert cap.publication_allowed is False
        assert cap.can_execute is False


def test_cross_sport_aliases_normalize_without_granting_authority():
    assert normalize_prop_stat("NFL", "pass yds") == "PASSING_YARDS"
    assert normalize_prop_stat("WNBA", "PTS+REB+AST") == "PRA"
    assert normalize_prop_stat("NBA", "PTS") == "POINTS"


def test_manifest_is_explicit_and_non_executable():
    manifest = declared_prop_lane_manifest()
    assert manifest["manifest_version"] == "WOW_V17_PROP_LANE_MANIFEST_V6"
    assert manifest["can_execute"] is False
    assert manifest["candidate_presence_does_not_grant_probability_authority"] is True
    assert manifest["build_target_presence_does_not_grant_probability_authority"] is True
    assert manifest["candidate_lane_count"] > 0
    assert manifest["build_required_lane_count"] > 0


def test_artifact_absence_refines_but_scorer_failure_is_not_rewritten():
    absence = _manifest_typed_absence(
        "NHL", "SHOTS_ON_GOAL",
        {"code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND", "ok": False},
    )
    assert absence is not None
    assert absence["code"] == "NHL_SOG_REAL_MULTI_SEASON_CORPUS_REQUIRED"
    assert absence["probability_publishable"] is False
    assert absence["can_execute"] is False
    scorer_failure = _manifest_typed_absence(
        "NHL", "SHOTS_ON_GOAL", {"code": "MODEL_SCORER_FAILED", "ok": False},
    )
    assert scorer_failure is None
