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
