"""Read-only MLB PLAYER_HITS official boxscore research label extractor.

Input: already-retained, caller-supplied `wow_mlb_forward_boxscore_snapshots`
metadata and `raw_body`; no network, DB, files, scorer, model fit or writes.
A source URL and self-consistent hash are integrity checks, NOT independent
proof that source bytes came from MLB or have licensed reuse rights.

The official MLBAM response has restrictive use/copyright text. This adapter
DOES NOT authorize training, redistribution or serving. Every emitted label is
research-only; historical source capture does not prove pregame availability.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping

SCHEMA = "WOW_MLB_HITS_OFFICIAL_OUTCOME_RESEARCH_V1"
SOURCE = "CLAIMED_MLB_STATS_API_UNAUTHENTICATED_RESEARCH_ONLY"

MAX_JSON_DEPTH = 64


def _json_depth_bounded(raw: str) -> bool:
    """Reject excessive JSON structure before invoking the recursive decoder."""
    depth = 0
    in_string = False
    escaped = False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return False
        elif char in "}]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string



class MLBHitsResearchHold(ValueError):
    """Canonical bounded error; never include untrusted source body."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _unique_json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous duplicate JSON members instead of silently using last."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MLBHitsResearchHold("MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(_value: str) -> None:
    """Non-standard NaN/Infinity must not become trusted research labels."""
    raise MLBHitsResearchHold("MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise MLBHitsResearchHold(code)


def captured_time(value: Any) -> datetime:
    require(isinstance(value, str), "MLB_HITS_RESEARCH_TIMESTAMP_INVALID")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as ex:
        raise MLBHitsResearchHold("MLB_HITS_RESEARCH_TIMESTAMP_INVALID") from ex
    require(timestamp.utcoffset() is not None, "MLB_HITS_RESEARCH_TIMESTAMP_INVALID")
    return timestamp.astimezone(timezone.utc)


def official_date(value: Any) -> date:
    require(isinstance(value, str), "MLB_HITS_RESEARCH_DATE_INVALID")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as ex:
        raise MLBHitsResearchHold("MLB_HITS_RESEARCH_DATE_INVALID") from ex
    require(parsed.isoformat() == value, "MLB_HITS_RESEARCH_DATE_INVALID")
    return parsed


def integer(value: Any, code: str, *, positive: bool = False) -> int:
    require(type(value) is int and (value > 0 if positive else 0 <= value <= 100), code)
    return value


def extract_mlb_hits_labels(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one immutable stored response; produce bounded research labels.

    `official_date` is NOT an exact game start, and after-date capture alone
    does NOT prove final game state. Future certification needs separate final
    status and independently recorded event start / pregame feature sources.
    """
    require(isinstance(snapshot, Mapping), "MLB_HITS_RESEARCH_SNAPSHOT_INVALID")
    require(snapshot.get("research_only") is True
            and snapshot.get("can_execute") is False
            and type(snapshot.get("http_status")) is int
            and snapshot["http_status"] == 200,
            "MLB_HITS_RESEARCH_SOURCE_NOT_ELIGIBLE")
    game_pk = integer(snapshot.get("game_pk"), "MLB_HITS_RESEARCH_GAME_ID_INVALID", positive=True)
    require(snapshot.get("source_url") == f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore",
            "MLB_HITS_RESEARCH_SOURCE_IDENTITY_MISMATCH")
    game_date = official_date(snapshot.get("official_date"))
    fetched = captured_time(snapshot.get("captured_at"))
    require(fetched.date() > game_date, "MLB_HITS_RESEARCH_CHRONOLOGY_UNVERIFIED")
    raw = snapshot.get("raw_body")
    require(isinstance(raw, str) and bool(raw) and len(raw) <= 256_000,
            "MLB_HITS_RESEARCH_SNAPSHOT_INVALID")
    try:
        raw_bytes = raw.encode("utf-8")
    except UnicodeEncodeError as ex:
        raise MLBHitsResearchHold("MLB_HITS_RESEARCH_SNAPSHOT_INVALID") from ex
    require(len(raw_bytes) <= 256_000, "MLB_HITS_RESEARCH_SNAPSHOT_INVALID")
    digest = sha256(raw_bytes).hexdigest()
    require(isinstance(snapshot.get("raw_sha256"), str)
            and re.fullmatch(r"[a-f0-9]{64}", snapshot["raw_sha256"]) is not None
            and digest == snapshot["raw_sha256"],
            "MLB_HITS_RESEARCH_SOURCE_HASH_MISMATCH")
    # A bounded lexical nesting scan runs BEFORE the C JSON decoder. Catching
    # RecursionError alone is not a reliable process-level depth budget when
    # runtime recursion limits change. Brackets inside escaped JSON strings
    # are not structural; json.loads remains the syntax authority.
    require(_json_depth_bounded(raw), "MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID")
    try:
        body = json.loads(
            raw,
            object_pairs_hook=_unique_json_object_pairs,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (ValueError, TypeError, RecursionError) as ex:
        raise MLBHitsResearchHold("MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID") from ex
    require(isinstance(body, dict) and isinstance(body.get("teams"), dict),
            "MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for side in ("away", "home"):
        team = body["teams"].get(side)
        require(isinstance(team, dict) and isinstance(team.get("players"), dict),
                "MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID")
        for key, entry in team["players"].items():
            require(isinstance(entry, dict), "MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID")
            person = entry.get("person")
            require(isinstance(person, dict), "MLB_HITS_RESEARCH_PLAYER_ID_INVALID")
            player_id = integer(person.get("id"), "MLB_HITS_RESEARCH_PLAYER_ID_INVALID", positive=True)
            require(key == f"ID{player_id}", "MLB_HITS_RESEARCH_PLAYER_ID_INVALID")
            require(player_id not in seen, "MLB_HITS_RESEARCH_DUPLICATE_PLAYER")
            seen.add(player_id)
            stats = entry.get("stats", {})
            require(isinstance(stats, dict), "MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID")
            batting = stats.get("batting")
            if batting is None or batting == {}:
                continue  # players who did not log batting are not 0-hit observations
            require(isinstance(batting, dict), "MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID")
            hits = integer(batting.get("hits"), "MLB_HITS_RESEARCH_STAT_INVALID")
            pa = integer(batting.get("plateAppearances"), "MLB_HITS_RESEARCH_STAT_INVALID")
            require(hits <= pa, "MLB_HITS_RESEARCH_STAT_CONTRADICTION")
            rows.append({
                "official_game_pk_claimed": game_pk,
                "official_player_id_claimed": player_id,
                "team_side": side, "hits": hits, "plate_appearances": pa,
                "candidate_hit_at_least_one": hits >= 1,
                "official_date_claimed": game_date.isoformat(),
                "captured_at": fetched.isoformat(),
                "raw_source_sha256": digest,
                "source_status": SOURCE,
                "source_authenticated": False,
                "candidate_label_status": "UNVERIFIED_FINAL_RESEARCH_ONLY",
                "pregame_feature_snapshot_id": None,
                "market_line_verified": False,
                "game_final_independently_verified": False,
                "source_rights_verified": False,
                "historical_training_authorized": False,
                "probability_publishable": False,
                "can_execute": False,
            })
    require(bool(rows), "MLB_HITS_RESEARCH_NO_BATTERS")
    return {
        "schema": SCHEMA, "sport": "MLB", "stat_type": "PLAYER_HITS",
        "official_game_pk_claimed": game_pk, "source_payload_sha256": digest,
        "player_count": len(seen), "hit_pa_label_count": len(rows), "rows": rows,
        "event_start_time_verified": False,
        "game_final_independently_verified": False,
        "source_authenticated": False,
        "pregame_features_verified": False,
        "source_rights_verified": False,
        "historical_training_authorized": False,
        "probability_publishable": False,
        "can_execute": False,
    }

