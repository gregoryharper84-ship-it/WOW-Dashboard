from __future__ import annotations

import pytest

from v17.spread_espn_close_proxy_source import (
    acquire_wnba_close_proxies,
    close_proxy_from_wnba_summary,
    extract_espn_team_abbreviations,
)
from v17.spread_historical_close_proxy import ESPN_WNBA_EVIDENCE_CLASS
from v17.spread_margin_challenger import SpreadChallengerUnavailable


def _summary(*, event_id="401", completed=True, home_id="17", away_id="5", details="NY -5.5"):
    return {
        "header": {
            "id": event_id,
            "competitions": [
                {
                    "id": event_id,
                    "status": {"type": {"completed": completed, "state": "post" if completed else "pre"}},
                    "competitors": [
                        {"homeAway": "home", "team": {"id": home_id, "abbreviation": "NY"}},
                        {"homeAway": "away", "team": {"id": away_id, "abbreviation": "LV"}},
                    ],
                }
            ],
        },
        "pickcenter": [{"details": details}],
    }


def test_extract_espn_team_abbreviations_requires_exact_final_event_and_team_ids():
    home, away = extract_espn_team_abbreviations(
        summary=_summary(),
        expected_event_id="espn-401",
        expected_home_team_id="espn-17",
        expected_away_team_id="espn-5",
    )
    assert (home, away) == ("NY", "LV")


def test_extract_espn_team_abbreviations_rejects_event_mismatch():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        extract_espn_team_abbreviations(
            summary=_summary(event_id="999"),
            expected_event_id="espn-401",
            expected_home_team_id="espn-17",
            expected_away_team_id="espn-5",
        )
    assert exc.value.code == "SPREAD_ESPN_SUMMARY_EVENT_ID_MISMATCH"


def test_extract_espn_team_abbreviations_rejects_home_away_identity_mismatch():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        extract_espn_team_abbreviations(
            summary=_summary(home_id="55"),
            expected_event_id="espn-401",
            expected_home_team_id="espn-17",
            expected_away_team_id="espn-5",
        )
    assert exc.value.code == "SPREAD_ESPN_SUMMARY_TEAM_IDENTITY_MISMATCH"


def test_extract_espn_team_abbreviations_requires_final_event():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        extract_espn_team_abbreviations(
            summary=_summary(completed=False),
            expected_event_id="espn-401",
            expected_home_team_id="espn-17",
            expected_away_team_id="espn-5",
        )
    assert exc.value.code == "SPREAD_ESPN_SUMMARY_NOT_FINAL"


def test_close_proxy_from_wnba_summary_preserves_exact_signed_line_and_research_only_class():
    evidence = close_proxy_from_wnba_summary(
        summary=_summary(details="NY -5.5"),
        event_id="espn-401",
        home_team_id="espn-17",
        away_team_id="espn-5",
    )
    assert evidence.event_id == "espn-401"
    assert evidence.home_spread == -5.5
    assert evidence.evidence_class == ESPN_WNBA_EVIDENCE_CLASS
    payload = evidence.payload()
    assert payload["historical_certification_evidence_only"] is True
    assert payload["live_card_receipt_eligible"] is False
    assert payload["clv_evidence"] is False
    assert payload["probability_publishable"] is False
    assert payload["rank_eligible"] is False
    assert payload["can_execute"] is False


def test_acquire_wnba_close_proxies_keeps_row_blockers_typed_and_hashes_successes():
    identities = {
        "espn-401": {
            "game_id": "espn-401",
            "home_team_id": "espn-17",
            "away_team_id": "espn-5",
        },
        "espn-402": {
            "game_id": "espn-402",
            "home_team_id": "espn-17",
            "away_team_id": "espn-5",
        },
    }

    def loader(provider_event_id: str):
        if provider_event_id == "401":
            return _summary(event_id="401")
        raise RuntimeError("network fixture failure")

    evidence, audit = acquire_wnba_close_proxies(
        identities=identities,
        test_event_ids=["espn-401", "espn-402", "espn-403"],
        summary_loader=loader,
    )
    assert set(evidence) == {"espn-401"}
    assert audit["test_event_n"] == 3
    assert audit["bound_event_n"] == 1
    assert audit["coverage"] == pytest.approx(1 / 3)
    assert audit["aggregate_source_payload_sha256"]
    assert audit["blocker_counts"] == {
        "SPREAD_ESPN_SUMMARY_ACQUISITION_FAILED:RuntimeError": 1,
        "SPREAD_WNBA_CLOSE_PROXY_EVENT_IDENTITY_MISSING": 1,
    }
    assert audit["live_card_receipt_eligible"] is False
    assert audit["clv_evidence"] is False
    assert audit["probability_publishable"] is False
    assert audit["can_execute"] is False
