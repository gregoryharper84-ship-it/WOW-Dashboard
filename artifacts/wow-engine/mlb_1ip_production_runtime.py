"""End-to-end MLB 1IP production-capability adapter.

This joins official evidence acquisition to the existing governed 1IP
specialist. It does not bypass the fitted-artifact gate used by the canonical
HTTP scoring path and it never authorizes execution.
"""
from __future__ import annotations

from typing import Any, Callable

from mlb_1ip_live_acquisition import hydrate_mlb_1ip_evidence
from mlb_1ip_specialist import score_mlb_1ip

CAN_EXECUTE = False


def acquire_and_score_1ip(
    *,
    player: str,
    event_start_time: str,
    line: float,
    direction: str,
    market_evidence_present: bool = False,
    hydrator: Callable[..., dict[str, Any]] = hydrate_mlb_1ip_evidence,
) -> dict[str, Any]:
    evidence = hydrator(player=player, event_start_time=event_start_time)
    result = score_mlb_1ip(
        starter_status=evidence["starter_status"],
        official_lineup_status=evidence["official_lineup_status"],
        projected_top_four=evidence.get("projected_top_four"),
        pitcher_bf_distribution=evidence["pitcher_bf_distribution"],
        baseline_pitches_per_batter=evidence["baseline_pitches_per_batter"],
        line_value=float(line),
        side=direction,
        failure_path_prior=evidence.get("failure_path_prior"),
        market_evidence_present=market_evidence_present,
    )
    return {
        "evidence": evidence,
        "result": result,
        "final_refresh_required": bool(result.get("final_refresh_required")),
        "probability_publishable": False,
        "can_execute": False,
    }
