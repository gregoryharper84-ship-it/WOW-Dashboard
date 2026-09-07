"""Delivery and recoverability layer for the WOW V17 Nightly Multi-Scout.

This module does not score, rank, calibrate, or promote Scout evidence. It stamps
an already-produced discovery handoff with a durable run identity and emits a
small manifest so a specific Scout run can be recovered later.

Scout authority remains DISCOVERY_ONLY / RESEARCH_INTEREST and can_execute=false.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,127}$")


def safe_run_id(value: str) -> str:
    value = value.strip()
    if not RUN_ID_PATTERN.fullmatch(value):
        raise ValueError("run_id must be 6-128 chars using letters, numbers, dot, underscore, or hyphen")
    return value


def github_run_url() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not (server and repo and run_id):
        return None
    return f"{server.rstrip('/')}/{repo}/actions/runs/{run_id}"


def stamp_payload(payload: dict[str, Any], run_id: str) -> dict[str, Any]:
    run_id = safe_run_id(run_id)
    handoff = payload.get("model_handoff") if isinstance(payload.get("model_handoff"), dict) else {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), list) else []
    team_rows = handoff.get("team_event_candidates") if isinstance(handoff.get("team_event_candidates"), list) else []
    prop_rows = handoff.get("prop_candidates") if isinstance(handoff.get("prop_candidates"), list) else []

    delivery = {
        "run_id": run_id,
        "research_run_id": run_id,
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "github_run_url": github_run_url(),
        "source_revision": os.environ.get("GITHUB_SHA"),
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "artifact_name": f"wow-v17-multiscout-{run_id}",
        "retention_days": 90,
        "recovery_key": run_id,
    }

    payload["run_id"] = run_id
    payload["research_run_id"] = run_id
    payload["delivery"] = delivery
    payload["reconciliation"] = {
        "coverage_rows": len(coverage),
        "team_event_candidate_rows": len(team_rows),
        "prop_candidate_rows": len(prop_rows),
        "candidate_rows_total": len(team_rows) + len(prop_rows),
        "exact_run_identity_present": True,
    }
    return payload


def build_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    delivery = payload["delivery"]
    recon = payload["reconciliation"]
    return {
        "schema_version": "wow.v17.multiscout.delivery.v1",
        "run_id": payload["run_id"],
        "research_run_id": payload["research_run_id"],
        "status": payload.get("status"),
        "generated_at": payload.get("generated_at"),
        "window": payload.get("window"),
        "scout_team": payload.get("scout_team", []),
        "model_handoff_ready": payload.get("model_handoff_ready", False),
        "delivery": delivery,
        "reconciliation": recon,
        "governance": {
            "discovery_only": True,
            "research_ceiling": "RESEARCH_INTEREST",
            "scout_probability_authority": False,
            "controlling_specialist_required": True,
            "can_execute": False,
        },
    }


def persist(input_path: Path, output_dir: Path, run_id: str) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Scout handoff must be a JSON object")
    if payload.get("can_execute", payload.get("governance", {}).get("can_execute")) is not False:
        raise ValueError("Scout handoff violated can_execute=false")

    payload = stamp_payload(payload, run_id)
    manifest = build_manifest(payload)
    output_dir.mkdir(parents=True, exist_ok=True)

    encoded_payload = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    encoded_manifest = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    # Preserve the legacy path for downstream consumers while adding durable keys.
    input_path.write_text(encoded_payload, encoding="utf-8")
    run_specific = output_dir / f"model-handoff-{run_id}.json"
    run_specific.write_text(encoded_payload, encoding="utf-8")
    (output_dir / "run-manifest.json").write_text(encoded_manifest, encoding="utf-8")
    (output_dir / "latest-pointer.json").write_text(json.dumps({
        "run_id": run_id,
        "research_run_id": run_id,
        "artifact_name": manifest["delivery"]["artifact_name"],
        "github_run_url": manifest["delivery"].get("github_run_url"),
        "generated_at": manifest.get("generated_at"),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    manifest = persist(Path(args.input), Path(args.output_dir), args.run_id)
    print(json.dumps({
        "status": manifest.get("status"),
        "run_id": manifest["run_id"],
        "artifact_name": manifest["delivery"]["artifact_name"],
        "can_execute": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
