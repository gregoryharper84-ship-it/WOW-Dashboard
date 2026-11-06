"""Contract tests for optional third-party board discovery (no network or secrets)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone, timedelta
import unittest

from v17.authorized_prop_board_discovery import (
    PropFeedHold, discover_authorized_prop_board, normalize_page, normalize_projection,
)

NOW = datetime(2026, 11, 6, 19, 0, tzinfo=timezone.utc)


def sample(**overrides):
    row = {
        "id": "pp_1", "source": "prizepicks", "is_demo": False,
        "league": "NBA", "player": {"id": "p_1", "name": "Example Player", "team": "BOS"},
        "event": {"id": "source_game_1", "opponent": "NYK", "starts_at": "2026-11-06T23:00:00Z"},
        "market": {"stat": "Points", "period": "Full game"},
        "line": 22.5,
        "selections": [
            {"side": "more", "available": True, "tier": "standard"},
            {"side": "less", "available": True, "tier": "standard"},
        ],
        "status": "active", "observed_at": "2026-11-06T18:59:00Z",
    }
    row.update(overrides)
    return row


def page(rows, cursor=None, health="fresh"):
    return {
        "data": rows,
        "meta": {
            "mode": "live", "source_status": health,
            "next_cursor": cursor,
        },
    }


class AuthorizedPropBoardDiscoveryTests(unittest.TestCase):
    def test_exact_more_and_less_are_distinct_research_leads(self):
        result = normalize_page(page([sample()]), league="NBA", now=NOW)
        self.assertEqual(result["source_rows"], 1)
        self.assertEqual([r["direction"] for r in result["directional_candidates"]], ["MORE", "LESS"])
        for row in result["directional_candidates"]:
            self.assertEqual(row["exact_display_line"], "22.5")
            self.assertEqual(row["controlling_route"], "WOW_PROP_LANE")
            self.assertIsNone(row["official_event_id"])
            self.assertIsNone(row["model_probability"])
            self.assertFalse(row["probability_publishable"])
            self.assertFalse(row["can_execute"])

    def test_demon_more_only_does_not_invent_less_or_payout(self):
        row = sample(selections=[
            {"side": "more", "available": True, "tier": "demon"},
            {"side": "less", "available": False, "tier": "unknown"},
        ])
        results = normalize_projection(row, league="NBA", now=NOW)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["offer_type"], "demon")
        self.assertEqual(results[0]["direction"], "MORE")
        self.assertFalse(results[0]["pricing_independently_verified"])

    def test_demo_and_started_and_stale_are_blocked(self):
        for value, code in [
            ({"is_demo": True}, "PROP_FEED_DEMO_OR_PROVENANCE_UNVERIFIED"),
            ({"event": {"starts_at": "2026-11-06T18:00:00Z"}}, "EVENT_ALREADY_STARTED"),
            ({"observed_at": "2026-11-06T18:00:00Z"}, "PROP_FEED_ROW_STALE"),
            ({"line": "NaN"}, "PROP_FEED_EXACT_LINE_INVALID"),
            ({"selections": []}, "PROP_FEED_NO_AVAILABLE_DIRECTIONS"),
        ]:
            with self.subTest(value=value), self.assertRaises(PropFeedHold) as cm:
                normalize_projection(sample(**value), league="NBA", now=NOW)
            self.assertEqual(cm.exception.code, code)

    def test_unhealthy_page_not_trusted(self):
        with self.assertRaises(PropFeedHold) as cm:
            normalize_page(page([sample()], health="stale"), league="NBA", now=NOW)
        self.assertEqual(cm.exception.code, "PROP_FEED_SOURCE_STALE_OR_UNHEALTHY")

    def test_requires_operator_authorization_and_key(self):
        with self.assertRaises(PropFeedHold) as cm:
            discover_authorized_prop_board(sport="NBA", authorized=False, api_key="test", now=NOW)
        self.assertEqual(cm.exception.code, "PROP_FEED_ACCOUNT_USE_NOT_AUTHORIZED")
        with self.assertRaises(PropFeedHold) as cm:
            discover_authorized_prop_board(sport="NBA", authorized=True, api_key="", now=NOW)
        self.assertEqual(cm.exception.code, "PROP_FEED_CREDENTIAL_MISSING")

    def test_vendor_undocumented_sports_do_not_claim_live_feed_coverage(self):
        for sport in ("MLB", "NCAAB"):
            with self.subTest(sport=sport), self.assertRaises(PropFeedHold) as cm:
                discover_authorized_prop_board(
                    sport=sport, authorized=True, api_key="test", now=NOW,
                )
            self.assertEqual(cm.exception.code, "PROP_FEED_SPORT_NOT_SUPPORTED")

    def test_complete_pagination_with_exact_once_reconciliation(self):
        calls = []
        def fetch(*, key, league, cursor):
            calls.append((league, cursor))
            if cursor is None:
                return page([sample()], cursor="next")
            return page([sample(id="pp_2", line=23.5, selections=[
                {"side": "more", "available": True, "tier": "goblin"}
            ])])
        receipt = discover_authorized_prop_board(
            sport="NBA", authorized=True, api_key="secret",
            fetch_page=fetch, now=NOW, max_pages=2,
        )
        self.assertEqual(calls, [("NBA", None), ("NBA", "next")])
        self.assertEqual(receipt["source_rows_received"], 2)
        self.assertEqual(receipt["unique_base_props"], 2)
        self.assertEqual(receipt["directional_candidates_count"], 3)
        self.assertTrue(receipt["source_coverage_complete_for_requested_sport"])
        self.assertEqual(receipt["automated_score_attempts"], 0)

    def test_page_budget_exhaustion_is_not_complete(self):
        def fetch(*, key, league, cursor):
            return page([sample()], cursor="next")
        receipt = discover_authorized_prop_board(
            sport="NBA", authorized=True, api_key="secret",
            fetch_page=fetch, now=NOW, max_pages=1,
        )
        self.assertEqual(receipt["blockers"], ["PROP_FEED_PAGE_BUDGET_EXHAUSTED"])
        self.assertFalse(receipt["source_coverage_complete_for_requested_sport"])

    def test_blocked_rows_accounted_without_dropping_other_sides(self):
        receipt = discover_authorized_prop_board(
            sport="NBA", authorized=True, api_key="secret",
            fetch_page=lambda **kwargs: page([
                sample(), sample(id="pp_2", is_demo=True)
            ]), now=NOW,
        )
        self.assertEqual(receipt["source_rows_received"], 2)
        self.assertEqual(receipt["unique_base_props"], 1)
        self.assertEqual(receipt["source_rows_blocked"], 1)
        self.assertEqual(receipt["directional_candidates_count"], 2)
        self.assertTrue(receipt["source_coverage_complete_for_requested_sport"])

    def test_no_provenance_promotion_from_cross_sport_alias(self):
        row = sample(league="CFB")
        receipt = discover_authorized_prop_board(
            sport="NCAAF", authorized=True, api_key="secret",
            fetch_page=lambda **kwargs: page([row]), now=NOW,
        )
        self.assertEqual(receipt["directional_candidates"][0]["sport"], "NCAAF")
        self.assertIsNone(receipt["directional_candidates"][0]["official_player_id"])
        self.assertFalse(receipt["source_license_independently_verified"])

    def test_invalid_direction_cannot_default_to_more(self):
        row = sample(selections=[{"side": "over", "available": True, "tier": "standard"}])
        p = normalize_page(page([row]), league="NBA", now=NOW)
        self.assertEqual(p["directional_candidates"], [])
        self.assertEqual(p["blocked_source_rows"][0]["typed_blocker"], "PROP_FEED_DIRECTION_UNRESOLVED")


    def test_readonly_route_idempotent_and_blocked_without_authorization(self):
        from unittest.mock import patch
        from fastapi import Depends, FastAPI
        from v17.authorized_prop_board_route import install_authorized_prop_board_route

        app = FastAPI()
        dependency = Depends(lambda: None)
        install_authorized_prop_board_route(app, auth_dependency=dependency)
        install_authorized_prop_board_route(app, auth_dependency=dependency)
        matches = [route for route in app.router.routes
                   if getattr(route, "path", "") == "/v17/prop-board/authorized-discovery"]
        self.assertEqual(len(matches), 1)
        with patch.dict("os.environ", {"WOW_V17_PROP_FEED_ACCOUNT_USE_AUTHORIZED": "0"}):
            blocked = matches[0].endpoint(sport="NBA", max_pages=1)
        self.assertEqual(blocked["typed_blocker"], "PROP_FEED_ACCOUNT_USE_NOT_AUTHORIZED")
        self.assertFalse(blocked["probability_publishable"])
        self.assertFalse(blocked["can_execute"])


if __name__ == "__main__":
    unittest.main()
