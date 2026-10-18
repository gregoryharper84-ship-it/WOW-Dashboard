from types import SimpleNamespace
from dataclasses import replace

import pytest

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
    canonicalize_nba_discovery_identity,
    canonicalize_nfl_discovery_identity,
    canonicalize_ncaaf_discovery_identity,
    canonicalize_nhl_discovery_identity,
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



@pytest.mark.parametrize("sport,failure", [
    ("NFL", "db_client_unavailable"),
    ("NFL", "canonical_resolver_exception"),
    ("NFL", "canonical_match_rejected"),
    ("MLB", "canonical_resolver_exception"),
    ("MLB", "canonical_match_rejected"),
    ("MLB", "canonical_match_empty_game_id"),
])
def test_failed_nfl_or_mlb_canonical_proof_never_allows_model_routing(
    monkeypatch, sport, failure,
):
    """Provider event-id aliases must not become official fitted-scorer IDs."""
    from v17.cross_sport_winner_discovery import (
        DiscoveryInventory, IDENTITY_UNRESOLVED, route_discovered_slate,
    )
    from v17 import nfl_team_event_specialist as nfl
    from v17 import mlb_team_event_hydration as mlb

    if sport == "NFL":
        event = replace(_nfl_alias_event(), official_event_id="espn-401872972")
        api = SimpleNamespace(get_client=lambda: None) if failure == "db_client_unavailable" else _EventApi()
        if failure == "canonical_resolver_exception":
            def fail(*_args, **_kwargs):
                raise RuntimeError("upstream canonical provider unavailable")
            monkeypatch.setattr(nfl, "resolve_nfl_team_event_evidence", fail)
        elif failure == "canonical_match_rejected":
            monkeypatch.setattr(
                nfl, "resolve_nfl_team_event_evidence",
                lambda *_args, **_kwargs: {
                    "ok": False, "code": "NFL_TEAM_EVENT_CANONICAL_IDENTITY_UNVERIFIED",
                },
            )
        resolved = canonicalize_nfl_discovery_identity(
            event,
            req=SimpleNamespace(
                requested_slate_date="2026-10-04",
                requested_timezone="America/Chicago",
            ),
            event_api=api,
            settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
        )
        slate_date = "2026-10-04"
    else:
        event = replace(_mlb_alias_event(), official_event_id="espn-849826")
        if failure == "canonical_resolver_exception":
            def fail(*_args, **_kwargs):
                raise RuntimeError("upstream canonical provider unavailable")
            monkeypatch.setattr(mlb, "resolve_mlb_team_event_evidence", fail)
        elif failure == "canonical_match_rejected":
            monkeypatch.setattr(
                mlb, "resolve_mlb_team_event_evidence",
                lambda *_args, **_kwargs: {
                    "ok": False, "code": "MLB_TEAM_EVENT_CANONICAL_IDENTITY_AMBIGUOUS",
                },
            )
        else:
            monkeypatch.setattr(
                mlb, "resolve_mlb_team_event_evidence",
                lambda *_args, **_kwargs: {"ok": True, "canonical_official_event_id": None},
            )
        resolved = canonicalize_mlb_discovery_identity(
            event,
            req=SimpleNamespace(
                requested_slate_date="2026-10-07",
                requested_timezone="America/Chicago",
            ),
            event_api=_EventApi(),
        )
        slate_date = "2026-10-07"

    assert resolved.official_event_id is None
    assert resolved.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert resolved.raw["canonical_identity_blocker"]
    inv = DiscoveryInventory(
        requested_slate_date=slate_date,
        requested_timezone="America/Chicago",
        events=[resolved],
    )
    invoked = []
    rows = route_discovered_slate(
        inv,
        resolve_model=lambda _event: invoked.append("model") or object(),
        score_row=lambda *_args: invoked.append("scorer") or {
            "probability_publishable": True, "rank_eligible": True,
        },
    )
    assert invoked == []
    assert len(rows) == 1
    assert rows[0].bucket == IDENTITY_UNRESOLVED
    assert rows[0].rank_eligible is False
    assert rows[0].probability_publishable is False



@pytest.mark.parametrize("blocker,visible", [
    ("NBA_CANONICAL_EVENT_NOT_FOUND", "NBA_CANONICAL_EVENT_NOT_FOUND"),
    ("NBA_SCHEDULE_SOURCE_UNAVAILABLE", "NBA_SCHEDULE_SOURCE_UNAVAILABLE"),
    ("sensitive provider message / api-key=NEVER_LOG", None),
    ("A" * 120, None),
])
def test_common_fence_cannot_score_identity_unresolved_alias_or_log_provider_text(
    blocker, visible,
):
    """Even a future buggy canonicalizer must not promote an ESPN alias."""
    from v17.cross_sport_winner_discovery import (
        DiscoveryInventory, IDENTITY_UNRESOLVED, route_discovered_slate,
    )
    event = DiscoveredEvent(
        sport="NBA", league="NBA", sport_key="basketball_nba",
        official_event_id="espn-401999999",
        home_team="Miami Heat", away_team="Orlando Magic",
        commence_time_utc="2026-10-16T23:00:00Z",
        event_status="PREGAME", source="DISCOVERY_FEED",
        raw={
            "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
            "canonical_identity_blocker": blocker,
            "provider_event_id": "espn-401999999",
            "provider_payload": "sensitive provider message / api-key=NEVER_LOG",
        },
    )
    inv = DiscoveryInventory(
        requested_slate_date="2026-10-16",
        requested_timezone="America/Chicago", events=[event],
    )
    calls = []
    routed = route_discovered_slate(
        inv,
        resolve_model=lambda _event: calls.append("model") or object(),
        score_row=lambda *_args: calls.append("scorer") or {
            "probability_publishable": True, "rank_eligible": True,
        },
    )
    assert calls == []
    assert len(routed) == 1
    outcome = routed[0]
    assert outcome.bucket == IDENTITY_UNRESOLVED
    assert outcome.rank_eligible is False
    assert outcome.probability_publishable is False
    assert outcome.detail["model_invoked"] is False
    assert outcome.identity["official_event_id"] is None
    assert outcome.identity["event_key"].startswith("NBA:UNRESOLVED:")
    assert "espn-401999999" not in outcome.identity["event_key"]
    assert outcome.as_dict()["official_event_id"] is None
    assert outcome.detail.get("canonical_identity_blocker") == visible
    assert "NEVER_LOG" not in str(outcome.as_dict())
    assert "provider_payload" not in str(outcome.as_dict())


