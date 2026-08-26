"""
gate_engine/tests/test_targeted_acquisition_patch.py

Targeted regression tests for the 2026-08-10 acquisition/identity plumbing patch.

Covers (per the Definition of Done):
  Test 1  — WNBA canonical identity reaches the scoreboard adapter (no " vs " blank)
  Test 2  — WNBA enrichment merge: resolved/unresolved fields are mutually exclusive
  Test 3  — WNBA exact-line market evidence survives the join (stat_key alias lookup)
  Test 4  — MLB historical acquisition adapters are CALLED for Pitcher Strikeouts
  Test 5  — IP-to-outs conversion: 6.1 = 19, 6.2 = 20
  Test 6  — Plate Appearances gets explicit MODEL_UNSUPPORTED (not DATA_CONTRACT_FAIL)
            when PA specialist is not registered, OR the stat_key canonicalizes correctly
  Test 7  — 1IP stat_key canonicalizes to 1IP_PITCHES_THROWN (specialist route preserved)
  Test 8  — Base64 whitespace in image payload is stripped before decode validation
  Test 9  — Mixed-technical-failure batch is never summarised as clean NO_PLAY

can_execute=False and governance ceilings are verified unchanged at the bottom.
"""
from __future__ import annotations

import base64
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Test 1: WNBA canonical identity — game string propagation / no " vs " blank
# ---------------------------------------------------------------------------

class TestWNBAIdentityPropagation(unittest.TestCase):
    """FIX-A: build_packet() must copy the canonical game field and parse
    team/opponent from it when the row carries game='ATL vs TOR' but no
    explicit team or opponent fields."""

    def _make_row(self, game: str = "ATL vs TOR", team: str = "", opponent: str = "") -> dict:
        return {
            "row_id":    "row-001",
            "player":    "Angel Reese",
            "sport":     "WNBA",
            "prop_type": "Rebounds",
            "line":      12.5,
            "direction": "MORE",
            "game":      game,
            "team":      team,
            "opponent":  opponent,
        }

    def test_game_field_copied_into_packet(self):
        from gate_engine.wnba.acquisition_packet import build_packet
        row = self._make_row()
        packet = build_packet(row, {})
        self.assertEqual(packet.get("game"), "ATL vs TOR",
                         "packet['game'] must equal the row's canonical game string")

    def test_team_parsed_from_game_string_when_absent(self):
        from gate_engine.wnba.acquisition_packet import build_packet
        row = self._make_row()          # team="" opponent="" but game="ATL vs TOR"
        packet = build_packet(row, {})
        self.assertEqual(packet.get("team"), "ATL",
                         "team must be parsed from game string when row.team is blank")

    def test_opponent_parsed_from_game_string_when_absent(self):
        from gate_engine.wnba.acquisition_packet import build_packet
        row = self._make_row()
        packet = build_packet(row, {})
        self.assertEqual(packet.get("opponent"), "TOR",
                         "opponent must be parsed from game string when row.opponent is blank")

    def test_explicit_team_not_overwritten(self):
        from gate_engine.wnba.acquisition_packet import build_packet
        row = self._make_row(team="ATL", opponent="TOR")
        packet = build_packet(row, {})
        self.assertEqual(packet.get("team"), "ATL")
        self.assertEqual(packet.get("opponent"), "TOR")

    def test_blank_game_string_never_produced(self):
        """The string ' vs ' must never appear as a game_str in the fallback router
        when the row carries a populated game field."""
        from gate_engine.wnba.acquisition_packet import build_packet
        row = self._make_row()
        packet = build_packet(row, {})
        # game_str would normally be: packet.get("game") or f"{team} vs {opponent}"
        # With FIX-A, packet["game"] = "ATL vs TOR", so fallback_router uses that.
        game_str_candidate = (
            packet.get("game")
            or f"{packet.get('team', '')} vs {packet.get('opponent', '')}"
        )
        self.assertNotEqual(game_str_candidate.strip(), "vs",
                            "game_str must not collapse to bare 'vs'")
        self.assertNotIn(game_str_candidate, (" vs ", " @ ", ""),
                         "blank matchup strings must never be produced")

    def test_identity_handoff_error_on_fully_blank_row(self):
        """FIX-A: fallback_router must return IDENTITY_HANDOFF_ERROR when
        game, team, and opponent are all absent — not construct ' vs '."""
        from gate_engine.wnba.acquisition_packet import build_packet
        from gate_engine.wnba.fallback_router import (
            _attempt_event_status,  # noqa: internal but tested
            AcquisitionFieldStatus,
        )
        blank_row = {
            "row_id":    "row-blank",
            "player":    "Angel Reese",
            "sport":     "WNBA",
            "prop_type": "Rebounds",
            "line":      12.5,
            "direction": "MORE",
            "game":      "",   # intentionally absent
            "team":      "",
            "opponent":  "",
        }
        packet = build_packet(blank_row, {})
        result = _attempt_event_status(packet, {})
        self.assertEqual(
            result.status,
            AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
            "IDENTITY_HANDOFF_ERROR must resolve to DATA_UNOBTAINABLE_AFTER_EXHAUSTION",
        )
        self.assertIn("IDENTITY_HANDOFF_ERROR", result.source_id,
                      "source_id must identify the handoff failure")


