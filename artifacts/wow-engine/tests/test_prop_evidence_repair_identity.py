"""Regression: governed evidence fallback must query persisted canonical identity.

The primary evidence RPC compares sport/stat case-insensitively, while
``wow_prop_evidence_snapshots`` stores uppercase sport, uppercase canonical
stat_type and whitespace-collapsed player names. The fallback locator used the
raw caller strings, so a valid governed snapshot could be skipped and reported
as ``PROP_EVIDENCE_SNAPSHOT_NOT_FOUND``.
"""
from types import SimpleNamespace

from prop_evidence_repair import _fallback_snapshot_ids


class _Query:
    def __init__(self, rows, filters):
        self._rows = rows
        self._filters = filters

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def lt(self, key, value):
        self._filters.append((key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _value):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _Client:
    def __init__(self, rows):
        self._rows = rows
        self.filters: list[tuple[str, object]] = []

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return _Query(self._rows, self.filters)


def _req(**overrides):
    values = {
        "event_id": "espn-401909944",
        "sport": "NBA",
        "player": "LeBron James",
        "stat_type": "POINTS",
        "line": 25.5,
        "event_start_time": "2099-01-01T00:00:00+00:00",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fallback_lookup_normalizes_sport_stat_and_player_identity():
    client = _Client([{"source_snapshot_id": "snap-1", "captured_at": "2099-01-01T00:00:00+00:00"}])

    ids = _fallback_snapshot_ids(
        client,
        _req(sport="nba", player="  LeBron   James ", stat_type="points"),
    )

    assert ids == ["snap-1"]
    assert ("sport", "NBA") in client.filters
    assert ("player", "LeBron James") in client.filters
    assert ("stat_type", "POINTS") in client.filters
    assert ("event_id", "espn-401909944") in client.filters
    assert ("line", 25.5) in client.filters


def test_fallback_lookup_preserves_already_canonical_identity():
    client = _Client([])

    assert _fallback_snapshot_ids(client, _req(sport="NFL", stat_type="PASSING_YARDS")) == []

    assert ("sport", "NFL") in client.filters
    assert ("stat_type", "PASSING_YARDS") in client.filters


def test_fallback_lookup_normalizes_hyphenated_stat_labels():
    client = _Client([])

    _fallback_snapshot_ids(client, _req(sport="NFL", stat_type="pass-yards"))

    assert ("sport", "NFL") in client.filters
    assert ("stat_type", "PASS_YARDS") in client.filters


def test_fallback_lookup_never_rewrites_event_or_line_filters():
    client = _Client([])

    _fallback_snapshot_ids(client, _req(event_id=" 2026020276 ", line=6.5))

    assert ("event_id", " 2026020276 ") in client.filters
    assert ("line", 6.5) in client.filters