def test_unverified_event_key_retains_distinct_opaque_reconciliation_identity():
    def build(alias):
        return DiscoveredEvent(
            sport="NBA", league="NBA", sport_key="basketball_nba",
            official_event_id=alias,
            home_team="Miami Heat", away_team="Orlando Magic",
            commence_time_utc="2026-10-16T23:00:00Z",
            event_status="PREGAME", source="DISCOVERY_FEED",
            raw={"canonical_identity_status": "ALIAS_ONLY_UNRESOLVED"},
        )
    first, second = build("espn-401999999"), build("espn-401999998")
    identity_a = first.identity()
    identity_b = second.identity()
    assert identity_a == first.identity(), "opaque alias identity must be deterministic"
    assert identity_a["official_event_id"] is None
    assert identity_b["official_event_id"] is None
    assert identity_a["event_key"].startswith("NBA:UNRESOLVED:")
    assert identity_a["event_key"] != identity_b["event_key"]
    assert "espn-" not in identity_a["event_key"] + identity_b["event_key"]


def test_verified_canonical_identity_still_persists_official_id():
    event = DiscoveredEvent(
        sport="NBA", league="NBA", sport_key="basketball_nba",
        official_event_id="nba-official-0025",
        home_team="Miami Heat", away_team="Orlando Magic",
        commence_time_utc="2026-10-16T23:00:00Z",
        event_status="PREGAME", source="CANONICAL_SCHEDULE",
        raw={"canonical_identity_status": "CANONICAL_RESOLVED"},
    )
    assert event.identity()["official_event_id"] == "nba-official-0025"
    assert event.identity()["event_key"] == "NBA:nba-official-0025"


def test_common_fence_keeps_wrong_date_precedence_before_identity_failure():
    from v17.cross_sport_winner_discovery import (
        DiscoveryInventory, WRONG_DATE, route_discovered_slate,
    )
    event = DiscoveredEvent(
        sport="NBA", league="NBA", sport_key="basketball_nba",
        official_event_id="espn-401999999",
        home_team="Miami Heat", away_team="Orlando Magic",
        commence_time_utc="2026-10-17T23:00:00Z",
        event_status="PREGAME", source="DISCOVERY_FEED",
        raw={
            "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
            "canonical_identity_blocker": "NBA_CANONICAL_EVENT_NOT_FOUND",
        },
    )
    inv = DiscoveryInventory(
        requested_slate_date="2026-10-16",
        requested_timezone="America/Chicago", events=[event],
    )
    calls = []
    routed = route_discovered_slate(
        inv,
        resolve_model=lambda _event: calls.append("model") or object(),
        score_row=lambda *_args: calls.append("score") or {},
    )
    assert calls == []
    assert len(routed) == 1
    assert routed[0].bucket == WRONG_DATE
    # Terminal precedence stays WRONG_DATE, but the false official-ID field
    # is never persisted even for a row outside this slate.
    assert routed[0].identity["official_event_id"] is None
    assert routed[0].identity["event_key"].startswith("NBA:UNRESOLVED:")
    assert "espn-401999999" not in routed[0].identity["event_key"]
    assert "canonical_identity_blocker" not in routed[0].detail
    assert routed[0].probability_publishable is False


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



@pytest.mark.parametrize("failure", [
    "missing_team_alias",
    "official_lookup_raises",
    "official_lookup_missing_id",
])
def test_wnba_unverified_espn_alias_can_never_be_promoted_to_official_id_or_scored(
    monkeypatch, failure,
):
    """An ESPN raw event id cannot bypass the WNBA schedule-identity gate."""
    from v17 import wnba_spread_event_identity as identity
    from v17.cross_sport_winner_discovery import (
        DiscoveryInventory, IDENTITY_UNRESOLVED, route_discovered_slate,
    )

    original = _wnba_alias_event()
    event = replace(original, official_event_id="espn-401900001")
    if failure == "missing_team_alias":
        event = replace(
            event, raw={"provider_event_id": "401900001"},
        )
    elif failure == "official_lookup_raises":
        class OfficialScheduleHold(RuntimeError):
            code = "WNBA_SPREAD_FORWARD_CANONICAL_EVENT_NOT_FOUND"

        def failed_match(**_kwargs):
            raise OfficialScheduleHold("no official WNBA schedule match")

        monkeypatch.setattr(
            identity, "resolve_wnba_current_event_identity", failed_match,
        )
    else:
        monkeypatch.setattr(
            identity, "resolve_wnba_current_event_identity",
            lambda **_kwargs: {"event_id": "", "can_execute": False},
        )

    resolved = canonicalize_wnba_discovery_identity(event)
    assert resolved.official_event_id is None
    assert resolved.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert resolved.raw["provider_event_id"] == "401900001"
    assert resolved.raw["canonical_identity_blocker"].startswith("WNBA_")

    inv = DiscoveryInventory(
        requested_slate_date="2026-10-07",
        requested_timezone="America/Chicago",
        events=[resolved],
    )
    invoked = []
    rows = route_discovered_slate(
        inv,
        resolve_model=lambda _event: invoked.append("model") or object(),
        score_row=lambda *_args: invoked.append("score") or {
            "probability_publishable": True,
            "rank_eligible": True,
        },
    )
    assert invoked == [], "identity-unresolved WNBA must not reach a scorer"
    assert len(rows) == 1
    assert rows[0].bucket == IDENTITY_UNRESOLVED
    assert rows[0].rank_eligible is False
    assert rows[0].probability_publishable is False


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



