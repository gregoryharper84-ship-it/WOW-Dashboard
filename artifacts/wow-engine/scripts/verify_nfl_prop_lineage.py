"""Offline, read-only NFL prop certification-lineage verifier and receipt builder.

Re-derives what can be recomputed from files in the repository and combines it with
a committed evidence file of external lookups. It never trains, fetches, writes to a
database, promotes, or grants capability. Each lineage claim is reported as its own
assertion; none implies another.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

RECEIPT_SCHEMA = "WOW_NFL_PROP_LINEAGE_RECEIPT_V1"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LANES = ("ANYTIME_TD", "PASSING_YARDS", "RECEIVING_YARDS", "RUSHING_YARDS")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_checksum(payload: Any) -> str:
    """The exact function the trainer used for artifact_checksum."""
    return sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def verify_internal_consistency(artifacts: list[dict], report: Mapping[str, Any]) -> dict[str, dict]:
    """Recompute artifact_checksum from each payload and the dataset-hash formula."""
    bundle_hash = report["source"]["bundle_hash"]
    out: dict[str, dict] = {}
    for art in artifacts:
        lane = art["stat_type"]
        expected_dataset = sha256_bytes(
            (bundle_hash + "|" + lane + "|NFL_PROP_SPLIT_2021_2023_2024_2025").encode("utf-8")
        )
        out[lane] = {
            "artifact_checksum_matches_payload": canonical_checksum(art["artifact_payload"]) == art["artifact_checksum"],
            "training_dataset_hash_matches_formula": expected_dataset == art["training_dataset_hash"],
            "can_execute_false": art.get("can_execute") is False,
            "probability_publishable_false": art.get("probability_publishable") is False,
        }
    return out


def verify_registry_matches(artifacts: list[dict], registry_rows: Mapping[str, Mapping[str, Any]]) -> dict[str, dict]:
    by_lane = {a["stat_type"]: a for a in artifacts}
    out: dict[str, dict] = {}
    for lane in LANES:
        art, reg = by_lane.get(lane), registry_rows.get(lane)
        out[lane] = {
            "registry_row_present": reg is not None,
            "checksum_equal": bool(art and reg and art["artifact_checksum"] == reg["artifact_checksum"]),
            "dataset_hash_equal": bool(art and reg and art["training_dataset_hash"] == reg["training_dataset_hash"]),
            "code_sha_equal": bool(art and reg and art["training_code_sha"] == reg["training_code_sha"]),
            "training_rows_equal": bool(art and reg and art["training_rows"] == reg["training_rows"]),
            "registry_probability_publishable": None if reg is None else reg["probability_publishable"],
        }
    return out


def _lane_metrics(report: Mapping[str, Any]) -> dict[str, dict]:
    keep = ("holdout_rows", "calibration_rows", "holdout_season", "calibration_season", "selected_blend_weight",
            "mae_ratio_vs_naive", "brier_ratio_vs_naive", "holdout_ood_rate_z_gt_6")
    return {lane: {k: report["routes"][lane][k] for k in keep if k in report["routes"][lane]} for lane in LANES}


def build_receipt(artifacts_path: Path = DATA / "wow_nfl_prop_artifacts_v1.json",
                  report_path: Path = DATA / "wow_nfl_prop_training_report_v1.json",
                  trainer_path: Path = ROOT / "scripts" / "train_nfl_props.py",
                  evidence_path: Path = DATA / "wow_nfl_prop_lineage_evidence_2026-11-11.json") -> dict[str, Any]:
    artifacts_bytes, report_bytes = artifacts_path.read_bytes(), report_path.read_bytes()
    artifacts, report = json.loads(artifacts_bytes), json.loads(report_bytes)
    ev = json.loads(evidence_path.read_text(encoding="utf-8"))
    internal = verify_internal_consistency(artifacts, report)
    registry = verify_registry_matches(artifacts, ev["registry_snapshot"]["rows"])
    trainer_sha = sha256_bytes(trainer_path.read_bytes())
    code = ev["training_code"]
    same_code = trainer_sha == code["trainer_sha256_at_commit"]
    repro_files_equal = (
        sha256_bytes(artifacts_bytes) == ev["refit"]["output_artifacts_sha256"]
        and sha256_bytes(report_bytes) == ev["refit"]["output_report_sha256"]
    )
    envs_match = ev["original_fit_run"]["packages"] == ev["refit"]["packages"]
    all_internal = all(all(v.values()) for v in internal.values())
    all_registry = all(
        v["registry_row_present"] and v["checksum_equal"] and v["dataset_hash_equal"] and v["code_sha_equal"]
        and v["training_rows_equal"] for v in registry.values()
    )
    return {
        "schema": RECEIPT_SCHEMA,
        "can_execute": False,
        "probability_publishable": False,
        "grants_model_capability": False,
        "note": "Lineage evidence only. Not certification, promotion, calibration approval or publication authority.",
        "chain": {
            "source_bytes": {
                "bundle_id": "nflverse-player-weekly-2021-2025-20260920",
                "bundle_hash": report["source"]["bundle_hash"],
                "assets": report["source"]["assets"],
                "redownloaded_2026_11_11_hashes_match": ev["refit"]["datasets_redownloaded_sha256_matched_report"],
            },
            "training_code_revision": {"commit_sha": code["commit_sha"], "parent_sha": code["parent_sha"],
                                       "trainer_sha256": trainer_sha, "trainer_matches_commit": same_code},
            "environment": {"original": ev["original_fit_run"], "refit": ev["refit"]},
            "fit_run": {"workflow_run_id": ev["original_fit_run"]["workflow_run_id"],
                        "conclusion": ev["original_fit_run"]["conclusion"],
                        "created_at_utc": ev["original_fit_run"]["created_at_utc"]},
            "artifacts": {lane: {"artifact_checksum": a["artifact_checksum"], "training_dataset_hash": a["training_dataset_hash"],
                                 "training_rows": a["training_rows"]} for a in artifacts for lane in [a["stat_type"]]},
            "evaluation_metrics": _lane_metrics(report),
            "certification_decision": {
                "registry_lifecycle": {l: r["lifecycle_state"] for l, r in ev["registry_snapshot"]["rows"].items()},
                "calibrator_version": ev["registry_snapshot"]["calibrator_version"],
                "registry_probability_publishable": {l: r["probability_publishable"] for l, r in ev["registry_snapshot"]["rows"].items()},
                "limitation": "Calibration is a bootstrap pre-calibration, not a fitted Phase-B calibrator; no publication authority.",
            },
            "activation_record": ev["activation_record"],
        },
        "assertions": {
            "1_verified_historical_source_identity": {
                "status": "VERIFIED_EXTERNALLY_NOT_IN_REPO_REFS" if (same_code and code["resolvable_via_github_api"]) else "UNVERIFIED",
                "detail": "Commit resolvable on GitHub and its trainer bytes equal the repository script; the commit is not reachable from any branch in a clone, so ORIGINAL_TRAINING_CODE_UNRECOVERABLE does not apply, but its availability depends on GitHub retention.",
            },
            "2_internal_checksum_consistency": {"status": "VERIFIED" if all_internal else "FAILED", "per_lane": internal},
            "3_independently_reproduced_statistical_results": {
                "status": "REPRODUCED_EXACT" if repro_files_equal else "NOT_REPRODUCED",
                "detail": "Re-execution of the same trainer; metrics, coefficients and payloads equal. Not an independent reimplementation.",
            },
            "4_independently_reproduced_artifact_bytes": {
                "status": "REPRODUCED_EXACT" if repro_files_equal else "NOT_REPRODUCED",
                "environment_pins_identical_to_original": envs_match,
                "artifacts_file_sha256": sha256_bytes(artifacts_bytes),
                "report_file_sha256": sha256_bytes(report_bytes),
            },
            "5_verified_production_registration": {
                "status": "REGISTRY_ROWS_MATCH_VALIDATION_PROJECT_ONLY" if all_registry else "MISMATCH",
                "per_lane": registry,
                "limitation": "Checked checksum/hash/code/rows fields on the validation project; registry payload bytes and the production project identity were not verified.",
            },
        },
    }


def main() -> None:  # pragma: no cover
    print(json.dumps(build_receipt(), indent=2, sort_keys=True))


if __name__ == "__main__":  # pragma: no cover
    main()
