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
            "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT",
            "eligible_outcomes": ["HOME", "AWAY"],
        },
        {
            "event_id": "SOCCER-102", "sport": "SOCCER", "home": "A",
            "away": "B", "starts_at": "2026-11-11T23:30:00Z",
            "settlement_basis": "REGULATION",
            "eligible_outcomes": ["HOME", "DRAW", "AWAY"],
            "draw_pick_rule": "DRAW_PICK_ALLOWED",
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


def numeric_context(**overrides):
    """Unverified caller-declared NHL source snapshot, not a trusted receipt."""
    values = {
        "event_id": "NHL-101", "source_kind": "MARKET_NO_VIG",
        "selection": "AWAY", "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT",
        "market_type": "FULL_GAME_MONEYLINE", "market_outcomes": ["HOME", "AWAY"],
        "observed_at": "2026-11-11T17:00:00Z",
        "source_snapshot_id": "candidate-snapshot-001", "de_vig_method": "POWER",
    }
    values.update(overrides)
    return values


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
        self.assertFalse(card["can_execute"])
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

    def test_nhl_regulation_or_alias_is_rejected_not_fully_settled(self):
        for basis in ("REGULATION", "FULL_GAME", "FULL_GAME_INCLUDING_OT_SO", "", "NHL_3_WAY"):
            invalid = slate()
            invalid[0]["settlement_basis"] = basis
            with self.subTest(basis=basis), self.assertRaisesRegex(
                ValueError, "WINNER_CARD_NHL_SETTLEMENT_BASIS_INVALID|WINNER_CARD_SETTLEMENT_BASIS_REQUIRED"
            ):
                build(events=invalid)
        self.assertEqual(
            build()["rows"][0]["settlement_basis"],
            "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT",
        )

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
        good[0].update(probability_source="MARKET_NO_VIG", display_probability=.54,
                       numeric_source_context=numeric_context())
        card = build(primaries=good)
        self.assertEqual(card["rows"][0]["primary"]["display_probability"], .54)
        self.assertFalse(card["rows"][0]["primary"]["display_probability_is_calibrated"])
        self.assertEqual(card["rows"][0]["primary"]["numeric_source_context_status"],
                         "CALLER_DECLARED_MATCH_NOT_INDEPENDENTLY_AUTHENTICATED")
        good[0]["probability_source"] = "QUALITATIVE_RESEARCH"
        with self.assertRaisesRegex(ValueError, "UNSOURCED_NUMERIC_PROBABILITY"):
            build(primaries=good)

    def test_malformed_probabilities_are_rejected(self):
        for numeric in (float("nan"), float("inf"), True, -0.2, 1.2, "0.65", 10**10000):
            bad = picks()
            bad[0].update(probability_source="EXTERNAL_PROJECTION", display_probability=numeric)
            with self.subTest(numeric=numeric), self.assertRaisesRegex(ValueError, "PROBABILITY_INVALID"):
                build(primaries=bad)

    def test_supported_settlements_and_draw_rules_are_strict(self):
        for sport, basis in (("NBA", "FULL_GAME_INCLUDING_OVERTIME"),
                             ("NFL", "FULL_GAME_INCLUDING_OVERTIME"),
                             ("NCAAB", "FULL_GAME_INCLUDING_OVERTIME"),
                             ("NCAAF", "FULL_GAME_INCLUDING_OVERTIME")):
            events = slate()
            events[0].update(sport=sport, settlement_basis=basis)
            self.assertEqual(build(events=events)["rows"][0]["sport"], sport)
            events[0]["settlement_basis"] = "REGULATION"
            with self.assertRaisesRegex(ValueError, "SETTLEMENT_BASIS_INVALID"):
                build(events=events)
            events[0]["settlement_basis"] = basis
            events[0]["eligible_outcomes"] = ["HOME", "DRAW", "AWAY"]
            with self.assertRaisesRegex(ValueError, "SETTLEMENT_OUTCOMES_INVALID"):
                build(events=events)

        unsupported = slate()
        unsupported[0]["sport"] = "TENNIS"
        with self.assertRaisesRegex(ValueError, "SPORT_SETTLEMENT_UNSUPPORTED"):
            build(events=unsupported)
        soccer = slate()
        soccer[1].pop("draw_pick_rule")
        with self.assertRaisesRegex(ValueError, "SOCCER_OUTCOME_RULES_INVALID"):
            build(events=soccer)
        soccer = slate()
        soccer[1]["eligible_outcomes"] = ["HOME", "AWAY"]
        soccer[1]["draw_pick_rule"] = "DRAW_COUNTS_AS_INCORRECT"
        participant_card = build(events=soccer, primaries=[picks()[0], pick("SOCCER-102", "HOME")])
        self.assertEqual(participant_card["rows"][1]["primary"]["selection"], "HOME")
        soccer[1]["eligible_outcomes"] = ["DRAW"]
        with self.assertRaisesRegex(ValueError, "SOCCER_OUTCOME_RULES_INVALID"):
            build(events=soccer)

    def test_numeric_market_context_binds_event_side_basis_outcomes_method(self):
        base = picks()
        base[0].update(
            probability_source="MARKET_NO_VIG", display_probability=.54,
            numeric_source_context=numeric_context(),
        )
        self.assertEqual(build(primaries=base)["rows"][0]["primary"]["display_probability"], .54)
        mutations = (
            ({"event_id": "WRONG"}, "MARKET_MISMATCH"),
            ({"selection": "HOME"}, "MARKET_MISMATCH"),
            ({"source_kind": "EXTERNAL_PROJECTION"}, "MARKET_MISMATCH"),
            ({"settlement_basis": "REGULATION"}, "MARKET_MISMATCH"),
            ({"market_type": "REGULATION_1X2"}, "MARKET_MISMATCH"),
            ({"market_outcomes": ["HOME", "AWAY", "DRAW"]}, "MARKET_MISMATCH"),
            ({"market_outcomes": [["HOME"], "AWAY"]}, "MARKET_MISMATCH"),
            ({"market_outcomes": ["HOME", "HOME"]}, "MARKET_MISMATCH"),
            ({"observed_at": "2026-11-11T16:59:59Z"}, "TIMESTAMP_MISMATCH"),
            ({"de_vig_method": "MULTIPLICATIVE"}, "METHOD_INVALID"),
            ({"source_snapshot_id": ""}, "SOURCE_SNAPSHOT_ID_REQUIRED"),
        )
        for override, blocker in mutations:
            bad = copy.deepcopy(base)
            bad[0]["numeric_source_context"] = numeric_context(**override)
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, blocker):
                build(primaries=bad)
        bad = copy.deepcopy(base)
        bad[0].pop("numeric_source_context")
        with self.assertRaisesRegex(ValueError, "SOURCE_CONTEXT_REQUIRED"):
            build(primaries=bad)

    def test_stale_numeric_evidence_does_not_appear_as_current(self):
        stale = picks()
        stale[0].update(probability_source="MARKET_NO_VIG",
                        display_probability=.54,
                        observed_at="2026-11-11T15:59:00Z",
                        numeric_source_context=numeric_context(observed_at="2026-11-11T15:59:00Z"))
        with self.assertRaisesRegex(ValueError, "NUMERIC_SOURCE_STALE"):
            build(primaries=stale)

        older_projection = picks()
        older_projection[0].update(
            probability_source="EXTERNAL_PROJECTION",
            display_probability=.56, observed_at="2026-11-11T11:59:00Z",
            numeric_source_context=numeric_context(
                source_kind="EXTERNAL_PROJECTION", observed_at="2026-11-11T11:59:00Z"),
        )
        with self.assertRaisesRegex(ValueError, "NUMERIC_SOURCE_STALE"):
            build(primaries=older_projection)

        current_projection = picks()
        current_projection[0].update(
            probability_source="EXTERNAL_PROJECTION", display_probability=.56,
            numeric_source_context=numeric_context(source_kind="EXTERNAL_PROJECTION"),
        )
        self.assertFalse(build(primaries=current_projection)["rows"][0]["primary"]["display_probability_is_calibrated"])

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
