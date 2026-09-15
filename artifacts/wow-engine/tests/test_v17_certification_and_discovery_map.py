"""CD-001..CD-010: certification separation and the authoritative discovery map.

Source: WOW-PATCH-2026-09-14-NFL-CERTIFICATION-AND-AUTHORITATIVE-DISCOVERY-MAP
and GOVERNANCE-DECISION-2026-09-14-NFL.

Two things are pinned here.

**Certification is not registration.** A registered, UP, scoring bridge proves
routing capability. It does not by itself prove calibrated prospective fitness
or promote a sport into the certified set. NFL is now independently certified
through its governed champion + calibrator evidence, so registration must
preserve that catalog state rather than create it.

**Discovery keys are verified, never guessed.** Provider sport ids come from the
live TheRundown registry. A family the provider does not carry (boxing) returns
``NO_CONFIGURED_DISCOVERY_FEED`` — an explicit acquisition blocker, distinct from
a query that succeeded and found nothing. And a discovered regime variant
(preseason, playoffs, spring training, summer league) never inherits a
regular-season fitted model.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17 import cross_sport_winner_discovery as discovery
from v17 import rundown_sport_registry as registry
from v17.team_event_bridge_runtime import (
    TEAM_EVENT_BRIDGES,
    register_team_event_bridge,
    team_event_bridge_health,
)
from v17.team_event_capability_manifest import (
    CERTIFIED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
)
from v17.team_event_model_registry_audit import (
    CANDIDATE_REGISTERED_UNCERTIFIED,
    CERTIFIED,
    NOT_CERTIFIED,
    certification_state,
)

SLATE_DATE = "2026-09-15"
SLATE_TZ = "UTC"


@pytest.fixture(autouse=True)
def _isolated_registry():
    original = dict(TEAM_EVENT_BRIDGES)
    TEAM_EVENT_BRIDGES.clear()
    try:
        yield
    finally:
        TEAM_EVENT_BRIDGES.clear()
        TEAM_EVENT_BRIDGES.update(original)


def _register(sport, *, supported_regimes=None):
    return register_team_event_bridge(
        sport,
        adapter_name=f"TEST_{sport}_ADAPTER",
        controlling_specialist=f"TEST_{sport}_SPECIALIST",
        scorer=lambda *a, **k: {"probability_publishable": False, "can_execute": False},
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS[sport],
        standard_package_validation=False,
        supported_regimes=supported_regimes,
    )


def _event(event_id, *, home="Alpha", away="Beta"):
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": f"{SLATE_DATE}T18:00:00Z",
        "status": "SCHEDULED",
    }


def _sweep(rows_by_family, *, families, targets=None, include_regime_variants=False):
    served: set[str] = set()

    def fetch(family, target=None):
        if family in served:
            return []
        served.add(family)
        return rows_by_family.get(family, [])

    return discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=fetch,
        supported_sports=families,
        discovery_targets=targets,
        include_regime_variants=include_regime_variants,
    )


def _audit_for(inventory, family):
    return next(row for row in inventory.acquisition_audit if row["family"] == family)


# ---------------------------------------------------------------------------
# CD-001 / CD-002 — certification is a separate axis from registration
# ---------------------------------------------------------------------------

def test_cd_001_nfl_bridge_registration_preserves_governed_certification():
    # NFL certification now exists before any in-memory bridge registration.
    status, certification_id = certification_state("NFL", registered=False)
    assert status == CERTIFIED
    assert certification_id == CERTIFIED_TEAM_EVENT_SPORTS["NFL"]

    _register("NFL")
    health = team_event_bridge_health()

    assert health["NFL"]["registered_capability"] is True
    assert health["NFL"]["status"] == "UP"
    assert health["NFL"]["certification_status"] == CERTIFIED
    assert health["NFL"]["certification_id"] == CERTIFIED_TEAM_EVENT_SPORTS["NFL"]
    assert health["NFL"]["can_execute"] is False


def test_cd_001_certified_mlb_keeps_its_certification_record():
    _register("MLB")
    health = team_event_bridge_health()
    assert health["MLB"]["certification_status"] == CERTIFIED
    assert health["MLB"]["certification_id"] == CERTIFIED_TEAM_EVENT_SPORTS["MLB"]


def test_cd_001_an_unregistered_sport_is_not_certified_and_not_a_candidate():
    health = team_event_bridge_health()
    for sport in ("NHL", "SOCCER", "TENNIS", "PGA"):
        assert health[sport]["certification_status"] == NOT_CERTIFIED
        assert health[sport]["certification_id"] is None
        assert health[sport]["registered_capability"] is False


def test_cd_002_certification_is_driven_only_by_the_governed_catalog(monkeypatch):
    # Use NHL to prove registration cannot self-certify. Only the governed
    # catalog transition changes certification status.
    assert certification_state("NHL", registered=True)[0] == CANDIDATE_REGISTERED_UNCERTIFIED
    monkeypatch.setitem(CERTIFIED_TEAM_EVENT_SPORTS, "NHL", "NHL_OUTRIGHT_WIN_EXPERT")
    status, certification_id = certification_state("NHL", registered=True)
    assert status == CERTIFIED
    assert certification_id == "NHL_OUTRIGHT_WIN_EXPERT"

    _register("NHL")
    health = team_event_bridge_health()
    assert health["NHL"]["certification_status"] == CERTIFIED
    assert health["NHL"]["certification_id"] == "NHL_OUTRIGHT_WIN_EXPERT"


def test_a_registered_bridge_can_never_self_promote_into_the_certified_set():
    _register("NHL")
    assert "NHL" not in CERTIFIED_TEAM_EVENT_SPORTS
    assert team_event_bridge_health()["NHL"]["certification_status"] == (
        CANDIDATE_REGISTERED_UNCERTIFIED
    )


# ---------------------------------------------------------------------------
# CD-003..CD-006 — authoritative provider ids, no guessed keys
# ---------------------------------------------------------------------------

def test_cd_003_ncaaf_uses_the_authoritative_provider_id():
    assert registry.discovery_sport_ids("NCAAF") == (1,)
    assert registry.family_for_sport_id(1) == "NCAAF"


def test_cd_004_tennis_uses_the_authoritative_atp_and_wta_ids():
    assert registry.discovery_sport_ids("TENNIS") == (38, 39)
    assert registry.league_for_sport_id(38) == "ATP"
    assert registry.league_for_sport_id(39) == "WTA"
    # The previous configuration guessed proxy key names for these.
    targets = discovery.rundown_discovery_targets("TENNIS")
    assert {target.sport_id for target in targets} == {38, 39}
    assert all(target.sport_key is None for target in targets)


def test_cd_005_golf_uses_the_authoritative_pga_id():
    assert registry.discovery_sport_ids("PGA") == (40,)
    assert registry.league_for_sport_id(40) == "PGA"


def test_cd_006_soccer_discovery_spans_every_configured_competition():
    ids = registry.discovery_sport_ids("SOCCER")
    assert set(ids) == {10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 33, 34}
    assert len(ids) > 1, "soccer must not collapse to EPL alone"

    targets = discovery.rundown_discovery_targets("SOCCER")
    leagues = {target.league for target in targets}
    assert {"MLS", "EPL", "FRA1", "GER1", "ESP1", "ITA1", "LIGAMX"}.issubset(leagues)
    # One governed family, exact competition preserved per target.
    assert {target.family for target in targets} == {"SOCCER"}


def test_soccer_rows_keep_their_exact_competition_not_the_family():
    targets = {"SOCCER": discovery.rundown_discovery_targets("SOCCER")[:1]}
    inventory = _sweep({"SOCCER": [_event("soc-1")]}, families=("SOCCER",), targets=targets)
    row = inventory.events[0]
    assert row.sport == "SOCCER"
    assert row.league == "MLS"
    assert row.provider_sport_id == 10


def test_every_authoritative_id_in_the_patch_is_registered():
    expected = {
        1: "NCAAF", 2: "NFL", 3: "MLB", 4: "NBA", 5: "NCAAB", 6: "NHL",
        7: "MMA", 8: "WNBA", 10: "SOCCER", 11: "SOCCER", 12: "SOCCER",
        13: "SOCCER", 14: "SOCCER", 15: "SOCCER", 16: "SOCCER", 17: "SOCCER",
        18: "SOCCER", 19: "SOCCER", 33: "SOCCER", 34: "SOCCER",
        38: "TENNIS", 39: "TENNIS", 40: "PGA",
    }
    for sport_id, family in expected.items():
        assert registry.family_for_sport_id(sport_id) == family, sport_id
        assert registry.is_regular_season(sport_id) is True, sport_id


# ---------------------------------------------------------------------------
# CD-007 — boxing has no provider key
# ---------------------------------------------------------------------------

def test_cd_007_boxing_has_no_provider_feed_and_is_never_guessed():
    assert registry.discovery_sport_ids("BOXING") == ()
    assert registry.has_configured_feed("BOXING") is False
    assert discovery.rundown_discovery_targets("BOXING") == ()


def test_cd_007_boxing_reports_no_configured_feed_not_an_empty_slate():
    def fetch(family, target=None):
        pytest.fail("boxing has no configured target and must never be queried")

    inventory = discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=fetch,
        supported_sports=("BOXING",),
    )
    audit = _audit_for(inventory, "BOXING")
    assert audit["request_status"] == discovery.NO_CONFIGURED_DISCOVERY_FEED
    assert audit["request_status"] != discovery.NO_EVENTS_RETURNED
    assert audit["blocker_if_any"] == discovery.NO_CONFIGURED_DISCOVERY_FEED
    assert audit["provider_sport_ids_attempted"] == []
    assert audit["events_returned"] == 0

    rows = discovery.route_discovered_slate(
        inventory, resolve_model=lambda e: None, score_row=lambda e, m: {}
    )
    reconciliation = discovery.reconcile(inventory, rows)
    assert reconciliation["row_reconciliation"] == "PASS"
    assert "BOXING" in reconciliation["families_without_configured_feed"]


# ---------------------------------------------------------------------------
# CD-008 — regime protection
# ---------------------------------------------------------------------------

def test_cd_008_nfl_preseason_is_discoverable_on_its_own_provider_id():
    assert registry.regime_for_sport_id(25) == registry.PRESEASON
    assert registry.regime_for_sport_id(26) == registry.PLAYOFFS
    assert registry.is_regular_season(25) is False
    # Regime ids are held separately and are not swept by default.
    assert 25 not in registry.discovery_sport_ids("NFL")
    assert 25 in registry.discovery_sport_ids("NFL", include_regime_variants=True)


def test_cd_008_a_regular_season_model_never_inherits_preseason():
    registration = _register("NFL", supported_regimes=(registry.REGULAR_SEASON,))
    targets = {
        "NFL": (
            discovery.DiscoveryTarget(
                family="NFL", provider="RUNDOWN", league="NFL",
                regime=registry.PRESEASON, sport_id=25,
            ),
        )
    }
    inventory = _sweep({"NFL": [_event("nfl-pre-1")]}, families=("NFL",), targets=targets)
    assert inventory.events[0].regime == registry.PRESEASON

    rows = discovery.route_discovered_slate(
        inventory,
        resolve_model=lambda e: registration,
        score_row=lambda e, m: pytest.fail("a preseason row must not reach the scorer"),
    )
    row = rows[0].as_dict()
    # Discovered and retained, but explicitly not routed.
    assert row["bucket"] == "MODEL_UNAVAILABLE"
    assert row["detail"]["reason"] == "NFL_PRESEASON_REGIME_NOT_SUPPORTED_BY_FITTED_MODEL"
    assert row["detail"]["backend_route_status"] == "SPORT_SPECIFIC_TEAM_EVENT_REGIME_NOT_REGISTERED"
    assert row["detail"]["model_invoked"] is False
    assert row["detail"]["supported_regimes"] == [registry.REGULAR_SEASON]
    assert row["rank_eligible"] is False
    assert row["can_execute"] is False
    assert discovery.reconcile(inventory, rows)["row_reconciliation"] == "PASS"


def test_cd_008_regular_season_rows_still_route_to_the_regular_season_model():
    registration = _register("NFL", supported_regimes=(registry.REGULAR_SEASON,))
    targets = {"NFL": discovery.rundown_discovery_targets("NFL")}
    inventory = _sweep({"NFL": [_event("nfl-1")]}, families=("NFL",), targets=targets)
    assert inventory.events[0].regime == registry.REGULAR_SEASON

    invoked: list[str] = []
    discovery.route_discovered_slate(
        inventory,
        resolve_model=lambda e: registration,
        score_row=lambda e, m: invoked.append(e.regime) or {},
    )
    assert invoked == [registry.REGULAR_SEASON]


@pytest.mark.parametrize(
    "sport_id,regime",
    [
        (23, registry.PRESEASON), (24, registry.PLAYOFFS), (27, registry.PRESEASON),
        (28, registry.PLAYOFFS), (30, registry.SPRING_TRAINING), (31, registry.PLAYOFFS),
        (32, registry.SUMMER_LEAGUE),
    ],
)
def test_cd_008_every_regime_variant_is_protected(sport_id, regime):
    assert registry.regime_for_sport_id(sport_id) == regime
    event = discovery.DiscoveredEvent(
        sport=registry.family_for_sport_id(sport_id), league="X", sport_key=str(sport_id),
        official_event_id="e1", home_team="A", away_team="B",
        commence_time_utc=f"{SLATE_DATE}T18:00:00Z", event_status="PREGAME",
        source="TEST", regime=regime, provider="RUNDOWN", provider_sport_id=sport_id,
    )
    regular_only = SimpleNamespace(supported_regimes=(registry.REGULAR_SEASON,))
    assert discovery.model_supports_regime(event, regular_only) is False


def test_a_model_that_declares_no_regimes_is_regular_season_only():
    # Fails closed: an undeclared contract is not an open one.
    undeclared = SimpleNamespace()
    regular = discovery.DiscoveredEvent(
        sport="NFL", league="NFL", sport_key="2", official_event_id="e1",
        home_team="A", away_team="B", commence_time_utc=f"{SLATE_DATE}T18:00:00Z",
        event_status="PREGAME", source="TEST", regime=registry.REGULAR_SEASON,
    )
    playoffs = discovery.DiscoveredEvent(**{**vars(regular), "regime": registry.PLAYOFFS})
    assert discovery.model_supports_regime(regular, undeclared) is True
    assert discovery.model_supports_regime(playoffs, undeclared) is False


def test_an_unknown_provider_id_is_never_assumed_regular_season():
    assert registry.regime_for_sport_id(9999) == "UNKNOWN_REGIME"
    assert registry.is_regular_season(9999) is False


# ---------------------------------------------------------------------------
# CD-009 / CD-010 — empty slate vs provider failure
# ---------------------------------------------------------------------------

def test_cd_009_a_successful_query_with_no_events_is_no_events_returned():
    targets = {"NHL": discovery.rundown_discovery_targets("NHL")}

    def fetch(family, target=None):
        return []

    inventory = discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=fetch,
        supported_sports=("NHL",),
        discovery_targets=targets,
    )
    audit = _audit_for(inventory, "NHL")
    assert audit["request_status"] == discovery.NO_EVENTS_RETURNED
    assert audit["request_status"] != discovery.NO_CONFIGURED_DISCOVERY_FEED
    assert audit["provider_sport_ids_attempted"] == [6]
    assert audit["blocker_if_any"] is None


def test_cd_010_a_provider_failure_is_never_reported_as_a_zero_event_success():
    targets = {"NHL": discovery.rundown_discovery_targets("NHL")}

    def fetch(family, target=None):
        raise discovery.DiscoveryFeedError("RUNDOWN_HTTP_503")

    inventory = discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=fetch,
        supported_sports=("NHL",),
        discovery_targets=targets,
    )
    audit = _audit_for(inventory, "NHL")
    assert audit["request_status"] == discovery.PROVIDER_REQUEST_FAILED
    assert audit["request_status"] not in {
        discovery.NO_EVENTS_RETURNED,
        discovery.NO_CONFIGURED_DISCOVERY_FEED,
    }
    assert audit["blocker_if_any"] == "RUNDOWN_HTTP_503"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("RUNDOWN_BURST_THROTTLED", discovery.PROVIDER_RATE_LIMITED),
        ("RUNDOWN_QUOTA_EXHAUSTED", discovery.PROVIDER_RATE_LIMITED),
        ("RUNDOWN_HTTP_429", discovery.PROVIDER_RATE_LIMITED),
        ("RUNDOWN_SCHEMA_UNRECOGNISED", discovery.PROVIDER_SCHEMA_FAILURE),
        ("RUNDOWN_MARKET_CATALOG_NOT_ODDS_SNAPSHOT", discovery.PROVIDER_SCHEMA_FAILURE),
        ("RUNDOWN_EVENT_SNAPSHOT_WITHOUT_PRICES", discovery.PROVIDER_SCHEMA_FAILURE),
        ("RUNDOWN_HTTP_503", discovery.PROVIDER_REQUEST_FAILED),
    ],
)
def test_cd_010_acquisition_failures_are_classified_by_provider_reason(code, expected):
    assert discovery.classify_acquisition_failure(code) == expected


def test_cd_010_rate_limiting_is_distinguishable_in_the_family_audit():
    targets = {"MLB": discovery.rundown_discovery_targets("MLB")}

    def fetch(family, target=None):
        raise discovery.DiscoveryFeedError("RUNDOWN_QUOTA_EXHAUSTED")

    inventory = discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=fetch,
        supported_sports=("MLB",),
        discovery_targets=targets,
    )
    assert _audit_for(inventory, "MLB")["request_status"] == discovery.PROVIDER_RATE_LIMITED


def test_the_full_family_sweep_terminates_every_family_exactly_once():
    families = discovery.SUPPORTED_DISCOVERY_SPORTS

    def fetch(family, target=None):
        return []

    inventory = discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=fetch,
        supported_sports=families,
        budget_seconds=60,
    )
    audited = [row["family"] for row in inventory.acquisition_audit]
    assert audited == list(families)
    assert len(audited) == len(set(audited))
    for row in inventory.acquisition_audit:
        assert row["request_status"] in discovery.ACQUISITION_STATUSES
    # Boxing is the only declared family the provider registry does not carry.
    unconfigured = [
        row["family"]
        for row in inventory.acquisition_audit
        if row["request_status"] == discovery.NO_CONFIGURED_DISCOVERY_FEED
    ]
    assert unconfigured == ["BOXING"]


def test_duplicate_fixtures_across_provider_ids_are_suppressed_and_counted():
    # One fixture surfacing under two competition ids is one event, and the
    # suppression is recorded rather than silent.
    targets = {"SOCCER": discovery.rundown_discovery_targets("SOCCER")[:2]}

    def fetch(family, target=None):
        return [_event("soc-shared")]

    inventory = discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=fetch,
        supported_sports=("SOCCER",),
        discovery_targets=targets,
    )
    assert len(inventory.events) == 1
    audit = _audit_for(inventory, "SOCCER")
    assert audit["events_returned"] == 1
    assert audit["duplicate_rows_suppressed"] == 1
    assert audit["provider_sport_ids_attempted"] == [10, 11]


def test_the_discovery_registry_is_inspectable_without_a_provider_call():
    payload = registry.registry_payload()
    assert payload["provider"] == "RUNDOWN"
    assert payload["registry_verified_on"] == "2026-09-14"
    assert payload["families"]["BOXING"]["configured"] is False
    assert payload["families"]["BOXING"]["blocker"] == registry.NO_CONFIGURED_DISCOVERY_FEED
    assert payload["families"]["SOCCER"]["configured"] is True
    assert len(payload["families"]["SOCCER"]["regular_season_sport_ids"]) == 12
    assert payload["families"]["NFL"]["regime_variant_sport_ids"] == [25, 26]
    assert payload["can_execute"] is False