def _nhl_alias_event():
    return DiscoveredEvent(
        sport="NHL",
        league="NHL",
        sport_key="icehockey_nhl",
        official_event_id=None,
        home_team="Dallas Stars",
        away_team="Pittsburgh Penguins",
        commence_time_utc="2026-10-08T00:00:00Z",
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw={
            "provider_event_id": "401900777",
            "official_event_id": None,
            "prediction_authority": False,
        },
    )


def test_nhl_daily_discovery_alias_rewrites_only_after_first_party_match(monkeypatch):
    import v17.nhl_event_identity as identity

    captured = {}

    def resolve(**kwargs):
        captured.update(kwargs)
        return {
            "event_id": "2026020041",
            "identity_provider": "NHL_PUBLIC_WEB_API",
            "identity_verified_at": "2026-10-07T13:00:00+00:00",
            "market_features_used": False,
            "can_execute": False,
        }

    monkeypatch.setattr(identity, "resolve_nhl_current_event_identity", resolve)
    out = canonicalize_nhl_discovery_identity(_nhl_alias_event())

    assert captured["home_team"] == "Dallas Stars"
    assert captured["away_team"] == "Pittsburgh Penguins"
    assert out.official_event_id == "2026020041"
    assert out.raw["provider_event_id"] == "401900777"
    assert out.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out.raw["canonical_identity_source"] == "NHL_PUBLIC_WEB_API"
    assert out.raw["canonical_identity_market_features_used"] is False
    assert out.raw["prediction_authority"] is False


def test_nhl_daily_discovery_alias_stays_unresolved_on_official_failure(monkeypatch):
    import v17.nhl_event_identity as identity

    class IdentityFailure(RuntimeError):
        code = "NHL_CANONICAL_EVENT_NOT_FOUND"

    monkeypatch.setattr(
        identity,
        "resolve_nhl_current_event_identity",
        lambda **_kwargs: (_ for _ in ()).throw(IdentityFailure()),
    )
    out = canonicalize_nhl_discovery_identity(_nhl_alias_event())

    assert out.official_event_id is None
    assert out.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert out.raw["canonical_identity_blocker"] == "NHL_CANONICAL_EVENT_NOT_FOUND"



def _ncaaf_alias_event():
    return DiscoveredEvent(
        sport="NCAAF",
        league="NCAAF",
        sport_key="americanfootball_ncaaf",
        official_event_id=None,
        home_team="Arizona State Sun Devils",
        away_team="Baylor Bears",
        commence_time_utc="2026-10-04T02:30:00Z",
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw={
            "provider_event_id": "401900999",
            "_wow_secondary_event_id": "401900999",
            "official_event_id": None,
            "prediction_authority": False,
        },
    )


def test_ncaaf_daily_discovery_rewrites_only_after_cfbd_match(monkeypatch):
    import v17.ncaaf_event_identity as identity

    monkeypatch.setattr(
        identity,
        "resolve_ncaaf_current_event_identity",
        lambda **_kwargs: {
            "event_id": "401856817",
            "identity_provider": "CFBD:/games",
            "identity_resolution": "CFBD_EXACT_PARTICIPANTS_START_MATCH",
            "prediction_authority": False,
            "can_execute": False,
        },
    )
    out = canonicalize_ncaaf_discovery_identity(_ncaaf_alias_event())
    assert out.official_event_id == "401856817"
    assert out.raw["provider_event_id"] == "401900999"
    assert out.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out.raw["canonical_identity_source"] == "CFBD:/games"
    assert out.raw["canonical_identity_market_features_used"] is False


def test_ncaaf_daily_discovery_alias_stays_unresolved_on_cfbd_failure(monkeypatch):
    import v17.ncaaf_event_identity as identity

    class IdentityFailure(RuntimeError):
        code = "NCAAF_CANONICAL_EVENT_NOT_FOUND"

    def fail(**_kwargs):
        raise IdentityFailure("no exact CFBD event")

    monkeypatch.setattr(identity, "resolve_ncaaf_current_event_identity", fail)
    out = canonicalize_ncaaf_discovery_identity(_ncaaf_alias_event())
    assert out.official_event_id is None
    assert out.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert out.raw["canonical_identity_blocker"] == "NCAAF_CANONICAL_EVENT_NOT_FOUND"



def _nba_alias_event():
    return DiscoveredEvent(
        sport="NBA",
        league="NBA",
        sport_key="basketball_nba",
        official_event_id=None,
        home_team="Los Angeles Lakers",
        away_team="Oklahoma City Thunder",
        commence_time_utc="2026-10-08T00:30:00Z",
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw={
            "provider_event_id": "401999999",
            "_wow_secondary_event_id": "401999999",
            "_wow_secondary_home_team_id": "13",
            "_wow_secondary_away_team_id": "24",
            "official_event_id": None,
            "prediction_authority": False,
        },
    )


def test_nba_daily_discovery_rewrites_only_after_schedule_match(monkeypatch):
    import v17.nba_event_identity as identity

    monkeypatch.setattr(
        identity,
        "resolve_nba_current_event_identity",
        lambda **_kwargs: {
            "event_id": "espn-401950001",
            "identity_provider": "SPORTSDATAVERSE_ESPN",
            "identity_resolution": "SPORTSDATAVERSE_SCHEDULE_EXACT_TEAM_DATE_MATCH",
            "prediction_authority": False,
            "can_execute": False,
        },
    )
    out = canonicalize_nba_discovery_identity(_nba_alias_event())
    assert out.official_event_id == "espn-401950001"
    assert out.raw["provider_event_id"] == "401999999"
    assert out.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out.raw["canonical_identity_source"] == "SPORTSDATAVERSE_ESPN"
    assert out.raw["canonical_identity_market_features_used"] is False


