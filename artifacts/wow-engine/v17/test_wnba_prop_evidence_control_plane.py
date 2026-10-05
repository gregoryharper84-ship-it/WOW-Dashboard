from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import wnba_prop_evidence_control_plane as subject

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
EVENT = datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, mode):
        self.db = db
        self.mode = mode
        self.filters = {}
        self.pending = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, on_conflict=None):
        self.pending = dict(row)
        self.db.upserts.append((self.pending, on_conflict))
        return self

    def execute(self):
        if self.pending is not None:
            return _Result([self.pending])
        identity = (
            self.filters.get("event_id"),
            self.filters.get("sport"),
            self.filters.get("player"),
            self.filters.get("stat_type"),
        )
        return _Result([{"source_snapshot_id": "existing"}] if identity in self.db.existing else [])


class _DB:
    def __init__(self, existing=None):
        self.existing = set(existing or [])
        self.upserts = []

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _Query(self, name)


def _players():
    return [
        {
            "event_id": "WNBA:game-1",
            "official_game_id": "game-1",
            "event_start_time": EVENT.isoformat(),
            "player": "Alpha Guard",
            "player_id": "1001",
            "team": "AAA",
            "opponent": "BBB",
        },
        {
            "event_id": "WNBA:game-1",
            "official_game_id": "game-1",
            "event_start_time": EVENT.isoformat(),
            "player": "Beta Forward",
            "player_id": "2001",
            "team": "BBB",
            "opponent": "AAA",
        },
    ]


