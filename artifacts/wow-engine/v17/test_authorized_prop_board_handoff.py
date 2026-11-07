"""Offline regression for third-party discovery -> official pregame prop handoff."""
from datetime import datetime, timezone
from copy import deepcopy

from v17.authorized_prop_board_discovery import (
    PROVIDER, SCHEMA, normalize_page,
)
from v17.authorized_prop_board_handoff import compile_authorized_prop_handoff as _compile_authorized_prop_handoff


def compile_authorized_prop_handoff(*args, **kwargs):
    # Trusted, server-side exact sport/stat capability fixture. Never infer
    # route support from the third-party offer or an untrusted assertion.
    kwargs.setdefault("scorer_compatibility", lambda sport, stat: sport == "NBA" and stat == "POINTS")
    return _compile_authorized_prop_handoff(*args, **kwargs)

NOW = datetime(2026, 11, 6, 19, 0, tzinfo=timezone.utc)


def board():
    raw = {
        "id": "pp_12", "source": "prizepicks", "is_demo": False,
        "league": "NBA", "player": {"id": "pp_p8", "name": "Test Player", "team": "BOS"},
        # Real provider documents no official event ID in this object.
        "event": {"opponent": "NYK", "starts_at": "2026-11-06T23:00:00Z"},
        "market": {"stat": "Points", "period": "Full game"},
        "line": 22.5, "status": "active", "observed_at": "2026-11-06T18:58:00Z",
        "selections": [
            {"side": "more", "available": True, "tier": "standard"},
            {"side": "less", "available": True, "tier": "standard"},
        ],
    }
    result = normalize_page({
        "meta": {"mode": "live", "source_status": "fresh", "next_cursor": None},
        "data": [raw],
    }, league="NBA", now=NOW)
    return {
        "schema": SCHEMA, "source_provider": PROVIDER,
        "directional_candidates": result["directional_candidates"],
        "source_coverage_complete_for_requested_sport": True,
        "blockers": [], "can_execute": False,
    }


def official(row):
    return {
        "official_event_id": "NBA:official-game-1",
        "official_player_id": "NBA:official-player-1",
        "sport": "NBA", "player": "Test Player",
        "stat_type": "POINTS", "stat_display": "Points",
        "period": "Full game", "team": "BOS",
        "opponent": "NYK", "event_start_time": "2026-11-06T23:00:00+00:00",
        "event_status": "SCHEDULED", "official_source": "NBA_OFFICIAL_SCHEDULE",
        "verified_at": "2026-11-06T18:59:00Z",
    }


def fresh_offer(row):
    return {
        "source_provider": PROVIDER,
        "source_projection_id": row["source_projection_id"],
        "direction": row["direction"],
        "offer_type": row["offer_type"],
        "exact_display_line": row["exact_display_line"],
        "available": True,
        "source_observed_at": "2026-11-06T18:59:00Z",
    }


def test_absent_entitlement_never_prepares_action_inputs():
    out = compile_authorized_prop_handoff(
        board(), now=NOW, source_use_permitted=False,
        official_resolver=official, exact_offer_refresh=fresh_offer,
    )
    assert len(out["scoring_input_proposals"]) == 0
    assert len(out["blocked_rows"]) == 2
    assert {r["typed_blocker"] for r in out["blocked_rows"]} == {
        "PROP_HANDOFF_SOURCE_USE_NOT_PERMITTED"
    }
    assert out["can_execute"] is False


def test_missing_server_side_resolver_fails_closed():
    out = compile_authorized_prop_handoff(
        board(), now=NOW, source_use_permitted=True,
        exact_offer_refresh=fresh_offer,
    )
    assert out["received_directional_candidates"] == 2
    assert len(out["blocked_rows"]) == 2
    assert out["reconciliation_valid"] is True
    assert {r["typed_blocker"] for r in out["blocked_rows"]} == {
        "PROP_IDENTITY_OFFICIAL_RESOLVER_UNAVAILABLE"
    }


