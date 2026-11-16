"""Settled basketball rows must carry the validated tz-aware scheduled start (tip-off) in UTC."""
from datetime import datetime, timezone

import basketball_event_hydration_runtime as hydration
from test_v17_basketball_hydration_fallback import _espn_event, _sportsdataverse_row

RETRIEVED = "2026-09-22T19:00:00+00:00"


def _utc(text):
    dt = datetime.fromisoformat(text)
    assert dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0
    return dt


def test_espn_row_records_scheduled_start_utc():
    row = hydration.normalize_espn_game(_espn_event(), "WNBA", RETRIEVED)
    assert _utc(row["scheduled_start_utc"]) == datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)


def test_sportsdataverse_row_converts_offset_tipoff_to_utc():
    row = hydration.normalize_sportsdataverse_game(_sportsdataverse_row(), "WNBA", RETRIEVED)
    assert _utc(row["scheduled_start_utc"]) == datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)  # 19:00-04:00


def test_balldontlie_row_records_start_when_provided_and_null_otherwise():
    raw = {
        "id": 9, "status": "Final", "home_team_score": 100, "visitor_team_score": 90,
        "home_team": {"id": 1}, "visitor_team": {"id": 2}, "date": "2026-09-20",
        "datetime": "2026-09-20T23:00:00Z", "_wow_source_observed_at": "2026-09-22T18:00:00+00:00",
    }
    row = hydration.normalize_game(raw, "NBA", RETRIEVED)
    assert _utc(row["scheduled_start_utc"]) == datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)
    del raw["datetime"]
    assert hydration.normalize_game(raw, "NBA", RETRIEVED)["scheduled_start_utc"] is None


def test_start_never_after_source_observation():
    for row in (
        hydration.normalize_espn_game(_espn_event(), "WNBA", RETRIEVED),
        hydration.normalize_sportsdataverse_game(_sportsdataverse_row(), "WNBA", RETRIEVED),
    ):
        assert _utc(row["scheduled_start_utc"]) < _utc(row["source_retrieved_at"])


# ---- review-finding regressions (CodeRabbit + engineering review on #2603) ----
import copy  # noqa: E402

import pytest  # noqa: E402


def test_balldontlie_start_inconsistent_with_game_date_is_omitted_not_stored():
    raw = {
        "id": 9, "status": "Final", "home_team_score": 100, "visitor_team_score": 90,
        "home_team": {"id": 1}, "visitor_team": {"id": 2}, "date": "2026-09-20",
        "datetime": "2026-09-27T23:00:00Z",  # a week off: cannot be this game's tip-off
        "_wow_source_observed_at": "2026-09-28T18:00:00+00:00",
    }
    row = hydration.normalize_game(raw, "NBA", "2026-09-28T19:00:00+00:00")
    assert row is not None and row["scheduled_start_utc"] is None


def test_sportsdataverse_naive_source_timestamp_is_not_recorded_as_utc_evidence():
    raw = _sportsdataverse_row()
    raw["game_date_time"] = "2026-09-20T19:00:00"  # no offset: admission may assume ET, storage must not
    row = hydration.normalize_sportsdataverse_game(raw, "WNBA", RETRIEVED)
    assert row is not None and row["scheduled_start_utc"] is None


class _Q:
    def __init__(self, store):
        self.store, self._filters, self._upsert, self._update, self._ignore = store, [], None, None, False

    def select(self, _f):
        return self

    def in_(self, key, values):
        self._filters.append(lambda r: r.get(key) in values)
        return self

    def eq(self, key, value):
        self._filters.append(lambda r: r.get(key) == value)
        return self

    def is_(self, key, _null):
        self._filters.append(lambda r: r.get(key) is None)
        return self

    def update(self, values):
        self._update = values
        return self

    def upsert(self, rows, on_conflict, ignore_duplicates=False):
        self._upsert, self._ignore = rows, ignore_duplicates
        self.store.upserts.append(copy.deepcopy(rows))
        return self

    def execute(self):
        from types import SimpleNamespace
        if self._upsert is not None:
            out = []
            for row in self._upsert:
                hit = next((r for r in self.store.rows if r["game_id"] == row["game_id"]), None)
                if hit is None:
                    self.store.rows.append(dict(row)); out.append(dict(row))
                elif not self._ignore:
                    hit.update(row)
            return SimpleNamespace(data=out)
        rows = [r for r in self.store.rows if all(f(r) for f in self._filters)]
        if self._update is not None:
            for r in rows:
                r.update(self._update)
        return SimpleNamespace(data=[dict(r) for r in rows])