def test_nba_daily_discovery_alias_stays_unresolved_without_schedule_match(monkeypatch):
    import v17.nba_event_identity as identity

    class IdentityFailure(RuntimeError):
        code = "NBA_CANONICAL_EVENT_NOT_FOUND"

    def fail(**_kwargs):
        raise IdentityFailure("no exact schedule event")

    monkeypatch.setattr(identity, "resolve_nba_current_event_identity", fail)
    out = canonicalize_nba_discovery_identity(_nba_alias_event())
    assert out.official_event_id is None
    assert out.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert out.raw["canonical_identity_blocker"] == "NBA_CANONICAL_EVENT_NOT_FOUND"


@pytest.mark.parametrize("sport", ["NFL", "WNBA"])
def test_unresolved_canonicalized_aliases_keep_distinct_opaque_terminal_keys(sport):
    """Two provider IDs for identical teams/start must remain two HELD receipts."""
    from v17.cross_sport_winner_discovery import (
        DiscoveryInventory, IDENTITY_UNRESOLVED, route_discovered_slate,
    )

    if sport == "NFL":
        first = _nfl_alias_event()
        slate = "2026-10-04"

        def canonicalize(event):
            return canonicalize_nfl_discovery_identity(
                event,
                req=SimpleNamespace(
                    requested_slate_date=slate,
                    requested_timezone="America/Chicago",
                ),
                event_api=SimpleNamespace(),  # No official ledger: HOLD
                settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
            )

    else:
        first = _wnba_alias_event()
        slate = "2026-10-07"

        def canonicalize(event):
            return canonicalize_wnba_discovery_identity(event)

    aliases = ("provider-alias-001", "provider-alias-002")
    events = []
    for alias in aliases:
        raw = {
            **first.raw,
            "provider_event_id": alias,
            # Missing WNBA team alias makes canonical matching unavailable.
            "_wow_secondary_home_team_id": "",
        }
        # NFL intentionally uses an unavailable ledger; WNBA lacks team alias.
        unresolved = canonicalize(replace(first, raw=raw, official_event_id=None))
        assert unresolved.official_event_id is None
        assert unresolved.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
        events.append(unresolved)

    keys = [event.identity()["event_key"] for event in events]
    assert len(set(keys)) == 2
    assert all(key.startswith(sport + ":UNRESOLVED:") for key in keys)
    assert keys == [event.identity()["event_key"] for event in events]
    assert all(alias not in "".join(keys) for alias in aliases)
    assert all(event.identity()["official_event_id"] is None for event in events)

    calls = []
    rows = route_discovered_slate(
        DiscoveryInventory(
            requested_slate_date=slate,
            requested_timezone="America/Chicago",
            events=events,
        ),
        resolve_model=lambda _event: calls.append("model") or object(),
        score_row=lambda *_args: calls.append("scorer") or {
            "probability_publishable": True,
            "rank_eligible": True,
        },
    )
    assert len(rows) == 2
    assert {row.identity["event_key"] for row in rows} == set(keys)
    assert all(row.bucket == IDENTITY_UNRESOLVED for row in rows)
    assert all(row.probability_publishable is False for row in rows)
    assert all(row.rank_eligible is False for row in rows)
    assert calls == []


def test_secondary_provider_alias_identity_survives_missing_official_id():
    """Some providers retain alias only in the secondary raw event field."""
    base = _wnba_alias_event()
    def held(alias):
        raw = dict(base.raw)
        raw.pop("provider_event_id", None)
        raw["_wow_secondary_event_id"] = alias
        raw["_wow_secondary_home_team_id"] = ""
        return canonicalize_wnba_discovery_identity(
            replace(base, raw=raw, official_event_id=None)
        )
    a, b = held("secondary-001"), held("secondary-002")
    assert a.official_event_id is None and b.official_event_id is None
    assert a.identity()["event_key"] != b.identity()["event_key"]
    assert "secondary-" not in a.identity()["event_key"] + b.identity()["event_key"]


