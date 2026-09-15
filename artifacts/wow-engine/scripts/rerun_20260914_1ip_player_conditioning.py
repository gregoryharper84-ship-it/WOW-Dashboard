"""Research rerun of the supplied 2026-09-14 PrizePicks MLB 1IP board.

Only exact lines on the active certified aggregate artifact grid are scored.
Player discrimination comes from the BF mixture candidate under validation.
These outputs are research-only until the candidate completes V17 review and
promotion. Unsupported PrizePicks lines remain deterministic OOD.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mlb_1ip_bf_mixture import score_player_bf_mixture
from mlb_1ip_live_acquisition import hydrate_mlb_1ip_evidence
from prop_auto_hydration import PropAutoHydrationError

CAN_EXECUTE = False

BOARD = [
    {"player": "Gavin Williams", "line": 16.5, "event_start": "2026-09-14T22:40:00Z", "offer_type": "STANDARD"},
    {"player": "Sean Newcomb", "line": 15.5, "event_start": "2026-09-14T22:40:00Z", "offer_type": "STANDARD"},
    {"player": "Nick Lodolo", "line": 12.5, "event_start": "2026-09-14T22:40:00Z", "offer_type": "GOBLIN_MORE_ONLY"},
    {"player": "Tarik Skubal", "line": 15.5, "event_start": "2026-09-14T22:40:00Z", "offer_type": "STANDARD"},
    {"player": "José Soriano", "line": 15.5, "event_start": "2026-09-14T23:07:00Z", "offer_type": "STANDARD"},
    {"player": "Troy Melton", "line": 14.5, "event_start": "2026-09-14T23:07:00Z", "offer_type": "STANDARD"},
    {"player": "Brandon Young", "line": 15.5, "event_start": "2026-09-14T23:10:00Z", "offer_type": "STANDARD"},
    {"player": "Jonah Tong", "line": 15.5, "event_start": "2026-09-14T23:10:00Z", "offer_type": "STANDARD"},
    {"player": "Will Warren", "line": 15.5, "event_start": "2026-09-14T23:40:00Z", "offer_type": "STANDARD"},
    {"player": "Dean Kremer", "line": 16.5, "event_start": "2026-09-14T23:40:00Z", "offer_type": "STANDARD"},
    {"player": "Reynaldo López", "line": 16.5, "event_start": "2026-09-14T23:40:00Z", "offer_type": "STANDARD"},
    {"player": "David Peterson", "line": 11.5, "event_start": "2026-09-14T23:40:00Z", "offer_type": "GOBLIN_MORE_ONLY"},
    {"player": "Quinn Mathews", "line": 14.5, "event_start": "2026-09-14T23:45:00Z", "offer_type": "STANDARD"},
    {"player": "Landen Roupp", "line": 15.5, "event_start": "2026-09-14T23:45:00Z", "offer_type": "STANDARD"},
    {"player": "Tomoyuki Sugano", "line": 15.5, "event_start": "2026-09-15T00:40:00Z", "offer_type": "STANDARD"},
    {"player": "Casey Mize", "line": 14.5, "event_start": "2026-09-15T00:40:00Z", "offer_type": "STANDARD"},
    {"player": "Kade Anderson", "line": 15.5, "event_start": "2026-09-15T01:38:00Z", "offer_type": "STANDARD"},
    {"player": "Reid Detmers", "line": 15.5, "event_start": "2026-09-15T01:38:00Z", "offer_type": "STANDARD"},
    {"player": "Sandy Alcantara", "line": 15.5, "event_start": "2026-09-15T01:40:00Z", "offer_type": "STANDARD"},
    {"player": "Corbin Burnes", "line": 15.5, "event_start": "2026-09-15T01:40:00Z", "offer_type": "STANDARD"},
]


def _bundle() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "research-fixtures" / "mlb_1ip_player_conditioning_dev_bundle_20260914.json"
    return json.loads(path.read_text())


def main() -> None:
    bundle = _bundle()
    aggregate = bundle["aggregate_artifact"]["artifact_payload"]
    supported = {float(v) for v in bundle["aggregate_artifact"]["validated_lines"]}
    bf = bundle["bf_artifact"]
    rows: list[dict[str, Any]] = []

    for board in BOARD:
        line = float(board["line"])
        base = {**board, "stat_type": "1ST_INNING_PITCHES_THROWN", "can_execute": False}
        if line not in supported:
            rows.append({
                **base,
                "status": "REJECT_OOD",
                "code": "MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT",
                "supported_lines": sorted(supported),
                "probability_publishable": False,
            })
            continue
        try:
            evidence = hydrate_mlb_1ip_evidence(
                player=board["player"],
                event_start_time=board["event_start"],
            )
        except PropAutoHydrationError as exc:
            rows.append({
                **base,
                "status": "MODEL_INPUTS_INSUFFICIENT",
                "code": str(exc.code),
                "detail": exc.detail,
                "probability_publishable": False,
            })
            continue
        except Exception as exc:
            rows.append({
                **base,
                "status": "PROVIDER_OR_ACQUISITION_FAILURE",
                "code": "MLB_1IP_AUTO_HYDRATION_INTERNAL_ERROR",
                "error_type": type(exc).__name__,
                "probability_publishable": False,
            })
            continue

        directions = ["MORE"] if board["offer_type"] == "GOBLIN_MORE_ONLY" else ["MORE", "LESS"]
        scored_sides = []
        for side in directions:
            scored = score_player_bf_mixture(
                aggregate_artifact_payload=aggregate,
                bf_league_prior=bf["league_prior"],
                bf_alpha=float(bf["alpha"]),
                pitcher_id=int(evidence["pitcher_id"]),
                recent_batters_faced=evidence["recent_1ip_batters_faced"],
                line_value=line,
                side=side,
            )
            scored_sides.append(scored)

        best = max(scored_sides, key=lambda x: float(x["selected_probability"]))
        rows.append({
            **base,
            "status": "RESEARCH_SHADOW_PLAYER_CONDITIONED",
            "starter_name": evidence["starter_name"],
            "pitcher_id": evidence["pitcher_id"],
            "starter_status": evidence["starter_status"],
            "lineup_status": evidence["official_lineup_status"],
            "projected_top_four": evidence["projected_top_four"],
            "recent_1ip_pitch_totals": evidence["recent_1ip_pitch_totals"],
            "recent_1ip_batters_faced": evidence["recent_1ip_batters_faced"],
            "captured_at": evidence["captured_at"],
            "sides": scored_sides,
            "best_side": best["side"],
            "best_probability": best["selected_probability"],
            "aggregate_baseline_probability_for_best_side": best["aggregate_baseline_probability"],
            "player_delta_vs_aggregate": best["player_probability_delta_vs_aggregate"],
            "model_family": best["model_family"],
            "probability_publishable": False,
        })

    ranked = sorted(
        [r for r in rows if r.get("status") == "RESEARCH_SHADOW_PLAYER_CONDITIONED"],
        key=lambda r: float(r["best_probability"]),
        reverse=True,
    )
    out = {
        "purpose": "SEP14_PRIZEPICKS_1IP_PLAYER_CONDITIONED_RERUN",
        "model_family": "MLB_1IP_PLAYER_CONDITIONED_BF_MIXTURE_V1",
        "aggregate_artifact_checksum": bundle["aggregate_artifact"]["artifact_checksum"],
        "bf_artifact_checksum": bf["artifact_checksum"],
        "bf_alpha": bf["alpha"],
        "rows_in": len(BOARD),
        "rows": rows,
        "ranked_supported_rows": ranked,
        "governance": {
            "research_only": True,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        },
    }
    out_dir = Path(os.environ.get("MLB_1IP_CURRENT_RERUN_OUT", "/tmp/mlb-1ip-current-rerun"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sep14_current_slate_rerun.json").write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps({
        "rows_in": len(BOARD),
        "scored": len(ranked),
        "top": [
            {"player": r["player"], "line": r["line"], "best_side": r["best_side"], "best_probability": r["best_probability"]}
            for r in ranked[:10]
        ],
        "probability_publishable": False,
        "can_execute": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