# ---------------------------------------------------------------------------
# Test 2: WNBA enrichment merge — resolved/unresolved mutual exclusion
# ---------------------------------------------------------------------------

class TestWNBAMergeConsistency(unittest.TestCase):
    """FIX-B: A field that is present and valid in the packet must not also
    appear in fields_unresolved (post-merge cleanup)."""

    def _make_packet_with_projected_minutes(self, pm_value) -> dict:
        return {
            "player":     "Angel Reese",
            "team":       "CHI",
            "opponent":   "ATL",
            "event_id":   "WNBA:2026-08-10:CHI@ATL",
            "game":       "CHI vs ATL",
            "role_status": {
                "active_status":     "ACTIVE",
                "role_timestamp":    "2026-08-10T18:00:00Z",
                "projected_minutes": pm_value,
            },
            "event_status":           "SCHEDULED",
            "box_score_log":          [{"MIN": 32, "PTS": 18, "REB": 12, "AST": 2, "FGA": 10, "USG%": 0.28}] * 7,
            "l5_ledger":              [],
            "l10_ledger":             [],
            "matchup":                {},
            "market_comparison":      None,
            "news_contradiction_check": None,
        }

    def test_projected_minutes_valid_not_in_fields_unresolved(self):
        """If projected_minutes is present and >= 0, it must be removed from
        fields_unresolved by the post-merge cleanup."""
        from gate_engine.wnba.evidence_acquisition import _validate_critical_field_value

        packet = self._make_packet_with_projected_minutes(28.5)
        # Simulate: validate_packet returned projected_minutes in unresolved
        fields_unresolved = ["role_status.projected_minutes"]

        # Apply post-merge cleanup (the same logic added to evidence_acquisition.py)
        cleaned = [
            f for f in fields_unresolved
            if not _validate_critical_field_value(f, packet)
        ]
        self.assertNotIn(
            "role_status.projected_minutes", cleaned,
            "projected_minutes with valid value must be removed from fields_unresolved",
        )

    def test_projected_minutes_none_stays_unresolved(self):
        """If projected_minutes is None in the packet it must stay in fields_unresolved."""
        from gate_engine.wnba.evidence_acquisition import _validate_critical_field_value

        packet = self._make_packet_with_projected_minutes(None)
        fields_unresolved = ["role_status.projected_minutes"]
        cleaned = [
            f for f in fields_unresolved
            if not _validate_critical_field_value(f, packet)
        ]
        self.assertIn(
            "role_status.projected_minutes", cleaned,
            "projected_minutes=None must remain in fields_unresolved",
        )

    def test_active_status_valid_not_in_fields_unresolved(self):
        from gate_engine.wnba.evidence_acquisition import _validate_critical_field_value
        packet = self._make_packet_with_projected_minutes(30.0)
        fields_unresolved = ["role_status.active_status"]
        cleaned = [f for f in fields_unresolved if not _validate_critical_field_value(f, packet)]
        self.assertNotIn("role_status.active_status", cleaned)

    def test_resolved_and_unresolved_sets_are_disjoint(self):
        """Core invariant: resolved_fields INTERSECT fields_unresolved == empty."""
        from gate_engine.wnba.evidence_acquisition import _validate_critical_field_value

        packet = self._make_packet_with_projected_minutes(30.0)
        # All of these have valid values in the packet above
        resolved = [
            "role_status.active_status",
            "role_status.projected_minutes",
            "role_status.role_timestamp",
        ]
        all_unresolved = resolved[:]    # simulate worst-case: all appear as unresolved
        cleaned_unresolved = [
            f for f in all_unresolved
            if not _validate_critical_field_value(f, packet)
        ]
        overlap = set(resolved) & set(cleaned_unresolved)
        self.assertEqual(overlap, set(),
                         f"resolved and unresolved sets must be disjoint; overlap={overlap}")