@pytest.mark.parametrize("source_shape", ["uuid_only", "event_field_only"])
def test_actual_nfl_failed_canonicalizer_retains_distinct_source_alias_keys(source_shape):
    """A uuid-only feed or event-field-only alias must not collapse HELD rows."""
    from datetime import datetime, timezone
    from v17.cross_sport_winner_discovery import (
        DiscoveryInventory, IDENTITY_UNRESOLVED, normalize_discovered_event,
        route_discovered_slate,
    )

    events = []
    aliases = ("a827e4a1-1111-4567-890a-123456789aaa",
               "b927e4a1-2222-4567-890a-123456789bbb")
    for alias in aliases:
        if source_shape == "uuid_only":
            discovered = normalize_discovered_event(
                {
                    "event_uuid": alias,
                    "home_team": "Chicago Bears",
                    "away_team": "New York Jets",
                    "start_time": "2026-10-04T17:00:00Z",
                    "status": "scheduled",
                },
                sport="NFL",
                sport_key="americanfootball_nfl",
                source="DISCOVERY_FEED",
                now=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
            assert discovered.official_event_id == alias
            assert "provider_event_id" not in discovered.raw
        else:
            # Some first-party callers construct DiscoveredEvent with the
            # untrusted alias on the event, and no private raw copy.
            discovered = replace(
                _nfl_alias_event(),
                official_event_id=alias,
                raw={"prediction_authority": False},
            )
        held = canonicalize_nfl_discovery_identity(
            discovered,
            req=SimpleNamespace(
                requested_slate_date="2026-10-04",
                requested_timezone="America/Chicago",
            ),
            event_api=SimpleNamespace(),  # no ledger client -> fail closed
            settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
        )
        assert held.official_event_id is None
        assert held.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
        assert held.raw["canonical_identity_blocker"] == "NFL_EVENT_LEDGER_CLIENT_UNAVAILABLE"
        if source_shape == "uuid_only":
            assert held.raw["event_uuid"] == alias
        else:
            assert held.raw["provider_event_id"] == alias
        events.append(held)

    keys = [event.identity()["event_key"] for event in events]
    assert len(set(keys)) == 2
    assert all(key.startswith("NFL:UNRESOLVED:") for key in keys)
    assert all(alias not in "".join(keys) for alias in aliases)
    assert keys == [event.identity()["event_key"] for event in events]
    assert all(event.identity()["official_event_id"] is None for event in events)

    invoked = []
    rows = route_discovered_slate(
        DiscoveryInventory(
            requested_slate_date="2026-10-04",
            requested_timezone="America/Chicago",
            events=events,
        ),
        resolve_model=lambda _event: invoked.append("resolve") or object(),
        score_row=lambda *_args: invoked.append("score") or {
            "probability_publishable": True,
            "rank_eligible": True,
        },
    )
    assert len(rows) == 2
    assert {row.identity["event_key"] for row in rows} == set(keys)
    assert all(row.bucket == IDENTITY_UNRESOLVED for row in rows)
    assert all(row.probability_publishable is False for row in rows)
    assert all(row.rank_eligible is False for row in rows)
    assert invoked == []


def test_nfl_failed_ledger_keys_match_discovery_id_over_shared_event_uuid():
    """Different primary IDs must stay distinct even with identical UUID alias."""
    from datetime import datetime, timezone
    from v17.cross_sport_winner_discovery import (
        DiscoveryInventory, IDENTITY_UNRESOLVED, normalize_discovered_event,
        route_discovered_slate,
    )

    held_events = []
    for event_id in ("primary-aaa", "primary-bbb"):
        event = normalize_discovered_event(
            {
                "id": event_id,
                "event_uuid": "shared-uuid-from-provider",
                "home_team": "Chicago Bears",
                "away_team": "New York Jets",
                "start_time": "2026-10-04T17:00:00Z",
                "status": "scheduled",
            },
            sport="NFL",
            sport_key="americanfootball_nfl",
            source="DISCOVERY_FEED",
            now=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        assert event.official_event_id == event_id
        held = canonicalize_nfl_discovery_identity(
            event,
            req=SimpleNamespace(
                requested_slate_date="2026-10-04",
                requested_timezone="America/Chicago",
            ),
            event_api=SimpleNamespace(),
            settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
        )
        assert held.official_event_id is None
        assert held.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
        held_events.append(held)

    keys = [event.identity()["event_key"] for event in held_events]
    assert len(set(keys)) == 2
    assert all(key.startswith("NFL:UNRESOLVED:") for key in keys)
    assert all(secret not in "".join(keys) for secret in (
        "primary-aaa", "primary-bbb", "shared-uuid-from-provider",
    ))
    # Terminal routing keeps both IDs without scorer or model invocation.
    calls = []
    rows = route_discovered_slate(
        DiscoveryInventory(
            requested_slate_date="2026-10-04",
            requested_timezone="America/Chicago",
            events=held_events,
        ),
        resolve_model=lambda *_: calls.append("model") or object(),
        score_row=lambda *_: calls.append("score") or {
            "rank_eligible": True,
            "probability_publishable": True,
        },
    )
    assert len(rows) == 2
    assert {row.identity["event_key"] for row in rows} == set(keys)
    assert all(row.bucket == IDENTITY_UNRESOLVED for row in rows)
    assert all(row.probability_publishable is False for row in rows)
    assert calls == []


@pytest.mark.parametrize("alias_case", [
    "two_ids_one_provider",
    "two_ids_one_event_id",
    "two_ids_one_uuid",
    "one_id_two_providers",
    "one_id_two_secondary_aliases",
])
def test_multiple_private_aliases_keep_unresolved_terminal_candidates_distinct(alias_case):
    """A shared single alias must never erase another unverified source candidate.

    When official ledger evidence is unavailable, every provided private
    identifier contributes to the *opaque* HOLD key. No provider field
    becomes an official id and no scorer is invoked.
    """
    from datetime import datetime, timezone
    from v17.cross_sport_winner_discovery import (
        DiscoveryInventory, IDENTITY_UNRESOLVED, normalize_discovered_event,
        route_discovered_slate,
    )

    alias_cases = {
        "two_ids_one_provider": (
            {"id": "a-id", "provider_event_id": "shared-provider"},
            {"id": "b-id", "provider_event_id": "shared-provider"},
        ),
        "two_ids_one_event_id": (
            {"id": "a-id", "event_id": "shared-event"},
            {"id": "b-id", "event_id": "shared-event"},
        ),
        "two_ids_one_uuid": (
            {"id": "a-id", "event_uuid": "shared-uuid"},
            {"id": "b-id", "event_uuid": "shared-uuid"},
        ),
        "one_id_two_providers": (
            {"id": "shared-id", "provider_event_id": "provider-a"},
            {"id": "shared-id", "provider_event_id": "provider-b"},
        ),
        "one_id_two_secondary_aliases": (
            {"id": "shared-id", "_wow_secondary_event_id": "secondary-a"},
            {"id": "shared-id", "_wow_secondary_event_id": "secondary-b"},
        ),
    }
    candidates = []
    for raw_ids in alias_cases[alias_case]:
        event = normalize_discovered_event(
            {
                **raw_ids,
                "home_team": "Chicago Bears",
                "away_team": "New York Jets",
                "start_time": "2026-10-04T17:00:00Z",
                "status": "scheduled",
            },
            sport="NFL",
            sport_key="americanfootball_nfl",
            source="DISCOVERY_FEED",
            now=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        held = canonicalize_nfl_discovery_identity(
            event,
            req=SimpleNamespace(
                requested_slate_date="2026-10-04",
                requested_timezone="America/Chicago",
            ),
            event_api=SimpleNamespace(),
            settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
        )
        assert held.official_event_id is None
        assert held.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
        candidates.append(held)

    keys = [c.identity()["event_key"] for c in candidates]
    assert len(set(keys)) == 2
    assert all(k.startswith("NFL:UNRESOLVED:") for k in keys)
    secret_identifiers = {
        str(value)
        for raw_ids in alias_cases[alias_case]
        for value in raw_ids.values()
    }
    assert all(secret not in "".join(keys) for secret in secret_identifiers)
    assert candidates[0].identity()["event_key"] == candidates[0].identity()["event_key"]

    called = []
    rows = route_discovered_slate(
        DiscoveryInventory(
            requested_slate_date="2026-10-04",
            requested_timezone="America/Chicago",
            events=candidates,
        ),
        resolve_model=lambda *_: called.append("model") or object(),
        score_row=lambda *_: called.append("score") or {
            "probability_publishable": True,
            "rank_eligible": True,
        },
    )
    assert len(rows) == 2
    assert {row.identity["event_key"] for row in rows} == set(keys)
    assert all(row.bucket == IDENTITY_UNRESOLVED for row in rows)
    assert all(row.probability_publishable is False for row in rows)
    assert all(row.rank_eligible is False for row in rows)
    assert called == []


@pytest.mark.parametrize("alias_provider", ["ODDS_API_EVENTS", "ESPN_SCOREBOARD"])
def test_discovery_preserves_same_fixture_distinct_unverified_provider_aliases(
    alias_provider, monkeypatch,
):
    """Reproduce real discovery path, not just DiscoveredEvent.identity()."""
    from datetime import datetime, timezone
    from v17 import cross_sport_winner_discovery as discovery
    from v17 import cross_sport_discovery_feed as feed
    from v17 import quota_aware_degraded_discovery as quota

    now = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
    aliases = ("event-source-A", "event-source-B")
    base = {
        "home_team": "Chicago Bears",
        "away_team": "New York Jets",
        "commence_time": "2026-10-18T21:00:00Z",
        "status": "scheduled",
    }
    if alias_provider == "ODDS_API_EVENTS":
        provider_rows = [
            feed._odds_api_alias_row({**base, "id": alias}, sport_key="americanfootball_nfl")
            for alias in aliases
        ]
    else:
        provider_rows = [
            {**base, "id": alias, "discovery_provider": "ESPN_SCOREBOARD"}
            for alias in aliases
        ]
        original_normalize = discovery.normalize_discovered_event

        def installed_normalizer(raw, *args, **kwargs):
            return quota._normalize_public_alias(
                raw, original_normalize(raw, *args, **kwargs)
            )

        monkeypatch.setattr(discovery, "normalize_discovered_event", installed_normalizer)

    calls = []
    def fetch(_family, _target):
        calls.append(1)
        return provider_rows if len(calls) == 1 else []

    inventory = discovery.discover_winner_slate(
        requested_slate_date="2026-10-18",
        requested_timezone="America/Chicago",
        fetch_sport_events=fetch,
        supported_sports=("NFL",),
        now=now,
        budget_seconds=30,
    )
    assert len(inventory.events) == 2
    assert all(event.official_event_id is None for event in inventory.events)
    keys = [event.identity()["event_key"] for event in inventory.events]
    assert len(set(keys)) == 2
    assert all(key.startswith("NFL:UNRESOLVED:") for key in keys)
    assert all(alias not in "".join(keys) for alias in aliases)
    assert sum(detail["duplicate_rows_suppressed"] for detail in inventory.acquisition_details) == 0
    assert sum(detail["events_returned"] for detail in inventory.acquisition_details) == 2
    called = []
    rows = discovery.route_discovered_slate(
        inventory,
        resolve_model=lambda *_: called.append("model") or object(),
        score_row=lambda *_: called.append("score") or {
            "probability_publishable": True,
            "rank_eligible": True,
        },
    )
    assert len(rows) == 2
    assert {row.identity["event_key"] for row in rows} == set(keys)
    assert all(row.bucket == discovery.IDENTITY_UNRESOLVED for row in rows)
    assert all(row.probability_publishable is False for row in rows)
    assert all(row.rank_eligible is False for row in rows)
    assert called == []


def test_discovery_same_source_alias_deduplicates_exact_fixture_without_scoring():
    from datetime import datetime, timezone
    from v17 import cross_sport_winner_discovery as discovery
    from v17 import cross_sport_discovery_feed as feed

    same_event = feed._odds_api_alias_row(
        {
            "id": "one-provider-alias",
            "home_team": "Chicago Bears",
            "away_team": "New York Jets",
            "commence_time": "2026-10-18T21:00:00Z",
            "status": "scheduled",
        },
        sport_key="americanfootball_nfl",
    )
    attempted = []

    def fetch(_family, _target):
        attempted.append(1)
        return [same_event, dict(reversed(list(same_event.items())))] if len(attempted) == 1 else []

    inventory = discovery.discover_winner_slate(
        requested_slate_date="2026-10-18",
        requested_timezone="America/Chicago",
        fetch_sport_events=fetch,
        supported_sports=("NFL",),
        now=datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc),
        budget_seconds=30,
    )
    assert len(inventory.events) == 1
    assert sum(detail["duplicate_rows_suppressed"] for detail in inventory.acquisition_details) == 1
    assert inventory.events[0].identity()["official_event_id"] is None


@pytest.mark.parametrize("case", [
    "distinct_teams_no_ids",
    "same_teams_different_start_no_ids",
    "different_source_aliases_without_official_ids",
    "different_secondary_aliases_without_official_ids",
    "same_teams_same_start_different_private_rows",
])
def test_full_discovery_never_collapses_distinct_idless_candidates(case):
    """No SPORT:UNRESOLVED constant may suppress separate acquired events."""
    from datetime import datetime, timezone
    from v17 import cross_sport_winner_discovery as discovery

    base = {
        "home_team": "Chicago Bears",
        "away_team": "New York Jets",
        "commence_time": "2026-10-18T21:00:00Z",
        "status": "scheduled",
    }
    first, second = dict(base), dict(base)
    if case == "distinct_teams_no_ids":
        second.update(home_team="Green Bay Packers", away_team="Detroit Lions")
    elif case == "same_teams_different_start_no_ids":
        second["commence_time"] = "2026-10-18T23:00:00Z"
    elif case == "different_source_aliases_without_official_ids":
        first["provider_event_id"] = "alias-A"
        second["provider_event_id"] = "alias-B"
    elif case == "different_secondary_aliases_without_official_ids":
        first["_wow_secondary_event_id"] = "secondary-A"
        second["_wow_secondary_event_id"] = "secondary-B"
    else:
        first["game_sequence"] = 1
        second["game_sequence"] = 2

    called = []
    def fetch(_family, _target):
        called.append("fetch")
        return [first, second] if len(called) == 1 else []

    inventory = discovery.discover_winner_slate(
        requested_slate_date="2026-10-18",
        requested_timezone="America/Chicago",
        fetch_sport_events=fetch,
        supported_sports=("NFL",),
        now=datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc),
        budget_seconds=30,
    )
    assert len(inventory.events) == 2
    assert sum(d["duplicate_rows_suppressed"] for d in inventory.acquisition_details) == 0
    keys = [e.identity()["event_key"] for e in inventory.events]
    assert len(set(keys)) == 2
    assert all(key.startswith("NFL:UNRESOLVED:") for key in keys)
    assert all(e.identity()["official_event_id"] is None for e in inventory.events)
    assert "alias-" not in str(keys) and "secondary-" not in str(keys)

    invoked = []
    routed = discovery.route_discovered_slate(
        inventory,
        resolve_model=lambda *_: invoked.append("resolve") or object(),
        score_row=lambda *_: invoked.append("score") or {
            "probability_publishable": True,
            "rank_eligible": True,
        },
    )
    assert len(routed) == 2
    assert all(r.bucket == discovery.IDENTITY_UNRESOLVED for r in routed)
    assert all(r.probability_publishable is False for r in routed)
    assert invoked == []
    assert discovery.reconcile(inventory, routed)["row_reconciliation"] == "PASS"


def test_idless_exact_duplicate_with_reordered_fields_is_deduped_once():
    from datetime import datetime, timezone
    from v17 import cross_sport_winner_discovery as discovery

    row = {
        "home_team": "Chicago Bears", "away_team": "New York Jets",
        "commence_time": "2026-10-18T21:00:00Z", "status": "scheduled",
    }
    seen = []
    def fetch(_family, _target):
        seen.append(1)
        return [row, dict(reversed(list(row.items())))] if len(seen) == 1 else []
    inventory = discovery.discover_winner_slate(
        requested_slate_date="2026-10-18",
        requested_timezone="America/Chicago",
        fetch_sport_events=fetch,
        supported_sports=("NFL",),
        now=datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc),
        budget_seconds=30,
    )
    assert len(inventory.events) == 1
    assert sum(d["duplicate_rows_suppressed"] for d in inventory.acquisition_details) == 1


