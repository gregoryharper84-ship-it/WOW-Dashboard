"""Read-only cross-sport feature-geometry and challenger audit for #1347.

This experiment reads D1 candidate/training evidence, reproduces the latest
candidate where possible, audits scaler geometry, and runs non-serving
stationarity ablations through the repository's existing chronological
candidate lifecycles.

It never writes model rows, certifies, promotes, publishes, routes, or executes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row

from v17.binary_candidate_lifecycle import BinaryTrainingRow, train_binary_candidate
from v17.multiclass_candidate_lifecycle import (
    MulticlassTrainingRow,
    train_multiclass_candidate,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
AUDIT_VERSION = "V17_CROSS_SPORT_GEOMETRY_AUDIT_V1"

_SUSPECT_RE = re.compile(
    r"(games_prior$|games_prior_log$|surface_games_prior_log$|"
    r"games_since_structural_change$|matches_prior$|fights_prior$)"
)
_RAW_CUMULATIVE = {
    "home_games_prior",
    "away_games_prior",
    "home_games_since_structural_change",
    "away_games_since_structural_change",
}
_MULTICLASS_SPORTS = {"SOCCER"}


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _latest_candidates(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select distinct on (sport,league)
              candidate_id,created_at,sport,league,market_family,model_family,
              model_artifact_version,feature_schema_version,training_dataset_hash,
              artifact_payload,calibrator_payload,validation_metrics,
              training_rows,calibration_rows,test_rows,research_screen_pass,
              source_review_status,lifecycle_state,promoted,active,
              probability_publishable,can_execute
            from public.wow_d1_candidate_artifacts
            order by sport,league,created_at desc
            """
        )
        return [dict(row) for row in cur.fetchall()]


