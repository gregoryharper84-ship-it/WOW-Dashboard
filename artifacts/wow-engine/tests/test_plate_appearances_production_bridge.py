from __future__ import annotations

from datetime import datetime, timezone

from prop_auto_hydration import PropAutoHydrationError
from prop_auto_hydration_plate_appearances import hydrate_mlb_plate_appearance_evidence
from prop_distribution_contract import CertifiedBundle, PropInferenceRequest
from prop_fitted_provider import ResolvedArtifact
from prop_model_adapters_plate_appearances import (
    COVERAGE_FAILURE_LINEUP_UNCONFIRMED,
    mlb_batter_plate_appearances_nb_v1_adapter,
)


PLAYER_ID = 99
TEAM_ID = 123
GAME_PK = 456
EVENT_START = "2026-09-05T18:00:00+00:00"
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _int(value, *, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_player_id(player, **_kwargs):
    assert player == "Test Batter"
    return PLAYER_ID, "Test Batter"


def _game_log_payload():
    splits = []
    for day in range(10, 0, -1):
        splits.append(
            {
                "date": f"2026-08-{day:02d}",
                "gameNumber": 1,
                "opponent": {"abbreviation": "OPP"},
                "stat": {
                    "plateAppearances": 4 + (day % 2),
                    "atBats": 4,
                    "hits": 1,
                    "baseOnBalls": 1,
                    "strikeOuts": 1,
                },
            }
        )
    return {"stats": [{"splits": splits}]}


def _request_json(*, lineup=True):
    def request(url, *, params, http_get):
        del params, http_get
        if url.endswith(f"/people/{PLAYER_ID}"):
            return {"people": [{"currentTeam": {"id": TEAM_ID}}]}
        if url.endswith("/schedule"):
            return {
                "dates": [
                    {
                        "games": [
                            {
                                "gamePk": GAME_PK,
                                "gameDate": EVENT_START,
                                "teams": {
                                    "away": {"team": {"id": TEAM_ID, "abbreviation": "AWY"}},
                                    "home": {"team": {"id": 321, "abbreviation": "HME"}},
                                },
                                "venue": {"name": "Test Park"},
                                "status": {"detailedState": "Scheduled"},
                            }
                        ]
                    }
                ]
            }
        if url.endswith(f"/game/{GAME_PK}/boxscore"):
            batting_order = [11, 12, PLAYER_ID, 14, 15, 16, 17, 18, 19] if lineup else []
            return {
                "teams": {
                    "away": {"battingOrder": batting_order},
                    "home": {"battingOrder": []},
                }
            }
        if url.endswith(f"/people/{PLAYER_ID}/stats"):
            return _game_log_payload()
        raise AssertionError(f"unexpected URL: {url}")

    return request


def _hydrate(*, lineup=True):
    return hydrate_mlb_plate_appearance_evidence(
        player="Test Batter",
        event_start_time=EVENT_START,
        resolve_player_id=_resolve_player_id,
        request_json=_request_json(lineup=lineup),
        int_value=_int,
        error_type=PropAutoHydrationError,
        mlb_stats_api_base="https://statsapi.mlb.com/api/v1",
        evidence_version="PROP_EVIDENCE_V1",
        min_games=10,
        http_get=lambda *_args, **_kwargs: None,
        now=NOW,
    )


def test_pa_hydrator_uses_official_lineup_and_away_encoding_without_imputation():
    evidence = _hydrate(lineup=True)

    assert len(evidence["game_log"]) == 10
    assert len(evidence["box_score_log"]) == 10
    assert evidence["opportunity_ledger"]["batting_slot"] == 3
    assert evidence["opportunity_ledger"]["team_alignment"] == 0
    assert evidence["opportunity_ledger"]["team_alignment_encoding"] == "0=AWAY,1=HOME"
    assert evidence["role_status"]["status"] == "STARTER_CONFIRMED"
    assert "MLB_STATS_API_OFFICIAL_BATTING_ORDER" in evidence["source_timestamps"]


def test_pa_hydrator_freezes_history_when_lineup_is_unconfirmed_but_does_not_guess_slot():
    evidence = _hydrate(lineup=False)

    assert len(evidence["game_log"]) == 10
    assert evidence["opportunity_ledger"]["batting_slot"] is None
    assert evidence["opportunity_ledger"]["team_alignment"] == 0
    assert evidence["role_status"]["status"] == "LINEUP_UNCONFIRMED"
    assert evidence["opportunity_ledger"]["lineup_confirmation"] == "UNCONFIRMED_HOLD_AT_MODEL_COVERAGE"
    assert "MLB_STATS_API_OFFICIAL_BATTING_ORDER" not in evidence["source_timestamps"]


def _artifact():
    bundle = CertifiedBundle(
        model_artifact_version="pa-v1",
        calibrator_version="pa-cal-v1",
        feature_transform_version="pa-transform-v1",
        specialist_version="wow.mlb-batter-plate-appearances-expert@1",
        certification_id="cert-pa-v1",
        feature_schema_version="PROP_FEATURES_V1",
        training_dataset_hash="dataset-hash",
        training_code_sha="code-sha",
        artifact_checksum="artifact-sha",
        lifecycle_state="PROSPECTIVE_CERTIFIED",
        supported_sport="MLB",
        supported_stat_type="PLATE_APPEARANCES",
        supported_line_min=2.5,
        supported_line_max=5.5,
    )
    return ResolvedArtifact(
        artifact_id="artifact-pa-v1",
        model_family="MLB_BATTER_PLATE_APPEARANCES_NB_V1",
        artifact_format="PROP_NB_SHRINKAGE_V1",
        artifact_payload={
            "league_mean_pa_by_cell": {"3_0": 4.0, "3_1": 3.8},
            "league_mean_pa_overall": 3.6,
            "dispersion_r": 20.0,
            "shrinkage_k_rate": 10.0,
            "max_support_k": 8,
            "feature_transform_version": "pa-transform-v1",
        },
        training_rows=1000,
        validation_metrics={"test_log_loss": 1.0},
        bundle=bundle,
    )


def _request():
    return PropInferenceRequest(
        event_id="game-456",
        player_id="wow-name:test",
        sport="MLB",
        league_season="2026",
        stat_type="PLATE_APPEARANCES",
        evidence_snapshot_id="snapshot-pa-1",
        market_identity_id="market-pa-1",
        as_of_timestamp="2026-09-05T12:00:00+00:00",
        request_id="request-pa-1",
        feature_schema_version="PROP_FEATURES_V1",
    )


def test_pa_adapter_consumes_generic_governed_evidence_envelope():
    distribution = mlb_batter_plate_appearances_nb_v1_adapter(
        _artifact(),
        _request(),
        {
            "game_log": [4, 5, 4, 4, 5, 4, 4, 5, 4, 4],
            "opportunity_ledger": {"batting_slot": 3, "team_alignment": 0},
        },
    )

    assert distribution.coverage.in_distribution is True
    assert distribution.coverage.coverage_failures == ()
    assert distribution.failure_path_evidence["batting_slot"] == 3
    assert distribution.failure_path_evidence["team_alignment"] == 0
    assert distribution.failure_path_evidence["input_contract"] == "GENERIC_GOVERNED_PROP_EVIDENCE_V1"


def test_pa_adapter_turns_unconfirmed_lineup_into_model_coverage_hold():
    distribution = mlb_batter_plate_appearances_nb_v1_adapter(
        _artifact(),
        _request(),
        {
            "game_log": [4, 5, 4, 4, 5, 4, 4, 5, 4, 4],
            "opportunity_ledger": {"batting_slot": None, "team_alignment": 0},
        },
    )

    assert distribution.coverage.in_distribution is False
    assert COVERAGE_FAILURE_LINEUP_UNCONFIRMED in distribution.coverage.coverage_failures
    assert distribution.failure_path_evidence["batting_slot"] is None
