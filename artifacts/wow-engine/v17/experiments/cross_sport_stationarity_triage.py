"""Research-only stationarity challenger triage for issue #1377.

Consumes the governed cross-sport geometry audit output and identifies which
sport-specific stationarity variants merit deeper replay/counterexample review.

This module does not train, score, certify, promote, publish, route, or execute
sporting probabilities. It only evaluates experiment metrics already produced
by the read-only #1347 audit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
EXPERIMENT_ID = "V17_CROSS_SPORT_STATIONARITY_WAVE_1377_V1"

TARGET_LANES: dict[str, set[str]] = {
    "MLB": {"MLB"},
    "NCAAB": {"NCAAB"},
    "SOCCER": {"BUNDESLIGA", "EPL", "LALIGA", "SERIE_A"},
}


def _require_fail_closed(report: Mapping[str, Any]) -> None:
    if report.get("can_execute") is not False:
        raise RuntimeError("STATIONARITY_TRIAGE_INPUT_CAN_EXECUTE_INVALID")
    if report.get("probability_publishable") is not False:
        raise RuntimeError("STATIONARITY_TRIAGE_INPUT_PUBLICATION_INVALID")
    if report.get("automatic_promotion") is not False:
        raise RuntimeError("STATIONARITY_TRIAGE_INPUT_PROMOTION_INVALID")


def _metric(value: Mapping[str, Any], name: str) -> float:
    raw = value.get(name)
    if not isinstance(raw, (int, float)):
        raise RuntimeError(f"STATIONARITY_TRIAGE_METRIC_MISSING:{name}")
    return float(raw)


def _qualifies_for_deep_replay(
    incumbent: Mapping[str, Any],
    variant: Mapping[str, Any],
) -> bool:
    """Research triage only; never a production promotion decision."""
    if variant.get("research_screen_pass") is not True:
        return False
    return (
        _metric(variant, "calibrated_brier")
        <= _metric(incumbent, "calibrated_brier")
        and _metric(variant, "calibrated_log_loss")
        <= _metric(incumbent, "calibrated_log_loss")
    )


def _find_lane(report: Mapping[str, Any], sport: str, league: str) -> Mapping[str, Any] | None:
    payload = (report.get("sports") or {}).get(sport) or {}
    for lane in payload.get("lanes") or []:
        if str(lane.get("league") or "").upper() == league:
            return lane
    return None


def triage(report: Mapping[str, Any]) -> dict[str, Any]:
    _require_fail_closed(report)
    lanes: list[dict[str, Any]] = []

    for sport, leagues in TARGET_LANES.items():
        for league in sorted(leagues):
            lane = _find_lane(report, sport, league)
            if lane is None:
                lanes.append(
                    {
                        "sport": sport,
                        "league": league,
                        "status": "BLOCKED_WITH_EXACT_REASON",
                        "blocker": "TARGET_LANE_AUDIT_RESULT_MISSING",
                        "can_execute": False,
                    }
                )
                continue

            severe = sorted(
                str(item.get("feature"))
                for item in ((lane.get("geometry") or {}).get("features") or [])
                if item.get("severe") is True
            )
            if not severe:
                lanes.append(
                    {
                        "sport": sport,
                        "league": league,
                        "status": "BLOCKED_WITH_EXACT_REASON",
                        "blocker": "TARGET_LANE_NO_SEVERE_CUMULATIVE_OOD",
                        "can_execute": False,
                    }
                )
                continue

            replay = lane.get("replay") or {}
            if replay.get("status") != "REPRODUCED":
                lanes.append(
                    {
                        "sport": sport,
                        "league": league,
                        "status": "BLOCKED_WITH_EXACT_REASON",
                        "blocker": str(
                            replay.get("blocker")
                            or f"INCUMBENT_REPLAY_{replay.get('status') or 'UNKNOWN'}"
                        ),
                        "severe_features": severe,
                        "can_execute": False,
                    }
                )
                continue

            incumbent = replay.get("incumbent") or {}
            variants = replay.get("variants") or {}
            evaluated: list[dict[str, Any]] = []
            for mode in ("drop", "log1p", "stationary"):
                value = variants.get(mode)
                if not isinstance(value, Mapping) or "calibrated_brier" not in value:
                    evaluated.append(
                        {
                            "mode": mode,
                            "status": "BLOCKED_WITH_EXACT_REASON",
                            "blocker": str(
                                value.get("blocker")
                                if isinstance(value, Mapping)
                                else "VARIANT_RESULT_MISSING"
                            ),
                        }
                    )
                    continue
                evaluated.append(
                    {
                        "mode": mode,
                        "status": "EVALUATED",
                        "calibrated_brier": _metric(value, "calibrated_brier"),
                        "calibrated_log_loss": _metric(value, "calibrated_log_loss"),
                        "delta_calibrated_brier": _metric(
                            value, "delta_calibrated_brier"
                        ),
                        "delta_calibrated_log_loss": _metric(
                            value, "delta_calibrated_log_loss"
                        ),
                        "delta_ece": _metric(value, "delta_ece"),
                        "research_screen_pass": value.get("research_screen_pass") is True,
                        "advances_to_deep_replay": _qualifies_for_deep_replay(
                            incumbent, value
                        ),
                    }
                )

            advancing = [
                row
                for row in evaluated
                if row.get("status") == "EVALUATED"
                and row.get("advances_to_deep_replay") is True
            ]
            advancing.sort(
                key=lambda row: (
                    row["calibrated_brier"],
                    row["calibrated_log_loss"],
                    row["mode"],
                )
            )
            lanes.append(
                {
                    "sport": sport,
                    "league": league,
                    "status": "EXPERIMENT_CREATED",
                    "severe_features": severe,
                    "incumbent": {
                        "calibrated_brier": _metric(incumbent, "calibrated_brier"),
                        "calibrated_log_loss": _metric(
                            incumbent, "calibrated_log_loss"
                        ),
                        "research_screen_pass": incumbent.get("research_screen_pass")
                        is True,
                    },
                    "variants": evaluated,
                    "research_leader": advancing[0]["mode"] if advancing else None,
                    "deep_replay_required": bool(advancing),
                    "probability_publishable": False,
                    "automatic_promotion": False,
                    "can_execute": False,
                }
            )

    return {
        "experiment_id": EXPERIMENT_ID,
        "parent_issue": 1347,
        "issue": 1377,
        "terminal_authority": TERMINAL_AUTHORITY,
        "terminal_state": "EXPERIMENT_CREATED",
        "probability_publishable": False,
        "automatic_promotion": False,
        "can_execute": False,
        "lanes": lanes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = triage(report)
    Path(args.output).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    for lane in result["lanes"]:
        print(
            f"{lane['sport']}/{lane['league']}: "
            f"{lane['status']} leader={lane.get('research_leader') or 'NONE'}"
        )
    print(
        "terminal_state=EXPERIMENT_CREATED "
        "probability_publishable=false automatic_promotion=false can_execute=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
