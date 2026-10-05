from datetime import datetime, timezone

import nfl_prop_auto_hydration as nfl
from v17 import nfl_prop_memory_safety as memory
from v17 import nfl_prop_evidence_control_plane as control


class _Response:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content
        self.text = content.decode("utf-8")

    def json(self):
        raise AssertionError("CSV path should not parse JSON")


def _csv(season: int, weeks: range, *, unused_size: int = 0) -> bytes:
    header = (
        "player_display_name,season,week,game_id,team,opponent_team,position,"
        "passing_yards,attempts,rushing_yards,carries,receiving_yards,targets,"
        "rushing_tds,receiving_tds,special_teams_tds,unused_blob\n"
    )
    blob = "x" * unused_size
    lines = [header]
    for week in weeks:
        lines.append(
            f"Test Player,{season},{week},{season}_{week:02d}_AAA_BBB,AAA,BBB,WR,"
            f"0,0,0,0,{50 + week},5,0,{1 if week % 3 == 0 else 0},0,{blob}\n"
        )
    return "".join(lines).encode("utf-8")


def test_compact_cache_preserves_exact_history_outputs(monkeypatch) -> None:
    payloads = {
        2026: _csv(2026, range(1, 4)),
        2025: _csv(2025, range(1, 13)),
    }

    def fake_get(url, **kwargs):
        season = 2026 if "2026.csv" in url else 2025
        return _Response(payloads[season])

    event_start = datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)
    expected = nfl._history(
        player="Test Player",
        canonical_stat="RECEIVING_YARDS",
        event_start=event_start,
        http_get=fake_get,
        now_ts=event_start.timestamp() - 60,
    )

    monkeypatch.setattr(nfl, "_cached_nflverse_rows", memory._cached_compact_nflverse_rows)
    actual = nfl._history(
        player="Test Player",
        canonical_stat="RECEIVING_YARDS",
        event_start=event_start,
        http_get=fake_get,
        now_ts=event_start.timestamp() - 60,
    )

    assert actual == expected
    assert actual[0] == [56.0, 57.0, 58.0, 59.0, 60.0, 61.0, 62.0, 51.0, 52.0, 53.0]


def test_live_compact_cache_is_singleflight_and_drops_unused_columns(monkeypatch) -> None:
    calls = {"n": 0}
    payload = _csv(2026, range(1, 4), unused_size=32_768)

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return _Response(payload)

    memory._COMPACT_CACHE.clear()
    monkeypatch.setattr(nfl.httpx, "get", fake_get)

    first_rows, first_digest = memory._cached_compact_nflverse_rows(
        2026,
        http_get=fake_get,
        now_ts=1000.0,
    )
    second_rows, second_digest = memory._cached_compact_nflverse_rows(
        2026,
        http_get=fake_get,
        now_ts=1001.0,
    )

    assert calls["n"] == 1
    assert first_rows is second_rows
    assert first_digest == second_digest
    assert first_rows
    assert isinstance(first_rows[0], tuple)
    assert not isinstance(first_rows[0], dict)
    assert first_rows[0].get("unused_blob") is None
    assert first_rows[0].get("receiving_yards") == 51.0


def test_installer_releases_legacy_cache_and_preserves_no_execution_authority(monkeypatch) -> None:
    original = nfl._cached_nflverse_rows
    monkeypatch.setattr(nfl, memory._STATE_KEY, False, raising=False)
    nfl._CSV_CACHE[2026] = (1.0, [{"unused": "legacy"}], "digest")
    memory._COMPACT_CACHE[2026] = (1.0, tuple(), "old")

    assert memory.install_nfl_prop_memory_safety() is True
    assert nfl._CSV_CACHE == {}
    assert memory._COMPACT_CACHE == {}
    assert nfl._cached_nflverse_rows is memory._cached_compact_nflverse_rows
    assert not hasattr(memory, "can_execute")

    monkeypatch.setattr(nfl, "_cached_nflverse_rows", original)


def test_forward_request_cache_preserves_compact_singleflight_without_retaining_raw_csv(monkeypatch) -> None:
    calls = {"n": 0}
    payload = _csv(2026, range(1, 13), unused_size=16_384)

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return _Response(payload)

    # Production passes the default httpx.get through a request-local cache.
    # Preserve that live/default identity for the compact process cache.
    monkeypatch.setattr(nfl.httpx, "get", fake_get)
    wrapped = control._cached_http_get(fake_get)
    assert getattr(wrapped, "_wow_nflverse_compact_cache_eligible", False) is True

    # The request-local wrapper must not retain the raw season response itself.
    url = nfl.NFLVERSE_URL.format(season=2026)
    wrapped(url)
    wrapped(url)
    assert calls["n"] == 2

    # The governed compact season cache is the single owner: one source fetch,
    # one compact parsed tuple, and no repeated full-season materialization.
    calls["n"] = 0
    memory._COMPACT_CACHE.clear()
    first_rows, first_digest = memory._cached_compact_nflverse_rows(
        2026,
        http_get=wrapped,
        now_ts=1000.0,
    )
    second_rows, second_digest = memory._cached_compact_nflverse_rows(
        2026,
        http_get=wrapped,
        now_ts=1001.0,
    )
    assert calls["n"] == 1
    assert first_rows is second_rows
    assert first_digest == second_digest
    assert first_rows[0].get("unused_blob") is None


def test_forward_request_cache_still_deduplicates_json_identity_calls() -> None:
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return object()

    wrapped = control._cached_http_get(fake_get)
    first = wrapped("https://example.invalid/identity", params={"a": 1})
    second = wrapped("https://example.invalid/identity", params={"a": 1})
    assert first is second
    assert calls["n"] == 1
    assert getattr(wrapped, "_wow_nflverse_compact_cache_eligible", False) is False