def _cohort_rows(conn: psycopg.Connection, candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            with ranked as (
              select t.*,
                     row_number() over (
                       partition by t.official_event_id
                       order by t.created_at desc
                     ) as rn
              from public.wow_d1_training_rows t
              where t.sport=%s
                and t.league=%s
                and t.feature_schema_version=%s
                and t.created_at <= %s
            )
            select official_event_id,event_start_time,feature_as_of,features,
                   outcome_json,source_manifest_sha256,archived_pregame_snapshot,
                   historical_reconstruction,market_features_used,can_execute
            from ranked
            where rn=1
            order by event_start_time,official_event_id
            """,
            (
                candidate["sport"],
                candidate["league"],
                candidate["feature_schema_version"],
                candidate["created_at"],
            ),
        )
        return [dict(row) for row in cur.fetchall()]


def _feature_geometry(
    candidate: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    artifact = _json(candidate["artifact_payload"]) or {}
    names = list(artifact.get("feature_names") or [])
    means = list(artifact.get("scaler_mean") or [])
    scales = list(artifact.get("scaler_scale") or [])
    if not names or len(names) != len(means) or len(names) != len(scales):
        return {
            "status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": "SCALER_GEOMETRY_METADATA_INCOMPLETE",
            "features": [],
        }

    latest_start = int(len(rows) * 0.80)
    current = rows[latest_start:]
    findings = []
    for idx, name in enumerate(names):
        if not _SUSPECT_RE.search(name):
            continue
        scale = float(scales[idx])
        if not math.isfinite(scale) or scale <= 0:
            findings.append(
                {
                    "feature": name,
                    "status": "BLOCKED_WITH_EXACT_REASON",
                    "blocker": "SCALER_SCALE_NONPOSITIVE",
                }
            )
            continue
        values = []
        zs = []
        for row in current:
            features = _json(row["features"]) or {}
            raw = features.get(name)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            value = float(raw)
            if not math.isfinite(value):
                continue
            values.append(value)
            zs.append((value - float(means[idx])) / scale)
        if not zs:
            continue
        gt3 = sum(1 for z in zs if abs(z) > 3.0)
        findings.append(
            {
                "feature": name,
                "current_rows": len(zs),
                "mean_value": sum(values) / len(values),
                "mean_z": sum(zs) / len(zs),
                "min_z": min(zs),
                "max_z": max(zs),
                "gt3_n": gt3,
                "gt3_pct": gt3 / len(zs),
                "severe": name in _RAW_CUMULATIVE and gt3 / len(zs) >= 0.50,
            }
        )
    return {"status": "PASS", "features": findings}


def _binary_rows(rows: Sequence[Mapping[str, Any]]) -> list[BinaryTrainingRow]:
    output = []
    for row in rows:
        outcome = _json(row["outcome_json"]) or {}
        value = outcome.get("positive_outcome")
        if not isinstance(value, bool):
            value = outcome.get("home_win")
        if not isinstance(value, bool):
            raise ValueError(f"BINARY_OUTCOME_MISSING:{row['official_event_id']}")
        output.append(
            BinaryTrainingRow(
                event_id=str(row["official_event_id"]),
                event_start_time=row["event_start_time"].isoformat(),
                feature_as_of=row["feature_as_of"].isoformat(),
                positive_outcome=value,
                features={k: float(v) for k, v in (_json(row["features"]) or {}).items()},
                source_manifest_sha256=str(row["source_manifest_sha256"]),
            )
        )
    output.sort(key=lambda row: row.event_start_time)
    return output


def _multiclass_rows(rows: Sequence[Mapping[str, Any]]) -> list[MulticlassTrainingRow]:
    output = []
    for row in rows:
        outcome = str((_json(row["outcome_json"]) or {}).get("outcome") or "").upper()
        if not outcome:
            raise ValueError(f"MULTICLASS_OUTCOME_MISSING:{row['official_event_id']}")
        output.append(
            MulticlassTrainingRow(
                event_id=str(row["official_event_id"]),
                event_start_time=row["event_start_time"].isoformat(),
                feature_as_of=row["feature_as_of"].isoformat(),
                outcome=outcome,
                features={k: float(v) for k, v in (_json(row["features"]) or {}).items()},
                source_manifest_sha256=str(row["source_manifest_sha256"]),
            )
        )
    output.sort(key=lambda row: (row.event_start_time, row.event_id))
    return output


def _transform_binary(
    rows: Sequence[BinaryTrainingRow],
    severe: set[str],
    mode: str,
) -> tuple[list[BinaryTrainingRow], tuple[str, ...]]:
    base_names = tuple(sorted(rows[0].features))
    if mode == "drop":
        names = tuple(name for name in base_names if name not in severe)
        return list(rows), names

    transformed = []
    for row in rows:
        features = dict(row.features)
        for name in severe:
            value = max(0.0, float(features[name]))
            if mode == "log1p":
                features[name] = math.log1p(value)
            elif mode == "stationary":
                if name.endswith("games_prior"):
                    features[name] = 1.0 if value >= 8.0 else 0.0
                else:
                    features[name] = min(value, 10.0)
            else:
                raise ValueError(mode)
        transformed.append(replace(row, features=features))
    return transformed, base_names


def _transform_multi(
    rows: Sequence[MulticlassTrainingRow],
    severe: set[str],
    mode: str,
) -> tuple[list[MulticlassTrainingRow], tuple[str, ...]]:
    base_names = tuple(sorted(rows[0].features))
    if mode == "drop":
        names = tuple(name for name in base_names if name not in severe)
        return list(rows), names

    transformed = []
    for row in rows:
        features = dict(row.features)
        for name in severe:
            value = max(0.0, float(features[name]))
            if mode == "log1p":
                features[name] = math.log1p(value)
            elif mode == "stationary":
                if name.endswith("games_prior"):
                    features[name] = 1.0 if value >= 8.0 else 0.0
                else:
                    features[name] = min(value, 10.0)
            else:
                raise ValueError(mode)
        transformed.append(replace(row, features=features))
    return transformed, base_names


def _metric_dict(candidate: Any) -> dict[str, Any]:
    metrics = asdict(candidate.metrics)
    return {
        **metrics,
        "calibrator_method": str(candidate.calibrator_payload.get("method") or ""),
        "research_screen_pass": bool(candidate.research_screen_pass),
        "dataset_hash": candidate.dataset_hash,
    }


def _replay_lane(
    candidate: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    geometry: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _json(candidate["artifact_payload"]) or {}
    names = tuple(str(x) for x in (artifact.get("feature_names") or []))
    expected_n = (
        int(candidate["training_rows"])
        + int(candidate["calibration_rows"])
        + int(candidate["test_rows"])
    )
    if len(rows) != expected_n:
        return {
            "status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": f"D1_REPLAY_COHORT_COUNT_MISMATCH:{len(rows)}:{expected_n}",
        }

    severe = {
        item["feature"]
        for item in geometry.get("features", [])
        if item.get("severe") is True
    }
    sport = str(candidate["sport"])
    family = str(candidate["model_family"])

    try:
        if sport in _MULTICLASS_SPORTS:
            training = _multiclass_rows(rows)
            expected_classes = tuple(str(x) for x in (artifact.get("classes") or ["HOME", "DRAW", "AWAY"]))
            incumbent = train_multiclass_candidate(
                training,
                model_family=family,
                feature_names=names,
                expected_classes=expected_classes,
                min_rows=max(500, min(len(training), 500)),
            )
        else:
            training = _binary_rows(rows)
            incumbent = train_binary_candidate(
                training,
                model_family=family,
                feature_names=names,
                min_rows=max(300, min(len(training), 300)),
            )
    except Exception as exc:
        return {
            "status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": f"INCUMBENT_REPLAY_FAILED:{type(exc).__name__}:{exc}",
        }

    expected_hash = str(candidate["training_dataset_hash"] or "")
    if incumbent.dataset_hash != expected_hash:
        return {
            "status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": "D1_REPLAY_DATASET_HASH_MISMATCH",
            "expected_hash": expected_hash,
            "replayed_hash": incumbent.dataset_hash,
            "replayed_metrics": _metric_dict(incumbent),
        }

    result = {
        "status": "REPRODUCED",
        "incumbent": _metric_dict(incumbent),
        "severe_features": sorted(severe),
        "variants": {},
    }
    if not severe:
        result["decision"] = "NO_SEVERE_CUMULATIVE_OOD_ABLATION_REQUIRED"
        return result

    for mode in ("drop", "log1p", "stationary"):
        try:
            if sport in _MULTICLASS_SPORTS:
                variant_rows, variant_names = _transform_multi(training, severe, mode)
                fitted = train_multiclass_candidate(
                    variant_rows,
                    model_family=f"{family}_GEOMETRY_{mode.upper()}_V1",
                    feature_names=variant_names,
                    expected_classes=incumbent.classes,
                    min_rows=max(500, min(len(variant_rows), 500)),
                )
            else:
                variant_rows, variant_names = _transform_binary(training, severe, mode)
                fitted = train_binary_candidate(
                    variant_rows,
                    model_family=f"{family}_GEOMETRY_{mode.upper()}_V1",
                    feature_names=variant_names,
                    min_rows=max(300, min(len(variant_rows), 300)),
                )
            metrics = _metric_dict(fitted)
            inc = _metric_dict(incumbent)
            metrics["delta_calibrated_brier"] = (
                metrics["calibrated_brier"] - inc["calibrated_brier"]
            )
            metrics["delta_calibrated_log_loss"] = (
                metrics["calibrated_log_loss"] - inc["calibrated_log_loss"]
            )
            ece_key = "calibrated_ece" if "calibrated_ece" in metrics else "ece"
            inc_ece_key = "calibrated_ece" if "calibrated_ece" in inc else "ece"
            metrics["delta_ece"] = metrics[ece_key] - inc[inc_ece_key]
            result["variants"][mode] = metrics
        except Exception as exc:
            result["variants"][mode] = {
                "status": "BLOCKED_WITH_EXACT_REASON",
                "blocker": f"ABLATION_REPLAY_FAILED:{type(exc).__name__}:{exc}",
            }

    comparable = [
        (mode, value)
        for mode, value in result["variants"].items()
        if "calibrated_brier" in value and "calibrated_log_loss" in value
    ]
    if comparable:
        comparable.sort(
            key=lambda item: (
                item[1]["calibrated_brier"],
                item[1]["calibrated_log_loss"],
            )
        )
        result["best_retrospective_variant"] = comparable[0][0]
    result["decision"] = "CHALLENGER_REPLAY_COMPLETE_NO_PRODUCTION_PROMOTION"
    return result


def run(database_url: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "audit_version": AUDIT_VERSION,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "can_execute": False,
        "probability_publishable": False,
        "automatic_promotion": False,
        "sports": {},
    }
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.execute("set transaction read only")
        candidates = _latest_candidates(conn)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            grouped.setdefault(str(candidate["sport"]), []).append(candidate)

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
            for candidate in lanes:
                cohort = _cohort_rows(conn, candidate)
                geometry = _feature_geometry(candidate, cohort)
                replay = _replay_lane(candidate, cohort, geometry)
                sport_rows.append(
                    {
                        "league": candidate["league"],
                        "model_family": candidate["model_family"],
                        "model_artifact_version": candidate["model_artifact_version"],
                        "feature_schema_version": candidate["feature_schema_version"],
                        "source_review_status": candidate["source_review_status"],
                        "persisted_research_screen_pass": candidate["research_screen_pass"],
                        "cohort_rows": len(cohort),
                        "expected_rows": int(candidate["training_rows"])
                        + int(candidate["calibration_rows"])
                        + int(candidate["test_rows"]),
                        "geometry": geometry,
                        "replay": replay,
                        "uncertainty_status": (
                            "SEPARATE_DECISION_LOWER_BOUND_REPLAY_REQUIRED"
                        ),
                        "forward_status": (
                            "TRUE_FORWARD_SHADOW_NOT_SEEDED_BY_READ_ONLY_AUDIT"
                        ),
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
    parser.add_argument("--database-url", default=os.getenv("WOW_SCOUT_DATABASE_URL"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit(
            "BLOCKED_WITH_EXACT_REASON:WOW_SCOUT_DATABASE_URL_NOT_CONFIGURED"
        )
    report = run(args.database_url)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(f"AUDIT_VERSION={report['audit_version']}")
    for sport, payload in report["sports"].items():
        if payload["status"] != "AUDITED":
            print(f"{sport}: {payload['status']} {payload.get('blocker')}")
            continue
        for lane in payload["lanes"]:
            replay = lane["replay"]
            severe = [
                f["feature"]
                for f in lane["geometry"].get("features", [])
                if f.get("severe")
            ]
            print(
                f"{sport}/{lane['league']}: replay={replay['status']} "
                f"severe={','.join(severe) or 'NONE'} "
                f"best={replay.get('best_retrospective_variant','NONE')}"
            )
    print("probability_publishable=false can_execute=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
