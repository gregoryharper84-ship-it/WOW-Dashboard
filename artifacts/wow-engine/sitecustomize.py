"""Temporary one-shot NBA/WNBA specialist training hook.

Python imports sitecustomize automatically when the repository root is present
on PYTHONPATH. Production enables this hook only by setting both:

  PYTHONPATH=.
  WOW_BASKETBALL_TRAIN_ON_START=NBA|WNBA

With the flag absent/invalid this module is a no-op. The governed replay checks
source provenance, persists deterministic pregame feature rows and hashes, then
fits/calibrates league-specific SHADOW evidence. It never promotes artifacts or
enables execution. Remove this hook after the training replay completes.
"""
from __future__ import annotations

import json
import os
import sys


def _run_once() -> None:
    sport = str(os.getenv("WOW_BASKETBALL_TRAIN_ON_START") or "").upper().strip()
    if sport not in {"NBA", "WNBA"}:
        return

    try:
        from basketball_training_replay import run_training_replay

        result = run_training_replay(sport)
        result["startup_hook"] = "WOW_BASKETBALL_ONE_SHOT_TRAIN_V1"
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
