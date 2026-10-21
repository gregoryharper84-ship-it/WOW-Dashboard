"""Backlog-only V17 multi-source historical outcome collector.

Read-only, manual-invocation, bronze-stage collector. This module does not
score, train, write to Supabase, deploy, or schedule collection.
"""
from __future__ import annotations

import argparse
import csv
import io
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from historical_data_backbone import (
    EvidenceDomain,
    SourceRightsState,
    load_source_manifest,
    payload_sha256,
)
from v17.model_source_entitlements import SOURCES

CAN_EXECUTE = False
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MANIFEST_PATH = Path(__file__).resolve().parents[1] / 'historical_source_manifest_v1.json'
WNBA_RELEASE = (
    'https://github.com/sportsdataverse/sportsdataverse-data/releases/download/'
    'wnba_stats_player_game_logs/player_game_logs_{season}.csv'
)
MLB_FEED = 'https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live'
Download = Callable[[str], bytes]


class CollectionBlocked(RuntimeError):
    def __init__(self, code: str, detail: str = '') -> None:
        self.code = code
        super().__init__(f'{code}: {detail}')


@dataclass(frozen=True)
class CollectedBatch:
    provider: str
    sport: str
    source_url: str
    retrieved_at: str
    raw_payload_hash: str
    license_id: str | None
    rows: tuple[dict, ...]
    skipped_nonplayed: int = 0
    collection_status: str = 'BRONZE_OUTCOMES_ONLY'
    probability_publishable: bool = False
    training_materialized: bool = False
    can_execute: bool = False

    def as_dict(self) -> dict:
        return {
            'provider': self.provider,
            'sport': self.sport,
            'source_url': self.source_url,
            'retrieved_at': self.retrieved_at,
            'raw_payload_hash': self.raw_payload_hash,
            'license_id': self.license_id,
            'collection_status': self.collection_status,
            'probability_publishable': self.probability_publishable,
            'training_materialized': self.training_materialized,
            'can_execute': self.can_execute,
            'row_count': len(self.rows),
            'skipped_nonplayed': self.skipped_nonplayed,
            'rows': list(self.rows),
        }


def _approved_source(sport: str, provider: str, manifest_path: Path = MANIFEST_PATH):
    matching = [s for s in load_source_manifest(manifest_path)
                if s.sport == sport and s.provider == provider]
    if len(matching) != 1:
        raise CollectionBlocked('HISTORICAL_SOURCE_UNREGISTERED', f'{sport}/{provider}')
    entry = matching[0]
    if entry.evidence_domain is not EvidenceDomain.SPORTING:
        raise CollectionBlocked('MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL', provider)
    if entry.rights_state is not SourceRightsState.V17_APPROVED:
        raise CollectionBlocked('HISTORICAL_SOURCE_NOT_APPROVED', f'{provider}:{entry.rights_state.value}')
    return entry


