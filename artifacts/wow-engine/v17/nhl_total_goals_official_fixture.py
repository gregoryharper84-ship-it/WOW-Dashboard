"""Verified first-party NHL full-season regime fixture admission (research only).

A fixture stores a minimal official game *projection*, not the raw NHL API
payload, original feed signature, or any archived pregame observation.
Its two mirror teams must be traced from successful official team-season
requests and must reconcile to the exact source game.
This loader builds chronological prior-game-only research inputs, NOT a
governed scoring, licensing, goalie or probability certification.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from nhl_candidate_pipeline import teams_for_start_year
from v17.nhl_total_goals_challenger import build_total_rows
from v17.nhl_total_goals_regime_mixture_research import RegimeResearchRow

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
PROVENANCE = "FIRST_PARTY_NHL_RETRIEVED_RETROSPECTIVE_FINAL_PROJECTION"
EXPECTED_GAMES = 1312
EXPECTED_TEAM_MIRRORS = 2624
APPROVED_SEASONS = frozenset(range(2021, 2026))

# Full-season canonical-game SHA256 matched independently against the WOW D1
# validation DB on 2026-10-26. The second commitment covers the NHL public
# final REG/OT/SO classifications (which do NOT exist in the D1 game table).
# This does not certify source license, historical pregame timing or the model.
PINNED_OFFICIAL_SOURCE_CHECKSUMS = {
    2021: ("df61708c88ccd73c768bd3c460f1281465664ebb724b69dc73e2fcdca469d7ba",
           "6b59e3d7a71c4dd96100c9169728d3f259cfc4db4d1817b1e4b344e51bc05c1d"),
    2022: ("d5dc6272742bb8d089f2933b1a1c7c109957d2e7843e049ced385e65d59b2d8a",
           "246fd855db10a11f3315e69f40052a81ca06b8f6556e493ddcced80b9eb2cbf9"),
    2023: ("b1bf59a4746cb6fa58cc3ace0a27502e75db851025b08003934eda9fb9bfbe25",
           "6b57bfe4a6190e302edf3b047273dd243f27a49e71f898873990509fe63cf02b"),
    2024: ("8b40660b2327dd9aaf2b1f0f3e1b1c96a117b53d23faf52f8d6e1dce69a53995",
           "ce3a8431a18070b1464becf550671585794a88153333865f7b97e816ade288a7"),
    2025: ("5390675a5829359217c5778f57d92ea7aa7cb54d48cee8a61ec6c5b21664472e",
           "3cded0f5a74b2e4c8fb1fc842f94d0700453205e41017f4e3d8928a86cbf05f2"),
}


class NHLProjectedSourceError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class OfficialProjection:
    season_start_year: int
    event_id: str
    start_utc: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    final_regime: str
    source_projection_sha256: str
    outcome_projection_sha256: str


def _sha(payload: Any) -> str:
    return sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()


def parse_projected_official_season(encoded: str, *, year: int) -> tuple[list[OfficialProjection], dict[str, Any]]:
    """Accept exactly one authentic two-mirror 82-game-per-team NHL season."""
    if type(year) is not int or year not in APPROVED_SEASONS:
        raise NHLProjectedSourceError("NHL_TOTALS_OFFICIAL_SEASON_INVALID", str(year))
    try:
        compressed = base64.b64decode(encoded.strip(), validate=True)
        if len(compressed) > 100_000:
            raise ValueError("compressed input too large")
        content = gzip.decompress(compressed)
        if len(content) > 2_000_000:
            raise ValueError("expanded payload too large")
        body = json.loads(content)
    except (ValueError, TypeError, OSError, EOFError, json.JSONDecodeError) as exc:
        raise NHLProjectedSourceError("NHL_TOTALS_OFFICIAL_ARCHIVE_INVALID", str(exc)) from exc
    season = year * 10000 + year + 1
    expected_url = (
        "https://api-web.nhle.com/v1/club-schedule-season/{team}/" + str(season)
    )
    if (body.get("season") != season or
        body.get("source_url_template") != expected_url or
        body.get("historical_reconstruction") is not True or
        body.get("archived_pregame_snapshot") is not False or
        body.get("source_game_projection_not_raw_payload") is not True or
        body.get("source_instances") != EXPECTED_TEAM_MIRRORS):
        raise NHLProjectedSourceError(
            "NHL_TOTALS_OFFICIAL_PROVENANCE_INVALID", str(season)
        )
    raw = body.get("records")
    if not isinstance(raw, list) or len(raw) != EXPECTED_GAMES:
        raise NHLProjectedSourceError(
            "NHL_TOTALS_OFFICIAL_COVERAGE_INCOMPLETE", str(season)
        )
    admitted = []
    seen = set()
    expected_teams = set(teams_for_start_year(year))
    played = {t: 0 for t in expected_teams}
    for source in raw:
        try:
            game_id = str(source["id"])
            home = source["homeTeam"]
            away = source["awayTeam"]
            h, a = home["score"], away["score"]
            ht, at = home["abbrev"], away["abbrev"]
            mirrors = source["sourceMirrorTeams"]
            period = source["gameOutcome"]["lastPeriodType"]
            clock = datetime.fromisoformat(str(source["startTimeUTC"]).replace("Z", "+00:00"))
            if clock.utcoffset() is None:
                raise ValueError("naive kickoff")
            clock = clock.astimezone(timezone.utc)
            if (len(game_id) != 10 or not game_id.isdecimal()
                or not game_id.startswith(str(year)+"02")
                or game_id in seen or source["season"] != season
                or source["gameType"] != 2 or source["gameState"] not in ("OFF", "FINAL")):
                raise ValueError("event identity or final state")
            if (not isinstance(ht, str) or not isinstance(at, str)
                or ht not in expected_teams or at not in expected_teams
                or ht == at or type(h) is not int or type(a) is not int
                or min(h,a) < 0 or h == a):
                raise ValueError("team/score identity")
            if (not isinstance(mirrors, list) or len(mirrors) != 2
                or len(set(mirrors)) != 2 or set(mirrors) != {ht, at}):
                raise ValueError("two genuine team source mirrors required")
            if period not in ("REG", "OT", "SO"):
                raise ValueError("unknown final period")
            if period in ("OT","SO") and (abs(h-a) != 1 or (h+a) % 2 != 1):
                raise ValueError("OT/SO awarded-goal count inconsistent")
            seen.add(game_id)
            played[ht] += 1
            played[at] += 1
            # Commitment to the exact normalized FIRST-PARTY PROJECTION,
            # not the original D1/Supabase raw payload SHA256.
            projected = {k:v for k,v in source.items() if k != "sourceMirrorTeams"}
            admitted.append(OfficialProjection(
                season_start_year=year, event_id=game_id,
                start_utc=clock.isoformat(),home_team=ht,away_team=at,
                home_goals=h,away_goals=a,final_regime=period,
                source_projection_sha256=_sha(projected),
                outcome_projection_sha256=_sha({
                    "id":game_id,"period":period,"home_goals":h,"away_goals":a,
                }),
            ))
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            raise NHLProjectedSourceError(
                "NHL_TOTALS_OFFICIAL_GAME_PROJECTION_INVALID", str(exc)
            ) from exc
    if len(played) != 32 or any(v != 82 for v in played.values()):
        raise NHLProjectedSourceError(
            "NHL_TOTALS_OFFICIAL_TEAM_COVERAGE_INVALID", str(played)
        )
    if len(seen) != EXPECTED_GAMES:
        raise NHLProjectedSourceError(
            "NHL_TOTALS_OFFICIAL_DUPLICATE_EVENT", str(season)
        )
    # Verify every event identity/time/team/score AND terminal-regime label,
    # not merely the five per-season game counts. Canonical source digest was
    # independently reproduced from frozen NHL public projection and stored
    # D1 final-game result identities across all 6,560 official games.
    canonical = sorted(raw, key=lambda game: int(game["id"]))
    identity = "\\n".join(
        "|".join((
            str(g["id"]), str(g["startTimeUTC"]),
            str(g["homeTeam"]["abbrev"]), str(g["awayTeam"]["abbrev"]),
            str(g["homeTeam"]["score"]), str(g["awayTeam"]["score"]),
        )) for g in canonical
    )
    endings = "\\n".join(
        str(g["id"]) + "|" + str(g["gameOutcome"]["lastPeriodType"])
        for g in canonical
    )
    source_sha = sha256(identity.encode()).hexdigest()
    regime_sha = sha256(endings.encode()).hexdigest()
    if (source_sha, regime_sha) != PINNED_OFFICIAL_SOURCE_CHECKSUMS[year]:
        raise NHLProjectedSourceError(
            "NHL_TOTALS_OFFICIAL_SOURCE_FINGERPRINT_MISMATCH",
            str(season),
        )
    admitted.sort(key=lambda g: (g.start_utc, g.event_id))
    period_counts = {r:sum(1 for x in admitted if x.final_regime==r) for r in ("REG","OT","SO")}
    return admitted, {
        "status": "NHL_TOTALS_OFFICIAL_HISTORICAL_PROJECTION_VERIFIED",
        "season":season,"games":len(admitted),
        "team_mirror_instances":EXPECTED_TEAM_MIRRORS,
        "games_per_team":82,
        "ending_regime_counts":period_counts,
        "source_provenance":PROVENANCE,
        "source_projection_not_d1_raw_sha":True,
        "immutable_pregame_snapshots":False,
        "probability_publishable":False,"can_execute":False,
    }


def build_real_reconstructed_regime_rows(
    seasons: Sequence[tuple[int, Sequence[OfficialProjection]]],
) -> tuple[list[RegimeResearchRow], dict[str, Any]]:
    """Recreate the prior-game features and exact official regime targets.

    This consumes completely admitted full seasons only. A partial source
    cannot silently change the denominator; fit/calibration are separate.
    """
    if not seasons or len({year for year,_ in seasons})!=len(seasons):
        raise NHLProjectedSourceError(
            "NHL_TOTALS_OFFICIAL_SEASON_SET_INVALID", "distinct seasons required"
        )
    source_games = []
    outcomes = {}
    for year, games in seasons:
        if type(year) is not int or year not in APPROVED_SEASONS or len(games) != EXPECTED_GAMES:
            raise NHLProjectedSourceError("NHL_TOTALS_OFFICIAL_COVERAGE_INCOMPLETE", str(year))
        for g in games:
            if not isinstance(g, OfficialProjection) or g.season_start_year != year:
                raise NHLProjectedSourceError(
                    "NHL_TOTALS_OFFICIAL_ROW_UNVERIFIED", str(year)
                )
            if g.event_id in outcomes:
                raise NHLProjectedSourceError(
                    "NHL_TOTALS_OFFICIAL_DUPLICATE_EVENT", g.event_id
                )
            outcomes[g.event_id] = g
            source_games.append({
                "sport":"NHL","source_provider":"NHL_PUBLIC_WEB_API",
                "historical_reconstruction":True,"can_execute":False,
                "season":str(year*10000+year+1),
                "official_event_id":g.event_id,
                "event_start_time":g.start_utc,
                "home_team":g.home_team,"away_team":g.away_team,
                "home_score":g.home_goals,"away_score":g.away_goals,
                "source_payload_sha256":g.source_projection_sha256,
                "game_type":2,
            })
    source_rows, source_audit = build_total_rows(source_games)
    result=[]
    for row in source_rows:
        label=outcomes[row.event_id]
        if (label.home_goals+label.away_goals!=row.total_goals
            or label.source_projection_sha256!=row.source_hash
            or label.start_utc!=row.start_utc):
            raise NHLProjectedSourceError(
                "NHL_TOTALS_OFFICIAL_MODEL_JOIN_MISMATCH",row.event_id
            )
        result.append(RegimeResearchRow(
            event_id=row.event_id,season_start_year=row.season_start_year,
            start_utc=row.start_utc,feature_as_of=row.reconstructed_as_of,
            features=tuple(row.features),total_goals=row.total_goals,
            terminal_regime=label.final_regime,
            official_source_sha256=label.source_projection_sha256,
            outcome_regime_sha256=label.outcome_projection_sha256,
            historical_reconstruction=True,
            archived_pregame_snapshot=False,
        ))
    return result, {
        "status":"NHL_TOTALS_REAL_SOURCE_RESEARCH_FEATURES_READY",
        "total_official_source_games":len(source_games),
        "eligible_prior_game_features":len(result),
        "feature_build_audit":source_audit,
        "source_projection_not_d1_raw_sha":True,
        "immutable_pregame_snapshots":False,
        "governed_model_fitted":False,
        "calibrated_lower_bound":None,
        "probability_publishable":False,"can_execute":False,
    }


def load_checked_in_season(year: int, *, fixture_directory: Path) -> tuple[list[OfficialProjection], dict[str, Any]]:
    """Require explicitly checked-in, locally available archival fixture."""
    if year not in APPROVED_SEASONS:
        raise NHLProjectedSourceError("NHL_TOTALS_OFFICIAL_SEASON_INVALID", str(year))
    suffix = f"{year}{year+1}"
    location=fixture_directory / f"nhl_official_regimes_{suffix}.json.gz.b64"
    if not location.is_file():
        raise NHLProjectedSourceError(
            "NHL_TOTALS_OFFICIAL_FIXTURE_MISSING",str(location.name)
        )
    return parse_projected_official_season(location.read_text(),year=year)