@pytest.mark.parametrize("n_aliases", [2, 3])
def test_full_scan_scores_exactly_one_canonical_event_from_multiple_aliases(n_aliases):
    """Provider aliases all resolve to one official NFL schedule game."""
    from datetime import datetime, timezone
    from v17 import cross_sport_winner_discovery as discovery
    from v17 import cross_sport_discovery_feed as feed

    aliases = [f"odds-private-alias-{i}" for i in range(n_aliases)]
    rows = [
        feed._odds_api_alias_row({
            "id": alias,
            "home_team": "Chicago Bears",
            "away_team": "New York Jets",
            "commence_time": "2026-10-18T21:00:00Z",
            "status": "scheduled",
        }, sport_key="americanfootball_nfl")
        for alias in aliases
    ]
    fetch_calls = []
    def fetch(_family, _target):
        fetch_calls.append(1)
        return rows if len(fetch_calls) == 1 else []

    verified_id = "2026_05_NYJ_CHI"
    def canonicalize(event):
        assert event.official_event_id is None
        return replace(
            event,
            official_event_id=verified_id,
            raw={
                **event.raw,
                "canonical_identity_status": "CANONICAL_RESOLVED",
                "canonical_identity_source": "CANONICAL_NFLVERSE_LEDGER",
            },
        )

    model_calls, scorer_calls = [], []
    def resolve_model(event):
        model_calls.append(event.official_event_id)
        return SimpleNamespace(supported_regimes=("REGULAR_SEASON",))

    def score(event, _model):
        scorer_calls.append(event.official_event_id)
        return {
            "probability_publishable": True,
            "rank_eligible": True,
            "model_status": "FITTED_SPECIALIST_COMPLETED",
        }

    result = discovery.run_cross_sport_winner_scan(
        requested_slate_date="2026-10-18",
        requested_timezone="America/Chicago",
        fetch_sport_events=fetch,
        supported_sports=("NFL",),
        canonicalize_identity=canonicalize,
        resolve_model=resolve_model,
        score_row=score,
        now=datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc),
        budget_seconds=30,
    )
    terminal_rows = result["rows"]
    assert len(terminal_rows) == n_aliases
    assert result["discovery"]["events_discovered"] == n_aliases
    assert result["reconciliation"]["events_accounted"] == n_aliases
    assert result["reconciliation"]["row_reconciliation"] == "PASS"
    assert model_calls == [verified_id] and scorer_calls == [verified_id]

    published = [row for row in terminal_rows if row["probability_publishable"]]
    held = [row for row in terminal_rows if row["bucket"] == discovery.OTHER_GOVERNED_HOLD]
    assert len(published) == 1 and len(held) == n_aliases - 1
    assert published[0]["bucket"] == discovery.MODEL_COMPLETED
    assert published[0]["event_key"] == f"NFL:{verified_id}"
    assert published[0]["rank_eligible"] is True
    assert all(row["rank_eligible"] is False for row in held)
    assert all(row["probability_publishable"] is False for row in held)
    assert all(row["official_event_id"] is None for row in held)
    assert all(row["detail"] == {
        "reason": "DUPLICATE_CANONICAL_EVENT_HELD", "model_invoked": False,
    } for row in held)
    assert len({row["event_key"] for row in terminal_rows}) == n_aliases
    assert all(alias not in str(terminal_rows) for alias in aliases)
    assert result["can_execute"] is False


