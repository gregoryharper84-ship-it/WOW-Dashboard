from market import MarketQuote
from prop_market_audit import ADJACENT_LINE, EXACT_LINE, MARKET_IDENTITY_MISMATCH, audit_candidate_market


def _quote(side: str, line: float, *, event_id: str = "game-1") -> MarketQuote:
    return MarketQuote(
        side=side,
        american_odds=-110,
        line=line,
        settlement_basis="FULL_GAME_PLAYER_STAT",
        retrieved_at="2026-09-02T03:00:00+00:00",
        participant="Sean Manaea",
        stat="STRIKEOUTS",
        period="FULL_GAME",
        event_id=event_id,
        provider="SPORTSBOOK_TEST",
    )


def test_exact_two_way_pair_retains_no_vig_authority():
    audit = audit_candidate_market(
        event_id="game-1",
        participant="Sean Manaea",
        stat="STRIKEOUTS",
        period="FULL_GAME",
        line=2.5,
        settlement_rule=None,
        side_a=_quote("MORE", 2.5),
        side_b=_quote("LESS", 2.5),
    )
    assert audit.status == "PASS"
    assert audit.evidence_class == EXACT_LINE
    assert audit.line_distance == 0.0
    assert audit.can_use_two_way_no_vig is True
    assert audit.exact_line_market_confirmed is True
    assert audit.side_a is not None and audit.side_b is not None
    assert audit.reference_weight == 1.0


def test_adjacent_4_5_market_cannot_confirm_2_5_candidate():
    audit = audit_candidate_market(
        event_id="game-1",
        participant="Sean Manaea",
        stat="STRIKEOUTS",
        period="FULL_GAME",
        line=2.5,
        settlement_rule=None,
        side_a=_quote("MORE", 4.5),
        side_b=_quote("LESS", 4.5),
    )
    assert audit.status == "HOLD"
    assert audit.evidence_class == ADJACENT_LINE
    assert audit.line_distance == 2.0
    assert audit.can_use_two_way_no_vig is False
    assert audit.exact_line_market_confirmed is False
    # Operative model inputs are deliberately empty so 4.5 cannot become the
    # exact-line no-vig prior for a 2.5 promotion.
    assert audit.side_a is None and audit.side_b is None
    # Same-market prices remain available only as reference context.
    assert audit.reference_side_a is not None and audit.reference_side_b is not None
    assert audit.reference_weight < 1.0
    assert audit.approval_ceiling == "MODEL_QUALIFIED_HOLD"
    assert audit.can_execute is False


def test_adjacent_reference_does_not_relabel_wrong_event_as_adjacent():
    audit = audit_candidate_market(
        event_id="game-1",
        participant="Sean Manaea",
        stat="STRIKEOUTS",
        period="FULL_GAME",
        line=2.5,
        settlement_rule=None,
        side_a=_quote("MORE", 4.5, event_id="wrong-game"),
        side_b=_quote("LESS", 4.5, event_id="wrong-game"),
    )
    assert audit.status == "HOLD"
    assert audit.blocker == MARKET_IDENTITY_MISMATCH
    assert audit.reference_side_a is None and audit.reference_side_b is None
    assert audit.can_use_two_way_no_vig is False


def test_adjacent_market_metadata_never_changes_sporting_probability():
    # The audit object intentionally owns only market-evidence authority. There
    # is no sporting/model probability field to mutate, preventing a market-only
    # failure from erasing an already-completed fitted-model probability.
    audit = audit_candidate_market(
        event_id="game-1",
        participant="Sean Manaea",
        stat="STRIKEOUTS",
        period="FULL_GAME",
        line=2.5,
        settlement_rule=None,
        side_a=_quote("MORE", 4.5),
        side_b=_quote("LESS", 4.5),
    )
    assert audit.evidence_class == ADJACENT_LINE
    assert not hasattr(audit, "calibrated_probability")
    assert audit.can_execute is False