class _Store:
    def __init__(self, rows=()):
        self.rows, self.upserts = [dict(r) for r in rows], []

    def table(self, _name):
        return _Q(self)


def _canonical_row(game_id="espn-1", start="2026-11-05T00:00:00+00:00", **over):
    return {"game_id": game_id, "game_date": "2026-11-04", "season": 2027, "status": "Final",
            "home_score": 111, "away_score": 109, "settled": True,
            "source_provider": "SPORTSDATAVERSE_ESPN", "source_payload_sha256": "a" * 64,
            "source_retrieved_at": "2026-11-06T03:00:00Z", "scheduled_start_utc": start, **over}


def test_existing_canonical_nba_row_with_null_start_is_backfilled_without_touching_settlement():
    existing = _canonical_row(start=None)
    store = _Store([existing])
    incoming = _canonical_row(home_score=999, source_payload_sha256="b" * 64)  # would corrupt if rewritten
    new = hydration._persist_rows(store, "NBA", [incoming])
    assert new == 0
    row = store.rows[0]
    assert row["scheduled_start_utc"] == "2026-11-05T00:00:00+00:00"
    assert row["home_score"] == 111 and row["source_payload_sha256"] == "a" * 64  # observation immutable


def test_existing_non_null_start_is_never_overwritten():
    store = _Store([_canonical_row(start="2026-11-05T01:00:00+00:00")])
    hydration._persist_rows(store, "NBA", [_canonical_row(start="2026-11-05T09:00:00+00:00")])
    assert store.rows[0]["scheduled_start_utc"] == "2026-11-05T01:00:00+00:00"


def test_absent_column_is_unknown_not_null_so_no_backfill_attempt():
    legacy = _canonical_row(); del legacy["scheduled_start_utc"]
    store = _Store([legacy])
    hydration._persist_rows(store, "NBA", [_canonical_row()])
    assert "scheduled_start_utc" not in store.rows[0]


def test_non_canonical_upsert_without_start_cannot_wipe_a_recorded_start():
    prior = {"game_id": "7", "game_date": "2026-09-20", "source_provider": "BALLDONTLIE",
             "scheduled_start_utc": "2026-09-20T23:00:00+00:00", "home_score": 1}
    store = _Store([prior])
    hydration._persist_rows(store, "WNBA", [
        {"game_id": "7", "game_date": "2026-09-20", "source_provider": "BALLDONTLIE",
         "scheduled_start_utc": None, "home_score": 2},
        {"game_id": "8", "game_date": "2026-09-21", "source_provider": "BALLDONTLIE",
         "scheduled_start_utc": "2026-09-21T23:00:00+00:00", "home_score": 3},
    ])
    by_id = {r["game_id"]: r for r in store.rows}
    assert by_id["7"]["scheduled_start_utc"] == "2026-09-20T23:00:00+00:00" and by_id["7"]["home_score"] == 2
    assert by_id["8"]["scheduled_start_utc"] == "2026-09-21T23:00:00+00:00"


def test_mixed_version_rows_without_the_key_still_persist():
    store = _Store()
    row = _canonical_row(); del row["scheduled_start_utc"]
    assert hydration._persist_rows(store, "NBA", [row]) == 1
    assert store.rows[0]["game_id"] == "espn-1"