def test_distinct_verified_canonical_games_are_scored_once_each():
    from datetime import datetime, timezone
    from v17 import cross_sport_winner_discovery as discovery
    from v17 import cross_sport_discovery_feed as feed

    raw_rows = [
        feed._odds_api_alias_row({
            "id": f"private-{index}",
            "home_team": "Chicago Bears",
            "away_team": "New York Jets",
            "commence_time": "2026-10-18T21:00:00Z",
            "status": "scheduled",
        }, sport_key="americanfootball_nfl")
        for index in range(2)
    ]
    count = []
    def fetch(_family, _target):
        count.append(1)
        return raw_rows if len(count) == 1 else []

    score_ids = []
    result = discovery.run_cross_sport_winner_scan(
        requested_slate_date="2026-10-18",
        requested_timezone="America/Chicago",
        fetch_sport_events=fetch,
        supported_sports=("NFL",),
        canonicalize_identity=lambda event: replace(
            event, official_event_id=(
                "CANONICAL_ID_A" if event.raw["provider_event_id"] == "private-0"
                else "CANONICAL_ID_B"
            ), raw={**event.raw, "canonical_identity_status": "CANONICAL_RESOLVED"},
        ),
        resolve_model=lambda *_: SimpleNamespace(supported_regimes=("REGULAR_SEASON",)),
        score_row=lambda event, _: (
            score_ids.append(event.official_event_id) or
            {"probability_publishable": True, "rank_eligible": True}
        ),
        now=datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc),
        budget_seconds=30,
    )
    assert len(score_ids) == 2 and len(set(score_ids)) == 2
    assert sum(row["probability_publishable"] for row in result["rows"]) == 2
    assert result["reconciliation"]["row_reconciliation"] == "PASS"


