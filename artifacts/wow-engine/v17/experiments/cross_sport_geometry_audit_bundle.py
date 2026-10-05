"""Run the cross-sport geometry audit from an OIDC-fetched evidence bundle."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from v17.experiments.cross_sport_geometry_audit import (
    AUDIT_VERSION,
    _feature_geometry,
    _replay_lane,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


def run_bundle(bundle: dict) -> dict:
    if bundle.get("can_execute") is not False:
        raise RuntimeError("CROSS_SPORT_AUDIT_BUNDLE_GOVERNANCE_INVALID")
    grouped: dict[str, list[dict]] = {}
    for lane in bundle.get("lanes") or []:
        candidate = lane.get("candidate") or {}
        sport = str(candidate.get("sport") or "").upper()
        if not sport:
            raise RuntimeError("CROSS_SPORT_AUDIT_BUNDLE_SPORT_MISSING")
        grouped.setdefault(sport, []).append(lane)

    report = {
        "audit_version": AUDIT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(bundle.get("source") or "UNKNOWN"),
        "can_execute": False,
        "probability_publishable": False,
        "automatic_promotion": False,
        "sports": {},
    }

    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        lanes = grouped.get(sport, [])
        if not lanes:
            report["sports"][sport] = {
                "status": "BLOCKED_WITH_EXACT_REASON",
                "blocker": "D1_CANDIDATE_ARTIFACT_MISSING",
                "can_execute": False,
            }
            continue

        sport_rows = []
        for lane in lanes:
            candidate = dict(lane.get("candidate") or {})
            rows = list(lane.get("rows") or [])
            geometry = _feature_geometry(candidate, rows)
            replay = _replay_lane(candidate, rows, geometry)
            sport_rows.append(
                {
                    "league": candidate.get("league"),
                    "model_family": candidate.get("model_family"),
                    "model_artifact_version": candidate.get("model_artifact_version"),
                    "feature_schema_version": candidate.get("feature_schema_version"),
                    "source_review_status": candidate.get("source_review_status"),
                    "persisted_research_screen_pass": candidate.get(
                        "research_screen_pass"
                    ),
                    "cohort_rows": len(rows),
                    "expected_rows": int(candidate.get("training_rows") or 0)
                    + int(candidate.get("calibration_rows") or 0)
                    + int(candidate.get("test_rows") or 0),
                    "geometry": geometry,
                    "replay": replay,
                    "uncertainty_status": "SEPARATE_DECISION_LOWER_BOUND_REPLAY_REQUIRED",
                    "forward_status": "TRUE_FORWARD_SHADOW_NOT_SEEDED_BY_READ_ONLY_AUDIT",
                    "probability_publishable": False,
                    "automatic_promotion": False,
                    "can_execute": False,
                }
            )
        report["sports"][sport] = {
            "status": "AUDITED",
            "lanes": sport_rows,
            "can_execute": False,
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    bundle = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = run_bundle(bundle)
    Path(args.output).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for sport, payload in report["sports"].items():
        if payload["status"] != "AUDITED":
            print(f"{sport}: {payload['status']} {payload.get('blocker')}")
            continue
        for lane in payload["lanes"]:
            replay = lane["replay"]
            severe = [
                item["feature"]
                for item in lane["geometry"].get("features", [])
                if item.get("severe")
            ]
            print(
                f"{sport}/{lane['league']}: replay={replay['status']} "
                f"severe={','.join(severe) or 'NONE'} "
                f"best={replay.get('best_retrospective_variant','NONE')}"
            )
    print("probability_publishable=false automatic_promotion=false can_execute=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
