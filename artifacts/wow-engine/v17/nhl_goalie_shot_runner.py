"""GitHub Actions-only, no-secret replay driver for NHL historical challenger.

Cached completed boxscore parses reduce repeated public NHL requests; every
cached row is validated against its schedule identity. Never writes to a DB.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from nhl_candidate_pipeline import NHLCandidateError, fetch_historical_games
from v17.nhl_goalie_shot_challenger import Box, fetch_box, replay

YEARS = (2021, 2022, 2023, 2024, 2025)


def run(*, output: Path, cache_dir: Path) -> dict:
    games = fetch_historical_games(YEARS)
    if len({game.game_id for game in games}) != len(games):
        raise NHLCandidateError("NHL_GAME_ID_DUPLICATE", "history")
    cache_dir.mkdir(parents=True, exist_ok=True)
    boxes: dict[str, Box] = {}
    hits = 0
    for index, game in enumerate(games, 1):
        path = cache_dir / (game.game_id + ".json")
        cached = None
        if path.exists():
            try:
                payload = json.loads(path.read_text())
                cached = Box(**payload)
                if (cached.event_id, cached.season, cached.home, cached.away) != (
                        game.game_id, game.season_id, game.home_team, game.away_team):
                    raise NHLCandidateError("NHL_CACHE_IDENTITY_CONFLICT", game.game_id)
                if len(cached.source_hash) != 64:
                    raise NHLCandidateError("NHL_CACHE_HASH_INVALID", game.game_id)
                hits += 1
            except (TypeError, ValueError, KeyError, NHLCandidateError) as exc:
                # Never silently use untrusted or malformed cached rows.
                raise NHLCandidateError("NHL_CACHE_INVALID", game.game_id) from exc
        boxes[game.game_id] = cached or fetch_box(game)
        if cached is None:
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(asdict(boxes[game.game_id]), sort_keys=True))
            os.replace(temp, path)
        if index % 250 == 0:
            print(f"NHL research acquisition {index}/{len(games)}; cache_hits={hits}", flush=True)
    report = replay(games, boxes)
    report.update({"years": list(YEARS), "cache_hits": hits,
                   "official_games": len(games), "boxscores_acquired": len(boxes)})
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    output = Path("nhl_goalie_shot_report.json")
    cache_dir = Path(".cache/nhl-challenger-boxscores-v1")
    try:
        result = run(output=output, cache_dir=cache_dir)
        print(json.dumps(result, indent=2))
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write("## NHL research-only side-by-side replay\n\n")
                stream.write(f"Events: {result['common_rows']}; test: {result['test_n']}\n\n")
                stream.write(f"V1 Brier: {result['v1']['brier']:.6f}; V2 Brier: {result['v2']['brier']:.6f}\n\n")
                stream.write(f"Paired ΔBrier 95% CI: {result['delta_brier_bootstrap_95_ci']}\n\n")
                stream.write("No automatic promotion, serving changes, secrets or database writes.\n")
        return 0
    except Exception as exc:
        code = getattr(exc, "code", "NHL_RESEARCH_REPLAY_FAILED")
        output.write_text(json.dumps({
            "status": "BLOCKED_TYPED", "code": code,
            "exception_type": type(exc).__name__,
            "can_execute": False, "probability_publishable": False,
        }, indent=2) + "\n")
        print("NHL_REPLAY_BLOCKED:" + code + ":" + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