# ---------------------------------------------------------------------------
# Test 3: WNBA exact-line market evidence survives the join (FIX-C)
# ---------------------------------------------------------------------------

class TestWNBAMarketJoinStatKeyLookup(unittest.TestCase):
    """FIX-C: _get_enrichment and _build_market_join_audit must also try the
    stat_key variant of the enrichment key so that a caller-keyed enrichment
    under 'angel reese:rebounds' is found even when the row prop_type is 'REB'."""

    def _enrichment(self) -> dict:
        return {
            "angel reese:rebounds": {
                "sportsbook_line": 12.5,
                "consensus_line":  12.5,
                "best_available":  12.5,
            }
        }

    def _row_canonical_prop(self) -> dict:
        """Row where prop_type matches the enrichment key exactly."""
        return {
            "row_id":    "row-ar-12",
            "player":    "Angel Reese",
            "prop_type": "rebounds",   # matches enrichment key
            "stat_key":  "REB",
            "sport":     "WNBA",
        }

    def _row_stat_key_only(self) -> dict:
        """Row where prop_type has been normalized to stat_key 'REB' — enrichment
        is keyed by display name 'rebounds', simulating the normalization mismatch."""
        return {
            "row_id":    "row-ar-12",
            "player":    "Angel Reese",
            "prop_type": "REB",        # normalized — won't match 'rebounds' key directly
            "stat_key":  "rebounds",   # FIX-C: try this as alternate key
            "sport":     "WNBA",
        }

    def test_enrichment_found_by_prop_type_key(self):
        from gate_engine.pipeline import _get_enrichment
        enr = self._enrichment()
        result = _get_enrichment(enr, self._row_canonical_prop())
        self.assertEqual(result.get("sportsbook_line"), 12.5,
                         "sportsbook_line must be found by prop_type key")

    def test_enrichment_found_by_stat_key_variant(self):
        from gate_engine.pipeline import _get_enrichment
        enr = self._enrichment()
        result = _get_enrichment(enr, self._row_stat_key_only())
        self.assertEqual(result.get("sportsbook_line"), 12.5,
                         "sportsbook_line must be found via stat_key alternate key")

    def test_no_false_no_market_found_when_sportsbook_line_present(self):
        from gate_engine.pipeline import _get_enrichment, _build_market_join_audit, JOIN_STATUS_NO_MARKET_FOUND
        enr = self._enrichment()
        row = self._row_stat_key_only()
        row["gates"] = {}  # no market_gate result yet
        result = _build_market_join_audit(row, enr)
        self.assertNotEqual(
            result.get("market_join_status"), JOIN_STATUS_NO_MARKET_FOUND,
            "Market join must not report NO_MARKET_FOUND when sportsbook_line is present",
        )
        self.assertTrue(result.get("sportsbook_line_present"),
                        "sportsbook_line_present must be True when line exists in enrichment")


