"""Read-only identity/stat candidate reconciliation between NCAA and ESPN history.

The two public source releases have distinct player identifier contracts:
NCAA MFB uses contest_id / a display name; ESPN player_box carries a numeric
ESPN event and athlete id. This is a RESEARCH CROSSWALK only. Neither a name
match nor same upstream ESPN data proves independent official identity.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

CAN_EXECUTE = False
SCHEMA = "WOW_V17_NCAAF_NCAA_ESPN_PLAYER_CROSSWALK_RESEARCH_V1"
SOURCE_ID = "SPORTSDATAVERSE_ESPN_CFB_PLAYER_BOX"
LIMIT_BYTES = 150_000_000
LIMIT_ROWS = 500_000
APPROVED_HOSTS = frozenset(("github.com", "release-assets.githubusercontent.com",
                            "objects.githubusercontent.com"))
NCAA_COLUMNS = ("season", "contest_id", "espn_game_id", "team_id",
                "name", "category", "pass_yards", "completions",
                "pass_attempts", "yds_rush", "rush_attempts", "rec",
                "receiving_yards")
# IMPORTANT: GitHub release Parquet stores ESPN source keys in camelCase,
# including the literal slash-containing 'completions/passingAttempts'. The
# public loader exposes a separate snake_case normalized presentation. We
# read the raw Parquet file, not the loader output; never confuse the two.
ESPN_COLUMNS = ("season", "game_id", "team_id", "athlete_id",
                "athlete_name", "category", "passingYards",
                "completions/passingAttempts", "rushingYards",
                "rushingAttempts", "receptions", "receivingYards")
STAT_FIELDS = {
    "passing": ("PASSING_YARDS", "COMPLETIONS", "PASS_ATTEMPTS"),
    "rushing": ("RUSHING_YARDS", "RUSH_ATTEMPTS"),
    "receiving": ("RECEPTIONS", "RECEIVING_YARDS"),
}
INTEGER = re.compile(r"-?\d+(?:\.0+)?\Z")
ID_RE = re.compile(r"[0-9]+\Z")


class CrosswalkHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def espn_release_url(season: int) -> str:
    if type(season) is not int or not 2004 <= season <= 2030:
        raise CrosswalkHold("NCAAF_ESPN_SEASON_INVALID")
    return ("https://github.com/sportsdataverse/sportsdataverse-data/"
            "releases/download/espn_cfb_player_box/"
            f"player_box_{season}.parquet")


def _id(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip()
    if not ID_RE.fullmatch(raw):
        return None
    return str(int(raw)) if int(raw) else None


def _name(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _value(value: Any, *, signed: bool = False) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip()
    if not INTEGER.fullmatch(raw):
        return None
    out = int(float(raw))
    return out if out >= 0 or signed else None


def _passing_pair(value: Any) -> tuple[int | None, int | None]:
    parts = str(value or "").split("/")
    if len(parts) != 2:
        return None, None
    completions, attempts = _value(parts[0]), _value(parts[1])
    if completions is None or attempts is None or completions > attempts:
        return None, None
    return completions, attempts


def _stats(row: Mapping[str, Any], category: str, provider: str) -> dict[str, int]:
    if provider == "NCAA":
        mapping = {
            "passing": (("PASSING_YARDS", "pass_yards"),
                        ("COMPLETIONS", "completions"), ("PASS_ATTEMPTS", "pass_attempts")),
            "rushing": (("RUSHING_YARDS", "yds_rush"),
                        ("RUSH_ATTEMPTS", "rush_attempts")),
            "receiving": (("RECEPTIONS", "rec"),
                          ("RECEIVING_YARDS", "receiving_yards")),
        }
        out = {}
        for stat_type, key in mapping[category]:
            value = _value(row.get(key), signed=stat_type == "RUSHING_YARDS")
            if value is not None:
                out[stat_type] = value
        return out
    if category == "passing":
        c, a = _passing_pair(row.get("completions/passingAttempts"))
        fields = {"PASSING_YARDS": _value(row.get("passingYards")),
                  "COMPLETIONS": c, "PASS_ATTEMPTS": a}
    elif category == "rushing":
        fields = {"RUSHING_YARDS": _value(row.get("rushingYards"), signed=True),
                  "RUSH_ATTEMPTS": _value(row.get("rushingAttempts"))}
    else:
        fields = {"RECEPTIONS": _value(row.get("receptions")),
                  "RECEIVING_YARDS": _value(row.get("receivingYards"))}
    return {key: value for key, value in fields.items() if value is not None}


def _index(rows: Iterable[Mapping[str, Any]], *, season: int, provider: str
           ) -> tuple[dict[tuple[str, str, str], list[dict[str, Any]]], Counter, int]:
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    holds: Counter[str] = Counter()
    count = 0
    required = set(NCAA_COLUMNS if provider == "NCAA" else ESPN_COLUMNS)
    for row in rows:
        count += 1
        if count > LIMIT_ROWS:
            raise CrosswalkHold("NCAAF_CROSSWALK_ROW_LIMIT")
        if not isinstance(row, Mapping) or not required.issubset(row):
            raise CrosswalkHold(f"NCAAF_{provider}_SCHEMA_DRIFT")
        if row.get("season") != season:
            holds[f"{provider}_SEASON_MISMATCH"] += 1
            continue
        game_id = _id(row.get("espn_game_id" if provider == "NCAA" else "game_id"))
        athlete_id = _id(row.get("athlete_id")) if provider == "ESPN" else None
        name = _name(row.get("name" if provider == "NCAA" else "athlete_name"))
        category = str(row.get("category") or "").strip().lower()
        if not (game_id and name and category in STAT_FIELDS):
            holds[f"{provider}_IDENTITY_OR_CATEGORY_MISSING"] += 1
            continue
        if provider == "ESPN" and not athlete_id:
            holds["ESPN_ATHLETE_ID_MISSING"] += 1
            continue
        stats = _stats(row, category, provider)
        if not stats:
            holds[f"{provider}_NO_VALID_STATS"] += 1
            continue
        index[(game_id, category, name)].append({
            "game_id": game_id, "category": category, "athlete_id": athlete_id,
            "team_alias": _id(row.get("team_id")),
            "stats": stats,
        })
    return index, holds, count


def reconcile(ncaa_rows: Iterable[Mapping[str, Any]],
              espn_rows: Iterable[Mapping[str, Any]], *,
              season: int, ncaa_sha256: str, espn_sha256: str) -> dict[str, Any]:
    espn_release_url(season)
    if any(not re.fullmatch("[a-f0-9]{64}", h)
           for h in (ncaa_sha256, espn_sha256)):
        raise CrosswalkHold("NCAAF_CROSSWALK_SOURCE_HASH_INVALID")
    ncaa, ncaa_holds, n_ncaa = _index(ncaa_rows, season=season, provider="NCAA")
    espn, espn_holds, n_espn = _index(espn_rows, season=season, provider="ESPN")
    results: Counter[str] = Counter()
    metric_matches: Counter[str] = Counter()
    source_event_ids = {key[0] for key in ncaa}
    espn_event_ids = {key[0] for key in espn}
    candidate_player_keys: set[tuple[str, str]] = set()
    for key, left in ncaa.items():
        right = espn.get(key, ())
        if not right:
            results["NCAA_KEY_UNMATCHED"] += len(left)
            continue
        if len(left) != 1 or len(right) != 1:
            results["AMBIGUOUS_NAME_EVENT_CATEGORY"] += len(left)
            continue
        l, r = left[0], right[0]
        shared = set(l["stats"]) & set(r["stats"])
        if len(shared) < 2:
            results["INSUFFICIENT_SHARED_STATS"] += 1
            continue
        if any(l["stats"][stat] != r["stats"][stat] for stat in shared):
            results["STAT_VALUE_CONFLICT"] += 1
            continue
        if not any(l["stats"][stat] != 0 for stat in shared):
            results["WEAK_ZERO_ONLY_PARITY"] += 1
            continue
        if (r["game_id"], r["athlete_id"]) in candidate_player_keys:
            # Multiple legitimate stat categories per player are normal.
            # No re-use of a candidate athlete id in the same game is
            # authorized as a SECOND independent identity assertion.
            results["ADDITIONAL_CATEGORY_FOR_SAME_ATHLETE"] += 1
        candidate_player_keys.add((r["game_id"], r["athlete_id"]))
        results["CANDIDATE_EXACT_STATS"] += 1
        metric_matches.update(shared)
    unique_espn = len(espn)
    return {
        "schema": SCHEMA, "status": "NCAAF_NCAA_ESPN_SOURCE_CROSSWALK_RESEARCH_ONLY",
        "season": season,
        "ncaa_source_id": "SPORTSDATAVERSE_NCAA_MFB_PLAYER_STATS",
        "espn_source_id": SOURCE_ID,
        "ncaa_source_sha256": ncaa_sha256, "espn_source_sha256": espn_sha256,
        "ncaa_input_rows": n_ncaa, "espn_input_rows": n_espn,
        "ncaa_indexed_keys": len(ncaa), "espn_indexed_keys": unique_espn,
        "ncaa_source_event_ids": len(source_event_ids),
        "espn_source_event_ids": len(espn_event_ids),
        "shared_event_ids": len(source_event_ids & espn_event_ids),
        "candidate_stat_category_rows": results["CANDIDATE_EXACT_STATS"],
        "candidate_distinct_espn_game_player_ids": len(candidate_player_keys),
        "candidate_stat_parity_counts": dict(sorted(metric_matches.items())),
        "hold_reason_counts": dict(sorted((ncaa_holds + espn_holds + Counter({
            k: v for k, v in results.items() if k not in
            ("CANDIDATE_EXACT_STATS", "ADDITIONAL_CATEGORY_FOR_SAME_ATHLETE")
        })).items())),
        "additional_category_rows_for_same_player": results["ADDITIONAL_CATEGORY_FOR_SAME_ATHLETE"],
        "espn_id_is_verified_cfbd_player_id_in_this_run": False,
        "ncaa_team_id_crosswalk_verified": False,
        "source_vendor_independence_proven": False,
        "historical_pregame_feature_timestamp_verified": False,
        "pregame_line_verified": False,
        "model_training_authorized_by_crosswalk": False,
        "model_probability": None, "calibrated_lower_bound": None,
        "probability_publishable": False, "can_execute": False,
    }


def _download_espn_release(season: int, output_path: Path) -> str:
    import requests
    url = espn_release_url(season)
    sha = sha256()
    size = 0
    try:
        with requests.get(url, stream=True, allow_redirects=True,
                          timeout=(15, 120), headers={"User-Agent": "WOW-NCAAF-V17-Research/1.0"}) as res:
            if res.status_code != 200:
                raise CrosswalkHold(f"NCAAF_ESPN_HTTP_{res.status_code}")
            final = urlsplit(res.url)
            if final.scheme != "https" or final.hostname not in APPROVED_HOSTS:
                raise CrosswalkHold("NCAAF_ESPN_UNAPPROVED_REDIRECT")
            with output_path.open("wb") as dest:
                for chunk in res.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        size += len(chunk)
                        if size > LIMIT_BYTES:
                            raise CrosswalkHold("NCAAF_ESPN_RELEASE_TOO_LARGE")
                        sha.update(chunk)
                        dest.write(chunk)
    except requests.RequestException as exc:
        output_path.unlink(missing_ok=True)
        raise CrosswalkHold("NCAAF_ESPN_SOURCE_FETCH_FAILED") from exc
    if not size:
        raise CrosswalkHold("NCAAF_ESPN_ARCHIVE_EMPTY")
    return sha.hexdigest()


def audit_release(*, ncaa_parquet: str, season: int, output_dir: str) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise CrosswalkHold("NCAAF_CROSSWALK_PARQUET_READER_MISSING") from exc
    ncaa_file = Path(ncaa_parquet)
    if not ncaa_file.is_file() or ncaa_file.stat().st_size > LIMIT_BYTES:
        raise CrosswalkHold("NCAAF_CROSSWALK_NCAA_FILE_INVALID")
    ncaa_sha = sha256(ncaa_file.read_bytes()).hexdigest()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    espn_file = out / f"espn_cfb_player_box_{season}.parquet"
    try:
        espn_sha = _download_espn_release(season, espn_file)
        ncaa_table = pq.read_table(ncaa_file, columns=list(NCAA_COLUMNS))
        espn_table = pq.read_table(espn_file, columns=list(ESPN_COLUMNS))
        report = reconcile(ncaa_table.to_pylist(), espn_table.to_pylist(),
                           season=season, ncaa_sha256=ncaa_sha, espn_sha256=espn_sha)
        (out / "ncaaf_national_espn_crosswalk_manifest.json").write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        # Retain the checksum receipt, not another bulk copy of the source.
        espn_file.unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ncaa-parquet", required=True)
    parser.add_argument("--season", type=int, default=2024)
    parser.add_argument("--output-dir", default="ncaaf-crosswalk-research")
    args = parser.parse_args()
    report = audit_release(ncaa_parquet=args.ncaa_parquet, season=args.season,
                           output_dir=args.output_dir)
    print(json.dumps({
        "status": report["status"],
        "ncaa_input_rows": report["ncaa_input_rows"],
        "espn_input_rows": report["espn_input_rows"],
        "shared_event_ids": report["shared_event_ids"],
        "candidate_stat_category_rows": report["candidate_stat_category_rows"],
        "candidate_distinct_espn_game_player_ids": report["candidate_distinct_espn_game_player_ids"],
        "hold_reason_counts": report["hold_reason_counts"],
        "model_probability": None, "can_execute": False,
    }, sort_keys=True))
