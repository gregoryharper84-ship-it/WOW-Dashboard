"""Synthetic regressions for NHL evidence and 3-goal-loss research checks."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.experiments.nhl_evidence_quality.quality_gate import (
    REQUIRED_ROLES,
    audit_nhl_ml_loss_tails,
    screen_nhl_pregame_evidence,
)

EVENT = "2026020333"
START = "2026-11-17T19:00:00Z"
SNAP = "2026-11-17T18:20:00Z"
HEX = "a" * 64


def card(family="PLAYER_PROP"):
    source = []
    for role in sorted(REQUIRED_ROLES[family]):
        item = {
            "role": role, "official_event_id": EVENT, "provider": "synthetic-fixture",
            "source_hash": HEX, "as_of_utc": "2026-11-17T18:00:00Z",
            "captured_at_utc": "2026-11-17T18:10:00Z",
        }
        if role == "MARKET_OFFER":
            item.update(market_id="synthetic-market", exact_line=2.5,
                        side="OVER", book="synthetic-book")
        if role == "PLAYER_TOI_PP":
            item["metrics"] = {"toi_minutes_per_game": 17.5,
                               "pp_toi_minutes_per_game": 3.2}
        if role == "PLAYER_OPPORTUNITY":
            item["metrics"] = {"sog_per60": 8.1, "shot_attempts_per60": 13.2}
        source.append(item)
    return {
        "market_family": family, "official_event_id": EVENT,
        "event_start_utc": START, "snapshot_at_utc": SNAP,
        "pregame_receipt_id": "synthetic-frozen-forecast", "pregame_source_hash": HEX,
        "goalie_status": "CONFIRMED", "trusted_goalie_receipt_id": "synthetic-external-registry",
        "settlement_basis": "FULL_GAME_INCLUDING_OT_SO",
        "player_id": "8478402", "stat_type": "SHOTS_ON_GOAL", "exact_line": 2.5,
        "side": "OVER", "market_id": "synthetic-market",
        "source_evidence": source,
    }


def rows():
    base = {
        "settlement_basis": "FULL_GAME_INCLUDING_OT_SO",
        "settlement_source": "INDEPENDENT_OFFICIAL",
        "settlement_receipt_id": "claimed-official-receipt",
        "forecast_receipt_id": "frozen-forecast", "forecast_source_hash": HEX,
        "event_start_utc": START, "forecast_at_utc": SNAP,
        "finalized_at_utc": "2026-11-18T04:30:00Z",
    }
    samples = [
        ("2026020333", "BOS", "LAK", "BOS", 0, 3),
        ("2026020334", "FLA", "EDM", "FLA", 2, 5),
        ("2026020335", "PHI", "BUF", "BUF", 4, 3),
        ("2026020336", "SJS", "CGY", "SJS", 2, 5),
    ]
    return [{**base, "official_event_id": event, "home": home, "away": away,
             "predicted_winner": pick, "home_goals": h, "away_goals": a}
            for event, home, away, pick, h, a in samples]


def test_valid_sog_source_precheck_never_publishes_a_probability():
    r = screen_nhl_pregame_evidence(card())
    assert r["status"] == "NHL_RESEARCH_EVIDENCE_PRECHECK_PASSED"
    assert r["probability_publishable"] is False
    assert r["model_probability"] is None
    assert r["source_authentication"] == "REQUIRES_INDEPENDENT_SERVER_RECEIPTS"
    assert r["can_execute"] is False


def test_valid_ml_source_precheck_research_only():
    r = screen_nhl_pregame_evidence(card("FULL_GAME_ML"))
    assert r["status"] == "NHL_RESEARCH_EVIDENCE_PRECHECK_PASSED"
    assert r["probability_publishable"] is False


def test_missing_source_role_and_missing_goalie_hold():
    c = card()
    c["source_evidence"] = [r for r in c["source_evidence"] if r["role"] != "LINE_ASSIGNMENT"]
    c["trusted_goalie_receipt_id"] = ""
    r = screen_nhl_pregame_evidence(c)
    assert r["status"] == "NHL_EVIDENCE_HOLD"
    assert "LINE_ASSIGNMENT" in r["missing_roles"]
    assert "NHL_TRUSTED_GOALIE_CONFIRMATION_NOT_PROVEN" in r["blockers"]


def test_postgame_and_stale_sources_cannot_pass():
    c = card()
    c["snapshot_at_utc"] = "2026-11-17T20:00:00Z"
    assert "NHL_PREGAME_CHRONOLOGY_INVALID" in screen_nhl_pregame_evidence(c)["blockers"]
    c = card()
    c["source_evidence"][0]["as_of_utc"] = "2026-11-10T18:00:00Z"
    assert any(x.startswith("NHL_EVIDENCE_STALE_")
               for x in screen_nhl_pregame_evidence(c)["blockers"])


def test_market_line_and_identity_cannot_be_substituted():
    c = card()
    c["source_evidence"][-1]["official_event_id"] = "2026020999"
    assert "NHL_EVIDENCE_SOURCE_IDENTITY_INVALID" in screen_nhl_pregame_evidence(c)["blockers"]
    c = card()
    offer = next(x for x in c["source_evidence"] if x["role"] == "MARKET_OFFER")
    offer["exact_line"] = 3.5
    assert "NHL_EXACT_OFFER_SOURCE_MISMATCH" in screen_nhl_pregame_evidence(c)["blockers"]


def test_missing_real_opportunity_and_usage_metrics_hold():
    c = card()
    next(x for x in c["source_evidence"] if x["role"] == "PLAYER_OPPORTUNITY")["metrics"].pop("sog_per60")
    assert "NHL_PLAYER_OPPORTUNITY_METRICS_MISSING" in screen_nhl_pregame_evidence(c)["blockers"]
    c = card()
    next(x for x in c["source_evidence"] if x["role"] == "PLAYER_TOI_PP")["metrics"] = {}
    assert "NHL_PLAYER_USAGE_METRICS_MISSING" in screen_nhl_pregame_evidence(c)["blockers"]


def test_unhashable_malformed_inputs_fail_closed_not_crash():
    for key, bad in [("market_family", []), ("stat_type", []), ("source_evidence", [dict(role=[])])]:
        c = card()
        c[key] = bad
        r = screen_nhl_pregame_evidence(c)
        assert r["status"] == "NHL_EVIDENCE_HOLD"
    c = card()
    c["exact_line"] = 10 ** 10000
    assert screen_nhl_pregame_evidence(c)["status"] == "NHL_EVIDENCE_HOLD"


def test_exact_market_and_settlement_semantics():
    c = card()
    c["side"] = "BOTH"
    assert "NHL_PROP_EXACT_MARKET_INVALID" in screen_nhl_pregame_evidence(c)["blockers"]
    c = card("FULL_GAME_ML")
    c["settlement_basis"] = "REGULATION_3_WAY"
    assert "NHL_ML_SETTLEMENT_BASIS_INVALID" in screen_nhl_pregame_evidence(c)["blockers"]


def test_nov17_loss_severity_three_of_four_not_four_of_four():
    r = audit_nhl_ml_loss_tails(rows())
    assert r["status"] == "NHL_ML_LOSS_MARGIN_AUDIT_COMPLETE"
    assert (r["losses"], r["losses_by_3plus"]) == (4, 3)
    assert r["loss_margins"] == [3, 3, 1, 3]
    assert r["severe_loss_share_of_losses"] == 0.75
    assert r["fitted_tail_probability"] is None
    assert r["causal_attribution"] == "NOT_ESTABLISHED"


def test_no_hindsight_no_duplicate_no_unsupported_source():
    for change, expected in [
        (lambda r: r[0].update(forecast_at_utc="2026-11-18T00:01:00Z"),
         "NHL_AUDIT_CHRONOLOGY_INVALID"),
        (lambda r: r[1].update(official_event_id=r[0]["official_event_id"]),
         "NHL_AUDIT_DUPLICATE_EVENT"),
        (lambda r: r[2].update(settlement_source="BLOG"),
         "NHL_AUDIT_RECEIPT_INVALID"),
        (lambda r: r[0].update(home_goals=0.1), "NHL_AUDIT_SCORE_INVALID"),
    ]:
        sample = rows()
        change(sample)
        out = audit_nhl_ml_loss_tails(sample)
        assert out["status"] == "NHL_ML_AUDIT_HOLD"
        assert out["games_graded"] == 0
        assert expected in out["blockers"]


def test_no_fake_full_slate_coverage_and_execution():
    out = audit_nhl_ml_loss_tails(rows())
    assert out["games_graded"] == 4
    assert out["can_execute"] is False
    assert out["model_certification_change"] is False
    assert audit_nhl_ml_loss_tails([])["status"] == "NHL_ML_AUDIT_HOLD"