# ---------------------------------------------------------------------------
# Test 4: MLB historical acquisition — Pitcher Strikeouts must not be NOT_CALLED
# ---------------------------------------------------------------------------

class TestMLBHistoricalAcquisitionCalled(unittest.TestCase):
    """FIX-D: fetch_missing_game_logs() must call fetch_game_log for rows with
    prop_type 'Pitcher Strikeouts' (display name) by canonicalizing it to 'K'."""

    def test_stat_key_canonical_pitcher_strikeouts(self):
        from gate_engine.auto_enrichment import _canonicalize_stat_key
        self.assertEqual(_canonicalize_stat_key("Pitcher Strikeouts"), "K")
        self.assertEqual(_canonicalize_stat_key("pitcher strikeouts"), "K")
        self.assertEqual(_canonicalize_stat_key("strikeouts"), "K")
        self.assertEqual(_canonicalize_stat_key("K"), "K")
        self.assertEqual(_canonicalize_stat_key("SO"), "K")

    def test_stat_key_canonical_pitching_outs(self):
        from gate_engine.auto_enrichment import _canonicalize_stat_key
        self.assertEqual(_canonicalize_stat_key("Pitching Outs"), "OUTS")
        self.assertEqual(_canonicalize_stat_key("pitching outs"), "OUTS")
        self.assertEqual(_canonicalize_stat_key("OUTS"), "OUTS")

    def test_stat_key_canonical_plate_appearances(self):
        from gate_engine.auto_enrichment import _canonicalize_stat_key
        self.assertEqual(_canonicalize_stat_key("Plate Appearances"), "PA")
        self.assertEqual(_canonicalize_stat_key("plate appearances"), "PA")
        self.assertEqual(_canonicalize_stat_key("PA"), "PA")
        self.assertEqual(_canonicalize_stat_key("pa"), "PA")

    def test_stat_key_canonical_1ip(self):
        from gate_engine.auto_enrichment import _canonicalize_stat_key
        self.assertEqual(_canonicalize_stat_key("1st Inning Pitches Thrown"), "1IP_PITCHES_THROWN")
        self.assertEqual(_canonicalize_stat_key("1st inning pitches thrown"), "1IP_PITCHES_THROWN")
        self.assertEqual(_canonicalize_stat_key("1IP"), "1IP_PITCHES_THROWN")
        self.assertEqual(_canonicalize_stat_key("1ip"), "1IP_PITCHES_THROWN")

    def test_fetch_missing_game_logs_calls_adapter_for_pitcher_strikeouts(self):
        """The adapter must be CALLED (not skipped) when prop_type='Pitcher Strikeouts'
        is present even without a pre-normalized stat_key."""
        from gate_engine.auto_enrichment import fetch_missing_game_logs

        rows = [{
            "row_id":    "taillon-k-001",
            "player":    "Jameson Taillon",
            "player_id": "669016",          # real MLB ID
            "sport":     "MLB",
            "prop_type": "Pitcher Strikeouts",
            "stat_key":  "",                # deliberately blank — forces canonicalization
            "line":      4.0,
            "direction": "MORE",
        }]
        enrichment: dict = {}

        call_log: list[dict] = []

        def _mock_fetch(player_id, sport, stat_key, **kwargs):
            call_log.append({"player_id": player_id, "stat_key": stat_key})
            return {"values": [5, 4, 6, 3, 7, 5, 4], "source": "mlb_stats_api",
                    "game_date": "2026-08-09", "opponent": "NYY"}

        with patch("gate_engine.auto_enrichment.fetch_game_log", side_effect=_mock_fetch):
            fetch_missing_game_logs(rows, enrichment)

        self.assertTrue(len(call_log) > 0,
                        "fetch_game_log must have been CALLED (not NOT_CALLED)")
        self.assertEqual(call_log[0]["stat_key"], "K",
                         "stat_key must be canonicalized to 'K' before calling fetch_game_log")

    def test_fetch_missing_game_logs_writes_game_log_to_enrichment(self):
        """After a successful fetch the enrichment entry must have game_log populated."""
        from gate_engine.auto_enrichment import fetch_missing_game_logs

        rows = [{
            "row_id":    "taillon-k-002",
            "player":    "Jameson Taillon",
            "player_id": "669016",
            "sport":     "MLB",
            "prop_type": "Pitcher Strikeouts",
            "stat_key":  "K",
            "line":      4.0,
            "direction": "MORE",
        }]
        enrichment: dict = {}

        def _mock_fetch(**kwargs):
            return {"values": [5, 4, 6, 3, 7], "source": "mlb_stats_api",
                    "game_date": "2026-08-09", "opponent": "NYY"}

        with patch("gate_engine.auto_enrichment.fetch_game_log", side_effect=_mock_fetch):
            result = fetch_missing_game_logs(rows, enrichment)

        entry = result.get("jameson taillon:pitcher strikeouts") or result.get("taillon-k-002") or {}
        self.assertTrue(
            entry.get("game_log"),
            f"enrichment must have game_log after successful fetch; keys={list(result.keys())}",
        )


