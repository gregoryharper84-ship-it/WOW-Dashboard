"""Adversarial offline tests for read-only provisional winner-card rendering."""
import copy
import unittest

from v17.winner_research_presentation import assemble_provisional_winner_card


FREEZE = "2026-11-11T18:00:00Z"


def slate():
    return [
        {
            "event_id": "NHL-101", "sport": "NHL", "home": "Buffalo",
            "away": "Tampa Bay", "starts_at": "2026-11-11T23:00:00Z",
            "settlement_basis": "FULL_GAME_INCLUDING_OT_SO",
            "eligible_outcomes": ["HOME", "AWAY"],
        },
        {
            "event_id": "SOCCER-102", "sport": "SOCCER", "home": "A",
            "away": "B", "starts_at": "2026-11-11T23:30:00Z",
            "settlement_basis": "REGULATION_90_MINUTES",
            "eligible_outcomes": ["HOME", "DRAW", "AWAY"],
        },
    ]


def pick(event_id, selection, **extra):
    return {
        "event_id": event_id, "selection": selection,
        "probability_source": "QUALITATIVE_RESEARCH",
        "evidence_ref": "https://example.org/research-example",
        "observed_at": "2026-11-11T17:00:00Z",
        **extra,
    }


def picks():
    return [pick("NHL-101", "AWAY"), pick("SOCCER-102", "DRAW")]


def build(events=None, primaries=None, watches=()):
    return assemble_provisional_winner_card(
        slate() if events is None else events,
        picks() if primaries is None else primaries,
        frozen_at=FREEZE,
        upset_watch=watches,
    )


