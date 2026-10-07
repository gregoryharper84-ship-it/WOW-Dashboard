from types import SimpleNamespace

from v17.mlb_team_event_hydration import _same_mlb_team, _team_match_strength
from v17.sep16_evidence_handoff_rank_fix import (
    RUN_INVALID_EVIDENCE_BINDING,
    _annotate_schema_mismatch,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
from v17.cross_sport_winner_discovery import DiscoveredEvent
from v17.team_event_sport_parity import (
    build_discovery_evidence,
    canonicalize_mlb_discovery_identity,
    canonicalize_nfl_discovery_identity,
    canonicalize_wnba_discovery_identity,
    cross_sport_model_coverage,
    parity_health,
)


def test_every_cataloged_sport_has_same_parity_contract_shape():
    health = parity_health({})
    assert set(health) == set(EXPECTED_TEAM_EVENT_SPORTS)

    shapes = {tuple(sorted(row.keys())) for row in health.values()}
    assert len(shapes) == 1
    for sport, row in health.items():
        assert row["sport"] == sport
        assert row["cataloged"] is True
        assert row["discovery_required"] is True
        assert row["canonical_identity_required"] is True
        assert row["hydration_owner"] != "UNASSIGNED"
        assert row["publication_is_row_scoped"] is True
        assert row["market_probability_substitution_allowed"] is False
        assert row["generic_reasoning_substitution_allowed"] is False
        assert row["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert row["can_execute"] is False


def test_discovery_handoff_preserves_explicit_sporting_inputs_not_market_price():
    event = SimpleNamespace(
        sport="WNBA",
        provider="RUNDOWN",
        provider_sport_id="11",
        regime="REGULAR_SEASON",
        official_event_id="provider-123",
        raw={
            "price": -150,
            "implied_probability": 0.60,
            "evidence": {
                "home_win_pct": 0.64,
                "away_win_pct": 0.51,
                "expected_starters_rotation": "CONFIRMED",
            },
        },
    )
    registration = SimpleNamespace(
        required_inputs=("home_win_pct", "away_win_pct", "calibration_artifact")
    )

    evidence = build_discovery_evidence(event, registration)

    assert evidence["home_win_pct"] == 0.64
    assert evidence["away_win_pct"] == 0.51
    assert evidence["expected_starters_rotation"] == "CONFIRMED"
    assert "price" not in evidence
    assert "implied_probability" not in evidence
    assert evidence["market_probability_used_as_model"] is False
    assert evidence["generic_reasoning_used_as_model"] is False
    assert evidence["can_execute"] is False


def test_capability_state_is_equal_shape_not_fake_equal_availability():
    bridge_health = {
        "MLB": {
            "registered": True,
            "scorer_resolvable": True,
            "certification_status": "CERTIFIED",
        },
        "NBA": {
            "registered": False,
            "scorer_resolvable": False,
            "certification_status": "UNAVAILABLE",
        },
    }
    health = parity_health(bridge_health)

    assert health["MLB"]["model_capability_ready"] is True
    assert health["NBA"]["model_capability_ready"] is False
    assert health["MLB"]["publication_is_row_scoped"] is True
    assert health["NBA"]["publication_is_row_scoped"] is True
    assert health["NBA"]["can_execute"] is False


def test_mlb_provider_city_aliases_resolve_without_guessing_ambiguous_city():
    assert _same_mlb_team("San Francisco Giants", "San Francisco")
    assert _same_mlb_team("Minnesota Twins", "Minnesota")
    assert _team_match_strength("New York Yankees", "New York") == 1
    assert _team_match_strength("New York Mets", "New York") == 1
    assert _team_match_strength("Chicago Cubs", "Chicago") == 1
    assert _team_match_strength("Chicago White Sox", "Chicago") == 1
    assert _team_match_strength("Los Angeles Dodgers", "Los Angeles") == 1
    assert _team_match_strength("Los Angeles Angels", "Los Angeles") == 1
    assert _team_match_strength("San Francisco Giants", "Minnesota") == 0


def test_present_upstream_missing_downstream_is_run_invalid_not_no_pick():
    req = SimpleNamespace(
        official_event_id="823169",
        sport_specific_evidence={
            "home_lineup_status": "CONFIRMED",
            "away_lineup_status": "CONFIRMED",
        },
    )
    model_result = {
        "official_event_id": "823169",
        "independent_home_probability": 0.51,
        "independent_away_probability": 0.49,
        "favorite_failure_paths_json": [{"path": "BULLPEN"}],
        "favorite_failure_path_probability": 0.19,
        "largest_favorite_loss_path": "BULLPEN",
        "underdog_upset_path_json": [{"path": "BULLPEN"}],
        "lineup_context": {"status": "CONFIRMED"},
        "calibrated_probability": 0.52,
        "calibrated_lower_bound": 0.44,
    }
    downstream = {
        "blockers": [
            "HOME_LINEUP_NOT_CALLED",
            "AWAY_LINEUP_NOT_CALLED",
            "INDEPENDENT_PROBABILITY_MISSING",
            "FAVORITE_FAILURE_PATHS_MISSING",
            "OFFICIAL_EVENT_ID_EVIDENCE_MISSING",
        ],
        "calibrated_probability": 0.52,
        "calibrated_lower_bound": 0.44,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }

    out = _annotate_schema_mismatch(req, model_result, downstream)

    assert out["run_validity_status"] == RUN_INVALID_EVIDENCE_BINDING
    assert RUN_INVALID_EVIDENCE_BINDING in out["blockers"]
    assert out["calibrated_probability"] == 0.52
    assert out["calibrated_lower_bound"] == 0.44
    assert out["rank_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


class _EventApi:
    def __init__(self):
        self.db = object()

    def get_client(self):
        return self.db


def _nfl_alias_event():
    return DiscoveredEvent(
        sport="NFL",
        league="NFL",
        sport_key="2",
        official_event_id=None,
        home_team="Chicago Bears",
        away_team="New York Jets",
        commence_time_utc="2026-10-04T17:00:00Z",
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw={
            "provider_event_id": "401872972",
            "official_event_id": None,
            "prediction_authority": False,
        },
    )


def test_nfl_daily_discovery_alias_rewrites_only_after_canonical_ledger_match(monkeypatch):
    import v17.nfl_team_event_specialist as specialist

    captured = {}

    def resolve(req, *, db):
        captured["provider_event_id"] = req.official_event_id
        captured["db"] = db
        return {
            "ok": True,
            "canonical_event_id": "2026_04_NYJ_CHI",
            "identity_resolution": "PROVIDER_ID_TO_CANONICAL_SCHEDULE_MATCH",
            "canonical_source_snapshot_id": "snapshot-nfl",
        }

    monkeypatch.setattr(specialist, "resolve_nfl_team_event_evidence", resolve)
    api = _EventApi()
    req = SimpleNamespace(
        requested_slate_date="2026-10-04",
        requested_timezone="America/Chicago",
    )

    out = canonicalize_nfl_discovery_identity(
        _nfl_alias_event(),
        req=req,
        event_api=api,
        settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
    )

    assert captured["provider_event_id"] == "401872972"
    assert captured["db"] is api.db
    assert out.official_event_id == "2026_04_NYJ_CHI"
    assert out.raw["provider_event_id"] == "401872972"
    assert out.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out.raw["canonical_identity_source"] == "CANONICAL_NFLVERSE_LEDGER"
    assert out.raw["prediction_authority"] is False


def test_nfl_daily_discovery_alias_stays_unresolved_when_canonical_match_fails(monkeypatch):
    import v17.nfl_team_event_specialist as specialist

    monkeypatch.setattr(
        specialist,
        "resolve_nfl_team_event_evidence",
        lambda req, *, db: {
            "ok": False,
            "code": "NFL_PROVIDER_EVENT_ID_CANONICAL_MATCH_AMBIGUOUS",
        },
    )
    req = SimpleNamespace(
        requested_slate_date="2026-10-04",
        requested_timezone="America/Chicago",
    )

    out = canonicalize_nfl_discovery_identity(
        _nfl_alias_event(),
        req=req,
        event_api=_EventApi(),
        settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
    )

    assert out.official_event_id is None
    assert out.raw["provider_event_id"] == "401872972"
    assert out.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert (
        out.raw["canonical_identity_blocker"]
        == "NFL_PROVIDER_EVENT_ID_CANONICAL_MATCH_AMBIGUOUS"
    )


def test_discovery_evidence_preserves_provider_alias_after_nfl_canonicalization():
    event = _nfl_alias_event()
    event = DiscoveredEvent(
        **{
            **event.__dict__,
            "official_event_id": "2026_04_NYJ_CHI",
        }
    )
    evidence = build_discovery_evidence(event)
    assert evidence["discovery_provider_event_id"] == "401872972"
    assert evidence["market_probability_used_as_model"] is False
    assert evidence["can_execute"] is False


def _mlb_alias_event():
    return DiscoveredEvent(
        sport="MLB",
        league="MLB",
        sport_key="3",
        official_event_id=None,
        home_team="San Diego Padres",
        away_team="Milwaukee Brewers",
        commence_time_utc="2026-10-08T02:00:00Z",
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw={
            "provider_event_id": "espn-849826",
            "official_event_id": None,
            "prediction_authority": False,
        },
    )


def test_mlb_daily_discovery_alias_rewrites_only_after_canonical_ledger_match(monkeypatch):
    import v17.mlb_team_event_hydration as hydration

    captured = {}

    def resolve(req, *, event_api):
        captured["provider_event_id"] = req.official_event_id
        captured["event_api"] = event_api
        return {
            "ok": True,
            "canonical_official_event_id": "849826",
            "canonical_identity_resolution": "PARTICIPANTS_START_SLATE",
            "canonical_source_snapshot_id": "snapshot-849826",
            "canonical_snapshot_timestamp": "2026-10-07T20:00:00+00:00",
        }

    monkeypatch.setattr(hydration, "resolve_mlb_team_event_evidence", resolve)
    api = _EventApi()
    req = SimpleNamespace(
        requested_slate_date="2026-10-06",
        requested_timezone="America/Chicago",
    )

    out = canonicalize_mlb_discovery_identity(
        _mlb_alias_event(),
        req=req,
        event_api=api,
    )

    assert captured["provider_event_id"] == "espn-849826"
    assert captured["event_api"] is api
    assert out.official_event_id == "849826"
    assert out.raw["provider_event_id"] == "espn-849826"
    assert out.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out.raw["canonical_identity_source"] == "CANONICAL_MLB_LEDGER"
    assert out.raw["canonical_source_snapshot_id"] == "snapshot-849826"


def test_mlb_daily_discovery_alias_stays_unresolved_without_canonical_proof(monkeypatch):
    import v17.mlb_team_event_hydration as hydration

    monkeypatch.setattr(
        hydration,
        "resolve_mlb_team_event_evidence",
        lambda req, *, event_api: {
            "ok": False,
            "code": "MLB_TEAM_EVENT_CANONICAL_IDENTITY_AMBIGUOUS",
        },
    )
    out = canonicalize_mlb_discovery_identity(
        _mlb_alias_event(),
        req=SimpleNamespace(
            requested_slate_date="2026-10-06",
            requested_timezone="America/Chicago",
        ),
        event_api=_EventApi(),
    )

    assert out.official_event_id is None
    assert out.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert (
        out.raw["canonical_identity_blocker"]
        == "MLB_TEAM_EVENT_CANONICAL_IDENTITY_AMBIGUOUS"
    )


def test_model_coverage_separates_accounted_rows_from_identity_and_model_routing():
    coverage = cross_sport_model_coverage(
        [
            {
                "bucket": "EVENT_IDENTITY_UNRESOLVED",
                "detail": {"model_invoked": False},
            },
            {
                "bucket": "MODEL_UNAVAILABLE",
                "detail": {"model_invoked": False},
            },
            {
                "bucket": "MODEL_COMPLETED",
                "detail": {"model_invoked": True},
            },
        ],
        requested_model_budget=12,
    )

    assert coverage["discovered_rows"] == 3
    assert coverage["pregame_candidate_rows"] == 3
    assert coverage["identity_unresolved_rows"] == 1
    assert coverage["identity_resolved_pregame_rows"] == 2
    assert coverage["model_routed_rows"] == 2
    assert coverage["model_invoked_rows"] == 1
    assert coverage["model_routing_coverage_status"] == "PARTIAL_MODEL_ROUTING"
    assert coverage["requested_model_invocation_budget"] == 12
    assert (
        coverage["max_team_events_semantics"]
        == "MODEL_INVOCATION_BUDGET_NOT_DISCOVERY_ROW_CAP"
    )
    assert coverage["discovery_rows_retained_for_reconciliation"] is True
    assert coverage["can_execute"] is False



def _wnba_alias_event():
    return DiscoveredEvent(
        sport="WNBA",
        league="WNBA",
        sport_key="basketball_wnba",
        official_event_id=None,
        home_team="Minnesota Lynx",
        away_team="Phoenix Mercury",
        commence_time_utc="2026-10-08T00:00:00Z",
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw={
            "provider_event_id": "401900001",
            "_wow_secondary_home_team_id": "8",
            "_wow_secondary_away_team_id": "11",
            "official_event_id": None,
            "prediction_authority": False,
        },
    )


def test_wnba_daily_discovery_alias_rewrites_only_after_official_schedule_match(monkeypatch):
    import v17.wnba_spread_event_identity as identity

    captured = {}

    def resolve(**kwargs):
        captured.update(kwargs)
        return {
            "event_id": "wnba-stats-1022600201",
            "identity_provider": "WNBA_OFFICIAL_SCHEDULE_API",
            "identity_alias_provider": "ESPN_SCOREBOARD",
            "identity_verified_at": "2026-10-07T12:00:00+00:00",
            "can_execute": False,
        }

    monkeypatch.setattr(identity, "resolve_wnba_current_event_identity", resolve)
    out = canonicalize_wnba_discovery_identity(_wnba_alias_event())

    assert captured["event_id"] == "espn-401900001"
    assert captured["home_team_id"] == "espn-8"
    assert captured["away_team_id"] == "espn-11"
    assert out.official_event_id == "wnba-stats-1022600201"
    assert out.raw["provider_event_id"] == "401900001"
    assert out.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out.raw["canonical_identity_source"] == "WNBA_OFFICIAL_SCHEDULE_API"
    assert out.raw["prediction_authority"] is False


def test_wnba_daily_discovery_alias_stays_unresolved_when_official_match_fails(monkeypatch):
    import v17.wnba_spread_event_identity as identity

    class IdentityFailure(RuntimeError):
        code = "WNBA_SPREAD_FORWARD_CANONICAL_EVENT_NOT_FOUND"

    def fail(**_kwargs):
        raise IdentityFailure("no exact official event")

    monkeypatch.setattr(identity, "resolve_wnba_current_event_identity", fail)
    out = canonicalize_wnba_discovery_identity(_wnba_alias_event())

    assert out.official_event_id is None
    assert out.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert (
        out.raw["canonical_identity_blocker"]
        == "WNBA_SPREAD_FORWARD_CANONICAL_EVENT_NOT_FOUND"
    )


def test_wnba_daily_discovery_requires_team_aliases_before_official_lookup():
    event = _wnba_alias_event()
    event = DiscoveredEvent(
        **{
            **event.__dict__,
            "raw": {
                "provider_event_id": "401900001",
                "official_event_id": None,
            },
        }
    )
    out = canonicalize_wnba_discovery_identity(event)
    assert out.official_event_id is None
    assert out.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert out.raw["canonical_identity_blocker"] == "WNBA_PROVIDER_TEAM_ALIAS_MISSING"