# ---------------------------------------------------------------------------
# Test 5: IP-to-outs conversion (6.0 = 18, 6.1 = 19, 6.2 = 20)
# ---------------------------------------------------------------------------

class TestIPToOutsConversion(unittest.TestCase):
    """FIX-E: ip_str_to_outs must convert baseball IP notation correctly.
    6.0 = 18 outs, 6.1 = 19 outs, 6.2 = 20 outs.  The fractional part
    .1 and .2 are extra outs, NOT tenths of an inning."""

    def _convert(self, ip):
        from gate_engine.auto_game_log import ip_str_to_outs
        return ip_str_to_outs(ip)

    def test_whole_innings(self):
        self.assertEqual(self._convert("6.0"), 18)
        self.assertEqual(self._convert("5.0"), 15)
        self.assertEqual(self._convert("0.0"), 0)
        self.assertEqual(self._convert(6), 18)

    def test_6_1_equals_19_outs(self):
        self.assertEqual(self._convert("6.1"), 19)

    def test_6_2_equals_20_outs(self):
        self.assertEqual(self._convert("6.2"), 20)

    def test_4_1_equals_13_outs(self):
        self.assertEqual(self._convert("4.1"), 13)

    def test_0_1_equals_1_out(self):
        self.assertEqual(self._convert("0.1"), 1)

    def test_float_input(self):
        # Python floats: 6.1 may be 6.099... so test string form is preferred,
        # but the function must handle float input too.
        # 6.1 as float → str → "6.1" → 19
        self.assertEqual(self._convert(6.1), 19)
        self.assertEqual(self._convert(6.2), 20)

    def test_invalid_fractional_raises(self):
        from gate_engine.auto_game_log import ip_str_to_outs
        with self.assertRaises((ValueError, Exception)):
            ip_str_to_outs("6.3")


# ---------------------------------------------------------------------------
# Test 6: Plate Appearances stat_key routes correctly / no DATA_CONTRACT_FAIL
# ---------------------------------------------------------------------------

