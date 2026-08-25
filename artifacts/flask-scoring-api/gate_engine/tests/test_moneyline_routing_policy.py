"""Regression coverage for LLP Moneyline Probability Expert handoff authority."""
from __future__ import annotations

import pytest

from gate_engine.cross_sport_ranker import from_db, rank
from gate_engine.moneyline.routing_policy import (
    CALIBRATED_RESEARCH_RANKING_ELIGIBLE,
    CONTROLLING_SKILL,
    WATCH_ONLY_UNTIL_CALIBRATED,
    attest_sport_specific_weight_profile,
    build_specialist_handoff,
    is_verified_specialist_handoff,
)


def _ledger() -> dict:
    return {
        "id": 101,
        "date": "2026-08-21", "sport": "MLB", "league": "MLB",
        "market": "h2h", "side": "HOME", "odds": -120, "line": 0,
        "book": "example", "close": -125, "model_probability": 0.61,
        "no_vig_probability": 0.55, "edge": 0.06, "stake": 0.0,
        "final_label": "LLP_WATCH", "failure_tags": [], "clv": 0.01,
        "result": "PENDING", "roi": 0.0, "brier_bucket": "UNSET",
        "postmortem_note": "historical calibration record",
        "source_snapshot_id": "snapshot-calibration-101",
    }


def _profile() -> dict:
    return {"sport": "MLB", "profile_id": "mlb-moneyline-v1", "weights": {"form": 1.0}}


def _snapshot() -> dict:
    return {
        "independent_probability": 0.64,
        "calibrated_probability": 0.61,
        "calibrated_probability_lower_bound": 0.57,
        "calibrated_probability_upper_bound": 0.66,
        "net_edge": 0.06,
        "snapshot_hash": "snapshot-1",
    }


def _row(**overrides) -> dict:
    row = {
        "sport": "MLB", "team": "Boston Red Sox", "opponent": "New York Yankees",
        "event_id": "mlb-1", "market_type": "h2h", "slate_date": "2026-08-21",
    }
    row.update(overrides)
    return row


def _handoff(*, row: dict | None = None, enrichment: dict | None = None) -> dict:
    return build_specialist_handoff(
        row=row or _row(),
        enrichment=enrichment or {},
        probability_snapshot=_snapshot(),
        blockers=["UPSTREAM_BLOCKER"],
        governance_ceiling="MODEL_QUALIFIED_HOLD",
        model_id="mlb-moneyline-logit-v1",
        model_status="ACTIVE",
    )


@pytest.fixture(autouse=True)
def _server_attestation_and_persisted_ledger(monkeypatch):
    import gate_engine.llp_governance as governance
    import gate_engine.runtime_provenance as provenance

    monkeypatch.setattr(provenance, "_attestation_key", lambda: b"moneyline-policy-test-key")
    monkeypatch.setattr(governance, "get_calibration_ledger", lambda limit=500: [_ledger()])


def test_unproven_specialist_stays_watch_only_and_strips_authority_fields():
    handoff = _handoff(
        row=_row(
            terminal_label="LLP_APPROVED",
            final_label="LLP_PLAYABLE",
            stake=10,
            execute=True,
        )
    )

    assert handoff["displayed_tier"] == WATCH_ONLY_UNTIL_CALIBRATED
    assert handoff["ranking_eligible"] is False
    assert "SPORT_SPECIFIC_WEIGHT_PROFILE_REQUIRED:sport=MLB" in handoff["blockers"]
    assert "CALIBRATION_LEDGER_REQUIRED" in handoff["blockers"]
    assert {"terminal_label", "final_label", "stake", "execute"} <= set(handoff["rejected_authority_fields"])
    assert not {"terminal_label", "final_label", "llp_label", "approved", "playable", "stake", "execute"} & set(handoff)
    assert "governance_ceiling" not in handoff
    assert handoff["can_execute"] is False
    assert handoff["dry_run_only"] is True


def test_valid_historical_proofs_graduate_only_to_research_ranking():
    handoff = _handoff(
        enrichment={
            "sport_specific_weight_profile": attest_sport_specific_weight_profile(_profile()),
            "calibration_ledger": {"id": 101},
        }
    )

    assert handoff["displayed_tier"] == CALIBRATED_RESEARCH_RANKING_ELIGIBLE
    assert handoff["ranking_eligible"] is True
    assert handoff["specialist_status"] == "CALIBRATED_RESEARCH_ONLY"
    assert is_verified_specialist_handoff(handoff)


def test_handoff_integrity_detects_probability_or_blocker_mutation():
    handoff = _handoff(enrichment={
        "sport_specific_weight_profile": attest_sport_specific_weight_profile(_profile()),
        "calibration_ledger": {"id": 101},
    })
    handoff["calibrated_probability"] = 0.90
    assert is_verified_specialist_handoff(handoff) is False


def test_ungraduated_object_cannot_enter_wow_ranking():
    result = rank([_row(specialist_probability=_handoff())])
    assert result.n_eligible == 0
    assert result.highest_hit_probability == []
    assert result.best_multi_leg == []


def test_graduated_object_is_research_only_and_never_edge_or_slip_advice():
    handoff = _handoff(enrichment={
        "sport_specific_weight_profile": attest_sport_specific_weight_profile(_profile()),
        "calibration_ledger": {"id": 101},
    })
    result = rank([_row(specialist_probability=handoff)])

    assert result.n_eligible == 1
    assert len(result.highest_hit_probability) == 1
    ranked = result.highest_hit_probability[0].to_dict()
    assert ranked["research_only"] is True
    assert ranked["execution_advice"] is False
    assert ranked["blockers"] == ["UPSTREAM_BLOCKER"]
    assert ranked["displayed_tier"] == CALIBRATED_RESEARCH_RANKING_ELIGIBLE
    assert result.best_edge == []
    assert result.best_multi_leg == []


