"""
Tests for gate_engine/auto_game_log.py

All external API calls are mocked — no live network needed.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from gate_engine.auto_game_log import (
    fetch_game_log,
    GameLogUnavailable,
    _cache_set,
    _CACHE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_cache():
    _CACHE.clear()


def _nba_mock_df(rows: list[dict]):
    """Build a mock nba_api PlayerGameLog that returns a fake DataFrame."""
    import pandas as pd
    df = pd.DataFrame(rows)

    class _FakeGL:
        def get_data_frames(self):
            return [df]

    pgl_mock = MagicMock()
    pgl_mock.PlayerGameLog.return_value = _FakeGL()
    return pgl_mock


def _nba_rows(n=10, pts=20.0, reb=5.0, ast=4.0, mins=30.0):
    return [
        {"MIN": str(int(mins)), "PTS": pts, "REB": reb, "AST": ast,
         "STL": 1.0, "BLK": 0.5, "FG3M": 2.0, "FTM": 3.0, "TOV": 1.0,
         "GAME_DATE": f"2026-08-{i+1:02d}", "MATCHUP": "LAL vs. GSW"}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# NBA fetch tests
# ---------------------------------------------------------------------------

class TestNBAFetch:
    def setup_method(self):
        _clear_cache()

    def _mock_pgl(self, df):
        """Patch PlayerGameLog on the already-loaded nba_api module."""
        import nba_api.stats.endpoints.playergamelog as _pgl_mod
        mock_gl = MagicMock()
        mock_gl.get_data_frames.return_value = [df]
        return patch.object(_pgl_mod, "PlayerGameLog", return_value=mock_gl)

    def test_nba_pts_returns_list(self):
        import pandas as pd
        from gate_engine.auto_game_log import _fetch_nba
        df = pd.DataFrame(_nba_rows(10, pts=22.0))
        with self._mock_pgl(df):
            values, source = _fetch_nba("2544", "PTS", "2026-08-03", 10)
        assert len(values) == 10
        assert all(v == 22.0 for v in values)
        assert "nba_api" in source

    def test_nba_dnp_rows_filtered(self):
        import pandas as pd
        from gate_engine.auto_game_log import _fetch_nba
        rows = _nba_rows(5, pts=18.0)
        rows.append({"MIN": "0", "PTS": 0.0, "REB": 0.0, "AST": 0.0,
                     "STL": 0.0, "BLK": 0.0, "FG3M": 0.0, "FTM": 0.0,
                     "TOV": 0.0, "GAME_DATE": "2026-07-01", "MATCHUP": ""})
        df = pd.DataFrame(rows)
        with self._mock_pgl(df):
            values, _ = _fetch_nba("2544", "PTS", "2026-08-03", 10)
        assert len(values) == 5  # DNP excluded

    def test_nba_combo_pra(self):
        import pandas as pd
        from gate_engine.auto_game_log import _fetch_nba
        df = pd.DataFrame(_nba_rows(5, pts=20.0, reb=8.0, ast=5.0))
        with self._mock_pgl(df):
            values, _ = _fetch_nba("2544", "PTS+REB+AST", "2026-08-03", 5)
        assert len(values) == 5
        assert all(v == pytest.approx(33.0) for v in values)

    def test_nba_unknown_stat_key_raises(self):
        from gate_engine.auto_game_log import _fetch_nba, GameLogUnavailable
        # unknown stat key raises before touching nba_api — no mock needed
        with pytest.raises(GameLogUnavailable, match="not mapped"):
            _fetch_nba("2544", "FLYING_UNICORNS", "2026-08-03", 10)

    def test_nba_empty_df_raises(self):
        import pandas as pd
        from gate_engine.auto_game_log import _fetch_nba, GameLogUnavailable
        df = pd.DataFrame()
        with self._mock_pgl(df):
            with pytest.raises(GameLogUnavailable):
                _fetch_nba("9999", "PTS", "2026-08-03", 10)


# ---------------------------------------------------------------------------
# MLB fetch tests
# ---------------------------------------------------------------------------

def _mlb_splits(n=10, hits=1.0, runs=0.5, rbi=0.5, so=1.0):
    return [
        {"stat": {"hits": hits, "strikeOuts": so, "runs": runs, "rbi": rbi,
                  "totalBases": 2.0, "baseOnBalls": 0.0, "earnedRuns": 0.0},
         "date": f"2026-08-{i+1:02d}"}
        for i in range(n)
    ]


class TestMLBFetch:
    def setup_method(self):
        _clear_cache()

    def test_mlb_hits_returns_list(self):
        from gate_engine.auto_game_log import _fetch_mlb

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "stats": [{"splits": _mlb_splits(10, hits=2.0)}]
        }

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp):
            values, source, _meta = _fetch_mlb("592450", "H", "2026-08-03", 10)

        assert len(values) == 10
        assert all(v == 2.0 for v in values)
        assert "mlb" in source.lower()

    def test_mlb_reversed_most_recent_first(self):
        from gate_engine.auto_game_log import _fetch_mlb

        # Build 5 splits with ascending hit counts (oldest first)
        splits = [{"stat": {"hits": float(i), "strikeOuts": 0.0, "runs": 0.0,
                             "rbi": 0.0, "totalBases": 0.0, "baseOnBalls": 0.0,
                             "earnedRuns": 0.0}, "date": f"2026-08-{i+1:02d}"}
                  for i in range(5)]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stats": [{"splits": splits}]}

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp):
            values, _, _meta = _fetch_mlb("592450", "H", "2026-08-03", 5)

        # Most recent first → last element of splits becomes values[0]
        assert values[0] == 4.0

    def test_mlb_combo_h_r_rbi(self):
        from gate_engine.auto_game_log import _fetch_mlb

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "stats": [{"splits": _mlb_splits(5, hits=1.0, runs=1.0, rbi=1.0)}]
        }

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp):
            values, _, _meta = _fetch_mlb("592450", "H+R+RBI", "2026-08-03", 5)

        assert all(v == 3.0 for v in values)

    def test_mlb_api_error_raises(self):
        from gate_engine.auto_game_log import _fetch_mlb, GameLogUnavailable

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp):
            with pytest.raises(GameLogUnavailable, match="HTTP 404"):
                _fetch_mlb("999", "H", "2026-08-03", 10)

    def test_mlb_empty_splits_raises(self):
        from gate_engine.auto_game_log import _fetch_mlb, GameLogUnavailable

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stats": [{"splits": []}]}

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp):
            with pytest.raises(GameLogUnavailable, match="No MLB game log"):
                _fetch_mlb("999", "H", "2026-08-03", 10)


# ---------------------------------------------------------------------------
# MLB pitching outs routing tests
# ---------------------------------------------------------------------------

def _mlb_pitching_splits(n=10, outs=15, strikeouts=5, ip="5.0"):
    """Mock pitching split rows with the 'outs' field (integer recorded outs).

    The MLB Stats API pitching gameLog uses 'outs' (integer) NOT 'recordedOuts'.
    4.1 IP → outs=13, 5.0 IP → outs=15, 6.0 IP → outs=18.
    """
    return [
        {"stat": {
            "outs":        outs,
            "strikeOuts":  strikeouts,
            "inningsPitched": ip,
            "hits":        6,
            "earnedRuns":  2,
            "baseOnBalls": 2,
        }, "date": f"2026-08-{i+1:02d}"}
        for i in range(n)
    ]


class TestMLBPitchingOuts:
    """
    Regression suite for WOW-PATCH-2026-08-06 pitching outs pipeline fix.

    Before the fix:
      - _MLB_STAT_FIELDS had no "OUTS" entry → GameLogUnavailable for every request.
      - "OUTS" was absent from pitcher_keys → wrong split group queried.
      - The field name was incorrectly set to "recordedOuts"; the actual MLB Stats
        API field is "outs" (integer already in recorded-outs units).
    """

    def setup_method(self):
        _clear_cache()

    # ── Static registration checks ──────────────────────────────────────────

    def test_outs_is_registered_in_mlb_stat_fields(self):
        """'OUTS' must be in _MLB_STAT_FIELDS and map to the 'outs' API field."""
        from gate_engine.auto_game_log import _MLB_STAT_FIELDS
        assert "OUTS" in _MLB_STAT_FIELDS, (
            "'OUTS' missing from _MLB_STAT_FIELDS — pitching outs fetch silently fails"
        )
        assert _MLB_STAT_FIELDS["OUTS"] == "outs", (
            f"Expected 'outs' (MLB Stats API field) but got '{_MLB_STAT_FIELDS['OUTS']}'. "
            "'recordedOuts' is absent from gameLog splits; the field is 'outs'."
        )

    def test_outs_routes_to_pitching_split_group(self):
        """'OUTS' must be in pitcher_keys so the pitching split group is queried.

        The hitting split has no 'outs' field; a wrong group → 0 qualifying rows.
        """
        from gate_engine.auto_game_log import _fetch_mlb
        import inspect

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stats": [{"splits": _mlb_pitching_splits(3, outs=15)}]}

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp) as mock_get:
            values, source, _ = _fetch_mlb("676083", "OUTS", "2026-08-06", 3)

        # Verify the pitching split group was requested
        call_kwargs = mock_get.call_args
        url_or_kwargs = str(call_kwargs)
        assert "pitching" in url_or_kwargs, (
            "MLB Stats API call did not request 'pitching' group for OUTS stat_key"
        )

    def test_outs_returns_integer_values(self):
        """_fetch_mlb with stat_key='OUTS' must return the 'outs' integer values."""
        from gate_engine.auto_game_log import _fetch_mlb

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "stats": [{"splits": _mlb_pitching_splits(5, outs=15)}]
        }

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp):
            values, source, _ = _fetch_mlb("676083", "OUTS", "2026-08-06", 5)

        assert len(values) == 5
        assert all(v == 15.0 for v in values)
        assert "mlb" in source.lower()

    def test_outs_varies_per_start(self):
        """Values must be per-start, not accumulated — ordering is most-recent first."""
        from gate_engine.auto_game_log import _fetch_mlb

        # Oldest first in API response → should be reversed to most-recent first
        splits = [
            {"stat": {"outs": float(i * 3), "strikeOuts": i, "inningsPitched": f"{i}.0",
                      "hits": 5, "earnedRuns": 1, "baseOnBalls": 1},
             "date": f"2026-08-{i+1:02d}"}
            for i in range(1, 6)  # 3, 6, 9, 12, 15
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stats": [{"splits": splits}]}

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp):
            values, _, _ = _fetch_mlb("676083", "OUTS", "2026-08-06", 5)

        # Most recent first: split[-1]=15 should become values[0]
        assert values[0] == 15.0
        assert values[-1] == 3.0

    def test_outs_zero_rows_raises_game_log_unavailable(self):
        """Empty pitching split response must raise GameLogUnavailable, not return []."""
        from gate_engine.auto_game_log import _fetch_mlb, GameLogUnavailable

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stats": [{"splits": []}]}

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp):
            with pytest.raises(GameLogUnavailable):
                _fetch_mlb("676083", "OUTS", "2026-08-06", 10)


# ---------------------------------------------------------------------------
# WNBA fetch tests
# ---------------------------------------------------------------------------

def _bdl_stats(n=10, pts=15.0, reb=5.0, ast=3.0, mins=28.0):
    return [
        {"pts": pts, "reb": reb, "ast": ast, "stl": 1.0, "blk": 0.5,
         "fg3m": 1.0, "min": mins,
         "game": {"date": f"2026-08-{i+1:02d}"}}
        for i in range(n)
    ]


class TestWNBAFetch:
    def setup_method(self):
        _clear_cache()

    def test_wnba_pts_returns_list(self):
        from gate_engine.auto_game_log import _fetch_wnba

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": _bdl_stats(8, pts=18.0)}

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp), \
             patch.dict("os.environ", {"balldontlie": "test-key"}):
            values, source = _fetch_wnba("999", "PTS", "2026-08-03", 8)

        assert len(values) == 8
        assert all(v == 18.0 for v in values)
        assert "balldontlie" in source.lower()

    def test_wnba_dnp_filtered(self):
        from gate_engine.auto_game_log import _fetch_wnba

        rows = _bdl_stats(3, pts=12.0)
        rows.append({"pts": 0.0, "reb": 0.0, "ast": 0.0, "stl": 0.0,
                     "blk": 0.0, "fg3m": 0.0, "min": 0.0,
                     "game": {"date": "2026-07-01"}})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": rows}

        with patch("gate_engine.auto_game_log.requests.get", return_value=mock_resp), \
             patch.dict("os.environ", {"balldontlie": "test-key"}):
            values, _ = _fetch_wnba("999", "PTS", "2026-08-03", 10)

        assert len(values) == 3

    def test_wnba_no_key_raises(self):
        from gate_engine.auto_game_log import _fetch_wnba, GameLogUnavailable

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(GameLogUnavailable, match="balldontlie"):
                _fetch_wnba("999", "PTS", "2026-08-03", 10)


# ---------------------------------------------------------------------------
# Unsupported sport
# ---------------------------------------------------------------------------

class TestUnsupportedSport:
    def setup_method(self):
        _clear_cache()

    def test_nfl_raises(self):
        with pytest.raises(GameLogUnavailable, match="NFL"):
            fetch_game_log("123", "NFL", "PASS_YDS", "2026-08-03")

    def test_nhl_raises(self):
        with pytest.raises(GameLogUnavailable, match="NHL"):
            fetch_game_log("123", "NHL", "SOG", "2026-08-03")


# ---------------------------------------------------------------------------
# Caching behaviour
# ---------------------------------------------------------------------------

class TestCache:
    def setup_method(self):
        _clear_cache()

    def test_cache_hit_skips_api(self):
        _cache_set("NBA:123:PTS:2026-08-03", [20.0, 18.0, 22.0], "nba_api", 3)

        call_count = {"n": 0}

        def fake_fetch(*a, **kw):
            call_count["n"] += 1
            return ([25.0], "nba_api")

        with patch("gate_engine.auto_game_log._fetch_nba", side_effect=fake_fetch):
            result = fetch_game_log("123", "NBA", "PTS", "2026-08-03")

        assert call_count["n"] == 0
        assert result["values"] == [20.0, 18.0, 22.0]
        assert result["cached"] is True

    def test_cache_miss_calls_api(self):
        call_count = {"n": 0}

        def fake_fetch(*a, **kw):
            call_count["n"] += 1
            return ([20.0, 18.0], "nba_api")

        with patch("gate_engine.auto_game_log._fetch_nba", side_effect=fake_fetch):
            result = fetch_game_log("456", "NBA", "PTS", "2026-08-03")

        assert call_count["n"] == 1
        assert result["cached"] is False