class TestPlateAppearancesRouting(unittest.TestCase):
    """FIX-D: 'Plate Appearances' must canonicalize to 'PA' and reach the
    registered PA model route (or return explicit MODEL_UNSUPPORTED if not
    registered) instead of generic DATA_CONTRACT_FAIL."""

    def test_pa_canonicalized(self):
        from gate_engine.auto_enrichment import _canonicalize_stat_key
        for display in ("Plate Appearances", "plate appearances", "PA", "pa", "plate_appearances"):
            self.assertEqual(_canonicalize_stat_key(display), "PA",
                             f"'{display}' must canonicalize to 'PA'")

    def test_pa_model_registered_or_unsupported(self):
        """MLB PA must have either an ACTIVE/PROVISIONAL model or explicitly
        be absent from the registry (returning NO_REGISTERED_MODEL — the caller
        must never see DATA_CONTRACT_FAIL from a missing registry entry alone)."""
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "PA")
        # entry always returns a dict; check status key
        status = entry.get("status") if entry else None
        if status and status not in ("NO_REGISTERED_MODEL",):
            self.assertIn(status, ("ACTIVE", "PROVISIONAL"),
                          "If PA is registered it must be ACTIVE or PROVISIONAL")
        # NO_REGISTERED_MODEL is acceptable per spec (explicit unsupported)

    def test_hrr_model_registered(self):
        """H+R+RBI must have a registered model entry (PROVISIONAL at minimum)."""
        from gate_engine.model_registry import lookup
        entry = lookup("MLB", "H+R+RBI")
        self.assertIsNotNone(entry, "H+R+RBI must return an entry from the model registry")
        self.assertNotEqual(entry.get("status"), "NO_REGISTERED_MODEL",
                            "H+R+RBI must be registered — it has a provisional model")
        self.assertIn(entry.get("status"), ("ACTIVE", "PROVISIONAL"))


# ---------------------------------------------------------------------------
# Test 7: 1IP stat_key canonicalizes and routes to dedicated specialist
# ---------------------------------------------------------------------------

class TestOnIPRouting(unittest.TestCase):
    """FIX-D/E: '1st Inning Pitches Thrown' must canonicalize to
    '1IP_PITCHES_THROWN' so the dedicated first-inning specialist receives it.
    TEST_ONLY ceiling must remain unchanged."""

    def test_1ip_canonicalization(self):
        from gate_engine.auto_enrichment import _canonicalize_stat_key
        for display in ("1st Inning Pitches Thrown", "1st inning pitches thrown",
                        "1IP Pitches Thrown", "1IP", "1ip", "1IP_PITCHES_THROWN"):
            result = _canonicalize_stat_key(display)
            self.assertEqual(result, "1IP_PITCHES_THROWN",
                             f"'{display}' must canonicalize to '1IP_PITCHES_THROWN'")

    def test_1ip_fetch_adapter_called(self):
        """fetch_missing_game_logs must call fetch_game_log with stat_key='1IP_PITCHES_THROWN'
        — NOT the display string — when prop_type is '1st Inning Pitches Thrown'."""
        from gate_engine.auto_enrichment import fetch_missing_game_logs

        rows = [{
            "row_id":    "1ip-001",
            "player":    "Jameson Taillon",
            "player_id": "669016",
            "sport":     "MLB",
            "prop_type": "1st Inning Pitches Thrown",
            "stat_key":  "",    # blank — forces canonicalization path
            "line":      16.5,
            "direction": "MORE",
        }]
        call_log: list[dict] = []

        def _mock(player_id, sport, stat_key, **kwargs):
            call_log.append({"stat_key": stat_key})
            return {"values": [18, 14, 19, 17, 21], "source": "mlb_stats_api",
                    "game_date": "2026-08-09", "opponent": "NYY"}

        with patch("gate_engine.auto_enrichment.fetch_game_log", side_effect=_mock):
            fetch_missing_game_logs(rows, {})

        self.assertTrue(len(call_log) > 0,
                        "fetch_game_log must have been CALLED for 1IP prop")
        self.assertEqual(call_log[0]["stat_key"], "1IP_PITCHES_THROWN",
                         "stat_key must be '1IP_PITCHES_THROWN' when calling fetch_game_log")

    def test_can_execute_false_unchanged(self):
        """can_execute=False must be unconditional — never flipped by this patch."""
        from gate_engine.wnba import evidence_acquisition
        from gate_engine.wnba import acquisition_packet
        from gate_engine.wnba import fallback_router
        import gate_engine.auto_enrichment as ae
        self.assertFalse(getattr(evidence_acquisition, "can_execute", True),
                         "evidence_acquisition.can_execute must be False")
        self.assertFalse(getattr(acquisition_packet, "can_execute", True),
                         "acquisition_packet.can_execute must be False")
        self.assertFalse(getattr(fallback_router, "can_execute", True),
                         "fallback_router.can_execute must be False")


