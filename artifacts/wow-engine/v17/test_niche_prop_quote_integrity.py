"""Pure signed-capture contract tests; all sources and IDs are synthetic."""
from copy import deepcopy
import hashlib
import pytest

from v17.niche_prop_quote_integrity import (
    CaptureHold, seal_capture_for_trusted_collector,
    verify_capture, verify_lineup_payout,
)

SECRET = b"synthetic-signing-secret-64-characters" * 2
APPROVED = {"licensed-synthetic-data-provider": "synthetic-permission-receipt"}
AS_OF = "2026-11-23T20:00:00Z"
START = "2026-11-23T22:00:00Z"


def offer(idx=1, *, combo=False):
    return {
        "kind": "PROP_OFFER", "platform": "PRIZEPICKS",
        "sport": "NFL" if combo else "NBA",
        "settlement_basis": "FIRST_QUARTER_OFFICIAL" if combo else "FULL_GAME_INCL_OT",
        "source_id": "licensed-synthetic-data-provider",
        "rights_receipt_id": "synthetic-permission-receipt",
        "source_snapshot_sha256": hashlib.sha256(f"source-{idx}".encode()).hexdigest(),
        "offer_id": f"synthetic-offer-{idx}",
        "official_event_id": f"synthetic-event-{idx}",
        "official_player_ids": [f"synthetic-home-k-{idx}", f"synthetic-away-k-{idx}"] if combo else [f"synthetic-nba-player-{idx}"],
        "combo_team_ids": ["HOME", "AWAY"] if combo else [],
        "home_team_id": "HOME", "away_team_id": "AWAY",
        "category": "NFL_1Q_TWO_KICKER_FG_OVER_0_5" if combo else "NBA_LOW_VOLUME_UNDER",
        "stat_type": "FIELD_GOALS_MADE_COMBO" if combo else "FREE_THROW_ATTEMPTS",
        "market_period": "FIRST_QUARTER" if combo else "FULL_GAME",
        "side": "MORE" if combo else "LESS",
        "exact_line": 0.5 if combo else 5.5,
        "projection_type": "STANDARD", "status": "ACTIVE",
        "captured_at_utc": "2026-11-23T19:58:00Z",
        "event_start_utc": START,
    }


def verify(payload, *, expected=None, authorized=APPROVED):
    envelope = seal_capture_for_trusted_collector(payload, SECRET)
    return verify_capture(envelope, SECRET, expected or payload,
                          as_of_utc=AS_OF, approved_sources=authorized)


def test_internal_signer_proves_integrity_not_prizepicks_external_attestation():
    payload = offer()
    original = deepcopy(payload)
    result = verify(payload)
    assert payload == original
    assert result["status"] == "INTERNAL_CAPTURE_INTEGRITY_ONLY"
    assert result["source_content_independently_verified"] is False
    assert result["provider_rights_independently_verified"] is False
    assert result["quote_integrity_verified"] is True
    assert not result["can_execute"]
    assert len(result["offer_binding_digest"]) == 64


def test_single_offered_combo_requires_opposing_kickers_and_exact_threshold():
    good = offer(combo=True)
    assert verify(good)["quote_integrity_verified"]
    bad = deepcopy(good)
    bad["combo_team_ids"] = ["HOME", "HOME"]
    with pytest.raises(CaptureHold, match="KICKER_COMBO_TEAM"):
        verify(bad)
    bad = deepcopy(good)
    bad["official_player_ids"] = [good["official_player_ids"][0]] * 2
    with pytest.raises(CaptureHold, match="KICKER_COMBO_PLAYER"):
        verify(bad)