def test_exact_verified_input_proposals_do_not_claim_model_probability():
    out = compile_authorized_prop_handoff(
        board(), now=NOW, source_use_permitted=True,
        official_resolver=official, exact_offer_refresh=fresh_offer,
    )
    assert out["status"] == "INPUT_PROPOSALS_READY"
    assert len(out["scoring_input_proposals"]) == 2
    assert len(out["batches_for_future_action"]) == 1
    assert out["scoring_input_proposals"][0]["direction"] == "MORE"
    assert out["scoring_input_proposals"][1]["direction"] == "LESS"
    assert out["scoring_input_proposals"][0]["event_id"] == "NBA:official-game-1"
    assert out["scoring_input_proposals"][0]["line"] == 22.5
    assert out["scoring_input_proposals"][0]["stat_type"] == "POINTS"
    assert out["proposal_provenance"][0]["offer_type"] == "standard"
    assert out["proposal_provenance"][0]["exact_display_line"] == "22.5"
    assert out["proposal_provenance"][0]["official_event_id"] == "NBA:official-game-1"
    assert out["proposal_provenance"][0]["official_player_id"] == "NBA:official-player-1"
    assert out["proposal_provenance"][0]["source_projection_id"] == "pp_12"
    assert out["proposal_provenance"][0]["official_prizepicks_offer_independently_verified"] is False
    assert len(out["proposal_provenance"]) == 2
    assert out["scoring_attempted"] is False
    assert out["action_receipts"] == []
    assert out["model_probability"] is None
    assert out["calibrated_lower_bound"] is None
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False
    assert out["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_official_mismatch_can_not_become_action_input():
    for key, value, reason in [
        ("stat_display", "Rebounds", "PROP_IDENTITY_STAT_CONFLICT"),
        ("period", "1st Quarter", "PROP_IDENTITY_PERIOD_CONFLICT"),
        ("player", "Another Person", "PROP_IDENTITY_PLAYER_CONFLICT"),
        ("team", "NYK", "PROP_IDENTITY_TEAM_CONFLICT"),
        ("event_start_time", "2026-11-07T00:00:00Z", "PROP_IDENTITY_EVENT_START_CONFLICT"),
        ("event_status", "FINAL", "EVENT_NOT_PREGAME"),
        ("official_source", "PROPSMADNESS", "PROP_IDENTITY_AUTHORITY_INVALID"),
    ]:
        def resolver(row):
            result = official(row)
            result[key] = value
            return result
        out = compile_authorized_prop_handoff(
            board(), now=NOW, source_use_permitted=True,
            official_resolver=resolver, exact_offer_refresh=fresh_offer,
        )
        assert not out["scoring_input_proposals"], key
        assert {b["typed_blocker"] for b in out["blocked_rows"]} == {reason}, key


def test_offer_line_move_and_side_change_block_reuse():
    for change, reason in [
        ({"exact_display_line": "23.5"}, "PROP_OFFER_LINE_CHANGED"),
        ({"direction": "LESS"}, "PROP_OFFER_DIRECTION_CHANGED"),
        ({"available": False}, "PROP_OFFER_NOT_AVAILABLE"),
        ({"source_observed_at": "2026-11-06T18:00:00Z"}, "PROP_OFFER_REFRESH_INVALID"),
    ]:
        def refresh(row):
            result = fresh_offer(row)
            result.update(change)
            return result
        # Opposite side may legitimately match the changed direction: at
        # least one originally discovered side must be blocked.
        out = compile_authorized_prop_handoff(
            board(), now=NOW, source_use_permitted=True,
            official_resolver=official, exact_offer_refresh=refresh,
        )
        assert reason in {b["typed_blocker"] for b in out["blocked_rows"]}, change
        assert out["reconciliation_valid"]


def test_duplicate_thesis_reconciles_not_double_counts():
    receipt = board()
    receipt["directional_candidates"].append(
        deepcopy(receipt["directional_candidates"][0])
    )
    out = compile_authorized_prop_handoff(
        receipt, now=NOW, source_use_permitted=True,
        official_resolver=official, exact_offer_refresh=fresh_offer,
    )
    assert len(out["scoring_input_proposals"]) == 2
    assert len(out["duplicate_rows"]) == 1
    assert out["received_directional_candidates"] == 3
    assert out["reconciliation_valid"]
    assert len({r["row_key"] for r in out["scoring_input_proposals"]}) == 2


def test_no_source_upgrades_from_user_supplied_boolean_flags():
    receipt = board()
    for row in receipt["directional_candidates"]:
        row["official_identity_verified"] = True
        row["probability_publishable"] = True
    out = compile_authorized_prop_handoff(
        receipt, now=NOW, source_use_permitted=True,
        official_resolver=official, exact_offer_refresh=fresh_offer,
    )
    assert not out["scoring_input_proposals"]
    assert {x["typed_blocker"] for x in out["blocked_rows"]} == {
        "PROP_HANDOFF_RESEARCH_AUTHORITY_CONFLICT"
    }


def test_official_opponent_conflict_is_held_but_trusted_alias_is_accepted():
    def conflicting(row):
        return {**official(row), "opponent": "PHI"}
    result = compile_authorized_prop_handoff(
        board(), now=NOW, source_use_permitted=True,
        official_resolver=conflicting, exact_offer_refresh=fresh_offer,
    )
    assert not result["scoring_input_proposals"]
    assert {r["typed_blocker"] for r in result["blocked_rows"]} == {"PROP_IDENTITY_OPPONENT_CONFLICT"}

    def aliased(row):
        return {**official(row), "opponent": "New York Knicks", "opponent_aliases": ["NYK"]}
    accepted = compile_authorized_prop_handoff(
        board(), now=NOW, source_use_permitted=True,
        official_resolver=aliased, exact_offer_refresh=fresh_offer,
    )
    assert accepted["status"] == "INPUT_PROPOSALS_READY"
    assert len(accepted["scoring_input_proposals"]) == 2


def test_incomplete_discovery_retains_partial_proposals_and_original_blockers():
    receipt = board()
    receipt["source_coverage_complete_for_requested_sport"] = False
    receipt["blockers"] = ["PROP_FEED_CREDIT_BUDGET_EXHAUSTED", "PROP_FEED_CURSOR_CYCLE"]
    out = compile_authorized_prop_handoff(
        receipt, now=NOW, source_use_permitted=True,
        official_resolver=official, exact_offer_refresh=fresh_offer,
    )
    assert len(out["scoring_input_proposals"]) == 2
    assert out["status"] == "BLOCKED_OR_EMPTY"
    assert out["source_coverage_complete_for_requested_sport"] is False
    assert out["source_discovery_blockers"] == receipt["blockers"]
    assert out["probability_publishable"] is False and out["can_execute"] is False


def test_partial_game_and_tiered_offers_cannot_be_submitted_as_full_game_standard():
    for field, value in (("period_display", "First quarter"), ("offer_type", "goblin")):
        receipt = board()
        for row in receipt["directional_candidates"]:
            row[field] = value
        def resolver(row):
            resolved = official(row)
            if field == "period_display":
                resolved["period"] = value
            return resolved
        def refresh(row):
            fresh = fresh_offer(row)
            if field == "offer_type":
                fresh["offer_type"] = value
            return fresh
        out = compile_authorized_prop_handoff(
            receipt, now=NOW, source_use_permitted=True,
            official_resolver=resolver, exact_offer_refresh=refresh,
        )
        assert out["scoring_input_proposals"] == [], field
        assert {r["typed_blocker"] for r in out["blocked_rows"]} == {
            "PROP_HANDOFF_EXACT_ROUTE_UNRESOLVED"
        }, field


def test_bad_discovery_blocker_contract_fails_closed():
    receipt = board()
    receipt["blockers"] = [{"untrusted": "object"}]
    import pytest
    with pytest.raises(ValueError, match="PROP_HANDOFF_DISCOVERY_BLOCKERS_INVALID"):
        compile_authorized_prop_handoff(
            receipt, now=NOW, source_use_permitted=True,
            official_resolver=official, exact_offer_refresh=fresh_offer,
        )


def test_official_resolver_and_offer_refresh_exceptions_remain_typed():
    def failed(*args):
        raise RuntimeError("provider offline")
    for resolver, refresh, expected in (
        (failed, fresh_offer, "PROP_IDENTITY_OFFICIAL_RESOLVER_FAILED"),
        (official, failed, "PROP_OFFER_REFRESH_FAILED"),
    ):
        out = compile_authorized_prop_handoff(
            board(), now=NOW, source_use_permitted=True,
            official_resolver=resolver, exact_offer_refresh=refresh,
        )
        assert out["scoring_input_proposals"] == []
        assert {r["typed_blocker"] for r in out["blocked_rows"]} == {expected}


def test_scorer_compatibility_must_be_explicitly_verified_server_side():
    source = board()
    for check in (None, lambda *_: False, lambda *_: "yes"):
        out = _compile_authorized_prop_handoff(
            source, now=NOW, source_use_permitted=True,
            official_resolver=official, exact_offer_refresh=fresh_offer,
            scorer_compatibility=check,
        )
        assert out["scoring_input_proposals"] == []
        assert {r["typed_blocker"] for r in out["blocked_rows"]} == {
            "PROP_HANDOFF_SCORER_COMPATIBILITY_UNVERIFIED"
        }
        assert out["status"] == "BLOCKED_OR_EMPTY"
    def unsupported(row):
        return {**official(row), "stat_type": "UNREGISTERED_PLAYER_STAT"}
    out = compile_authorized_prop_handoff(
        source, now=NOW, source_use_permitted=True,
        official_resolver=unsupported, exact_offer_refresh=fresh_offer,
    )
    assert not out["scoring_input_proposals"]
    assert {r["typed_blocker"] for r in out["blocked_rows"]} == {
        "PROP_HANDOFF_SCORER_COMPATIBILITY_UNVERIFIED"
    }
    def raises(*_args):
        raise ConnectionError("registry unavailable")
    out = _compile_authorized_prop_handoff(
        source, now=NOW, source_use_permitted=True,
        official_resolver=official, exact_offer_refresh=fresh_offer,
        scorer_compatibility=raises,
    )
    assert not out["scoring_input_proposals"]
    assert {r["typed_blocker"] for r in out["blocked_rows"]} == {
        "PROP_HANDOFF_SCORER_COMPATIBILITY_UNVERIFIED"
    }
