from __future__ import annotations

from datetime import datetime, timezone

from v17 import cross_sport_discovery_feed as discovery_feed
from v17 import cross_sport_winner_discovery as discovery
from v17 import rundown_sport_registry as registry
from v17.all_sport_capability_readiness import _readiness
from v17.team_event_capability_manifest import (
    EXPECTED_TEAM_EVENT_SPORTS,
    normalize_team_event_identity,
)
from v17.team_event_governance_profiles import governance_profile
from v17.team_event_sport_parity import parity_health


SLATE_DATE = "2026-09-23"
SLATE_TZ = "UTC"
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _event(event_id: str, home: str, away: str, hour: int = 18):
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": f"{SLATE_DATE}T{hour:02d}:00:00Z",
        "status": "SCHEDULED",
    }


def test_cricket_t20_is_in_discovery_catalog_without_model_promotion():
    assert "CRICKET" in EXPECTED_TEAM_EVENT_SPORTS
    assert normalize_team_event_identity("T20") == "CRICKET"
    assert registry.discovery_sport_ids("CRICKET") == (21,)

    targets = discovery.rundown_discovery_targets("CRICKET")
    assert len(targets) == 1
    assert targets[0].sport_id == 21
    assert targets[0].league == "T20"
    assert discovery_feed._family_for_proxy_key("cricket_ipl") == "CRICKET"

    profile = governance_profile("CRICKET")
    assert profile is not None
    assert profile.outcome_space == "TEAM_A_TEAM_B_TIE_NO_RESULT"
    assert profile.can_execute is False


def test_unsupported_cricket_row_is_retained_and_does_not_shrink_other_sports():
    targets = {
        "MLB": discovery.rundown_discovery_targets("MLB"),
        "CRICKET": discovery.rundown_discovery_targets("CRICKET"),
    }

    def fetch(family, target=None):
        if family == "MLB":
            return [_event("mlb-1", "A", "B")]
        if family == "CRICKET":
            return [_event("t20-1", "C", "D")]
        return []

    inventory = discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=fetch,
        supported_sports=("MLB", "CRICKET"),
        discovery_targets=targets,
        now=NOW,
        budget_seconds=30,
    )
    assert {event.sport for event in inventory.events} == {"MLB", "CRICKET"}

    sentinel_model = object()
    rows = discovery.route_discovered_slate(
        inventory,
        resolve_model=lambda event: sentinel_model if event.sport == "MLB" else None,
        score_row=lambda event, model: {
            "code": "MODEL_QUALIFIED_HOLD",
            "model_invoked": True,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        },
    )
    by_sport = {row.identity["sport"]: row.as_dict() for row in rows}

    cricket = by_sport["CRICKET"]
    assert cricket["bucket"] == "MODEL_UNAVAILABLE"
    assert cricket["rank_eligible"] is False
    assert cricket["probability_publishable"] is False
    assert cricket["market_probability_substitution_allowed"] is False
    assert cricket["generic_reasoning_substitution_allowed"] is False
    assert cricket["can_execute"] is False

    assert by_sport["MLB"]["bucket"] == "OTHER_GOVERNED_HOLD"
    assert discovery.reconcile(inventory, rows)["row_reconciliation"] == "PASS"


def test_nhl_preseason_is_part_of_full_regime_discovery_targets():
    regular = discovery.rundown_discovery_targets("NHL")
    full = discovery.rundown_discovery_targets("NHL", include_regime_variants=True)

    assert {target.sport_id for target in regular} == {6}
    assert {target.sport_id for target in full} == {6, 27, 28}
    preseason = next(target for target in full if target.sport_id == 27)
    assert preseason.regime == registry.PRESEASON


def test_cricket_parity_and_readiness_fail_closed_with_no_bridge_or_artifact():
    parity = parity_health({})["CRICKET"]
    assert parity["cataloged"] is True
    assert parity["hydration_owner"] == "MODEL_DEVELOPMENT_LANE"
    assert parity["governance_profile_installed"] is True
    assert parity["bridge_registered"] is False
    assert parity["model_capability_ready"] is False
    assert parity["market_probability_substitution_allowed"] is False
    assert parity["can_execute"] is False

    readiness = _readiness(
        "CRICKET",
        {
            "registered": False,
            "scorer_resolvable": False,
            "model_artifact_present": False,
            "certification_status": "NOT_CERTIFIED",
        },
    )
    assert readiness["operational_readiness_status"] == "UNAVAILABLE"
    assert readiness["hydration_mode"] == "MODEL_DEVELOPMENT_LANE"
    assert readiness["calibration_mode"] == "UNAVAILABLE"
    assert "TEAM_EVENT_BRIDGE_NOT_REGISTERED" in readiness["readiness_blockers"]
    assert "TEAM_EVENT_CERTIFICATION_NOT_ACTIVE" in readiness["readiness_blockers"]
    assert readiness["can_execute"] is False