def test_real_nfl_ledger_canonicalizer_duplicate_aliases_publish_only_one(monkeypatch):
    """Two provider IDs match the SAME official NFL schedule row once."""
    from datetime import datetime, timezone
    from v17 import cross_sport_winner_discovery as discovery
    from v17 import cross_sport_discovery_feed as feed
    import v17.nfl_team_event_specialist as specialist

    provider_ids = ("odds-alias-A", "odds-alias-B")
    resolver_calls = []
    api = _EventApi()
    def resolve_nfl(probe, *, db):
        assert db is api.db
        resolver_calls.append(probe.official_event_id)
        return {
            "ok": True,
            "canonical_event_id": "2026_05_NYJ_CHI",
            "identity_resolution": "PROVIDER_ID_TO_CANONICAL_SCHEDULE_MATCH",
            "canonical_source_snapshot_id": "official-fixture-test",
        }

    monkeypatch.setattr(specialist, "resolve_nfl_team_event_evidence", resolve_nfl)
    source_rows = [
        feed._odds_api_alias_row({
            "id": pid,
            "home_team": "Chicago Bears",
            "away_team": "New York Jets",
            "commence_time": "2026-10-18T21:00:00Z",
            "status": "scheduled",
        }, sport_key="americanfootball_nfl")
        for pid in provider_ids
    ]
    fetch_calls = []
    def fetch(_family, _target):
        fetch_calls.append(1)
        return source_rows if len(fetch_calls) == 1 else []

    req = SimpleNamespace(
        requested_slate_date="2026-10-18",
        requested_timezone="America/Chicago",
    )
    scored = []
    result = discovery.run_cross_sport_winner_scan(
        requested_slate_date=req.requested_slate_date,
        requested_timezone=req.requested_timezone,
        fetch_sport_events=fetch,
        supported_sports=("NFL",),
        canonicalize_identity=lambda event: canonicalize_nfl_discovery_identity(
            event,
            req=req,
            event_api=api,
            settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
        ),
        resolve_model=lambda *_: SimpleNamespace(supported_regimes=("REGULAR_SEASON",)),
        score_row=lambda event, _: (
            scored.append(event.official_event_id) or
            {"probability_publishable": True, "rank_eligible": True}
        ),
        now=datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc),
        budget_seconds=30,
    )
    assert resolver_calls == list(provider_ids)
    assert scored == ["2026_05_NYJ_CHI"]
    assert len(result["rows"]) == 2
    assert len([r for r in result["rows"] if r["probability_publishable"]]) == 1
    held = [r for r in result["rows"] if r["bucket"] == discovery.OTHER_GOVERNED_HOLD]
    assert len(held) == 1
    assert held[0]["detail"]["reason"] == "DUPLICATE_CANONICAL_EVENT_HELD"
    assert held[0]["official_event_id"] is None
    assert held[0]["rank_eligible"] is False
    assert all(alias not in str(result["rows"]) for alias in provider_ids)
    assert len({r["event_key"] for r in result["rows"]}) == 2
    assert result["reconciliation"]["row_reconciliation"] == "PASS"
    assert result["can_execute"] is False