def test_profile_or_ledger_claims_cannot_be_forged_from_request_data():
    handoff = _handoff(enrichment={
        "sport_specific_weight_profile": _profile(),
        "calibration_ledger": _ledger(),
    })
    assert handoff["ranking_eligible"] is False
    assert handoff["displayed_tier"] == WATCH_ONLY_UNTIL_CALIBRATED


def test_cross_sport_persisted_ledger_cannot_graduate_mlb(monkeypatch):
    foreign = _ledger()
    foreign["sport"] = "NBA"
    import gate_engine.llp_governance as governance
    monkeypatch.setattr(governance, "get_calibration_ledger", lambda limit=500: [foreign])

    handoff = _handoff(enrichment={
        "sport_specific_weight_profile": attest_sport_specific_weight_profile(_profile()),
        "calibration_ledger": {"id": 101},
    })
    assert handoff["ranking_eligible"] is False
    assert "CALIBRATION_LEDGER_SPORT_MISMATCH" in handoff["blockers"]


def test_tampered_graduation_state_cannot_bypass_handoff_attestation():
    handoff = _handoff()
    handoff["ranking_eligible"] = True
    handoff["displayed_tier"] = CALIBRATED_RESEARCH_RANKING_ELIGIBLE
    handoff["calibration_ledger_status"] = "CALIBRATION_LEDGER_COMPLETE"
    handoff["sport_specific_weight_profile_status"] = "VERIFIED"
    assert is_verified_specialist_handoff(handoff) is False


def test_llp_row_with_stripped_handoff_fails_closed_in_direct_ranking():
    stripped = _row(
        controlling_skill=CONTROLLING_SKILL,
        market_family="OUTRIGHT_WINNER",
        terminal_label="MONEY_QUALIFIED",
        lower_bound=0.60,
    )
    result = rank([stripped])
    assert result.n_eligible == 0
    assert result.highest_hit_probability == []


def test_unmarked_h2h_row_fails_closed_in_direct_ranking():
    result = rank([_row(
        market="h2h",
        terminal_label="MONEY_QUALIFIED",
        lower_bound=0.60,
        market_no_vig_probability=0.50,
    )])
    assert result.n_eligible == 0
    assert result.highest_hit_probability == []
    assert result.best_edge == []


def test_db_ranking_fails_closed_when_llp_handoff_was_not_persisted(monkeypatch):
    import gate_engine.prediction_ledger as prediction_ledger
    monkeypatch.setattr(prediction_ledger, "read_predictions", lambda *args, **kwargs: [{
        "sport": "MLB", "player_name": "Boston Red Sox", "stat_key": "h2h",
        "terminal_label": "MONEY_QUALIFIED", "lower_bound": 0.60,
        "pipeline_meta": {
            "controlling_skill": CONTROLLING_SKILL,
            "market_family": "OUTRIGHT_WINNER",
        },
    }])

    result = from_db(object())
    assert result.n_eligible == 0
    assert result.highest_hit_probability == []


def test_db_ranking_fails_closed_for_unmarked_h2h_row(monkeypatch):
    import gate_engine.prediction_ledger as prediction_ledger
    monkeypatch.setattr(prediction_ledger, "read_predictions", lambda *args, **kwargs: [{
        "sport": "MLB", "player_name": "Boston Red Sox", "stat_key": "h2h",
        "market": "h2h", "terminal_label": "MONEY_QUALIFIED", "lower_bound": 0.60,
        "market_probability": 0.50, "pipeline_meta": {},
    }])

    result = from_db(object())
    assert result.n_eligible == 0
    assert result.highest_hit_probability == []
    assert result.best_edge == []


def test_db_ranking_preserves_verified_specialist_handoff(monkeypatch):
    import gate_engine.prediction_ledger as prediction_ledger
    handoff = _handoff(enrichment={
        "sport_specific_weight_profile": attest_sport_specific_weight_profile(_profile()),
        "calibration_ledger": {"id": 101},
    })
    monkeypatch.setattr(prediction_ledger, "read_predictions", lambda *args, **kwargs: [{
        "sport": "MLB", "player_name": "Boston Red Sox", "stat_key": "h2h",
        "terminal_label": "MONEY_QUALIFIED", "lower_bound": 0.57,
        "pipeline_meta": {
            "controlling_skill": CONTROLLING_SKILL,
            "market_family": "OUTRIGHT_WINNER",
            "specialist_probability": handoff,
        },
    }])

    result = from_db(object())
    assert result.n_eligible == 1
    assert result.highest_hit_probability[0].research_only is True
    assert result.best_edge == []
    assert result.best_multi_leg == []


def test_prediction_ledger_persists_exact_signed_specialist_envelope():
    from gate_engine.prediction_ledger import write_prediction

    class Cursor:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql, params):
            self.conn.params = params

    class Connection:
        def __init__(self):
            self.params = None

        def cursor(self):
            return Cursor(self)

        def commit(self):
            pass

    handoff = _handoff(enrichment={
        "sport_specific_weight_profile": attest_sport_specific_weight_profile(_profile()),
        "calibration_ledger": {"id": 101},
    })
    conn = Connection()
    write_prediction(conn, _row(
        specialist_probability=handoff,
        controlling_skill=CONTROLLING_SKILL,
        market_family="OUTRIGHT_WINNER",
        objective="OUTRIGHT_WIN_PROBABILITY_ONLY",
    ))

    import json
    stored_meta = json.loads(conn.params[-1])
    assert stored_meta["specialist_probability"] == handoff
    assert stored_meta["controlling_skill"] == CONTROLLING_SKILL
    assert stored_meta["llp_moneyline_routing_required"] is True