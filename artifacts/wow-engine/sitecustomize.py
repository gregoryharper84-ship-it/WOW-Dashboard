"""Temporary one-shot NBA/WNBA specialist training hook.

Python imports sitecustomize automatically when the repository root is present
on PYTHONPATH. Production enables this hook only by setting both:

  PYTHONPATH=.
  WOW_BASKETBALL_TRAIN_ON_START=NBA|WNBA

With the flag absent/invalid this module is a no-op. Before fitting, every
settled training row is checked for complete source provenance. The trainer
never promotes artifacts; successful fits persist SHADOW evidence only and
preserve can_execute=false. This hook is intended to be removed after the
governed training replay completes.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


_TABLES = {
    "NBA": "wow_nba_training_games",
    "WNBA": "wow_wnba_training_games",
}


def _provenance_preflight(client: Any, sport: str) -> dict[str, Any]:
    table = _TABLES[sport]
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        result = (
            client.table(table)
            .select("game_id,source_provider,source_endpoint,source_retrieved_at,source_payload_sha256")
            .eq("settled", True)
            .order("game_id")
            .range(offset, offset + 999)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    if not rows:
        raise RuntimeError(f"{sport}_TRAINING_CORPUS_EMPTY")

    providers: set[str] = set()
    bad: list[str] = []
    for row in rows:
        provider = str(row.get("source_provider") or "").strip()
        endpoint = str(row.get("source_endpoint") or "").strip()
        retrieved_at = str(row.get("source_retrieved_at") or "").strip()
        payload_hash = str(row.get("source_payload_sha256") or "").strip().lower()
        if provider:
            providers.add(provider)
        if (
            not provider
            or not endpoint
            or not retrieved_at
            or len(payload_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in payload_hash)
        ):
            bad.append(str(row.get("game_id") or "UNKNOWN"))
            if len(bad) >= 10:
                break

    if bad:
        raise RuntimeError(
            f"{sport}_TRAINING_PROVENANCE_INCOMPLETE sample_game_ids={','.join(bad)}"
        )

    return {
        "settled_rows_checked": len(rows),
        "source_providers": sorted(providers),
        "provenance_complete": True,
    }


def _run_once() -> None:
    sport = str(os.getenv("WOW_BASKETBALL_TRAIN_ON_START") or "").upper().strip()
    if sport not in {"NBA", "WNBA"}:
        return

    try:
        from basketball_specialist_pipeline import _client, fit_and_persist

        client = _client()
        provenance = _provenance_preflight(client, sport)
        result = fit_and_persist(sport, client=client)
        result["startup_hook"] = "WOW_BASKETBALL_ONE_SHOT_TRAIN_V1"
        result["provenance_preflight"] = provenance
        result["can_execute"] = False
        print(
            "WOW_BASKETBALL_TRAIN_RESULT " + json.dumps(result, sort_keys=True),
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(
            "WOW_BASKETBALL_TRAIN_FAILED "
            + json.dumps(
                {
                    "sport": sport,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "probability_publishable": False,
                    "can_execute": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )


_run_once()