def test_tampered_receipt_unapproved_source_and_wrong_line_hold():
    signed = seal_capture_for_trusted_collector(offer(), SECRET)
    signed["payload"]["exact_line"] = 2.5
    with pytest.raises(CaptureHold, match="SIGNATURE"):
        verify_capture(signed, SECRET, offer(), as_of_utc=AS_OF, approved_sources=APPROVED)
    with pytest.raises(CaptureHold, match="SOURCE_RIGHTS"):
        verify(offer(), authorized={})
    altered = dict(offer(), exact_line=6.5)
    with pytest.raises(CaptureHold, match="EXACT_OFFER_IDENTITY"):
        verify(altered, expected=offer())
    with pytest.raises(CaptureHold, match="SIGNING_SECRET"):
        seal_capture_for_trusted_collector(offer(), b"short")
    wrong_stat = dict(offer(), stat_type="POINTS")
    with pytest.raises(CaptureHold, match="NICHE_ROUTE"):
        verify(wrong_stat)
    wrong_sport = dict(offer(), sport="NFL")
    with pytest.raises(CaptureHold, match="NICHE_ROUTE"):
        verify(wrong_sport)


@pytest.mark.parametrize("field,value,code", [
    ("captured_at_utc", "2026-11-23T19:35:00Z", "STALE"),
    ("captured_at_utc", "2026-11-23T20:01:00Z", "FUTURE"),
    ("status", "CLOSED", "NOT_ACTIVE"),
    ("projection_type", "UNKNOWN", "PROJECTION"),
    ("exact_line", float("inf"), "LINE"),
])
def test_stale_future_closed_malformed_and_unknown_tier_rejected(field,value,code):
    with pytest.raises(CaptureHold, match=code):
        verify(dict(offer(), **{field: value}))


def test_lineup_receipt_binds_offers_payout_and_latest_capture():
    a, b = offer(1), offer(2)
    signed_a = seal_capture_for_trusted_collector(a, SECRET)
    signed_b = seal_capture_for_trusted_collector(b, SECRET)
    verify_capture(signed_a, SECRET, a, as_of_utc=AS_OF, approved_sources=APPROVED)
    verify_capture(signed_b, SECRET, b, as_of_utc=AS_OF, approved_sources=APPROVED)
    payout = {
        "kind": "LINEUP_PAYOUT", "platform": "PRIZEPICKS",
        "source_id": "licensed-synthetic-data-provider",
        "rights_receipt_id": "synthetic-permission-receipt",
        "source_snapshot_sha256": hashlib.sha256(b"payout").hexdigest(),
        "structure": "FLEX", "leg_count": 2,
        "ordered_offer_binding_digests": [
            signed_a["payload_digest"], signed_b["payload_digest"]
        ],
        "payout_by_hits": {"0": 0.0, "1": 0.0, "2": 3.0},
        "captured_at_utc": "2026-11-23T19:59:00Z",
        "earliest_event_start_utc": START,
    }
    signed_payout = seal_capture_for_trusted_collector(payout, SECRET)
    receipt = verify_lineup_payout(signed_payout, SECRET,
                                   [signed_a, signed_b], as_of_utc=AS_OF,
                                   approved_sources=APPROVED)
    assert receipt["gross_multiplier_if_all_correct"] == 3.0
    assert receipt["payout_by_hits"]["1"] == 0.0
    assert receipt["source_content_independently_verified"] is False
    bad = dict(payout, ordered_offer_binding_digests=payout["ordered_offer_binding_digests"][::-1])
    with pytest.raises(CaptureHold, match="LINEUP_BINDING"):
        verify_lineup_payout(seal_capture_for_trusted_collector(bad, SECRET),
                             SECRET, [signed_a, signed_b], as_of_utc=AS_OF,
                             approved_sources=APPROVED)
    bad = dict(payout, captured_at_utc="2026-11-23T19:57:00Z")
    with pytest.raises(CaptureHold, match="LINEUP_CAPTURE_BEFORE_OFFERS"):
        verify_lineup_payout(seal_capture_for_trusted_collector(bad, SECRET),
                             SECRET, [signed_a, signed_b], as_of_utc=AS_OF,
                             approved_sources=APPROVED)
