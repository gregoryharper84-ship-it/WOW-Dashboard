"""
test_mlb_plate_appearances.py

Regression tests for Task #124:
  Prevent MLB Plate Appearances props from permanently failing with no path to a score.

Two gaps fixed:
  1. normalizer.py MLB _STAT_KEY_MAP now maps "plate appearances" / "plate_appearances"
     / "pa" / "plate apps" → canonical stat_key "PA"
  2. model_registry.py now has PROVISIONAL entries for ("MLB", "PA") and
     ("MLB", "PLATE_APPEARANCES")

A row with prop_type="plate_appearances", valid line/direction, and a 10-game
numeric game_log must pass normalization, reach the registry, and return a
PROVISIONAL (MODEL_QUALIFIED_HOLD) entry — NOT DATA_CONTRACT_FAIL.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MLB_ROSTER = [
    {
        "player_id":      "680776",
        "name_raw":       "Wade Meckler",
        "name":           "Wade Meckler",
        "name_norm":      "wade meckler",
        "name_normalized":"wade meckler",
        "team_abbr":      "SF",
        "team":           "SF",
        "sport":          "MLB",
        "position":       "OF",
        "source":         "mlb_stats_api",
    }
]

_GAME_TODAY = {"game_id": "mlb_sf_001", "game_time": "2026-08-07T19:00:00Z", "opponent": "LAD"}


def _patch_roster(roster):
    """Patch get_roster regardless of sport argument."""
    return patch("gate_engine.normalizer.get_roster", return_value=roster)


def _patch_game(game_result):
    return patch("gate_engine.normalizer._get_game", return_value=game_result)



# ===========================================================================
# Section 1: _map_stat_key alias resolution
# ===========================================================================

class TestPlateAppearancesAliases:
    """All four display-label aliases must resolve to stat_key='PA'."""

    def _map(self, label: str) -> dict:
        from gate_engine.normalizer import _map_stat_key
        return _map_stat_key(label, "MLB")

    def test_alias_plate_appearances_space(self):
        result = self._map("plate appearances")
        assert result["stat_key"] == "PA", f"Expected PA, got {result}"
        assert "UNKNOWN_PROP_TYPE" not in result.get("flags", [])

    def test_alias_plate_appearances_underscore(self):
        result = self._map("plate_appearances")
        assert result["stat_key"] == "PA"

    def test_alias_pa_short(self):
        result = self._map("pa")
        assert result["stat_key"] == "PA"

    def test_alias_plate_apps(self):
        result = self._map("plate apps")
        assert result["stat_key"] == "PA"

    def test_alias_case_insensitive_upper(self):
        """_map_stat_key lowercases internally; mixed-case input must work."""
        result = self._map("Plate Appearances")
        assert result["stat_key"] == "PA"

    def test_alias_case_insensitive_pa_upper(self):
        result = self._map("PA")
        assert result["stat_key"] == "PA"

    def test_no_stat_formula_leak(self):
        """PA is a plain stat_key, not a combo — stat_formula must be None."""
        result = self._map("plate appearances")
        assert result["stat_formula"] is None

    def test_unrelated_mlb_alias_unaffected(self):
        """Ensure existing MLB aliases still work after the patch."""
        from gate_engine.normalizer import _map_stat_key
        assert _map_stat_key("hits", "MLB")["stat_key"] == "H"
        assert _map_stat_key("home runs", "MLB")["stat_key"] == "HR"
        assert _map_stat_key("innings pitched", "MLB")["stat_key"] == "IP"


# ===========================================================================
# Section 2: model_registry entries
# ===========================================================================

class TestPlateAppearancesRegistry:
    """Both ('MLB','PA') and ('MLB','PLATE_APPEARANCES') must have PROVISIONAL entries."""

    def test_pa_registry_exists(self):
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "PA")
        assert entry["status"] == "PROVISIONAL", f"Unexpected status: {entry['status']}"

    def test_pa_registry_model_id(self):
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "PA")
        assert entry["model_id"] == "mlb_counting_poisson_v1"

    def test_pa_registry_minimum_inputs(self):
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "PA")
        assert "game_log" in entry["minimum_inputs"]

    def test_plate_appearances_registry_exists(self):
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "PLATE_APPEARANCES")
        assert entry["status"] == "PROVISIONAL"

    def test_plate_appearances_registry_model_id(self):
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "PLATE_APPEARANCES")
        assert entry["model_id"] == "mlb_counting_poisson_v1"

    def test_is_supported_pa(self):
        from gate_engine.model_registry import is_supported
        assert is_supported("MLB", "PA") is True

    def test_is_supported_plate_appearances(self):
        from gate_engine.model_registry import is_supported
        assert is_supported("MLB", "PLATE_APPEARANCES") is True

    def test_provisional_ceiling_present(self):
        """PROVISIONAL entry must carry the ceiling dict so downstream gates apply it."""
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "PA")
        ceiling = entry.get("provisional_ceiling", {})
        assert ceiling.get("maximum_label") == "MODEL_QUALIFIED_HOLD"
        assert ceiling.get("money_grade_allowed") is False

    def test_not_no_registered_model(self):
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "PA")
        assert entry["model_id"] != "NO_REGISTERED_MODEL"


# ===========================================================================
# Section 3: End-to-end normalize_legs path
# ===========================================================================

class TestPlateAppearancesNormalizeLegs:
    """
    A leg with prop_type='plate_appearances' must resolve to stat_key='PA'
    and not carry UNKNOWN_PROP_TYPE in its flags.
    """

    def _run(self, prop_type: str):
        from gate_engine.normalizer import normalize_legs
        legs = [
            {
                "player_name": "Wade Meckler",
                "prop_type":   prop_type,
                "line_value":  3.5,
                "direction":   "MORE",
                "sport":       "MLB",   # sport lives in the leg dict, not as a kwarg
            }
        ]
        with _patch_roster(_MLB_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs(legs)
        return rows[0] if rows else None

    def test_plate_appearances_resolves_stat_key(self):
        row = self._run("plate appearances")
        assert row is not None
        assert row["stat_key"] == "PA", f"stat_key={row['stat_key']!r}, flags={row['flags']}"

    def test_plate_appearances_no_unknown_flag(self):
        row = self._run("plate appearances")
        assert "UNKNOWN_PROP_TYPE" not in (row["flags"] or [])

    def test_plate_appearances_underscore_resolves(self):
        row = self._run("plate_appearances")
        assert row["stat_key"] == "PA"

    def test_pa_short_resolves(self):
        row = self._run("pa")
        assert row["stat_key"] == "PA"

    def test_plate_apps_resolves(self):
        row = self._run("plate apps")
        assert row["stat_key"] == "PA"


# ===========================================================================
# Section 4: auto_game_log field mapping for PA
# ===========================================================================

class TestPlateAppearancesGameLogFieldMapping:
    """_MLB_STAT_FIELDS must map PA to the MLB Stats API 'plateAppearances' field."""

    def test_pa_field_mapped(self):
        from gate_engine.auto_game_log import _MLB_STAT_FIELDS
        assert "PA" in _MLB_STAT_FIELDS, "PA missing from _MLB_STAT_FIELDS"
        assert _MLB_STAT_FIELDS["PA"] == "plateAppearances"

    def test_plate_appearances_alias_mapped(self):
        from gate_engine.auto_game_log import _MLB_STAT_FIELDS
        assert "PLATE_APPEARANCES" in _MLB_STAT_FIELDS
        assert _MLB_STAT_FIELDS["PLATE_APPEARANCES"] == "plateAppearances"

    def test_pa_not_in_pitcher_keys(self):
        """PA is a batter stat — _fetch_mlb must query the hitting split, not pitching."""
        # Replicate the pitcher_keys set from _fetch_mlb exactly
        pitcher_keys = {"H_allowed", "ER", "BB", "K", "SO", "OUTS"}
        assert "PA" not in pitcher_keys, "PA incorrectly routed to pitching split"
        assert "PLATE_APPEARANCES" not in pitcher_keys

    def test_fetch_mlb_pa_uses_hitting_split(self):
        """_fetch_mlb with stat_key='PA' should use the 'hitting' split group."""
        import requests
        from unittest.mock import patch, MagicMock

        fake_splits = [
            {"stat": {"plateAppearances": 4}, "team": {"name": "SF Giants"}},
            {"stat": {"plateAppearances": 3}, "team": {"name": "SF Giants"}},
            {"stat": {"plateAppearances": 5}, "team": {"name": "SF Giants"}},
        ]
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "stats": [{"splits": fake_splits}]
        }

        with patch("gate_engine.auto_game_log.requests.get", return_value=fake_resp) as mock_get:
            from gate_engine.auto_game_log import _fetch_mlb
            values, source, _meta = _fetch_mlb("680776", "PA", "2026-08-07", 3)

        # Verify the API was called with group=hitting (not pitching)
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["params"]["group"] == "hitting", (
            f"Expected group=hitting, got {call_kwargs['params']['group']!r}"
        )
        # Values should be reversed (most-recent first) and parsed correctly
        assert values == [5.0, 3.0, 4.0]
        assert source == "statsapi.mlb.com (MLB Stats API)"


# ===========================================================================
# Section 5: hit_probability routing for MLB PA
# ===========================================================================

class TestPlateAppearancesHitProbabilityRouting:
    """
    MLB PA routing — after Task-186 repair the PA guard is fail-closed.
    PA has no dedicated specialist in the registry (only the generic
    mlb_counting_poisson_v1 which is shared with SO/K/OUTS).
    compute() must return NO_REGISTERED_MODEL/None, never generic Poisson.
    """

    def test_is_counting_stat_pa(self):
        from gate_engine.hit_probability import _is_counting_stat
        assert _is_counting_stat("MLB", "PA") is True

    def test_is_counting_stat_plate_appearances(self):
        from gate_engine.hit_probability import _is_counting_stat
        assert _is_counting_stat("MLB", "PLATE_APPEARANCES") is True

    def test_is_counting_stat_pa_lowercase(self):
        from gate_engine.hit_probability import _is_counting_stat
        assert _is_counting_stat("MLB", "pa") is True

    def test_pa_not_binary(self):
        """PA must NOT be treated as binary — it's a high-count stat (3-5/game)."""
        from gate_engine.hit_probability import _is_mlb_binary
        # PA line of 3.5 is well above 1.5; should not be binary regardless
        assert _is_mlb_binary("MLB", "PA", 3.5) is False

    def test_pa_not_hits_prop(self):
        from gate_engine.hit_probability import _is_mlb_hits_prop
        assert _is_mlb_hits_prop("MLB", "PA", 3.5) is False

    def test_compute_pa_more_blocked_no_specialist(self):
        """
        After Task-186 repair: compute() with MLB PA returns NO_REGISTERED_MODEL
        because the registry entry uses the generic mlb_counting_poisson_v1 model
        (not a dedicated PA specialist). hit_probability=None, never Poisson.
        """
        from gate_engine.hit_probability import compute, MODEL_NO_REGISTERED_MODEL, MODEL_POISSON

        game_log = [4.0, 3.0, 5.0, 4.0, 3.0, 4.0, 5.0, 3.0, 4.0, 4.0]
        leg = {
            "sport":       "MLB",
            "stat_key":    "PA",
            "line_value":  3.5,
            "side":        "MORE",
            "player_name": "Wade Meckler",
        }
        result = compute(leg, game_log)

        assert result.model_used != MODEL_POISSON, (
            "PA must not route to generic Poisson (mlb_counting_poisson_v1); "
            "a dedicated specialist must be registered first"
        )
        assert result.model_used == MODEL_NO_REGISTERED_MODEL, (
            f"Expected NO_REGISTERED_MODEL for PA with generic Poisson registry; "
            f"got model_used={result.model_used!r}, note={result.calibration_note!r}"
        )
        assert result.hit_probability is None

    def test_compute_pa_less_blocked_no_specialist(self):
        """LESS side also returns NO_REGISTERED_MODEL — Poisson excluded for PA."""
        from gate_engine.hit_probability import compute, MODEL_NO_REGISTERED_MODEL, MODEL_POISSON

        game_log = [4.0, 3.0, 5.0, 4.0, 3.0, 4.0, 5.0, 3.0, 4.0, 4.0]
        leg = {
            "sport":       "MLB",
            "stat_key":    "PA",
            "line_value":  3.5,
            "side":        "LESS",
            "player_name": "Wade Meckler",
        }
        result = compute(leg, game_log)
        assert result.model_used != MODEL_POISSON
        assert result.model_used == MODEL_NO_REGISTERED_MODEL
        assert result.hit_probability is None

    def test_compute_pa_calibration_note_mentions_ceiling(self):
        """calibration_note for blocked PA must mention MODEL_QUALIFIED_HOLD or no specialist."""
        from gate_engine.hit_probability import compute

        leg = {"sport": "MLB", "stat_key": "PA", "line_value": 3.5, "side": "MORE"}
        result = compute(leg, [4.0, 3.0, 5.0])
        assert (
            "MODEL_QUALIFIED_HOLD" in result.calibration_note
            or "no specialist" in result.calibration_note.lower()
            or "generic" in result.calibration_note.lower()
        ), f"calibration_note should explain the block: {result.calibration_note!r}"