def _allowed_initial_url(url: str) -> bool:
    p = urlparse(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.query or p.fragment:
        return False
    if p.hostname == 'statsapi.mlb.com':
        return bool(re.fullmatch(r'/api/v1\.1/game/[0-9]{1,12}/feed/live', p.path))
    if p.hostname == 'github.com':
        return bool(re.fullmatch(
            r'/sportsdataverse/sportsdataverse-data/releases/download/'
            r'wnba_stats_player_game_logs/player_game_logs_20[0-9]{2}\.csv', p.path
        ) or re.fullmatch(
            r'/nflverse/nflverse-data/releases/download/'
            r'stats_player/stats_player_week_20[0-9]{2}\.csv', p.path
        ))
    if p.hostname == 'raw.githubusercontent.com':
        return p.path == '/nflverse/nfldata/master/data/games.csv'
    return False


def _redirect_allowed(original: str, target: str) -> bool:
    src, dst = urlparse(original), urlparse(target)
    if dst.scheme != 'https' or dst.username or dst.password or dst.port not in (None, 443):
        return False
    if src.hostname == 'github.com':
        return dst.hostname in {'objects.githubusercontent.com', 'release-assets.githubusercontent.com'}
    return False


class _RestrictedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _redirect_allowed(req.full_url, newurl):
            raise CollectionBlocked('COLLECTOR_REDIRECT_NOT_ALLOWED', newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_public_source(url: str) -> bytes:
    """Bounded HTTPS acquisition; never circumvents 403/429 or access controls."""
    if not _allowed_initial_url(url):
        raise CollectionBlocked('COLLECTOR_ORIGIN_NOT_ALLOWED', url)
    opener = build_opener(_RestrictedRedirects())
    try:
        with opener.open(Request(url, headers={'User-Agent': 'WOW-V17-historical-research/1.0'}), timeout=20) as res:
            body = res.read(MAX_DOWNLOAD_BYTES + 1)
    except HTTPError as exc:
        raise CollectionBlocked('COLLECTOR_HTTP_UNAVAILABLE', f'{exc.code}') from exc
    except URLError as exc:
        raise CollectionBlocked('COLLECTOR_NETWORK_UNAVAILABLE') from exc
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise CollectionBlocked('COLLECTOR_PAYLOAD_TOO_LARGE')
    return body


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_object(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CollectionBlocked('COLLECTOR_PAYLOAD_INVALID_JSON') from exc
    if not isinstance(value, dict):
        raise CollectionBlocked('COLLECTOR_PAYLOAD_INVALID_SCHEMA')
    return value


def _stat_int(value) -> int:
    if isinstance(value, bool):
        raise ValueError('boolean is not a stat')
    num = int(value)
    if num < 0 or float(value) != num:
        raise ValueError('stat must be a nonnegative integer')
    return num


def collect_mlb_game(game_pk: int, *, download: Download = download_public_source,
                     manifest_path: Path = MANIFEST_PATH) -> CollectedBatch:
    _approved_source('MLB', 'MLB_STATS_API', manifest_path)
    if type(game_pk) is not int or not (1 <= game_pk <= 999999999999):
        raise CollectionBlocked('COLLECTOR_GAME_ID_INVALID')
    url = MLB_FEED.format(game_pk=game_pk)
    if not _allowed_initial_url(url):
        raise CollectionBlocked('COLLECTOR_ORIGIN_NOT_ALLOWED')
    raw = download(url)
    obj = _json_object(raw)
    data = obj.get('gameData') or {}
    if str(data.get('game', {}).get('pk')) != str(game_pk):
        raise CollectionBlocked('COLLECTOR_EVENT_IDENTITY_MISMATCH')
    if (data.get('status') or {}).get('statusCode') not in {'F', 'O'}:
        raise CollectionBlocked('COLLECTOR_EVENT_NOT_FINAL')
    try:
        started = datetime.fromisoformat(data['datetime']['dateTime'].replace('Z', '+00:00'))
        assert started.tzinfo is not None
        box = obj['liveData']['boxscore']['teams']
        teams = data['teams']
        team_ids = {side: str(teams[side]['id']) for side in ('home', 'away')}
    except (KeyError, TypeError, ValueError, AssertionError) as exc:
        raise CollectionBlocked('COLLECTOR_EVENT_IDENTITY_INCOMPLETE') from exc
    retrieved = _now_utc()
    stats_map = {
        'batting': {'hits': 'HITS', 'runs': 'RUNS', 'rbi': 'RBI', 'homeRuns': 'HOME_RUNS',
                    'baseOnBalls': 'WALKS', 'strikeOuts': 'STRIKEOUTS', 'totalBases': 'TOTAL_BASES'},
        'pitching': {'strikeOuts': 'STRIKEOUTS', 'earnedRuns': 'EARNED_RUNS',
                     'baseOnBalls': 'WALKS_ALLOWED', 'hits': 'HITS_ALLOWED'},
    }
    rows = []
    for side, opponent in (('home', 'away'), ('away', 'home')):
        for player in (box.get(side, {}).get('players') or {}).values():
            pid = player.get('person', {}).get('id')
            if not isinstance(pid, int) or pid <= 0:
                raise CollectionBlocked('COLLECTOR_PLAYER_IDENTITY_INCOMPLETE')
            for family, mappings in stats_map.items():
                stat_values = (player.get('stats') or {}).get(family) or {}
                for raw_stat, stat_name in mappings.items():
                    if raw_stat not in stat_values or stat_values[raw_stat] is None:
                        continue
                    try:
                        value = _stat_int(stat_values[raw_stat])
                    except (ValueError, TypeError) as exc:
                        raise CollectionBlocked('COLLECTOR_STAT_INVALID', raw_stat) from exc
                    rows.append({
                        'source_record_id': f'{game_pk}:{pid}:{family}:{stat_name}',
                        'official_event_id': str(game_pk), 'player_id': str(pid),
                        'player_name': player.get('person', {}).get('fullName'),
                        'team_id': team_ids[side], 'opponent_id': team_ids[opponent],
                        'game_date': started.date().isoformat(),
                        'event_start_time': started.isoformat(),
                        'stat_type': f'{family.upper()}_{stat_name}', 'actual_value': value,
                        'evidence_domain': 'SPORTING', 'record_class': 'SETTLED_OUTCOME',
                        'training_materialized': False,
                    })
    ids = [row['source_record_id'] for row in rows]
    if len(ids) != len(set(ids)) or not rows:
        raise CollectionBlocked('COLLECTOR_DUPLICATE_OR_EMPTY_ROWS')
    return CollectedBatch('MLB_STATS_API', 'MLB', url, retrieved,
                          hashlib.sha256(raw).hexdigest(), None,
                          tuple(sorted(rows, key=lambda r: r['source_record_id'])))


def _column(columns: list[str], *aliases: str) -> str | None:
    by_fold = {column.casefold(): column for column in columns}
    for alias in aliases:
        if alias.casefold() in by_fold:
            return by_fold[alias.casefold()]
    return None


def _minutes(value: str) -> float:
    value = str(value or '').strip()
    if value.startswith('PT'):
        value = value[2:]
        minute = 0.0
        if 'M' in value:
            n, value = value.split('M', 1)
            minute = float(n)
        if value.endswith('S'):
            minute += float(value[:-1]) / 60
        return minute
    if ':' in value:
        m, s = value.split(':', 1)
        return float(m) + float(s) / 60
    return float(value)


def collect_wnba_season(season: int, *, download: Download = download_public_source,
                        manifest_path: Path = MANIFEST_PATH) -> CollectedBatch:
    _approved_source('WNBA', 'SPORTSDATAVERSE_WNBA_STATS', manifest_path)
    if type(season) is not int or not (2000 <= season <= 2099):
        raise CollectionBlocked('COLLECTOR_SEASON_INVALID')
    url = WNBA_RELEASE.format(season=season)
    if not _allowed_initial_url(url):
        raise CollectionBlocked('COLLECTOR_ORIGIN_NOT_ALLOWED')
    raw = download(url)
    retrieved = _now_utc()
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
        columns = list(reader.fieldnames or [])
        parsed = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise CollectionBlocked('COLLECTOR_PAYLOAD_INVALID_CSV') from exc
    if not parsed:
        raise CollectionBlocked('COLLECTOR_EMPTY_SOURCE')
    names = {
        'pid': _column(columns, 'player_id', 'person_id', 'personId'),
        'gid': _column(columns, 'game_id', 'gameId'),
        'date': _column(columns, 'game_date', 'gameDate'),
        'minutes': _column(columns, 'min', 'minutes'),
        'pts': _column(columns, 'pts', 'points'),
        'reb': _column(columns, 'reb', 'rebounds', 'rebounds_total'),
        'ast': _column(columns, 'ast', 'assists'),
        'three': _column(columns, '3PM', 'FG3M', 'fg3m', 'three_pm', 'threePointersMade'),
    }
    missing = [key for key, column in names.items() if column is None]
    if missing:
        raise CollectionBlocked('COLLECTOR_WNBA_REQUIRED_COLUMNS_MISSING', ','.join(missing))
    optional = {
        'name': _column(columns, 'player_name', 'playerName'),
        'team': _column(columns, 'team_abbreviation', 'teamAbbreviation'),
        'matchup': _column(columns, 'matchup'),
    }
    rows, identities, nonplayed = [], set(), 0
    for original in parsed:
        player = str(original[names['pid']] or '').strip().removesuffix('.0')
        game = str(original[names['gid']] or '').strip().removesuffix('.0')
        if not player or not game:
            raise CollectionBlocked('COLLECTOR_WNBA_IDENTITY_INCOMPLETE')
        ident = (game, player)
        if ident in identities:
            raise CollectionBlocked('COLLECTOR_DUPLICATE_SOURCE_ROW')
        identities.add(ident)
        raw_minutes = str(original[names['minutes']] or '').strip()
        if not raw_minutes:
            nonplayed += 1
            continue
        try:
            minutes = _minutes(raw_minutes)
            if not 0 <= minutes <= 60:
                raise ValueError('minutes out of range')
            if minutes == 0:
                nonplayed += 1
                continue
            stats = {k: _stat_int(original[names[k]]) for k in ('pts', 'reb', 'ast', 'three')}
            game_date = datetime.strptime(str(original[names['date']])[:10], '%Y-%m-%d').date().isoformat()
        except (ValueError, TypeError) as exc:
            raise CollectionBlocked('COLLECTOR_WNBA_PLAYED_ROW_INVALID', f'{game}:{player}') from exc
        rows.append({
            'source_record_id': f'{season}:{game}:{player}',
            'official_event_id': game, 'player_id': player,
            'player_name': original.get(optional['name']) if optional['name'] else None,
            'team_abbreviation': original.get(optional['team']) if optional['team'] else None,
            'matchup': original.get(optional['matchup']) if optional['matchup'] else None,
            'game_date': game_date,
            'stats': {'MIN': minutes, 'PTS': stats['pts'], 'REB': stats['reb'],
                      'AST': stats['ast'], 'FG3M': stats['three']},
            'source_payload_hash': payload_sha256(original),
            'role_evidence_status': 'UNRESOLVED',
            'evidence_domain': 'SPORTING', 'record_class': 'HISTORICAL_GAME_LOG',
            'training_materialized': False,
        })
    license_id = SOURCES['SPORTSDATAVERSE_WNBA_STATS'].license_id
    return CollectedBatch('SPORTSDATAVERSE_WNBA_STATS', 'WNBA', url, retrieved,
                          hashlib.sha256(raw).hexdigest(), license_id,
                          tuple(sorted(rows, key=lambda r: r['source_record_id'])),
                          skipped_nonplayed=nonplayed)


def main() -> None:
    p = argparse.ArgumentParser(description='Manual, read-only source collection (no DB writes)')
    p.add_argument('--provider', required=True, choices=('MLB_STATS_API', 'SPORTSDATAVERSE_WNBA_STATS'))
    p.add_argument('--game-pk', type=int)
    p.add_argument('--season', type=int)
    args = p.parse_args()
    if args.provider == 'MLB_STATS_API':
        if args.game_pk is None: p.error('--game-pk is required')
        batch = collect_mlb_game(args.game_pk)
    else:
        if args.season is None: p.error('--season is required')
        batch = collect_wnba_season(args.season)
    print(json.dumps(batch.as_dict(), sort_keys=True))


if __name__ == '__main__':
    main()