class WinnerResearchPresentationTests(unittest.TestCase):
    def test_one_primary_and_upset_does_not_change_it(self):
        inputs = picks()
        frozen = copy.deepcopy(inputs)
        card = build(primaries=inputs, watches=[
            pick("NHL-101", "HOME", reason="Independent goalie uncertainty"),
        ])
        self.assertEqual(inputs, frozen)
        self.assertEqual(card["rows"][0]["primary"]["selection"], "AWAY")
        self.assertEqual(card["rows"][0]["upset_watch"][0]["selection"], "HOME")
        self.assertIs(card["rows"][0]["upset_watch"][0]["changes_primary_selection"], False)
        self.assertEqual(card["rows"][1]["primary"]["selection"], "DRAW")
        self.assertEqual(card["status"], "PROVISIONAL_RESEARCH_ONLY")
        self.assertEqual(card["immutable_receipt_status"], "NOT_PERSISTED")
        self.assertFalse(card["probability_publishable"])
        self.assertFalse(card["rows"][0]["rank_eligible"])

    def test_missing_game_fails_whole_card(self):
        with self.assertRaisesRegex(ValueError, "INCOMPLETE_OFFICIAL_SLATE"):
            build(primaries=picks()[:1])

    def test_duplicate_event_or_primary_fails(self):
        with self.assertRaisesRegex(ValueError, "DUPLICATE_OFFICIAL_EVENT"):
            build(events=slate() + [slate()[0]])
        with self.assertRaisesRegex(ValueError, "DUPLICATE_PRIMARY"):
            build(primaries=picks() + [picks()[0]])

    def test_unknown_event_and_ineligible_selection_fail(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN_PICK_EVENT"):
            build(primaries=picks() + [pick("UNKNOWN", "HOME")])
        bad = picks()
        bad[0]["selection"] = "DRAW"
        with self.assertRaisesRegex(ValueError, "SELECTION_NOT_ELIGIBLE"):
            build(primaries=bad)

    def test_malformed_rows_and_outcomes_fail_with_typed_error(self):
        with self.assertRaisesRegex(ValueError, "ROW_NOT_OBJECT"):
            build(events=[None])
        with self.assertRaisesRegex(ValueError, "ROW_NOT_OBJECT"):
            build(primaries=[None])
        with self.assertRaisesRegex(ValueError, "ROW_NOT_OBJECT"):
            build(watches=[None])
        broken = slate()
        broken[0]["eligible_outcomes"] = [["HOME"], "AWAY"]
        with self.assertRaisesRegex(ValueError, "ELIGIBLE_OUTCOMES_INVALID"):
            build(events=broken)
        broken = slate()
        broken[0]["eligible_outcomes"] = ["HOME", "HOME", "AWAY"]
        with self.assertRaisesRegex(ValueError, "ELIGIBLE_OUTCOMES_INVALID"):
            build(events=broken)

    def test_nhl_full_game_excludes_regulation_draw(self):
        events = slate()
        events[0]["eligible_outcomes"].append("DRAW")
        with self.assertRaisesRegex(ValueError, "NHL_SETTLEMENT_OUTCOMES_INVALID"):
            build(events=events)

    def test_no_silent_model_probability_claim(self):
        for claim in (
            {"probability_source": "FITTED_CERTIFIED", "display_probability": .65},
            {"probability_source": "MARKET_NO_VIG", "display_probability": .65,
             "model_probability": .67},
            {"full_model": True},
        ):
            bad = picks()
            bad[0].update(claim)
            with self.subTest(claim=claim), self.assertRaisesRegex(ValueError, "UNVERIFIED_MODEL_CLAIM"):
                build(primaries=bad)

    def test_numeric_market_provenance_is_labeled_not_calibrated(self):
        good = picks()
        good[0].update(probability_source="MARKET_NO_VIG", display_probability=.54)
        card = build(primaries=good)
        self.assertEqual(card["rows"][0]["primary"]["display_probability"], .54)
        self.assertFalse(card["rows"][0]["primary"]["display_probability_is_calibrated"])
        good[0]["probability_source"] = "QUALITATIVE_RESEARCH"
        with self.assertRaisesRegex(ValueError, "UNSOURCED_NUMERIC_PROBABILITY"):
            build(primaries=good)

    def test_malformed_probabilities_are_rejected(self):
        for numeric in (float("nan"), float("inf"), True, -0.2, 1.2, "0.65"):
            bad = picks()
            bad[0].update(probability_source="EXTERNAL_PROJECTION", display_probability=numeric)
            with self.subTest(numeric=numeric), self.assertRaisesRegex(ValueError, "PROBABILITY_INVALID"):
                build(primaries=bad)

    def test_post_freeze_and_post_kickoff_fail(self):
        bad = picks()
        bad[0]["observed_at"] = "2026-11-11T18:01:00Z"
        with self.assertRaisesRegex(ValueError, "POST_FREEZE"):
            build(primaries=bad)
        with self.assertRaisesRegex(ValueError, "NOT_PREGAME"):
            events = slate()
            events[0]["starts_at"] = "2026-11-11T17:00:00Z"
            build(events=events)
        bad = picks()
        bad[0]["observed_at"] = "2026-11-11T17:00:00"
        with self.assertRaisesRegex(ValueError, "TIMEZONE_REQUIRED"):
            build(primaries=bad)

    def test_upset_requires_distinct_eligible_side_and_reason(self):
        for watch, expected in (
            (pick("NHL-101", "AWAY", reason="not an upset"), "UPSET_ALTERNATIVE_INVALID"),
            (pick("NHL-101", "DRAW", reason="not an outcome"), "UPSET_ALTERNATIVE_INVALID"),
            (pick("NHL-101", "HOME", reason=""), "UPSET_REASON_REQUIRED"),
        ):
            with self.subTest(watch=watch), self.assertRaisesRegex(ValueError, expected):
                build(watches=[watch])
        with self.assertRaisesRegex(ValueError, "DUPLICATE_UPSET_ALTERNATIVE"):
            watch = pick("NHL-101", "HOME", reason="goalie")
            build(watches=[watch, watch])

    def test_source_only_is_never_fitted_probability(self):
        with self.assertRaisesRegex(ValueError, "NUMERIC_PROBABILITY_REQUIRED"):
            bad = picks()
            bad[0]["probability_source"] = "MARKET_NO_VIG"
            build(primaries=bad)


if __name__ == "__main__":
    unittest.main()
