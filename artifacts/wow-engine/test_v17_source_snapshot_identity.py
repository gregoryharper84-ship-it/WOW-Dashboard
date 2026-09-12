from v17.research_identity_reconciliation import IdentityResolution
from v17.research_source_adapters import ResearchFetchResult
from v17.research_source_snapshot_store import snapshot_id_for


def test_snapshot_identity_is_stable_for_same_payload():
    a = ResearchFetchResult(True, "SPORTSDATAIO", "americanfootball_nfl", "injuries", "ESTABLISHED_STATS_PROVIDER", data=[{"PlayerID": 1}], observed_at="2026-09-12T04:00:00Z")
    b = ResearchFetchResult(True, "SPORTSDATAIO", "americanfootball_nfl", "injuries", "ESTABLISHED_STATS_PROVIDER", data=[{"PlayerID": 1}], observed_at="2026-09-12T05:00:00Z")
    assert snapshot_id_for(a) == snapshot_id_for(b)


def test_snapshot_identity_changes_when_provider_payload_changes():
    a = ResearchFetchResult(True, "SPORTSDATAIO", "americanfootball_nfl", "injuries", "ESTABLISHED_STATS_PROVIDER", data=[{"PlayerID": 1, "Status": "Questionable"}])
    b = ResearchFetchResult(True, "SPORTSDATAIO", "americanfootball_nfl", "injuries", "ESTABLISHED_STATS_PROVIDER", data=[{"PlayerID": 1, "Status": "Out"}])
    assert snapshot_id_for(a) != snapshot_id_for(b)


def test_unresolved_identity_has_no_prediction_or_execution_authority():
    result = IdentityResolution(status="IDENTITY_UNRESOLVED", provider="SPORTSDATAIO", sport_key="americanfootball_ncaaf", entity_type="TEAM", provider_entity_id="123", reason_code="VERIFIED_PROVIDER_MAPPING_MISSING")
    payload = result.to_dict()
    assert payload["verified"] is False
    assert payload["prediction_authority"] is False
    assert payload["can_execute"] is False


def test_linked_identity_still_has_no_prediction_or_execution_authority():
    result = IdentityResolution(status="LINKED", provider="SPORTSDATAIO", sport_key="baseball_mlb", entity_type="TEAM", provider_entity_id="15", canonical_entity_id="mlb:LAD", canonical_name="Los Angeles Dodgers", verified=True)
    payload = result.to_dict()
    assert payload["verified"] is True
    assert payload["prediction_authority"] is False
    assert payload["can_execute"] is False
