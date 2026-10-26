"""Read-only reproducible NHL totals model offline replay (no live scores).

Inputs are an authorized snapshot export of WOW D1 NHL public-source records
and independent official NHL club-schedule JSON payloads. Never queries
Supabase or the NHL API, uses credentials, mutates runtime, publishes
probabilities or writes any prediction receipt. Real-data certification
is a distinct contract from this research-only historical replay.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from v17.nhl_total_goals_source_adapter import adapt_persisted_nhl_games
from v17.nhl_total_goals_challenger import build_total_rows
from v17.nhl_total_goals_regime_source import (
    normalize_official_regime_schedule,
    reconcile_official_regime_games,
    reconcile_regime_labels_with_d1_events,
)
from v17.nhl_total_goals_model_bridge import build_regime_model_research_rows
from v17.nhl_total_goals_regime_mixture_research import fit_regime_mixture_research

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
CONTROLLING_TERMINAL = "V17_TERMINAL_REDUCER"
HISTORICAL_SEASONS = frozenset({"20212022", "20222023", "20232024", "20242025", "20252026"})


class NHLTotalsOfflineReplayError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def replay_verified_nhl_totals_research(
    d1_source_rows: Sequence[Mapping[str, Any]],
    official_schedules: Sequence[Mapping[str, Any]], *,
    allow_matching_source_versions: bool = False,
    require_complete_corpus: bool = True,
) -> dict[str, Any]:
    """Validate complete official NHL source coverage before fitting.

    A partial corpus returns a typed non-fitted research disposition ONLY when
    require_complete_corpus=False; a partial corpus must never be represented
    as a validated model or promote a backtested value recommendation.
    """
    games, src_audit = adapt_persisted_nhl_games(
        d1_source_rows, allow_matching_source_versions=allow_matching_source_versions
    )
    canonical = []
    for game in games:
        canonical.append({
            "source_provider": "NHL_PUBLIC_WEB_API",
            "historical_reconstruction": True,
            "can_execute": False,
            "season": game["season"], "official_event_id": game["official_event_id"],
            "event_start_time": game["event_start_time"],
            "home_participant": game["home_team"],
            "away_participant": game["away_team"],
            "outcome_json": {
                "home_score": game["home_score"], "away_score": game["away_score"]
            },
            "source_payload_sha256": game["source_payload_sha256"],
        })
    seasons = {game["season"] for game in canonical}
    if require_complete_corpus and seasons != HISTORICAL_SEASONS:
        raise NHLTotalsOfflineReplayError(
            "NHL_TOTALS_REPLAY_SEASON_COHORT_INCOMPLETE", ",".join(sorted(seasons))
        )
    schedules = []
    for record in official_schedules:
        if not isinstance(record, Mapping):
            raise NHLTotalsOfflineReplayError(
                "NHL_TOTALS_REPLAY_SCHEDULE_INPUT_INVALID", "non-mapping"
            )
        try:
            season = int(record["season"])
            team = str(record["team"])
            payload = record["payload"]
        except (KeyError, ValueError, TypeError) as exc:
            raise NHLTotalsOfflineReplayError(
                "NHL_TOTALS_REPLAY_SCHEDULE_INPUT_INVALID", str(exc)
            ) from exc
        schedules.append(normalize_official_regime_schedule(
            payload, expected_season=season, requested_team=team
        ))
    if not schedules:
        raise NHLTotalsOfflineReplayError(
            "NHL_TOTALS_REPLAY_SCHEDULE_MISSING", "no source schedules"
        )
    regimes, regime_audit = reconcile_official_regime_games(schedules)
    coverage = {
        "d1_official_games": len(canonical),
        "regime_official_games": len(regimes),
        "regime_team_source_instances": regime_audit["source_instances"],
        "seasons": sorted(seasons),
    }
    full = (
        seasons == HISTORICAL_SEASONS
        and len(canonical) == 6560 and len(regimes) == 6560
        and regime_audit["source_instances"] == 13120
        and regime_audit["mirrored_event_count"] == 6560
        and all(x["source_instance_count"] == 2 for x in regime_audit["source_version_ledger"])
    )
    if not full:
        if require_complete_corpus:
            raise NHLTotalsOfflineReplayError(
                "NHL_TOTALS_REPLAY_COVERAGE_INCOMPLETE", str(coverage)
            )
        return {
            "status": "NHL_TOTALS_PARTIAL_RESEARCH_NO_FIT",
            "coverage": coverage,
            "model_probability": None,
            "calibrated_lower_bound": None,
            "probability_publishable": False, "can_execute": False,
        }
    official_receipt = reconcile_regime_labels_with_d1_events(canonical, regimes)
    total_rows, feature_audit = build_total_rows([
        {
            "sport": "NHL", "source_provider": "NHL_PUBLIC_WEB_API",
            "historical_reconstruction": True, "can_execute": False,
            "official_event_id": game["official_event_id"],
            "season": game["season"],
            "event_start_time": game["event_start_time"],
            "home_team": game["home_team"], "away_team": game["away_team"],
            "home_score": game["home_score"], "away_score": game["away_score"],
            "source_payload_sha256": game["source_payload_sha256"],
        }
        for game in games
    ])
    model_rows, bridge_audit = build_regime_model_research_rows(
        total_rows, official_receipt
    )
    _artifact, model_receipt = fit_regime_mixture_research(model_rows)
    return {
        "status": "NHL_TOTALS_RESEARCH_MODEL_REPLAY_COMPLETED_NOT_CERTIFIED",
        "source_audit": src_audit,
        "coverage": coverage,
        "feature_audit": feature_audit,
        "bridge_audit": bridge_audit,
        "research_model_receipt": model_receipt,
        "model_probability": None, "calibrated_lower_bound": None,
        "serving_specialist_registered": False,
        "independent_certification": False,
        "pregame_archived_snapshots": False,
        "probability_publishable": False, "can_execute": False,
        "terminal_authority": CONTROLLING_TERMINAL,
    }
