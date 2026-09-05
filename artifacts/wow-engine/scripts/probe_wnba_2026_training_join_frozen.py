#!/usr/bin/env python3
"""Run the existing WNBA role/event-time readiness probe on frozen WOW bytes."""
from __future__ import annotations

import sys

from scripts import probe_wnba_2026_training_join as legacy
from wnba_training_source import (
    EXPECTED_SHA256,
    load_player_boxscores,
    load_player_game_logs,
    load_schedule,
)


def _frozen_download(url: str, expected_sha256: str) -> bytes:
    if "player_game_logs_2026.csv" in url:
        return load_player_game_logs()
    if "player_boxscores_2026.csv" in url:
        return load_player_boxscores()
    if "wnba_schedule_2026.csv" in url:
        return load_schedule()
    raise RuntimeError(f"WNBA_FROZEN_SOURCE_UNKNOWN_REQUEST:{url}")


def main() -> int:
    legacy.PLAYER_LOG_SHA256 = EXPECTED_SHA256["player_game_logs_2026.csv"]
    legacy.PLAYER_BOX_SHA256 = EXPECTED_SHA256["player_boxscores_2026.csv"]
    legacy.SCHEDULE_SHA256 = EXPECTED_SHA256["wnba_schedule_2026.csv"]
    legacy._download = _frozen_download
    return legacy.main()


if __name__ == "__main__":
    sys.exit(main())
