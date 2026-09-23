"""Build the deterministic WOW_BETTING_ENGINE live-editor synchronization packet.

The repository is authoritative for the canonical host instructions, PrizePicks
host addendum, and Action schema. This utility packages those exact bytes and a
SHA-256 manifest so the external ChatGPT Custom GPT editor save/reload can be
attested without guessing which repository revision was installed.

This utility never reads or emits WOW_ACTION_API_KEY or any other credential.
It cannot update the live GPT editor by itself and therefore never marks live
editor parity VERIFIED. can_execute is always false.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENGINE = ROOT / "artifacts" / "wow-engine"
INSTRUCTIONS = ENGINE / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"
PRIZEPICKS_ADDENDUM = ENGINE / "WOW_V17_CUSTOM_GPT_PRIZEPICKS_SKILL_ADDENDUM.txt"
ACTION_SCHEMA = ENGINE / "v17" / "openapi.wow-betting-engine.v17.yaml"

REQUIRED_OPERATIONS = (
    "getWowV17BackendHealth",
    "scoreWowPickRequest",
    "lookupWowV17PredictionReceipts",
)
REQUIRED_PRIZEPICKS_TOKENS = (
    "SOURCE_PAGE_UNREADABLE:<page_number>",
    "source_pages_total",
    "source_pages_readable",
    "source_pages_unreadable",
    "Player",
    "Matchup",
    "PrizePicks line",
    "Offer",
    "Available side(s)",
    "Current/live note",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repository_sha() -> str:
    from_env = str(os.environ.get("GITHUB_SHA") or "").strip()
    if from_env:
        return from_env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "UNRESOLVED"


def build_packet() -> tuple[bytes, dict]:
    instructions = INSTRUCTIONS.read_bytes()
    addendum = PRIZEPICKS_ADDENDUM.read_bytes()
    schema = ACTION_SCHEMA.read_bytes()

    schema_text = schema.decode("utf-8")
    addendum_text = addendum.decode("utf-8")
    missing_operations = [name for name in REQUIRED_OPERATIONS if name not in schema_text]
    missing_addendum_tokens = [token for token in REQUIRED_PRIZEPICKS_TOKENS if token not in addendum_text]
    if missing_operations:
        raise RuntimeError("GPT_EDITOR_SYNC_SCHEMA_OPERATION_MISSING:" + ",".join(missing_operations))
    if missing_addendum_tokens:
        raise RuntimeError("GPT_EDITOR_SYNC_ADDENDUM_CONTRACT_MISSING:" + ",".join(missing_addendum_tokens))

    packet = (
        b"WOW_BETTING_ENGINE V17 LIVE EDITOR SYNC PACKET\n"
        b"================================================\n\n"
        b"[CANONICAL HOST INSTRUCTIONS -- paste into the live GPT Instructions field]\n\n"
        + instructions.rstrip()
        + b"\n\n[PRIZEPICKS LIVE-HOST ADDENDUM -- append immediately after the canonical instructions]\n\n"
        + addendum.rstrip()
        + b"\n"
    )
    manifest = {
        "contract": "WOW_V17_GPT_EDITOR_SYNC_PACKET_V1",
        "custom_gpt_identity": "WOW_BETTING_ENGINE",
        "runtime_generation": "V17_ACTIVE",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "repository_sha": _repository_sha(),
        "canonical_instructions_path": str(INSTRUCTIONS.relative_to(ROOT)),
        "canonical_instructions_sha256": _sha256(instructions),
        "prizepicks_addendum_path": str(PRIZEPICKS_ADDENDUM.relative_to(ROOT)),
        "prizepicks_addendum_sha256": _sha256(addendum),
        "action_schema_path": str(ACTION_SCHEMA.relative_to(ROOT)),
        "action_schema_sha256": _sha256(schema),
        "combined_editor_packet_sha256": _sha256(packet),
        "required_operations": list(REQUIRED_OPERATIONS),
        "required_prizepicks_tokens": list(REQUIRED_PRIZEPICKS_TOKENS),
        "authentication_contract": "API_KEY_TO_BEARER_EXISTING_WOW_ACTION_API_KEY__SECRET_NOT_INCLUDED",
        "editor_update_required": True,
        "live_editor_verified": False,
        "acceptance_required": [
            "SAVE_AND_RELOAD_PRODUCTION_WOW_BETTING_ENGINE_EDITOR",
            "FRESH_CHAT_GET_WOW_V17_BACKEND_HEALTH",
            "FRESH_CHAT_SCORE_WOW_PICK_REQUEST",
            "FRESH_CHAT_LOOKUP_WOW_V17_PREDICTION_RECEIPTS",
            "FRESH_CHAT_MULTIPAGE_PRIZEPICKS_CANARY",
            "CONFIRM_CAN_EXECUTE_FALSE",
        ],
        "can_execute": False,
    }
    return packet, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    packet, manifest = build_packet()
    packet_path = args.output_dir / "WOW_V17_GPT_EDITOR_SYNC_PACKET.txt"
    manifest_path = args.output_dir / "WOW_V17_GPT_EDITOR_SYNC_MANIFEST.json"
    packet_path.write_bytes(packet)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
