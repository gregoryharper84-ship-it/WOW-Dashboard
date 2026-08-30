from market import MarketQuote, resolve_market_prior
from prop_market_audit import MARKET_IDENTITY_MISMATCH, audit_candidate_market
from prop_settlement import LINE_MISMATCH, NO_VIG_UNAVAILABLE, SettlementRule


def _rule():
    return SettlementRule(
        settlement_basis="FULL_GAME_STAT",
        boundary_operator="GT",
        equality_treatment="PUSH",
        void_treatment="RETURN_STAKE",
        rule_version="TEST_V1",
        source="UNIT_TEST",
    )


def _quote(side: str, *, line=5.5, event_id="MLB:CIN-CHC", participant="Chase Burns", stat="PITCHER_STRIKEOUTS", period="FULL_GAME"):
    return MarketQuote(
        side=side,
        american_odds=-110 if side == "MORE" else -105,
        line=line,
        settlement_basis="FULL_GAME_STAT",
        retrieved_at="2026-08-30T22:00:00+00:00",
        participant=participant,
        stat=stat,
        period=period,
        event_id=event_id,
    )


def _audit(a, b):
    return audit_candidate_market(
        event_id="MLB:CIN-CHC",
        participant="Chase Burns",
        stat="PITCHER_STRIKEOUTS",
        period="FULL_GAME",
        line=5.5,
        settlement_rule=_rule(),
        side_a=a,
        side_b=b,
    )


def test_exact_two_way_pair_passes_candidate_audit_and_novig_normalizes():
    audit = _audit(_quote("MORE"), _quote("LESS"))
    assert audit.status == "PASS"
    prior = resolve_market_prior(
        "MORE", audit.side_a, audit.side_b,
        as_of="2026-08-30T22:02:00+00:00",
    )
    assert prior.market_prior_available is True
    other = resolve_market_prior(
        "LESS", audit.side_a, audit.side_b,
        as_of="2026-08-30T22:02:00+00:00",
    )
    assert prior.market_prior_probability + other.market_prior_probability == 1.0


def test_line_mismatch_quarantines_both_quotes():
    audit = _audit(_quote("MORE", line=6.5), _quote("LESS", line=6.5))
    assert audit.status == "HOLD"
    assert audit.blocker == LINE_MISMATCH
    assert audit.side_a is None and audit.side_b is None


def test_identity_mismatch_quarantines_both_quotes():
    audit = _audit(_quote("MORE", participant="Different Player"), _quote("LESS", participant="Different Player"))
    assert audit.status == "HOLD"
    assert audit.blocker == MARKET_IDENTITY_MISMATCH
    assert audit.side_a is None and audit.side_b is None


def test_one_sided_market_cannot_claim_no_vig():
    audit = _audit(_quote("MORE"), None)
    assert audit.status == "HOLD"
    assert audit.blocker == NO_VIG_UNAVAILABLE
    assert audit.can_use_two_way_no_vig is False
