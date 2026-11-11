import copy
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.universal_sports_intelligence_research import (
    SPORT_DOMAINS, UNIVERSAL_DOMAINS, IntelligenceResearchHold,
    sport_intelligence_inventory, compile_intelligence_research_snapshot,
)

START = "2026-10-25T20:00:00Z"
CUT = "2026-10-25T19:00:00Z"


def fact(domain="quality", **updates):
    row = dict(domain=domain, metric="adjusted_score", entity_id="team-1",
               value=1.5, evidence_kind="SOURCE_OBSERVATION",
               source_url="https://stats.example.org/event/1", source_record_id="row-1",
               observed_at="2026-10-20T12:00:00Z",
               first_available_at="2026-10-20T12:10:00Z",
               retrieved_at="2026-10-25T18:00:00Z")
    row.update(updates)
    return row


def compile(facts=None, sport="NFL", **kwargs):
    args=dict(sport=sport,official_event_id="event-1",event_start_utc=START,
              as_of_utc=CUT,facts=[fact()] if facts is None else facts)
    args.update(kwargs)
    return compile_intelligence_research_snapshot(**args)


def test_inventory_has_exact_thirteen_sports_including_ncaab():
    out=sport_intelligence_inventory()
    assert out["sport_count"] == 13
    assert "NCAAB" in SPORT_DOMAINS and "CRICKET" in SPORT_DOMAINS
    assert "scenario_uncertainty" in UNIVERSAL_DOMAINS
    assert out["production_capability_attested"] is False
    assert out["registered_fitted_models"] == []


@pytest.mark.parametrize("sport", sorted(SPORT_DOMAINS))
def test_each_sport_accepts_provenance_only_research_and_never_models(sport):
    source=fact(domain=SPORT_DOMAINS[sport][0])
    out=compile([source],sport)
    assert out["fact_count"] == 1
    assert out["domains_with_research"] == [SPORT_DOMAINS[sport][0]]
    assert out["verified_model_features"] == out["model_consumption_receipts"] == []
    assert out["scenario_probabilities"] == []
    assert out["probability_publishable"] is False and out["can_execute"] is False
    assert out["research_facts"][0]["pregame_model_feature_eligible"] is False


def test_unknown_domains_remain_visible_and_hypotheses_never_turn_numeric():
    row=fact(domain="game_scripts",evidence_kind="CONTEXT_HYPOTHESIS",value="May slow pace")
    out=compile([row])
    assert "quality" in out["unavailable_universal_domains"]
    assert out["research_facts"][0]["evidence_kind"] == "CONTEXT_HYPOTHESIS"
    assert out["scenario_probabilities"] == []


@pytest.mark.parametrize("change,err",[
    ({"retrieved_at":"2026-10-25T21:00:00Z"},"INTEL_POST_CUTOFF_OR_CHRONOLOGY_CONFLICT"),
    ({"first_available_at":"2026-10-26T12:00:00Z"},"INTEL_POST_CUTOFF_OR_CHRONOLOGY_CONFLICT"),
    ({"source_url":"http://stats.example.org"},"INTEL_PROVENANCE_MISSING"),
    ({"domain":"fake_win_rate"},"INTEL_DOMAIN_UNSUPPORTED"),
    ({"model_probability":0.99},"INTEL_PROBABILITY_AUTHORITY_FORBIDDEN"),
    ({"market_implied_probability":0.9},"INTEL_PROBABILITY_AUTHORITY_FORBIDDEN"),
    ({"value":float("nan")},"INTEL_FEATURE_VALUE_INVALID"),
    ({"evidence_kind":"MODEL_PROBABILITY"},"INTEL_EVIDENCE_KIND_INVALID"),
])
def test_unsafe_or_misleading_facts_rejected(change,err):
    with pytest.raises(IntelligenceResearchHold,match=err):
        compile([fact(**change)])


def test_duplicate_rows_and_poststart_cutoff_fail_closed():
    row=fact()
    with pytest.raises(IntelligenceResearchHold,match="INTEL_DUPLICATE_SOURCE_RECORD"):
        compile([row,copy.deepcopy(row)])
    with pytest.raises(IntelligenceResearchHold,match="INTEL_CUTOFF_NOT_PREGAME"):
        compile([],as_of_utc=START)


def test_no_data_does_not_fabricate_coverage_or_strength():
    r=compile([])
    assert r["fact_count"] == 0
    assert set(r["unavailable_universal_domains"]) == set(UNIVERSAL_DOMAINS)
    assert set(r["unavailable_sport_domains"]) == set(SPORT_DOMAINS["NFL"])
    assert r["calibrated_probability"] is None