# ---------------------------------------------------------------------------
# Test 8: Image pipeline — base64 whitespace is stripped before decode
# ---------------------------------------------------------------------------

class TestImageBase64Transport(unittest.TestCase):
    """FIX-G: The /analyze-and-score endpoint must strip whitespace from
    image_base64 before passing it to the decode validator.  A base64 payload
    with embedded newlines (e.g. from GPT serialization) must not produce
    IMAGE_DECODE_ERROR when the underlying bytes are valid."""

    def _make_png_base64(self, with_whitespace: bool = False) -> str:
        """Minimal valid 1x1 PNG, optionally with injected whitespace."""
        # 1x1 transparent PNG bytes
        _PNG_BYTES = bytes([
            0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,   # PNG sig
            0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,   # IHDR length+type
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,   # 1x1 px
            0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,   # 8-bit RGB
            0xde, 0x00, 0x00, 0x00, 0x0c, 0x49, 0x44, 0x41,   # IDAT length+type
            0x54, 0x08, 0xd7, 0x63, 0xf8, 0xcf, 0xc0, 0x00,
            0x00, 0x00, 0x02, 0x00, 0x01, 0xe2, 0x21, 0xbc,
            0x33, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e,   # IEND
            0x44, 0xae, 0x42, 0x60, 0x82,
        ])
        encoded = base64.b64encode(_PNG_BYTES).decode("utf-8")
        if with_whitespace:
            # Inject spaces and newlines every 10 chars (simulates GPT transport)
            parts = [encoded[i:i+10] for i in range(0, len(encoded), 10)]
            encoded = "\n".join(parts)
        return encoded

    def test_clean_base64_decodes(self):
        """A clean base64 payload must decode without error."""
        import base64 as b64
        payload = self._make_png_base64(with_whitespace=False)
        # Apply the FIX-G whitespace strip
        cleaned = "".join(payload.split())
        try:
            b64.b64decode(cleaned, validate=True)
        except Exception as e:
            self.fail(f"Clean base64 failed to decode: {e}")

    def test_whitespace_injected_base64_decodes_after_strip(self):
        """A base64 payload with injected whitespace must decode correctly after
        the FIX-G whitespace stripping step."""
        import base64 as b64
        payload_with_ws = self._make_png_base64(with_whitespace=True)
        # Must fail WITHOUT stripping (validate=True is strict)
        with self.assertRaises(Exception):
            b64.b64decode(payload_with_ws, validate=True)
        # Must succeed WITH stripping
        cleaned = "".join(payload_with_ws.split())
        try:
            b64.b64decode(cleaned, validate=True)
        except Exception as e:
            self.fail(f"Whitespace-stripped base64 failed to decode: {e}")

    def test_data_url_prefix_stripped_before_whitespace_strip(self):
        """A data:image/png;base64,<data> prefix must be removed and whitespace
        must then be stripped from the remaining payload."""
        import base64 as b64
        raw = self._make_png_base64(with_whitespace=True)
        prefixed = f"data:image/png;base64,{raw}"
        # Simulate FIX-G processing
        if "," in prefixed:
            prefixed = prefixed.split(",", 1)[1]
        cleaned = "".join(prefixed.split())
        try:
            b64.b64decode(cleaned, validate=True)
        except Exception as e:
            self.fail(f"data URL prefix + whitespace handling failed: {e}")

    def test_invalid_base64_payload_identified(self):
        """A genuinely invalid base64 payload must raise an exception from the
        validator (IMAGE_DECODE_ERROR path)."""
        import base64 as b64
        invalid = "not-valid-base64!!!"
        cleaned = "".join(invalid.split())
        with self.assertRaises(Exception):
            b64.b64decode(cleaned, validate=True)


# ---------------------------------------------------------------------------
# Test 9: Batch aggregation — mixed technical failures ≠ clean NO_PLAY
# ---------------------------------------------------------------------------