def _raw():
    return {
        "captured_at": NOW.isoformat(),
        "game_log": [float(i) for i in range(10)],
        "box_score_log": [{"game": i} for i in range(10)],
        "role_status": {"status": "ACTIVE", "team_tricode": "AAA", "opponent_tricode": "BBB"},
        "role_timestamp": NOW.isoformat(),
        "opportunity_ledger": {"status": "READY", "availability_gate": "PASS"},
        "source_timestamps": {"WNBA_OFFICIAL": NOW.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "WNBA_OFFICIAL_STATS_CDN_INJURY_V1",
    }


def _install_common(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(subject.acquisition, "_request_schedule", lambda **_kwargs: {"leagueSchedule": {"gameDates": []}})
    monkeypatch.setattr(subject.acquisition, "_schedule_players", lambda *_args, **_kwargs: _players())
    monkeypatch.setattr(subject.acquisition, "auto_hydrate_prop_evidence", lambda **_kwargs: _raw())
    monkeypatch.setattr(subject.acquisition, "_validate_evidence", lambda row, stat: row.evidence.model_dump())
    monkeypatch.setattr(
        subject.acquisition,
        "_snapshot_payload",
        lambda row, normalized: (
            f"snapshot-{row.player}-{row.stat_type}",
            "fingerprint",
            {
                "source_snapshot_id": f"snapshot-{row.player}-{row.stat_type}",
                "event_id": row.event_id,
                "event_start_time": row.event_start_time,
                "sport": row.sport,
                "player": row.player,
                "stat_type": row.stat_type,
                "line": row.line,
            },
        ),
    )


def test_official_schedule_api_recovery_prevents_schedule_transport_data_unobtainable(
    monkeypatch: pytest.MonkeyPatch,
):
    real_request_schedule = subject.acquisition._request_schedule
    _install_common(monkeypatch)
    monkeypatch.setattr(subject.acquisition, "_request_schedule", real_request_schedule)

    schedule = {
        "leagueSchedule": {
            "gameDates": [
                {
                    "games": [
                        {
                            "gameId": "1042600202",
                            "gameDateTimeUTC": "2026-10-07T23:30:00Z",
                            "gameStatus": 1,
                            "homeTeam": {
                                "teamId": 1611661330,
                                "teamCity": "Atlanta",
                                "teamName": "Dream",
                                "teamTricode": "ATL",
                            },
                            "awayTeam": {
                                "teamId": 1611661313,
                                "teamCity": "New York",
                                "teamName": "Liberty",
                                "teamTricode": "NYL",
                            },
                        }
                    ]
                }
            ]
        }
    }
    calls: list[tuple[str, dict]] = []

    class Response:
        status_code = 200
        content = b""

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            if isinstance(self.payload, BaseException):
                raise self.payload
            return self.payload

    def fetcher(url, **kwargs):
        calls.append((str(url), kwargs))
        if str(url) == subject.acquisition.wnba.WNBA_SCHEDULE_URL:
            return Response(ValueError("legacy CDN body was not JSON"))
        if str(url) == subject.schedule_transport.SCHEDULE_API_URL:
            return Response(schedule)
        raise AssertionError(f"unexpected source: {url}")

    result = subject.acquire_wnba_forward_evidence_batch(
        subject.WNBAForwardEvidenceRequest(
            requested_date="2026-09-28",
            candidate_offset=0,
            max_candidates=1,
        ),
        db=_DB(),
        now=NOW,
        http_get=fetcher,
    )

    assert result["status"] == "COMPLETED"
    assert result["persisted"] == 1
    assert result["blockers"] == []
    assert result["source_diagnostics"] == []
    assert result["can_execute"] is False
    assert sum(
        1 for url, _kwargs in calls
        if url == subject.acquisition.wnba.WNBA_SCHEDULE_URL
    ) == subject.acquisition.wnba.HTTP_ATTEMPTS * 2
    api_calls = [
        kwargs for url, kwargs in calls
        if url == subject.schedule_transport.SCHEDULE_API_URL
    ]
    assert len(api_calls) == 1
    assert api_calls[0]["params"]["regionId"] == "1"
    assert all(
        url != subject.schedule_transport.SCHEDULE_PAGE_URL
        for url, _kwargs in calls
    )


def test_rotating_window_is_bounded_and_reports_next_offset(monkeypatch: pytest.MonkeyPatch):
    _install_common(monkeypatch)
    req = subject.WNBAForwardEvidenceRequest(
        requested_date="2026-09-28",
        candidate_offset=2,
        max_candidates=3,
    )
    db = _DB()
    result = subject.acquire_wnba_forward_evidence_batch(
        req,
        db=db,
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )
    assert result["total_candidates"] == 8
    assert result["window_candidate_n"] == 3
    assert result["attempted"] == 3
    assert result["persisted"] == 3
    assert result["next_offset"] == 5
    assert result["source_diagnostics"] == []
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False


def test_existing_exact_player_event_stat_is_skipped_without_hydration(monkeypatch: pytest.MonkeyPatch):
    _install_common(monkeypatch)
    calls = []
    monkeypatch.setattr(
        subject.acquisition,
        "auto_hydrate_prop_evidence",
        lambda **kwargs: calls.append(kwargs) or _raw(),
    )
    existing = {
        ("WNBA:game-1", "WNBA", "Alpha Guard", "POINTS"),
        ("WNBA:game-1", "WNBA", "Alpha Guard", "REBOUNDS"),
    }
    db = _DB(existing=existing)
    req = subject.WNBAForwardEvidenceRequest(
        requested_date="2026-09-28",
        candidate_offset=0,
        max_candidates=4,
    )
    result = subject.acquire_wnba_forward_evidence_batch(req, db=db, now=NOW, http_get=lambda *_a, **_k: object())
    assert result["attempted"] == 4
    assert result["already_captured"] == 2
    assert result["persisted"] == 2
    assert len(calls) == 2


def test_true_hydration_blocker_stays_row_scoped(monkeypatch: pytest.MonkeyPatch):
    _install_common(monkeypatch)

    def hydrate(**kwargs):
        if kwargs["stat_type"] == "ASSISTS":
            raise subject.PropAutoHydrationError("WNBA_PLAYER_AVAILABILITY_NOT_CLEAR", "held")
        return _raw()

    monkeypatch.setattr(subject.acquisition, "auto_hydrate_prop_evidence", hydrate)
    db = _DB()
    req = subject.WNBAForwardEvidenceRequest(
        requested_date="2026-09-28",
        candidate_offset=0,
        max_candidates=4,
    )
    result = subject.acquire_wnba_forward_evidence_batch(req, db=db, now=NOW, http_get=lambda *_a, **_k: object())
    assert result["attempted"] == 4
    assert result["persisted"] == 3
    assert result["held"] == 1
    assert result["status"] == "COMPLETED_WITH_ROW_BLOCKERS"
    assert any("WNBA_PLAYER_AVAILABILITY_NOT_CLEAR" in blocker for blocker in result["blockers"])
    assert result["source_diagnostics"] == []
    assert result["can_execute"] is False


def test_official_source_failure_preserves_typed_blocker_and_safe_source_receipt(monkeypatch: pytest.MonkeyPatch):
    exc = subject.acquisition.wnba.WNBAPropHydrationError(
        "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "unavailable",
        detail={
            "url": "https://stats.wnba.com/stats/leaguegamelog?token=must-not-leak",
            "attempts": 2,
            "errors": ["RuntimeError:HTTP_403", "ValueError:invalid json body"],
        },
    )
    monkeypatch.setattr(subject.acquisition, "_request_schedule", lambda **_kwargs: (_ for _ in ()).throw(exc))
    result = subject.acquire_wnba_forward_evidence_batch(
        subject.WNBAForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=1),
        db=_DB(),
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )
    assert result["status"] == "DATA_UNOBTAINABLE"
    assert result["blockers"] == [
        "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "WNBA_STATS_SCOREBOARD_V3_UNAVAILABLE",
    ]
    assert result["source_diagnostics"][0] == {
        "code": "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "host": "stats.wnba.com",
        "path": "/stats/leaguegamelog",
        "attempts": 2,
        "error_kinds": ["RuntimeError", "ValueError"],
    }
    assert result["source_diagnostics"][1] == {
        "code": "WNBA_STATS_SCOREBOARD_V3_UNAVAILABLE",
        "provider": subject.STATS_SCOREBOARD_PROVIDER,
    }
    assert "must-not-leak" not in str(result)
    assert result["can_execute"] is False


def test_dual_official_schedule_failure_preserves_each_safe_source_boundary(monkeypatch: pytest.MonkeyPatch):
    exc = subject.acquisition.wnba.WNBAPropHydrationError(
        "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "both official schedule transports failed",
        detail={
            "primary_source": "WNBA_CDN_SCHEDULE_CURRENT",
            "fallback_source": "WNBA_OFFICIAL_SCHEDULE_WEB_SSR",
            "primary_errors": [
                "JSONDecodeError:Expecting value: line 1 column 1",
                "RuntimeError:HTTP_502",
            ],
            "fallback_errors": [
                "WNBAPropHydrationError:WNBA_OFFICIAL_SCHEDULE_WEB_PARSE_EMPTY",
                "RuntimeError:HTTP_403",
            ],
        },
    )
    monkeypatch.setattr(subject.acquisition, "_request_schedule", lambda **_kwargs: (_ for _ in ()).throw(exc))
    result = subject.acquire_wnba_forward_evidence_batch(
        subject.WNBAForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=1),
        db=_DB(),
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )
    assert result["status"] == "DATA_UNOBTAINABLE"
    assert result["blockers"] == [
        "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "WNBA_STATS_SCOREBOARD_V3_UNAVAILABLE",
    ]
    diagnostic = result["source_diagnostics"][0]
    assert diagnostic["code"] == "WNBA_OFFICIAL_SOURCE_UNAVAILABLE"
    assert diagnostic["sources"] == [
        {
            "provider": "WNBA_CDN_SCHEDULE_CURRENT",
            "host": "cdn.wnba.com",
            "path": "/static/json/staticData/scheduleLeagueV2.json",
            "attempts": 2,
            "error_kinds": ["JSONDecodeError", "RuntimeError"],
            "error_codes": ["HTTP_502"],
        },
        {
            "provider": "WNBA_OFFICIAL_SCHEDULE_WEB_SSR",
            "host": "www.wnba.com",
            "path": "/schedule",
            "attempts": 2,
            "error_kinds": ["WNBAPropHydrationError", "RuntimeError"],
            "error_codes": ["WNBA_OFFICIAL_SCHEDULE_WEB_PARSE_EMPTY", "HTTP_403"],
        },
    ]
    assert result["source_diagnostics"][1] == {
        "code": "WNBA_STATS_SCOREBOARD_V3_UNAVAILABLE",
        "provider": subject.STATS_SCOREBOARD_PROVIDER,
    }
    assert "Expecting value" not in str(result)
    assert "line 1 column 1" not in str(result)
    assert result["can_execute"] is False