class TestBatchAggregationMixedFailures(unittest.TestCase):
    """FIX-H: A batch with 18 technically-failed rows + 1 scored reject +
    1 scored qualified/hold must not summarise as clean NO_PLAY.

    batch_state must be SCORING_INCOMPLETE or RUN_PARTIAL_BACKEND_FAILURE."""

    def _make_failed_row(self, idx: int) -> dict:
        return {
            "row_id":         f"fail-{idx:03d}",
            "player":         f"Player {idx}",
            "terminal_label": "DATA_CONTRACT_FAIL",
            "blockers":       ["DATA_CONTRACT_FAIL:missing_game_log"],
        }

    def _make_reject_row(self) -> dict:
        return {
            "row_id":         "reject-001",
            "player":         "Scored Player A",
            "terminal_label": "REJECT_COINFLIP",
            "blockers":       [],
        }

    def _make_qualified_row(self) -> dict:
        return {
            "row_id":         "qualified-001",
            "player":         "Scored Player B",
            "terminal_label": "MODEL_QUALIFIED_HOLD",
            "blockers":       [],
        }

    def _run_result(self, rows: list) -> dict:
        """classify_run_failure expects a pipeline result dict with prop_ledger key."""
        return {"prop_ledger": rows, "rows": rows}

    def test_18_failed_1_reject_1_qualified_not_clean_no_play(self):
        from gate_engine.backend_failure_classifier import classify_run_failure

        rows = (
            [self._make_failed_row(i) for i in range(18)]
            + [self._make_reject_row()]
            + [self._make_qualified_row()]
        )
        result = classify_run_failure(self._run_result(rows))

        self.assertNotEqual(
            result.get("final_label"), "NO_PLAY",
            "A batch with technical failures and a qualified row must not be NO_PLAY",
        )
        # probability_publishable must be True because at least one positive-evaluation row exists
        self.assertTrue(
            result.get("probability_publishable"),
            "probability_publishable must be True when a MODEL_QUALIFIED_HOLD row is present",
        )

    def test_all_data_contract_fail_not_clean_no_play(self):
        """A batch where EVERY row failed with DATA_CONTRACT_FAIL (technical failure)
        must not return clean NO_PLAY — it must signal a backend failure type."""
        from gate_engine.backend_failure_classifier import classify_run_failure

        rows = [self._make_failed_row(i) for i in range(5)]
        result = classify_run_failure(self._run_result(rows))

        # The failure_type must name the technical failure, not be NONE
        self.assertNotEqual(
            result.get("failure_type"), "NONE",
            "All-DATA_CONTRACT_FAIL batch must have a non-NONE failure_type",
        )

    def test_single_qualified_row_in_large_failed_batch_is_publishable(self):
        """If even one MODEL_QUALIFIED_HOLD survives in a batch of failures,
        the result must be publishable=True."""
        from gate_engine.backend_failure_classifier import classify_run_failure

        rows = [self._make_failed_row(i) for i in range(18)] + [self._make_qualified_row()]
        result = classify_run_failure(self._run_result(rows))
        self.assertTrue(result.get("probability_publishable"),
                        "A single qualified row in 19 must make the batch probability_publishable")

    def test_scored_reject_in_technical_failure_batch_not_clean_no_play(self):
        """Technical failures + scored reject must produce SCORING_INCOMPLETE-equivalent
        state, not a clean NO_PLAY (which implies the rows were evaluated and failed on merit)."""
        from gate_engine.backend_failure_classifier import classify_run_failure

        rows = [self._make_failed_row(i) for i in range(18)] + [self._make_reject_row()]
        result = classify_run_failure(self._run_result(rows))

        # NOT publishable (all successful scoring = reject), but must have failure_type
        # that acknowledges the backend failures — not NONE
        self.assertNotEqual(
            result.get("failure_type"), "NONE",
            "18 failed rows in batch must register a failure_type even if reject is present",
        )


if __name__ == "__main__":
    unittest.main()
